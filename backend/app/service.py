"""The question-answering pipeline, plus shared serializers for parent and operator views.

question -> length/rate checks -> PII scrub -> persist -> guards -> retrieve -> Gemini
         -> validate -> confidence/safety rules -> answer OR escalate -> log
"""
import json
import logging
import sqlite3

from fastapi import HTTPException

from . import guard, llm, pii, rag
from .config import CATEGORIES, get_settings
from .db import ago_iso, now_iso
from .security import decrypt_text, encrypt_text, pseudonym

log = logging.getLogger("service")


def retrieval_factor(retrieval_score: float) -> float:
    """Mild penalty that only bites when the cited sources share almost no words with the question
    (score < 0.1). Natural phrasing often has low word overlap with handbook text, so this must not
    punish correct answers; the model's own confidence does the heavy lifting."""
    return min(1.0, 0.85 + 1.5 * retrieval_score)


def final_confidence(model_confidence: float, retrieval_score: float) -> float:
    return round(model_confidence * retrieval_factor(retrieval_score), 3)


def _decide(db: sqlite3.Connection, scrubbed: str, category: str | None) -> dict:
    """Return the outcome for a question. Never raises for LLM problems (they become escalations)."""
    s = get_settings()
    g = guard.check(scrubbed)
    base = {"category": category, "flags": [], "escalate": None, "draft": None, "confidence": None, "sources": []}

    if g.injection:
        return {**base, "status": "refused", "text": guard.TEMPLATE_REFUSAL, "by": "template",
                "flags": ["off_topic", "injection_attempt"]}
    if g.emergency:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_EMERGENCY, "by": "template",
                "flags": ["sensitive", "emergency"], "escalate": ("high", "sensitive")}
    if g.sensitive:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_SENSITIVE, "by": "template",
                "flags": ["sensitive"], "escalate": ("high", "sensitive")}

    match = rag.exact_staff_match(db, scrubbed)
    if match:
        return {**base, "status": "answered", "text": match["answer"], "by": "staff_qa",
                "confidence": 0.99, "sources": [f"Q{match['id']}"]}

    docs = rag.retrieve(db, scrubbed)
    if not docs:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_ESCALATED, "by": "template",
                "flags": ["escalated"], "escalate": ("normal", "no_answer")}
    try:
        res = llm.answer(scrubbed, docs)
    except llm.LLMError:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_LLM_DOWN, "by": "template",
                "flags": ["escalated", "llm_error"], "escalate": ("normal", "llm_error")}

    category = category if category in CATEGORIES else res.category
    base["category"] = category
    if res.off_topic:
        return {**base, "status": "refused", "text": guard.TEMPLATE_REFUSAL, "by": "template", "flags": ["off_topic"]}
    if res.sensitive:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_SENSITIVE, "by": "template",
                "flags": ["sensitive"], "escalate": ("high", "sensitive")}

    by_id = {d.id: d for d in docs}
    valid_ids = [i for i in res.source_ids if i in by_id]
    retrieval_score = max((by_id[i].score for i in valid_ids), default=0.0)
    conf = final_confidence(res.confidence, retrieval_score)
    draft = res.answer or None

    if not res.answerable or not res.answer or not valid_ids:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_ESCALATED, "by": "template",
                "confidence": conf if res.answerable else min(conf, 0.5), "draft": draft,
                "flags": ["escalated"], "escalate": ("normal", "no_answer")}
    if conf < s.confidence_threshold:
        return {**base, "status": "pending_staff", "text": guard.TEMPLATE_ESCALATED, "by": "template",
                "confidence": conf, "draft": draft, "sources": valid_ids,
                "flags": ["escalated"], "escalate": ("normal", "low_confidence")}
    return {**base, "status": "answered", "text": res.answer[:2000], "by": "llm",
            "confidence": conf, "sources": valid_ids}


def ask(db: sqlite3.Connection, user: sqlite3.Row, text: str, mode: str, category: str | None) -> int:
    s = get_settings()
    text = (text or "").strip()
    if not text:
        raise HTTPException(422, "Please enter a question.")
    if len(text) > s.max_question_chars:
        raise HTTPException(422, f"Questions are limited to {s.max_question_chars} characters.")
    recent = db.execute(
        "SELECT COUNT(*) FROM questions WHERE user_id=? AND created_at > ?", (user["id"], ago_iso(hours=1))
    ).fetchone()[0]
    if recent >= s.rate_limit_per_hour:
        raise HTTPException(429, f"You've reached the limit of {s.rate_limit_per_hour} questions per hour. Please try again later.")

    scrub = pii.scrub(text, known_names=[user["name"]], known_emails=[user["email"]])
    flags = ["pii_scrubbed"] if scrub.types else []
    cat = category if category in CATEGORIES else None
    ts = now_iso()
    cur = db.execute(
        "INSERT INTO questions (user_id, parent_pseudo, scrubbed_text, input_mode, category, flags, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (user["id"], pseudonym(user["id"]), scrub.text, mode, cat, json.dumps(flags), ts),
    )
    qid = cur.lastrowid
    db.execute("INSERT INTO parent_question_text (question_id, original_enc) VALUES (?,?)", (qid, encrypt_text(text)))
    db.execute("INSERT INTO answers (question_id, status, created_at) VALUES (?,?,?)", (qid, "processing", ts))
    db.commit()  # the question is never lost, even if the LLM step fails

    try:
        d = _decide(db, scrub.text, cat)
    except Exception:  # defensive: any unexpected failure becomes an escalation, not a lost question
        log.exception("Unexpected failure while answering question %s", qid)
        d = {"status": "pending_staff", "text": guard.TEMPLATE_LLM_DOWN, "by": "template", "category": cat,
             "flags": ["escalated", "llm_error"], "escalate": ("normal", "llm_error"), "draft": None,
             "confidence": None, "sources": []}
    apply_decision(db, qid, d, flags)
    db.commit()
    return qid


def apply_decision(db: sqlite3.Connection, qid: int, d: dict, flags: list) -> None:
    ts = now_iso()
    all_flags = sorted(set(flags) | set(d["flags"]))
    db.execute("UPDATE questions SET flags=?, category=COALESCE(?, category) WHERE id=?", (json.dumps(all_flags), d.get("category"), qid))
    db.execute(
        "UPDATE answers SET text=?, confidence=?, source_ids=?, generated_by=?, status=?, draft_text=?, answered_at=? "
        "WHERE question_id=?",
        (d["text"], d["confidence"], json.dumps(d["sources"]), d["by"], d["status"], d["draft"], ts, qid),
    )
    if d["escalate"]:
        priority, reason = d["escalate"]
        db.execute(
            "INSERT OR IGNORE INTO escalations (question_id, status, priority, reason, created_at) VALUES (?,?,?,?,?)",
            (qid, "open", priority, reason, ts),
        )


def recover_stuck_questions(db: sqlite3.Connection) -> int:
    """On startup: any question still 'processing' (crash mid-flight) is escalated so it isn't lost."""
    rows = db.execute(
        "SELECT q.id, q.flags FROM questions q JOIN answers a ON a.question_id=q.id WHERE a.status='processing'"
    ).fetchall()
    for r in rows:
        apply_decision(
            db, r["id"],
            {"status": "pending_staff", "text": guard.TEMPLATE_LLM_DOWN, "by": "template", "category": None,
             "flags": ["escalated", "llm_error"], "escalate": ("normal", "llm_error"), "draft": None,
             "confidence": None, "sources": []},
            json.loads(r["flags"]),
        )
    db.commit()
    return len(rows)


# ---------- serializers ----------

def resolve_sources(db: sqlite3.Connection, ids: list) -> list:
    out = []
    for i in ids:
        if i.startswith("K") and i[1:].isdigit():
            r = db.execute("SELECT title FROM knowledge_entries WHERE id=?", (int(i[1:]),)).fetchone()
            out.append({"id": i, "title": r["title"] if r else "(removed entry)"})
        elif i.startswith("Q") and i[1:].isdigit():
            out.append({"id": i, "title": "Staff-approved answer"})
    return out


def parent_item(db: sqlite3.Connection, row: sqlite3.Row) -> dict:
    flags = json.loads(row["flags"])
    unread = db.execute(
        "SELECT 1 FROM notifications WHERE user_id=? AND question_id=? AND is_read=0 LIMIT 1",
        (row["user_id"], row["id"]),
    ).fetchone()
    pending = row["status"] == "pending_staff"
    return {
        "id": row["id"],
        "question": decrypt_text(row["original_enc"]),
        "created_at": row["created_at"],
        "pii_scrubbed": "pii_scrubbed" in flags,
        "status": row["status"],
        "answer": {
            "id": row["answer_id"],
            "text": row["text"],
            "by": row["generated_by"],
            "sources": [] if pending else resolve_sources(db, json.loads(row["source_ids"])),
            "feedback": row["feedback"],
        },
        "correction": {"text": row["corrected_text"], "at": row["corrected_at"]} if row["corrected_text"] else None,
        "unread": bool(unread),
    }


PARENT_SELECT = (
    "SELECT q.id, q.user_id, q.flags, q.created_at, p.original_enc, a.id AS answer_id, a.text, a.status, "
    "a.generated_by, a.source_ids, a.feedback, a.corrected_text, a.corrected_at "
    "FROM questions q JOIN answers a ON a.question_id=q.id "
    "JOIN parent_question_text p ON p.question_id=q.id "
)


def operator_item(db: sqlite3.Connection, r: sqlite3.Row) -> dict:
    """Operator view: scrubbed text and an anonymous parent label only. No user id, name or email."""
    return {
        "answer_id": r["answer_id"],
        "question_id": r["question_id"],
        "created_at": r["created_at"],
        "parent": r["parent_pseudo"],
        "question": r["scrubbed_text"],
        "category": r["category"],
        "answer": r["text"],
        "draft": r["draft_text"],
        "confidence": r["confidence"],
        "status": r["status"],
        "generated_by": r["generated_by"],
        "flags": json.loads(r["flags"]),
        "review_status": r["review_status"],
        "corrected_text": r["corrected_text"],
        "feedback": r["feedback"],
        "sources": resolve_sources(db, json.loads(r["source_ids"])),
    }


OPERATOR_SELECT = (
    "SELECT a.id AS answer_id, a.question_id, a.text, a.confidence, a.source_ids, a.generated_by, a.status, "
    "a.draft_text, a.review_status, a.corrected_text, a.feedback, q.created_at, q.parent_pseudo, "
    "q.scrubbed_text, q.category, q.flags "
    "FROM answers a JOIN questions q ON q.id=a.question_id "
)
