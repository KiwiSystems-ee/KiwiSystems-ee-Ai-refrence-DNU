"""
User accounts: signup, login, admin rights, and tracking who signed up,
from where, and when.

Passwords are never stored in plain text -- only a salted hash
(werkzeug's generate_password_hash / check_password_hash, the same
tooling Flask itself depends on).

There is no separate "admin password". Admin rights are just a flag
(is_admin) on a regular account -- an admin logs in with their normal
username and password, and can then add new users or reset a user's
password from the admin console.
"""

import threading
from datetime import datetime, timezone

from werkzeug.security import generate_password_hash, check_password_hash

import kv_store

STORE_KEY = "users"
_lock = threading.Lock()


def _load() -> dict:
    return kv_store.get_json(STORE_KEY, default={"users": []})


def _save(data: dict):
    kv_store.set_json(STORE_KEY, data)


def _find(data: dict, username: str):
    uname = username.strip().lower()
    for u in data["users"]:
        if u["username"].lower() == uname:
            return u
    return None


def username_exists(username: str) -> bool:
    return _find(_load(), username) is not None


def create_user(username: str, email: str, password: str, ip_address: str, is_admin: bool = False) -> dict:
    """Create a new account. Raises ValueError if the username is taken."""
    with _lock:
        data = _load()
        if _find(data, username):
            raise ValueError("Username already taken")

        user = {
            "username": username.strip(),
            "email": email.strip(),
            "password_hash": generate_password_hash(password),
            "ip_address": ip_address,
            "signed_up_at": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
            "is_admin": bool(is_admin),
        }
        data["users"].append(user)
        _save(data)
        return {k: v for k, v in user.items() if k != "password_hash"}


def get_user(username: str) -> dict:
    return _find(_load(), username)


def verify_login(username: str, password: str) -> bool:
    user = _find(_load(), username)
    if not user:
        return False
    return check_password_hash(user["password_hash"], password)


def is_admin(username: str) -> bool:
    user = _find(_load(), username)
    return bool(user and user.get("is_admin"))


def set_password(username: str, new_password: str) -> bool:
    """Reset a user's password. Returns False if the user doesn't exist."""
    with _lock:
        data = _load()
        user = _find(data, username)
        if not user:
            return False
        user["password_hash"] = generate_password_hash(new_password)
        _save(data)
        return True


def set_email(username: str, email: str) -> bool:
    """Set/update a user's email (used for 2FA). Returns False if the user doesn't exist."""
    with _lock:
        data = _load()
        user = _find(data, username)
        if not user:
            return False
        user["email"] = (email or "").strip()
        _save(data)
        return True


def list_users() -> list:
    """Return all users, newest first, without password hashes."""
    data = _load()
    users = [{k: v for k, v in u.items() if k != "password_hash"} for u in data["users"]]
    return list(reversed(users))


def seed_if_empty(seed_accounts: list):
    """On first run, pre-create a fixed set of accounts (e.g. known team members)."""
    with _lock:
        data = _load()
        if data["users"]:
            return  # already have users, don't touch anything
        for acct in seed_accounts:
            data["users"].append({
                "username": acct["username"],
                "email": acct.get("email", ""),
                "password_hash": generate_password_hash(acct["password"]),
                "ip_address": "seeded",
                "signed_up_at": datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z",
                "is_admin": bool(acct.get("is_admin", False)),
            })
        _save(data)
