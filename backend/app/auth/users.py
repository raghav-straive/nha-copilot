"""User store.

Dev seed users (one per RBAC role) are used locally. For a deployed instance,
set APP_USERS in the environment to override them with strong credentials:
    APP_USERS="admin:S0meStrong!pw:admin;analyst:An0ther!pw:analyst"
Passwords are bcrypt-hashed at load; plaintext never persists.
"""
from __future__ import annotations

import base64
import hashlib
import logging

import bcrypt
from dataclasses import dataclass

from app.config import get_settings

logger = logging.getLogger(__name__)
VALID_ROLES = {"viewer", "analyst", "senior_analyst", "admin"}


def _prepare(pw: str) -> bytes:
    """Reduce a password to a fixed 44-byte token for bcrypt.

    bcrypt silently ignores anything past 72 bytes. Slicing the raw UTF-8 at
    [:72] (the previous approach) can also cut a multi-byte character in half,
    so two different passwords could collapse to the same truncated bytes. A
    SHA-256 digest, base64-encoded, is the standard fix: every byte of the
    password contributes, the result is always well inside bcrypt's limit, and
    the base64 alphabet contains no NUL bytes (which bcrypt would treat as a
    string terminator).
    """
    digest = hashlib.sha256(pw.encode("utf-8")).digest()
    return base64.b64encode(digest)


def _hash(pw: str) -> bytes:
    return bcrypt.hashpw(_prepare(pw), bcrypt.gensalt())


def _verify(pw: str, hashed: bytes) -> bool:
    try:
        return bcrypt.checkpw(_prepare(pw), hashed)
    except ValueError:
        return False


@dataclass
class User:
    username: str
    password_hash: bytes
    role: str


# Dev seed users (used only when APP_USERS is not set). Password = "<username>123".
_SEED = [
    ("viewer", "viewer123", "viewer"),
    ("analyst", "analyst123", "analyst"),
    ("senior", "senior123", "senior_analyst"),
    ("admin", "admin123", "admin"),
]


def _parse_app_users(raw: str) -> list[tuple[str, str, str]]:
    """Parse APP_USERS='user:pw:role;user:pw:role' into tuples."""
    out: list[tuple[str, str, str]] = []
    for entry in raw.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3 or parts[2] not in VALID_ROLES:
            logger.warning("Ignoring malformed APP_USERS entry: %r", entry)
            continue
        out.append((parts[0].strip(), parts[1], parts[2].strip()))
    return out


def _build_users() -> dict[str, User]:
    raw = get_settings().app_users.strip()
    if raw:
        parsed = _parse_app_users(raw)
        if parsed:
            logger.info("Loaded %d user(s) from APP_USERS.", len(parsed))
            return {u: User(u, _hash(pw), r) for u, pw, r in parsed}
        logger.warning("APP_USERS set but no valid entries; falling back to dev seed.")
    logger.warning("Using DEV SEED users (weak passwords). Set APP_USERS in production.")
    return {u: User(u, _hash(pw), r) for u, pw, r in _SEED}


USERS: dict[str, User] = _build_users()


def authenticate(username: str, password: str) -> User | None:
    user = USERS.get(username)
    if user and _verify(password, user.password_hash):
        return user
    return None
