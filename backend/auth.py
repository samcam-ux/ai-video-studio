"""SQLite-backed auth with per-user credits.

DB lives at $DATA_DIR/app.db. On Railway, mount a persistent Volume at /data
and set DATA_DIR=/data so accounts survive redeploys.
"""
import hashlib
import json
import os
import secrets
import sqlite3
import threading
import time

DATA_DIR = os.environ.get("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(DATA_DIR, "app.db")

_lock = threading.Lock()
FREE_CREDITS = 3


def _db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init():
    with _lock:
        db = _db()
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            pw_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 3,
            created_at REAL NOT NULL,
            stripe_customer_id TEXT,
            subscription_status TEXT,
            name TEXT,
            company TEXT,
            industry TEXT,
            team_size TEXT
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_videos_email ON videos(email);
        """)
        db.commit()
        # Migrate existing DBs: add subscription + profile columns if missing
        for col in ("stripe_customer_id TEXT", "subscription_status TEXT",
                    "name TEXT", "company TEXT", "industry TEXT", "team_size TEXT"):
            name = col.split()[0]
            cur = db.execute("PRAGMA table_info(users)")
            if name not in [r[1] for r in cur.fetchall()]:
                db.execute(f"ALTER TABLE users ADD COLUMN {col}")
        db.commit()
        db.close()


_init()


def _hash(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()


def _get_user_row(email):
    db = _db()
    try:
        return db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    finally:
        db.close()


def _videos_for(email):
    db = _db()
    try:
        rows = db.execute("SELECT data FROM videos WHERE email=? ORDER BY id", (email,)).fetchall()
        return [json.loads(r["data"]) for r in rows]
    finally:
        db.close()


def _user_dict(row):
    return {
        "email": row["email"],
        "credits": row["credits"],
        "videos": _videos_for(row["email"]),
    }


def register(email: str, password: str, name: str | None = None,
             company: str | None = None, industry: str | None = None,
             team_size: str | None = None) -> dict:
    email = email.strip().lower()
    if not email or not password:
        raise ValueError("Email and password required")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    if _get_user_row(email):
        raise ValueError("Account already exists")
    salt = secrets.token_hex(16)
    token = secrets.token_hex(32)
    with _lock:
        db = _db()
        try:
            db.execute(
                "INSERT INTO users (email, pw_hash, salt, credits, created_at, name, company, industry, team_size) VALUES (?,?,?,?,?,?,?,?,?)",
                (email, _hash(password, salt), salt, FREE_CREDITS, time.time(),
                 name, company, industry, team_size),
            )
            db.execute(
                "INSERT INTO sessions (token, email, created_at) VALUES (?,?,?)",
                (token, email, time.time()),
            )
            db.commit()
        finally:
            db.close()
    return {"token": token, "credits": FREE_CREDITS}


def login(email: str, password: str) -> dict:
    email = email.strip().lower()
    row = _get_user_row(email)
    if not row or _hash(password, row["salt"]) != row["pw_hash"]:
        raise ValueError("Invalid login")
    token = secrets.token_hex(32)
    with _lock:
        db = _db()
        try:
            db.execute(
                "INSERT INTO sessions (token, email, created_at) VALUES (?,?,?)",
                (token, email, time.time()),
            )
            db.commit()
        finally:
            db.close()
    return {"token": token, "credits": row["credits"]}


def get_user(token: str):
    if not token:
        return None
    db = _db()
    try:
        s = db.execute("SELECT email FROM sessions WHERE token=?", (token,)).fetchone()
    finally:
        db.close()
    if not s:
        return None
    row = _get_user_row(s["email"])
    if not row:
        return None
    return _user_dict(row)


def spend_credit(email: str) -> bool:
    with _lock:
        db = _db()
        try:
            row = db.execute("SELECT credits FROM users WHERE email=?", (email,)).fetchone()
            if not row or row["credits"] <= 0:
                return False
            db.execute("UPDATE users SET credits = credits - 1 WHERE email=?", (email,))
            db.commit()
            return True
        finally:
            db.close()


def add_credits(email: str, n: int):
    with _lock:
        db = _db()
        try:
            db.execute("UPDATE users SET credits = credits + ? WHERE email=?", (n, email))
            db.commit()
        finally:
            db.close()


def add_video(email: str, video: dict):
    with _lock:
        db = _db()
        try:
            db.execute(
                "INSERT INTO videos (email, data, created_at) VALUES (?,?,?)",
                (email, json.dumps(video), time.time()),
            )
            db.commit()
        finally:
            db.close()


def update_video(email: str, prediction_id: str, updates: dict):
    """Merge updates into the stored video record. Returns the updated dict or None."""
    with _lock:
        db = _db()
        try:
            rows = db.execute("SELECT id, data FROM videos WHERE email=?", (email,)).fetchall()
            for r in rows:
                d = json.loads(r["data"])
                if d.get("prediction_id") == prediction_id:
                    d.update(updates)
                    db.execute("UPDATE videos SET data=? WHERE id=?", (json.dumps(d), r["id"]))
                    db.commit()
                    return d
            return None
        finally:
            db.close()


def refund_if_failed(email: str, prediction_id: str) -> bool:
    """Refund the video's credit cost if it failed and wasn't refunded yet."""
    with _lock:
        db = _db()
        try:
            rows = db.execute("SELECT id, data FROM videos WHERE email=?", (email,)).fetchall()
            for r in rows:
                d = json.loads(r["data"])
                if d.get("prediction_id") == prediction_id and not d.get("refunded"):
                    cost = d.get("cost", 1)
                    d["refunded"] = True
                    db.execute("UPDATE videos SET data=? WHERE id=?", (json.dumps(d), r["id"]))
                    db.execute("UPDATE users SET credits = credits + ? WHERE email=?", (cost, email))
                    db.commit()
                    return True
            return False
        finally:
            db.close()


def set_subscription(email: str, customer_id: str, status: str, monthly_credits: int):
    """Activate/update a subscription: set status and grant the month's credits."""
    with _lock:
        db = _db()
        try:
            db.execute(
                "UPDATE users SET stripe_customer_id=?, subscription_status=?, credits = credits + ? WHERE email=?",
                (customer_id, status, monthly_credits, email),
            )
            db.commit()
        finally:
            db.close()


def cancel_subscription_status(email: str):
    with _lock:
        db = _db()
        try:
            db.execute("UPDATE users SET subscription_status='cancelled' WHERE email=?", (email,))
            db.commit()
        finally:
            db.close()


def get_subscription_status(email: str) -> str | None:
    row = _get_user_row(email)
    return row["subscription_status"] if row else None
