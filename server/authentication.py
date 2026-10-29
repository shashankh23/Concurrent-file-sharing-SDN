"""Authentication, permissions and SDN-linked lockout (Person 3).

authenticate(username, password, ip) checks the password against the
database. Repeated failures from one IP address lock that IP out and ask the
SDN controller (through sdn_bridge) to drop its traffic to the server. After
LOCKOUT_SECONDS the lock expires and the controller is told to unblock the
IP again (the "recovery" step).

Settings can be changed with environment variables:
    FILESHARE_MAX_FAILED      failed logins before lockout   (default 3)
    FILESHARE_LOCKOUT_SECONDS lockout duration in seconds    (default 60)
"""
import os
import sys
import threading
import time
from collections import namedtuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database import database as db  # noqa: E402
import sdn_bridge  # noqa: E402

MAX_FAILED = int(os.environ.get("FILESHARE_MAX_FAILED", 3))
LOCKOUT_SECONDS = float(os.environ.get("FILESHARE_LOCKOUT_SECONDS", 60))

AuthResult = namedtuple("AuthResult", "ok reason user")
# reason is one of: "ok", "bad_credentials", "locked"

_lock = threading.Lock()
_failures = {}       # ip -> consecutive failed logins
_locked_until = {}   # ip -> time.monotonic() when the lock expires

# Hash checked when the username does not exist, so a wrong username takes as
# long as a wrong password (does not reveal which usernames are valid).
_DUMMY_HASH = db.hash_password("dummy-password")


def is_locked(ip):
    with _lock:
        until = _locked_until.get(ip)
        if until is None:
            return False
        if time.monotonic() >= until:
            _locked_until.pop(ip, None)
            return False
        return True


def _lock_ip(ip):
    with _lock:
        _locked_until[ip] = time.monotonic() + LOCKOUT_SECONDS
        _failures.pop(ip, None)
    sent = sdn_bridge.block_ip(ip)
    db.log_event(
        "SDN_BLOCK", ip=ip,
        detail=f"{MAX_FAILED} failed logins; locked {LOCKOUT_SECONDS:g}s; "
               f"controller notified={sent}",
    )
    print(f"[SECURITY] {ip} locked out for {LOCKOUT_SECONDS:g}s "
          f"(controller notified: {sent})")
    timer = threading.Timer(LOCKOUT_SECONDS, _unlock_ip, args=(ip,))
    timer.daemon = True
    timer.start()


def _unlock_ip(ip):
    with _lock:
        _locked_until.pop(ip, None)
    sent = sdn_bridge.unblock_ip(ip)
    db.log_event("SDN_UNBLOCK", ip=ip,
                 detail=f"lockout expired; controller notified={sent}")
    print(f"[SECURITY] {ip} lockout expired (controller notified: {sent})")


def authenticate(username, password, ip):
    """Return AuthResult(ok, reason, user_row). Logs every outcome."""
    if is_locked(ip):
        db.log_event("LOGIN_LOCKED", username, ip, "attempt while locked out")
        return AuthResult(False, "locked", None)

    user = db.get_user(username)
    stored = user["password_hash"] if user else _DUMMY_HASH
    valid = db.verify_password(password, stored) and user is not None

    if valid:
        with _lock:
            _failures.pop(ip, None)
        db.log_event("LOGIN_OK", username, ip)
        return AuthResult(True, "ok", user)

    db.log_event("LOGIN_FAIL", username, ip, "bad username or password")
    with _lock:
        _failures[ip] = _failures.get(ip, 0) + 1
        too_many = _failures[ip] >= MAX_FAILED
    if too_many:
        _lock_ip(ip)
    return AuthResult(False, "bad_credentials", None)


def check_permission(user, action):
    """action is 'upload' or 'download'. Admins may do both."""
    if user is None:
        return False
    if user["is_admin"]:
        return True
    return bool(user["can_" + action])


def can_overwrite(user, owner):
    """A file may be replaced by its owner or by an admin."""
    return user["is_admin"] or owner is None or owner == user["username"]


def reset_state():
    """Forget failures and locks (used by tests)."""
    with _lock:
        _failures.clear()
        _locked_until.clear()
