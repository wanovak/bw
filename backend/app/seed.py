"""First-start seeding: default operator, fictional knowledge base, and (optional) demo data."""
import json
import logging
import random
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from .config import get_settings
from .db import iso, now, now_iso
from .security import encrypt_text, hash_password, pseudonym

log = logging.getLogger("seed")


def _meta_get(db, key):
    r = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else None


def _meta_set(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?,?)", (key, value))


def run(db: sqlite3.Connection) -> None:
    s = get_settings()
    operator_id = _ensure_default_operator(db)
    if not _meta_get(db, "knowledge_seeded"):
        entries = json.loads((s.seed_dir / "knowledge.json").read_text())
        ts = now_iso()
        for e in entries:
            db.execute(
                "INSERT INTO knowledge_entries (title, category, body, updated_at, updated_by) VALUES (?,?,?,?,?)",
                (e["title"], e["category"], e["body"], ts, "Seed data"),
            )
        _meta_set(db, "knowledge_seeded", "1")
        log.info("Seeded %d knowledge entries", len(entries))
    if s.seed_demo_data and not _meta_get(db, "demo_seeded"):
        _seed_demo(db, operator_id)
        _meta_set(db, "demo_seeded", "1")
    db.commit()


def _ensure_default_operator(db: sqlite3.Connection) -> int:
    s = get_settings()
    existing = db.execute("SELECT id FROM users WHERE role='operator' ORDER BY id LIMIT 1").fetchone()
    if existing:
        return existing["id"]
    password = s.default_operator_password or secrets.token_urlsafe(9)
    cur = db.execute(
        "INSERT INTO users (email, name, role, password_hash, must_change_password, created_at) VALUES (?,?,?,?,1,?)",
        (s.default_operator_email, "Center Administrator", "operator", hash_password(password), now_iso()),
    )
    db.commit()
    banner = (
        "\n" + "=" * 64 +
        "\n DEFAULT OPERATOR ACCOUNT CREATED (shown once)\n"
        f"   email:    {s.default_operator_email}\n"
        f"   password: {password}\n"
        " You will be asked to change this password at first login.\n" + "=" * 64
    )
    log.warning(banner)
    try:
        path = s.data_dir / "initial_operator_credentials.txt"
        path.write_text(f"{s.default_operator_email}\n{password}\n")
        path.chmod(0o600)
    except OSError:
        pass
    return cur.lastrowid


# ---------- demo data (clearly fake; set SEED_DEMO_DATA=false to skip) ----------

DEMO_QUESTIONS = [
    ("What time do you close?", "Hours", "We're open Monday through Friday, 6:30 AM to 6:00 PM. Children must be picked up by 6:00 PM; a late fee of $1.00 per minute applies after 6:05 PM.", ["K1"]),
    ("How much is toddler tuition?", "Tuition & Fees", "Full-time toddler tuition (ages 1 to 2) is $275 per week, and part-time (3 days) is $190 per week.", ["K6"]),
    ("When can my child come back after a fever?", "Health & Illness", "Children need to be fever-free (under 100.4°F) for 24 hours without fever-reducing medication before returning.", ["K9"]),
    ("Is the center peanut-free?", "Meals", "Yes, Little Acorns is a peanut-free facility. Breakfast, lunch and an afternoon snack are included in tuition.", ["K12"]),
    ("Can I schedule a tour?", "Tours & Enrollment", "Tours run Tuesdays and Thursdays at 10:00 AM and 4:30 PM and take about 30 minutes. Call the front desk to book.", ["K14"]),
    ("Are you open on Thanksgiving?", "Hours", "We're closed on Thanksgiving and the day after, as well as December 24 through January 1.", ["K3"]),
    ("What is the sibling discount?", "Tuition & Fees", "Families receive a 10% discount on the older child's tuition when two or more siblings are enrolled.", ["K7"]),
    ("What should I pack for my toddler?", "Policies", "Please bring two changes of clothes, a labeled water bottle, a small blanket for nap time and diapers/wipes if needed.", ["K17"]),
    ("Do you accommodate food allergies?", "Meals", "Yes. We accommodate documented allergies and keep a posted allergy action plan for each child.", ["K13"]),
    ("How do I give medication to my child at school?", "Health & Illness", "Medication requires a signed authorization form and must be in the original labeled container.", ["K10"]),
    ("How does the waitlist work?", "Tours & Enrollment", "Join the waitlist with a $50 fee (applied to registration). We contact families in order as spots open in their child's age group.", ["K15"]),
    ("What are your late pickup fees?", "Hours", "After a 6:05 PM grace period, late pickup is $1.00 per minute per child.", ["K2"]),
]
DEMO_ESCALATED = [
    ("Do you provide diapers and wipes?", "Policies", "Families supply their own diapers and wipes, labeled with the child's name. We'll let you know when supplies run low.", True),
    ("Is there parking for drop-off?", "Other", "Yes, there's a lot at the front of the building with a short-term drop-off lane. Please don't leave children unattended in vehicles.", True),
    ("Are there security cameras in the classrooms?", "Policies", "Common areas and entrances have cameras for safety. Classrooms do not have live-stream cameras for parents.", True),
    ("Do you offer a school-age summer camp?", "Other", "", False),
    ("Do you offer drop-in or backup care?", "Policies", "", False),
    ("Do you provide transportation to the center?", "Policies", "", False),
    ("Do you offer swimming lessons?", "Other", "", False),
    ("Do you take field trips off-site?", "Policies", "", False),
]

DEMO_PSEUDO_IDS = list(range(9001, 9013))


def _seed_demo(db: sqlite3.Connection, operator_id: int) -> None:
    s = get_settings()
    rng = random.Random(42)
    op_name = db.execute("SELECT name FROM users WHERE id=?", (operator_id,)).fetchone()["name"]
    cur = db.execute(
        "INSERT INTO users (email, name, role, password_hash, created_at) VALUES (?,?,?,?,?)",
        (s.demo_parent_email, "Demo Parent", "parent", hash_password(s.demo_parent_password), now_iso()),
    )
    demo_user = cur.lastrowid
    end = now()
    spike_day = 9

    def insert(created: datetime, user_id, pseudo_id, text, cat, mode, answer, conf, sources, by, status, flags, original=None):
        c = iso(created)
        q = db.execute(
            "INSERT INTO questions (user_id, parent_pseudo, scrubbed_text, input_mode, category, flags, created_at) VALUES (?,?,?,?,?,?,?)",
            (user_id, pseudonym(pseudo_id), text, mode, cat, json.dumps(flags), c),
        ).lastrowid
        if user_id:
            db.execute("INSERT INTO parent_question_text VALUES (?,?)", (q, encrypt_text(original or text)))
        a = db.execute(
            "INSERT INTO answers (question_id, text, confidence, source_ids, generated_by, status, created_at, answered_at) VALUES (?,?,?,?,?,?,?,?)",
            (q, answer, conf, json.dumps(sources), by, status, c, c),
        ).lastrowid
        return q, a

    from . import guard

    for day_offset in range(29, -1, -1):
        base = rng.randint(3, 7) + (29 - day_offset) // 6
        n = base * 4 if day_offset == spike_day else base
        escalation_prob = max(0.06, 0.38 - (29 - day_offset) * 0.011)  # gap closes as staff answer questions
        for _ in range(n):
            created = (end - timedelta(days=day_offset)).replace(hour=rng.choice([6, 7, 7, 8, 12, 16, 17, 18, 20]), minute=rng.randint(0, 59), second=rng.randint(0, 59))
            if created > end:
                created = end - timedelta(minutes=rng.randint(1, 30))
            pseudo_id = rng.choice(DEMO_PSEUDO_IDS)
            mode = rng.choice(["text", "guided"])
            if rng.random() < escalation_prob:
                pool = [e for e in DEMO_ESCALATED if e[3]] if day_offset > 3 else DEMO_ESCALATED
                text, cat, staff_answer, answered = rng.choice(pool)
                if day_offset <= 2:
                    answered = False
                conf = round(rng.uniform(0.15, 0.7), 2)
                q, a = insert(created, None, pseudo_id, text, cat, mode, guard.TEMPLATE_ESCALATED, conf, [], "template", "pending_staff", ["escalated"])
                resolved = iso(created + timedelta(hours=rng.randint(1, 20)))
                if answered:
                    db.execute("INSERT INTO escalations (question_id, status, priority, reason, answered_by, answer_text, created_at, resolved_at) VALUES (?,?,?,?,?,?,?,?)",
                               (q, "answered", "normal", "no_answer", op_name, staff_answer, iso(created), resolved))
                    db.execute("UPDATE answers SET text=?, generated_by='operator', status='answered_by_staff', confidence=? WHERE id=?", (staff_answer, conf, a))
                else:
                    db.execute("INSERT INTO escalations (question_id, status, priority, reason, created_at) VALUES (?,?,?,?,?)",
                               (q, "open", "normal", "no_answer", iso(created)))
            else:
                text, cat, ans, srcs = rng.choice(DEMO_QUESTIONS)
                conf = round(rng.uniform(0.9, 0.99), 2)
                insert(created, None, pseudo_id, text, cat, mode, ans, conf, srcs, "llm", "answered", [])

    # Demo parent's own recent history (originals are stored encrypted and visible only to this parent).
    t0 = end - timedelta(hours=3)
    text, cat, ans, srcs = DEMO_QUESTIONS[0]
    insert(t0, demo_user, demo_user, text, cat, "text", ans, 0.97, srcs, "llm", "answered", [])
    text, cat, ans, srcs = DEMO_QUESTIONS[2]
    insert(t0 + timedelta(minutes=5), demo_user, demo_user, "My son Liam Carter has a fever of 101, when can he come back?"
           .replace("Liam Carter", "[NAME]"), cat, "text", ans, 0.96, srcs, "llm", "answered", ["pii_scrubbed"],
           original="My son Liam Carter has a fever of 101, when can he come back?")
    # Staff-approved Q&A pairs matching the demo history above (the intentional knowledge gaps stay open for live demos).
    for qtext, _cat, ans, answered in DEMO_ESCALATED:
        if answered:
            db.execute(
                "INSERT INTO qa_pairs (question, answer, source, created_by, created_at) VALUES (?,?,?,?,?)",
                (qtext, ans, "operator_answer", op_name, now_iso()),
            )
    log.info("Seeded demo data (demo parent: %s)", s.demo_parent_email)
