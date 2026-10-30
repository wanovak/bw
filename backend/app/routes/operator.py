import json
import sqlite3
import statistics
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import guard, pii, service
from ..config import CATEGORIES, get_settings
from ..db import audit, get_db, now, now_iso
from ..security import hash_password, require_operator, validate_password_strength
from .auth import EMAIL_RE

router = APIRouter(prefix="/api/operator", tags=["operator"], dependencies=[Depends(require_operator)])


# ---------- models ----------

class KnowledgeIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(default="Other", max_length=60)
    body: str = Field(min_length=1, max_length=8000)
    active: bool = True


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)
    add_to_kb: bool = True       # store as an approved Q&A pair the LLM can use
    add_as_entry: bool = False   # also create a knowledge entry
    confirm_pii: bool = False    # operator acknowledged a PII warning


class DismissIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)


class CorrectionIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    confirm_pii: bool = False


class QaPairIn(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=2000)
    active: bool = True


class OperatorIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


def _check_pii(text: str, confirmed: bool) -> None:
    types = pii.detect(text)
    if types and not confirmed:
        raise HTTPException(409, detail={"code": "pii_warning", "types": sorted(types)})


# ---------- summary ----------

@router.get("/summary")
def summary(db: sqlite3.Connection = Depends(get_db)):
    r = db.execute(
        "SELECT COUNT(*) AS open, COALESCE(SUM(priority='high'),0) AS high FROM escalations WHERE status='open'"
    ).fetchone()
    return {"open_escalations": r["open"], "high_priority": r["high"]}


# ---------- knowledge base ----------

def _entry(r: sqlite3.Row) -> dict:
    return {k: r[k] for k in ("id", "title", "category", "body", "updated_at", "updated_by")} | {"active": bool(r["active"])}


@router.get("/knowledge")
def list_knowledge(q: str = "", db: sqlite3.Connection = Depends(get_db)):
    like = f"%{q.strip()}%"
    rows = db.execute(
        "SELECT * FROM knowledge_entries WHERE title LIKE ? OR body LIKE ? ORDER BY category, title", (like, like)
    ).fetchall()
    return {"entries": [_entry(r) for r in rows], "categories": CATEGORIES}


@router.post("/knowledge")
def create_knowledge(body: KnowledgeIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        "INSERT INTO knowledge_entries (title, category, body, active, updated_at, updated_by) VALUES (?,?,?,?,?,?)",
        (body.title.strip(), body.category, body.body, int(body.active), now_iso(), op["name"]),
    )
    audit(db, op["email"], "knowledge.create", f"K{cur.lastrowid}", body.title)
    return _entry(db.execute("SELECT * FROM knowledge_entries WHERE id=?", (cur.lastrowid,)).fetchone())


@router.put("/knowledge/{entry_id}")
def update_knowledge(entry_id: int, body: KnowledgeIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    old = db.execute("SELECT * FROM knowledge_entries WHERE id=?", (entry_id,)).fetchone()
    if not old:
        raise HTTPException(404, "Entry not found.")
    db.execute(
        "INSERT INTO knowledge_versions (entry_id, title, body, changed_by, changed_at) VALUES (?,?,?,?,?)",
        (entry_id, old["title"], old["body"], old["updated_by"], old["updated_at"]),
    )
    db.execute(
        "UPDATE knowledge_entries SET title=?, category=?, body=?, active=?, updated_at=?, updated_by=? WHERE id=?",
        (body.title.strip(), body.category, body.body, int(body.active), now_iso(), op["name"], entry_id),
    )
    audit(db, op["email"], "knowledge.update", f"K{entry_id}", body.title)
    return _entry(db.execute("SELECT * FROM knowledge_entries WHERE id=?", (entry_id,)).fetchone())


@router.delete("/knowledge/{entry_id}")
def delete_knowledge(entry_id: int, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    old = db.execute("SELECT title FROM knowledge_entries WHERE id=?", (entry_id,)).fetchone()
    if not old:
        raise HTTPException(404, "Entry not found.")
    db.execute("DELETE FROM knowledge_entries WHERE id=?", (entry_id,))
    audit(db, op["email"], "knowledge.delete", f"K{entry_id}", old["title"])
    return {"ok": True}


@router.get("/knowledge/{entry_id}/versions")
def knowledge_versions(entry_id: int, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT id, title, body, changed_by, changed_at FROM knowledge_versions WHERE entry_id=? ORDER BY id DESC", (entry_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- approved Q&A pairs ----------

@router.get("/qa-pairs")
def list_qa_pairs(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM qa_pairs ORDER BY id DESC").fetchall()
    return [dict(r) | {"active": bool(r["active"])} for r in rows]


@router.put("/qa-pairs/{pair_id}")
def update_qa_pair(pair_id: int, body: QaPairIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    if not db.execute("SELECT 1 FROM qa_pairs WHERE id=?", (pair_id,)).fetchone():
        raise HTTPException(404, "Not found.")
    db.execute(
        "UPDATE qa_pairs SET question=?, answer=?, active=? WHERE id=?",
        (body.question, body.answer, int(body.active), pair_id),
    )
    audit(db, op["email"], "qa_pair.update", f"Q{pair_id}")
    return {"ok": True}


@router.delete("/qa-pairs/{pair_id}")
def delete_qa_pair(pair_id: int, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    db.execute("DELETE FROM qa_pairs WHERE id=?", (pair_id,))
    audit(db, op["email"], "qa_pair.delete", f"Q{pair_id}")
    return {"ok": True}


# ---------- escalation queue ----------

@router.get("/escalations")
def list_escalations(status: str = Query("open", pattern="^(open|answered|dismissed|all)$"), db: sqlite3.Connection = Depends(get_db)):
    where, params = "", []
    if status != "all":
        where, params = "WHERE e.status=?", [status]
    rows = db.execute(
        "SELECT e.id AS escalation_id, e.status AS e_status, e.priority, e.reason, e.answered_by, e.answer_text, "
        "e.dismiss_reason, e.created_at AS e_created, e.resolved_at, "
        + service.OPERATOR_SELECT.replace("SELECT ", "", 1).replace(
            "FROM answers a JOIN questions q ON q.id=a.question_id ",
            "FROM escalations e JOIN questions q ON q.id=e.question_id JOIN answers a ON a.question_id=q.id ",
        )
        + where
        + " ORDER BY (e.status='open') DESC, (e.priority='high') DESC, e.id DESC LIMIT 300",
        params,
    ).fetchall()
    out = []
    for r in rows:
        item = service.operator_item(db, r)
        item.update(
            escalation_id=r["escalation_id"], escalation_status=r["e_status"], priority=r["priority"],
            reason=r["reason"], answered_by=r["answered_by"], staff_answer=r["answer_text"],
            dismiss_reason=r["dismiss_reason"], escalated_at=r["e_created"], resolved_at=r["resolved_at"],
        )
        out.append(item)
    return out


def _open_escalation(db: sqlite3.Connection, esc_id: int) -> sqlite3.Row:
    esc = db.execute("SELECT * FROM escalations WHERE id=?", (esc_id,)).fetchone()
    if not esc:
        raise HTTPException(404, "Escalation not found.")
    if esc["status"] != "open":
        raise HTTPException(409, "Another operator has already handled this question.")
    return esc


def _notify(db: sqlite3.Connection, qid: int, kind: str) -> None:
    q = db.execute("SELECT user_id FROM questions WHERE id=?", (qid,)).fetchone()
    if q and q["user_id"]:
        db.execute(
            "INSERT INTO notifications (user_id, kind, question_id, created_at) VALUES (?,?,?,?)",
            (q["user_id"], kind, qid, now_iso()),
        )


@router.post("/escalations/{esc_id}/answer")
def answer_escalation(esc_id: int, body: AnswerIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    esc = _open_escalation(db, esc_id)
    _check_pii(body.answer, body.confirm_pii)
    ts = now_iso()
    # First answer wins: the conditional UPDATE is the lock.
    cur = db.execute(
        "UPDATE escalations SET status='answered', answered_by=?, answer_text=?, resolved_at=? WHERE id=? AND status='open'",
        (op["name"], body.answer, ts, esc_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(409, "Another operator has already handled this question.")
    qid = esc["question_id"]
    db.execute(
        "UPDATE answers SET text=?, generated_by='operator', status='answered_by_staff', answered_at=?, source_ids='[]' "
        "WHERE question_id=?",
        (body.answer, ts, qid),
    )
    q = db.execute("SELECT scrubbed_text FROM questions WHERE id=?", (qid,)).fetchone()
    if body.add_to_kb:
        db.execute(
            "INSERT INTO qa_pairs (question, answer, source, question_id, created_by, created_at) VALUES (?,?,?,?,?,?)",
            (q["scrubbed_text"], body.answer, "operator_answer", qid, op["name"], ts),
        )
    if body.add_as_entry:
        db.execute(
            "INSERT INTO knowledge_entries (title, category, body, updated_at, updated_by) VALUES (?,?,?,?,?)",
            (q["scrubbed_text"][:120], "Other", body.answer, ts, op["name"]),
        )
    _notify(db, qid, "staff_answer")
    audit(db, op["email"], "escalation.answer", f"Q{qid}", f"add_to_kb={body.add_to_kb} add_as_entry={body.add_as_entry}")
    return {"ok": True}


@router.post("/escalations/{esc_id}/dismiss")
def dismiss_escalation(esc_id: int, body: DismissIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    esc = _open_escalation(db, esc_id)
    ts = now_iso()
    cur = db.execute(
        "UPDATE escalations SET status='dismissed', answered_by=?, dismiss_reason=?, resolved_at=? WHERE id=? AND status='open'",
        (op["name"], body.reason, ts, esc_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(409, "Another operator has already handled this question.")
    db.execute(
        "UPDATE answers SET text=?, generated_by='template', status='dismissed', answered_at=? WHERE question_id=?",
        (guard.TEMPLATE_DISMISSED, ts, esc["question_id"]),
    )
    _notify(db, esc["question_id"], "staff_answer")
    audit(db, op["email"], "escalation.dismiss", f"Q{esc['question_id']}", body.reason)
    return {"ok": True}


# ---------- Q&A log & review ----------

@router.get("/log")
def qa_log(
    min_confidence: float | None = Query(None, ge=0, le=1),
    max_confidence: float | None = Query(None, ge=0, le=1),
    date_from: str | None = None,
    date_to: str | None = None,
    flag: str | None = Query(None, pattern="^[a-z_]+$"),
    status: str | None = Query(None, pattern="^[a-z_]+$"),
    review_status: str | None = Query(None, pattern="^[a-z_]+$"),
    needs_review: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
):
    where, params = [], []
    if min_confidence is not None:
        where.append("a.confidence >= ?"); params.append(min_confidence)
    if max_confidence is not None:
        where.append("a.confidence < ?"); params.append(max_confidence)
    if date_from:
        where.append("q.created_at >= ?"); params.append(date_from)
    if date_to:
        where.append("q.created_at < ?"); params.append(date_to + "T23:59:59Z" if len(date_to) == 10 else date_to)
    if flag:
        where.append("q.flags LIKE ?"); params.append(f'%"{flag}"%')
    if status:
        where.append("a.status = ?"); params.append(status)
    if review_status:
        where.append("a.review_status = ?"); params.append(review_status)
    if needs_review:
        band = get_settings().confidence_threshold + 0.05
        where.append(
            "a.review_status='unreviewed' AND (q.flags LIKE '%\"sensitive\"%' OR a.feedback='down' OR "
            "(a.status='answered' AND a.generated_by IN ('llm','staff_qa') AND a.confidence < ?))"
        )
        params.append(band)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = db.execute(f"SELECT COUNT(*) FROM answers a JOIN questions q ON q.id=a.question_id {clause}", params).fetchone()[0]
    rows = db.execute(
        service.OPERATOR_SELECT + clause + " ORDER BY q.id DESC LIMIT ? OFFSET ?", params + [limit, offset]
    ).fetchall()
    return {"total": total, "items": [service.operator_item(db, r) for r in rows]}


@router.post("/answers/{answer_id}/reviewed")
def mark_reviewed(answer_id: int, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        "UPDATE answers SET review_status='reviewed', reviewed_by=?, reviewed_at=? WHERE id=? AND review_status='unreviewed'",
        (op["name"], now_iso(), answer_id),
    )
    if cur.rowcount == 0:
        raise HTTPException(404, "Answer not found or already reviewed.")
    audit(db, op["email"], "answer.reviewed", f"A{answer_id}")
    return {"ok": True}


@router.post("/answers/{answer_id}/correction")
def submit_correction(answer_id: int, body: CorrectionIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    a = db.execute("SELECT * FROM answers WHERE id=?", (answer_id,)).fetchone()
    if not a:
        raise HTTPException(404, "Answer not found.")
    if a["status"] not in ("answered", "answered_by_staff"):
        raise HTTPException(400, "Only delivered answers can be corrected. Use the escalation queue for pending questions.")
    _check_pii(body.text, body.confirm_pii)
    ts = now_iso()
    db.execute(
        "UPDATE answers SET corrected_text=?, corrected_by=?, corrected_at=?, review_status='corrected', "
        "reviewed_by=?, reviewed_at=? WHERE id=?",
        (body.text, op["name"], ts, op["name"], ts, answer_id),
    )
    q = db.execute("SELECT id, scrubbed_text FROM questions WHERE id=?", (a["question_id"],)).fetchone()
    db.execute(
        "INSERT INTO qa_pairs (question, answer, source, question_id, created_by, created_at) VALUES (?,?,?,?,?,?)",
        (q["scrubbed_text"], body.text, "correction", q["id"], op["name"], ts),
    )
    _notify(db, q["id"], "correction")
    audit(db, op["email"], "answer.correction", f"A{answer_id}")
    return {"ok": True}


# ---------- operator accounts ----------

@router.get("/operators")
def list_operators(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT id, name, email, active, created_at FROM users WHERE role='operator' ORDER BY id").fetchall()
    return [dict(r) | {"active": bool(r["active"])} for r in rows]


@router.post("/operators")
def create_operator(body: OperatorIn, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Please enter a valid email address.")
    validate_password_strength(body.password)
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise HTTPException(409, "An account with that email already exists.")
    db.execute(
        "INSERT INTO users (email, name, role, password_hash, must_change_password, created_at) VALUES (?,?,?,?,1,?)",
        (email, body.name.strip(), "operator", hash_password(body.password), now_iso()),
    )
    audit(db, op["email"], "operator.create", email)
    return {"ok": True}


def _set_active(db: sqlite3.Connection, op: sqlite3.Row, user_id: int, active: bool) -> dict:
    target = db.execute("SELECT * FROM users WHERE id=? AND role='operator'", (user_id,)).fetchone()
    if not target:
        raise HTTPException(404, "Operator not found.")
    if not active:
        if target["id"] == op["id"]:
            raise HTTPException(400, "You can't deactivate your own account.")
        remaining = db.execute(
            "SELECT COUNT(*) FROM users WHERE role='operator' AND active=1 AND id<>?", (user_id,)
        ).fetchone()[0]
        if remaining == 0:
            raise HTTPException(400, "At least one active operator is required.")
    db.execute("UPDATE users SET active=? WHERE id=?", (int(active), user_id))
    audit(db, op["email"], "operator.activate" if active else "operator.deactivate", target["email"])
    return {"ok": True}


@router.post("/operators/{user_id}/deactivate")
def deactivate_operator(user_id: int, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    return _set_active(db, op, user_id, False)


@router.post("/operators/{user_id}/activate")
def activate_operator(user_id: int, op: sqlite3.Row = Depends(require_operator), db: sqlite3.Connection = Depends(get_db)):
    return _set_active(db, op, user_id, True)


@router.get("/audit")
def audit_log(limit: int = Query(100, ge=1, le=500), db: sqlite3.Connection = Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))]


# ---------- observability dashboard ----------

SPIKE_WINDOW = 14
SPIKE_SIGMAS = 2.0
SPIKE_MIN_COUNT = 5


def detect_spikes(daily_counts: list) -> list:
    """A day is a spike if its count > mean + 2 standard deviations of the trailing 14 days
    (and at least 5 questions, to avoid noise on near-empty days)."""
    flags = []
    for i, c in enumerate(daily_counts):
        window = daily_counts[max(0, i - SPIKE_WINDOW) : i]
        if len(window) < 5 or c < SPIKE_MIN_COUNT:
            flags.append(False)
            continue
        flags.append(c > statistics.mean(window) + SPIKE_SIGMAS * statistics.pstdev(window))
    return flags


@router.get("/dashboard")
def dashboard(days: int = Query(30, ge=1, le=365), db: sqlite3.Connection = Depends(get_db)):
    end = now()
    start_day = (end - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    # Extra 14 days of history so spike detection has a trailing window for the first displayed days.
    lookback_day = (end - timedelta(days=days - 1 + SPIKE_WINDOW)).strftime("%Y-%m-%d")

    rows = db.execute(
        "SELECT substr(q.created_at,1,10) AS day, COUNT(*) AS asked, "
        "SUM(CASE WHEN e.id IS NOT NULL THEN 1 ELSE 0 END) AS escalated, "
        "AVG(a.confidence) AS avg_conf "
        "FROM questions q JOIN answers a ON a.question_id=q.id LEFT JOIN escalations e ON e.question_id=q.id "
        "WHERE substr(q.created_at,1,10) >= ? GROUP BY day",
        (lookback_day,),
    ).fetchall()
    by_day = {r["day"]: r for r in rows}
    all_days = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1 + SPIKE_WINDOW, -1, -1)]
    counts_all = [by_day[d]["asked"] if d in by_day else 0 for d in all_days]
    spikes_all = detect_spikes(counts_all)
    series = []
    for d, spike in zip(all_days, spikes_all):
        if d < start_day:
            continue
        r = by_day.get(d)
        asked = r["asked"] if r else 0
        esc = r["escalated"] if r else 0
        series.append({
            "day": d, "asked": asked, "escalated": esc,
            "escalation_rate": round(esc / asked, 3) if asked else None,
            "avg_confidence": round(r["avg_conf"], 3) if r and r["avg_conf"] is not None else None,
            "spike": spike,
        })

    since = start_day
    t = db.execute(
        "SELECT COUNT(*) AS total, AVG(a.confidence) AS avg_conf, "
        "SUM(CASE WHEN e.id IS NOT NULL THEN 1 ELSE 0 END) AS escalated, "
        "SUM(q.flags LIKE '%\"off_topic\"%') AS off_topic, SUM(q.flags LIKE '%\"injection_attempt\"%') AS injection, "
        "SUM(q.flags LIKE '%\"sensitive\"%') AS sensitive, SUM(q.flags LIKE '%\"llm_error\"%') AS llm_errors, "
        "SUM(q.flags LIKE '%\"pii_scrubbed\"%') AS pii_scrubbed "
        "FROM questions q JOIN answers a ON a.question_id=q.id LEFT JOIN escalations e ON e.question_id=q.id "
        "WHERE substr(q.created_at,1,10) >= ?",
        (since,),
    ).fetchone()
    total = t["total"] or 0

    by_category = [
        dict(r) for r in db.execute(
            "SELECT COALESCE(category,'Other') AS category, COUNT(*) AS count FROM questions "
            "WHERE substr(created_at,1,10) >= ? GROUP BY 1 ORDER BY count DESC", (since,))
    ]
    by_mode = [
        dict(r) for r in db.execute(
            "SELECT input_mode AS mode, COUNT(*) AS count FROM questions WHERE substr(created_at,1,10) >= ? GROUP BY 1", (since,))
    ]
    top_questions = [
        dict(r) for r in db.execute(
            "SELECT scrubbed_text AS question, COUNT(*) AS count FROM questions WHERE substr(created_at,1,10) >= ? "
            "GROUP BY lower(scrubbed_text) ORDER BY count DESC, MAX(id) DESC LIMIT 10", (since,))
    ]
    resp_time = db.execute(
        "SELECT AVG((julianday(e.resolved_at) - julianday(e.created_at)) * 24.0) FROM escalations e "
        "WHERE e.status='answered' AND substr(e.created_at,1,10) >= ?", (since,)
    ).fetchone()[0]

    hourly_rows = db.execute(
        "SELECT substr(created_at,1,13) AS hour, COUNT(*) AS asked FROM questions WHERE created_at >= ? GROUP BY hour",
        ((end - timedelta(hours=47)).strftime("%Y-%m-%dT%H:00:00Z"),),
    ).fetchall()
    hmap = {r["hour"]: r["asked"] for r in hourly_rows}
    hourly = []
    for i in range(47, -1, -1):
        h = (end - timedelta(hours=i)).strftime("%Y-%m-%dT%H")
        hourly.append({"hour": h, "asked": hmap.get(h, 0)})

    return {
        "days": days,
        "totals": {
            "asked": total,
            "avg_confidence": round(t["avg_conf"], 3) if t["avg_conf"] is not None else None,
            "escalated": t["escalated"] or 0,
            "escalation_rate": round((t["escalated"] or 0) / total, 3) if total else None,
            "avg_hours_to_staff_answer": round(resp_time, 1) if resp_time is not None else None,
            "off_topic": t["off_topic"] or 0,
            "injection_attempts": t["injection"] or 0,
            "sensitive": t["sensitive"] or 0,
            "llm_errors": t["llm_errors"] or 0,
            "pii_scrubbed": t["pii_scrubbed"] or 0,
        },
        "series": series,
        "hourly": hourly,
        "by_category": by_category,
        "by_mode": by_mode,
        "top_questions": top_questions,
        "spike_rule": f"A day is flagged when its count is above the trailing {SPIKE_WINDOW}-day mean plus {SPIKE_SIGMAS:g} standard deviations (minimum {SPIKE_MIN_COUNT} questions).",
    }
