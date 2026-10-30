"""SQLite access and schema migrations (applied automatically on startup)."""
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import get_settings

SCHEMA_V1 = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE centers (id INTEGER PRIMARY KEY, name TEXT NOT NULL);

CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('parent','operator')),
  password_hash TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  must_change_password INTEGER NOT NULL DEFAULT 0,
  failed_attempts INTEGER NOT NULL DEFAULT 0,
  locked_until TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE knowledge_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  body TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  updated_by TEXT
);

CREATE TABLE knowledge_versions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER NOT NULL REFERENCES knowledge_entries(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  changed_by TEXT,
  changed_at TEXT NOT NULL
);

CREATE TABLE questions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  parent_pseudo TEXT NOT NULL,
  scrubbed_text TEXT NOT NULL,
  input_mode TEXT NOT NULL DEFAULT 'text',
  category TEXT,
  flags TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL
);
CREATE INDEX idx_questions_user ON questions(user_id, created_at);
CREATE INDEX idx_questions_created ON questions(created_at);

-- Original (unscrubbed) text: parent-only, encrypted, never exposed to operators or the LLM.
CREATE TABLE parent_question_text (
  question_id INTEGER PRIMARY KEY REFERENCES questions(id) ON DELETE CASCADE,
  original_enc TEXT NOT NULL
);

CREATE TABLE answers (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
  text TEXT NOT NULL DEFAULT '',
  confidence REAL,
  source_ids TEXT NOT NULL DEFAULT '[]',
  generated_by TEXT NOT NULL DEFAULT 'llm',          -- llm | staff_qa | template | operator
  status TEXT NOT NULL DEFAULT 'processing',         -- processing | answered | pending_staff | answered_by_staff | refused | dismissed
  draft_text TEXT,                                   -- LLM draft that was NOT shown to the parent
  review_status TEXT NOT NULL DEFAULT 'unreviewed',  -- unreviewed | reviewed | corrected
  reviewed_by TEXT,
  reviewed_at TEXT,
  corrected_text TEXT,
  corrected_by TEXT,
  corrected_at TEXT,
  feedback TEXT,                                     -- up | down
  created_at TEXT NOT NULL,
  answered_at TEXT
);
CREATE INDEX idx_answers_status ON answers(status);

CREATE TABLE qa_pairs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  source TEXT NOT NULL,                              -- operator_answer | correction | seed
  question_id INTEGER REFERENCES questions(id) ON DELETE SET NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_by TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE escalations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  question_id INTEGER NOT NULL UNIQUE REFERENCES questions(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'open',               -- open | answered | dismissed
  priority TEXT NOT NULL DEFAULT 'normal',           -- normal | high
  reason TEXT NOT NULL,                              -- no_answer | low_confidence | sensitive | llm_error
  answered_by TEXT,
  answer_text TEXT,
  dismiss_reason TEXT,
  created_at TEXT NOT NULL,
  resolved_at TEXT
);
CREATE INDEX idx_escalations_status ON escalations(status);

CREATE TABLE notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,                                -- staff_answer | correction
  question_id INTEGER NOT NULL,
  is_read INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  actor TEXT NOT NULL,
  action TEXT NOT NULL,
  target TEXT,
  detail TEXT,
  created_at TEXT NOT NULL
);
"""

MIGRATIONS = [SCHEMA_V1]


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def now_iso() -> str:
    return iso(now())


def ago_iso(**kwargs) -> str:
    return iso(now() - timedelta(**kwargs))


def connect() -> sqlite3.Connection:
    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    # One connection per request, used sequentially. FastAPI may run a dependency's setup, the endpoint
    # and its cleanup (commit) on different worker threads, so the thread check must be disabled.
    conn = sqlite3.connect(s.db_path, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def get_db():
    """FastAPI dependency: one connection per request, commit on success, rollback on error."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    conn = connect()
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            conn.executescript(sql)
            conn.execute(f"PRAGMA user_version={i}")
        if not conn.execute("SELECT 1 FROM centers").fetchone():
            conn.execute("INSERT INTO centers (id, name) VALUES (1, 'Little Acorns Learning Center')")
        conn.commit()
    finally:
        conn.close()


def audit(db: sqlite3.Connection, actor: str, action: str, target: str = "", detail: str = "") -> None:
    db.execute(
        "INSERT INTO audit_log (actor, action, target, detail, created_at) VALUES (?,?,?,?,?)",
        (actor, action, target, detail, now_iso()),
    )
