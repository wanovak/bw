"""Passwords, sessions, role enforcement, pseudonymous parent IDs, and field encryption."""
import hashlib
import hmac
import sqlite3
from datetime import timedelta

import bcrypt
import jwt
from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request

from .config import get_settings
from .db import get_db, now

COOKIE_NAME = "session"
MAX_PASSWORD_BYTES = 72  # bcrypt limit


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode()[:MAX_PASSWORD_BYTES], bcrypt.gensalt()).decode()


def check_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode()[:MAX_PASSWORD_BYTES], hashed.encode())
    except ValueError:
        return False


def validate_password_strength(password: str) -> None:
    if len(password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters.")
    if len(password.encode()) > MAX_PASSWORD_BYTES:
        raise HTTPException(422, "Password is too long (max 72 bytes).")


def create_token(user_id: int, role: str) -> str:
    s = get_settings()
    payload = {"sub": str(user_id), "role": role, "exp": now() + timedelta(hours=s.session_hours)}
    return jwt.encode(payload, s.jwt_secret, algorithm="HS256")


def set_session_cookie(response, user_id: int, role: str) -> None:
    s = get_settings()
    response.set_cookie(
        COOKIE_NAME,
        create_token(user_id, role),
        max_age=s.session_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=s.cookie_secure,
        path="/",
    )


def get_current_user(request: Request, db: sqlite3.Connection = Depends(get_db)) -> sqlite3.Row:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Not authenticated.")
    try:
        data = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        user_id = int(data["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(401, "Session expired. Please sign in again.")
    user = db.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
    if not user:
        raise HTTPException(401, "Not authenticated.")
    return user


def require_parent(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
    if user["role"] != "parent":
        raise HTTPException(403, "Parent access only.")
    return user


def require_operator(user: sqlite3.Row = Depends(get_current_user)) -> sqlite3.Row:
    if user["role"] != "operator":
        raise HTTPException(403, "Operator access only.")
    return user


def pseudonym(user_id: int) -> str:
    """Stable anonymous label for a parent (e.g. Parent-7F3A). Not reversible without the server secret."""
    digest = hmac.new(
        get_settings().parent_id_hmac_secret.encode(), f"parent:{user_id}".encode(), hashlib.sha256
    ).hexdigest()
    return "Parent-" + digest[:4].upper()


def _fernet() -> Fernet:
    return Fernet(get_settings().fernet_key)


def encrypt_text(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt_text(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()
