import re
import sqlite3
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from ..db import audit, get_db, iso, now, now_iso
from ..security import (
    COOKIE_NAME,
    check_password,
    get_current_user,
    hash_password,
    set_session_cookie,
    validate_password_strength,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILED = 5
LOCK_SECONDS = 60


class SignupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class ChangePasswordIn(BaseModel):
    current_password: str = Field(max_length=200)
    new_password: str = Field(max_length=200)


def user_out(u: sqlite3.Row) -> dict:
    return {
        "id": u["id"],
        "name": u["name"],
        "email": u["email"],
        "role": u["role"],
        "must_change_password": bool(u["must_change_password"]),
    }


@router.post("/signup")
def signup(body: SignupIn, response: Response, db: sqlite3.Connection = Depends(get_db)):
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Please enter a valid email address.")
    validate_password_strength(body.password)
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        raise HTTPException(409, "An account with that email already exists.")
    cur = db.execute(
        "INSERT INTO users (email, name, role, password_hash, created_at) VALUES (?,?,?,?,?)",
        (email, body.name.strip(), "parent", hash_password(body.password), now_iso()),
    )
    user = db.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()
    set_session_cookie(response, user["id"], user["role"])
    return user_out(user)


@router.post("/login")
def login(body: LoginIn, response: Response, db: sqlite3.Connection = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    generic = HTTPException(401, "Incorrect email or password.")
    if not user or not user["active"]:
        raise generic
    if user["locked_until"] and user["locked_until"] > now_iso():
        raise HTTPException(429, "Too many failed attempts. Please wait a minute and try again.")
    if not check_password(body.password, user["password_hash"]):
        attempts = user["failed_attempts"] + 1
        locked = iso(now() + timedelta(seconds=LOCK_SECONDS)) if attempts >= MAX_FAILED else None
        db.execute(
            "UPDATE users SET failed_attempts=?, locked_until=? WHERE id=?",
            (0 if locked else attempts, locked, user["id"]),
        )
        db.commit()  # persist the failure count even though we raise
        raise generic
    db.execute("UPDATE users SET failed_attempts=0, locked_until=NULL WHERE id=?", (user["id"],))
    set_session_cookie(response, user["id"], user["role"])
    return user_out(user)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: sqlite3.Row = Depends(get_current_user)):
    return user_out(user)


@router.post("/change-password")
def change_password(
    body: ChangePasswordIn,
    user: sqlite3.Row = Depends(get_current_user),
    db: sqlite3.Connection = Depends(get_db),
):
    if not check_password(body.current_password, user["password_hash"]):
        raise HTTPException(403, "Current password is incorrect.")
    validate_password_strength(body.new_password)
    db.execute(
        "UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
        (hash_password(body.new_password), user["id"]),
    )
    if user["role"] == "operator":
        audit(db, user["email"], "operator.change_password", user["email"])
    return {"ok": True}
