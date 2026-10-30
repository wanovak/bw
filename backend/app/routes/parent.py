import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import guard, service
from ..config import CATEGORIES, CENTER_NAME, CENTER_PHONE, get_settings
from ..db import get_db
from ..security import check_password, require_parent

router = APIRouter(prefix="/api/parent", tags=["parent"], dependencies=[Depends(require_parent)])


class AskIn(BaseModel):
    text: str = Field(max_length=2000)  # hard cap; the 500-char business rule is enforced (with a friendly message) in the service
    mode: str = Field(default="text", pattern="^(text|guided)$")
    category: str | None = None


class FeedbackIn(BaseModel):
    value: str = Field(pattern="^(up|down)$")


class DeleteAccountIn(BaseModel):
    password: str = Field(max_length=200)


@router.get("/config")
def config():
    ui = json.loads((get_settings().seed_dir / "ui_config.json").read_text())
    return {
        "center_name": CENTER_NAME,
        "center_phone": CENTER_PHONE,
        "categories": CATEGORIES,
        "examples": ui["examples"],
        "guided": ui["guided"],
        "max_chars": get_settings().max_question_chars,
    }


def _get_item(db: sqlite3.Connection, qid: int, user_id: int) -> dict:
    row = db.execute(service.PARENT_SELECT + "WHERE q.id=? AND q.user_id=?", (qid, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Question not found.")
    return service.parent_item(db, row)


@router.post("/questions")
def ask(body: AskIn, user: sqlite3.Row = Depends(require_parent), db: sqlite3.Connection = Depends(get_db)):
    qid = service.ask(db, user, body.text, body.mode, body.category)
    return _get_item(db, qid, user["id"])


@router.get("/questions")
def history(user: sqlite3.Row = Depends(require_parent), db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        service.PARENT_SELECT + "WHERE q.user_id=? ORDER BY q.id DESC LIMIT 200", (user["id"],)
    ).fetchall()
    return [service.parent_item(db, r) for r in rows]


@router.get("/notifications")
def notifications(user: sqlite3.Row = Depends(require_parent), db: sqlite3.Connection = Depends(get_db)):
    n = db.execute(
        "SELECT COUNT(DISTINCT question_id) FROM notifications WHERE user_id=? AND is_read=0", (user["id"],)
    ).fetchone()[0]
    return {"unread": n}


@router.post("/questions/{qid}/read")
def mark_read(qid: int, user: sqlite3.Row = Depends(require_parent), db: sqlite3.Connection = Depends(get_db)):
    db.execute("UPDATE notifications SET is_read=1 WHERE user_id=? AND question_id=?", (user["id"], qid))
    return {"ok": True}


@router.post("/answers/{answer_id}/feedback")
def feedback(
    answer_id: int, body: FeedbackIn, user: sqlite3.Row = Depends(require_parent), db: sqlite3.Connection = Depends(get_db)
):
    row = db.execute(
        "SELECT a.id FROM answers a JOIN questions q ON q.id=a.question_id WHERE a.id=? AND q.user_id=?",
        (answer_id, user["id"]),
    ).fetchone()
    if not row:
        raise HTTPException(404, "Answer not found.")
    db.execute("UPDATE answers SET feedback=? WHERE id=?", (body.value, answer_id))
    return {"ok": True}


@router.delete("/account")
def delete_account(body: DeleteAccountIn, user: sqlite3.Row = Depends(require_parent), db: sqlite3.Connection = Depends(get_db)):
    """Deletes the parent's account and the original (unscrubbed) question text. Scrubbed, anonymous
    rows remain in the operator log under an orphaned pseudonym."""
    if not check_password(body.password, user["password_hash"]):
        raise HTTPException(403, "Password is incorrect.")
    db.execute(
        "DELETE FROM parent_question_text WHERE question_id IN (SELECT id FROM questions WHERE user_id=?)", (user["id"],)
    )
    db.execute("DELETE FROM notifications WHERE user_id=?", (user["id"],))
    db.execute("UPDATE questions SET user_id=NULL WHERE user_id=?", (user["id"],))
    db.execute("DELETE FROM users WHERE id=?", (user["id"],))
    return {"ok": True}
