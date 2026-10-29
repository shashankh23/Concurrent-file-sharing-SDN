"""SQLite database for the file sharing system (Person 3).

Tables
  users         account, password hash and permissions
  files         metadata of every stored file (owner, size, upload time)
  transfers     one row per upload/download with duration and throughput
                (used by Person 4 for the performance evaluation)
  activity_log  logins, denied actions, SDN block/unblock events

Every function opens its own short-lived connection, so it is safe to call
from the many threads of the TCP server.

The database file is created automatically on first use. Set the
FILESHARE_DB environment variable to use a different file (the tests do).
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def db_path():
    return os.environ.get("FILESHARE_DB", os.path.join(BASE_DIR, "fileshare.db"))


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def connect():
    """Open a connection, commit on success, always close."""
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    can_upload    INTEGER NOT NULL DEFAULT 1,
    can_download  INTEGER NOT NULL DEFAULT 1,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    is_premium    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    filename       TEXT PRIMARY KEY,
    owner          TEXT NOT NULL,
    size           INTEGER NOT NULL,
    uploaded_at    TEXT NOT NULL,
    download_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transfers (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    username  TEXT NOT NULL,
    ip        TEXT,
    direction TEXT NOT NULL,          -- UPLOAD or DOWNLOAD
    filename  TEXT NOT NULL,
    size      INTEGER NOT NULL,
    seconds   REAL NOT NULL,
    mbps      REAL NOT NULL           -- megabits per second
);
CREATE TABLE IF NOT EXISTS activity_log (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    username TEXT,
    ip       TEXT,
    event    TEXT NOT NULL,
    detail   TEXT
);
"""

# username, password, can_upload, can_download, is_admin, is_premium
DEFAULT_USERS = [
    ("alice", "1234", 1, 1, 0, 1),    # premium client (h1 in the demo)
    ("bob", "abcd", 1, 1, 0, 0),
    ("guest", "guest", 0, 1, 0, 0),   # download only
    ("admin", "admin123", 1, 1, 1, 0),
]


# ---------------------------------------------------------------- passwords
PBKDF2_ITERATIONS = 100_000


def hash_password(password, salt_hex=None, iterations=PBKDF2_ITERATIONS):
    """Return 'pbkdf2$iterations$salt$hash'. Passwords are never stored."""
    salt_hex = salt_hex or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt_hex), iterations
    )
    return f"pbkdf2${iterations}${salt_hex}${digest.hex()}"


def verify_password(password, stored):
    try:
        _, iterations, salt_hex, _ = stored.split("$")
        candidate = hash_password(password, salt_hex, int(iterations))
    except (ValueError, AttributeError):
        return False
    return hmac.compare_digest(candidate, stored)


# ------------------------------------------------------------------- set-up
def init_db():
    """Create tables and, on a fresh database, the default users."""
    with connect() as conn:
        conn.executescript(SCHEMA)
        (count,) = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        if count == 0:
            for name, pw, up, down, admin, premium in DEFAULT_USERS:
                conn.execute(
                    "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                    (name, hash_password(pw), up, down, admin, premium, now()),
                )


# -------------------------------------------------------------------- users
def get_user(username):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()


def list_users():
    with connect() as conn:
        return conn.execute("SELECT * FROM users ORDER BY username").fetchall()


def create_user(username, password, can_upload=True, can_download=True,
                is_admin=False, is_premium=False):
    """Insert a user. Raises ValueError if the name is taken or invalid."""
    if not username or any(c.isspace() for c in username):
        raise ValueError("username must be non-empty and contain no spaces")
    if not password or any(c.isspace() for c in password):
        raise ValueError("password must be non-empty and contain no spaces")
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO users VALUES (?,?,?,?,?,?,?)",
                (username, hash_password(password), int(can_upload),
                 int(can_download), int(is_admin), int(is_premium), now()),
            )
    except sqlite3.IntegrityError:
        raise ValueError(f"user '{username}' already exists")


def set_password(username, password):
    with connect() as conn:
        cur = conn.execute(
            "UPDATE users SET password_hash = ? WHERE username = ?",
            (hash_password(password), username),
        )
        return cur.rowcount == 1


def set_permissions(username, **flags):
    """flags: can_upload, can_download, is_admin, is_premium (booleans)."""
    allowed = {"can_upload", "can_download", "is_admin", "is_premium"}
    items = {k: int(v) for k, v in flags.items() if k in allowed and v is not None}
    if not items:
        return False
    sets = ", ".join(f"{k} = ?" for k in items)
    with connect() as conn:
        cur = conn.execute(
            f"UPDATE users SET {sets} WHERE username = ?",
            (*items.values(), username),
        )
        return cur.rowcount == 1


def delete_user(username):
    with connect() as conn:
        cur = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        return cur.rowcount == 1


# ---------------------------------------------------------------------- log
def log_event(event, username=None, ip=None, detail=None):
    with connect() as conn:
        conn.execute(
            "INSERT INTO activity_log (ts, username, ip, event, detail) "
            "VALUES (?,?,?,?,?)",
            (now(), username, ip, event, detail),
        )


def recent_events(limit=20):
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
