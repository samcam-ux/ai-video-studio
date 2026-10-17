"""Simple token auth with per-user credits."""
import secrets
from typing import Dict, Optional

# In-memory store. Swap for a real DB in production.
_users: Dict[str, dict] = {}
_sessions: Dict[str, str] = {}  # token -> email

FREE_CREDITS = 3

def register(email: str, password: str) -> dict:
    if email in _users:
        raise ValueError("Account already exists")
    _users[email] = {
        "email": email,
        "password": password,  # NOTE: hash in production
        "credits": FREE_CREDITS,
        "videos": [],
    }
    token = secrets.token_hex(32)
    _sessions[token] = email
    return {"token": token, "credits": FREE_CREDITS}

def login(email: str, password: str) -> dict:
    u = _users.get(email)
    if not u or u["password"] != password:
        raise ValueError("Invalid login")
    token = secrets.token_hex(32)
    _sessions[token] = email
    return {"token": token, "credits": u["credits"]}

def get_user(token: str) -> Optional[dict]:
    email = _sessions.get(token)
    if not email:
        return None
    return _users.get(email)

def spend_credit(email: str) -> bool:
    u = _users.get(email)
    if not u or u["credits"] <= 0:
        return False
    u["credits"] -= 1
    return True

def add_credits(email: str, n: int):
    u = _users.get(email)
    if u:
        u["credits"] += n

def add_video(email: str, video: dict):
    u = _users.get(email)
    if u:
        u["videos"].append(video)
