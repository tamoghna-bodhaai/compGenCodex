from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Any


SESSION_COOKIE_NAME = "paper_studio_session"
ACCESS_CODE_PATTERN = re.compile(r"^\d{5}$")
current_user_context: ContextVar[dict[str, Any] | None] = ContextVar("current_user", default=None)


class AuthenticationConfigurationError(RuntimeError):
    """Raised when test access has not been configured in the environment."""


@dataclass(frozen=True)
class AuthSettings:
    allowed_email: str
    access_codes: tuple[str, ...]
    session_secret: str
    session_ttl_hours: int
    cookie_secure: bool

    @property
    def session_ttl_seconds(self) -> int:
        return self.session_ttl_hours * 60 * 60


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def get_auth_settings() -> AuthSettings:
    allowed_email = (os.getenv("AUTH_ALLOWED_EMAIL") or "").strip().lower()
    access_codes = tuple(code.strip() for code in (os.getenv("AUTH_ACCESS_CODES") or "").split(",") if code.strip())
    session_secret = os.getenv("AUTH_SESSION_SECRET") or ""
    try:
        session_ttl_hours = int(os.getenv("AUTH_SESSION_TTL_HOURS", "168"))
    except ValueError as error:
        raise AuthenticationConfigurationError("AUTH_SESSION_TTL_HOURS must be a positive integer.") from error
    if not allowed_email or not access_codes or any(not ACCESS_CODE_PATTERN.fullmatch(code) for code in access_codes) or len(session_secret) < 32 or session_ttl_hours <= 0:
        raise AuthenticationConfigurationError(
            "Authentication is not configured. Set AUTH_ALLOWED_EMAIL, AUTH_ACCESS_CODES, "
            "AUTH_SESSION_SECRET (32+ characters), and AUTH_SESSION_TTL_HOURS. Access codes must be five digits."
        )
    return AuthSettings(
        allowed_email=allowed_email,
        access_codes=access_codes,
        session_secret=session_secret,
        session_ttl_hours=session_ttl_hours,
        cookie_secure=_truthy(os.getenv("AUTH_COOKIE_SECURE")),
    )


def credentials_are_valid(email: str, access_code: str, settings: AuthSettings) -> bool:
    email_matches = hmac.compare_digest(email.strip().lower(), settings.allowed_email)
    code_matches = any(hmac.compare_digest(access_code, configured_code) for configured_code in settings.access_codes)
    return email_matches and code_matches


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session(email: str, settings: AuthSettings, now: int | None = None, session_version: int = 0) -> str:
    issued_at = int(time.time()) if now is None else now
    payload = {"email": email, "exp": issued_at + settings.session_ttl_seconds, "version": session_version}
    encoded_payload = _encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(settings.session_secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded_payload}.{_encode(signature)}"


def verify_session(token: str | None, settings: AuthSettings, now: int | None = None) -> str | None:
    if not token or "." not in token:
        return None
    encoded_payload, encoded_signature = token.split(".", 1)
    expected_signature = hmac.new(settings.session_secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    try:
        actual_signature = _decode(encoded_signature)
        payload = json.loads(_decode(encoded_payload))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not hmac.compare_digest(actual_signature, expected_signature):
        return None
    email = payload.get("email")
    expires_at = payload.get("exp")
    if not isinstance(email, str) or not isinstance(expires_at, int) or expires_at <= (int(time.time()) if now is None else now):
        return None
    return email


def authenticated_account(token: str | None, settings: AuthSettings) -> dict[str, Any] | None:
    email = verify_session(token, settings)
    if not email:
        return None
    user = account_for_email(email)
    if not user or user["status"] != "active":
        return None
    try:
        payload = json.loads(_decode(token.split(".", 1)[0]))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return user if payload.get("version", 0) == user["session_version"] else None


def hash_access_code(code: str, salt: bytes | None = None) -> str:
    if not ACCESS_CODE_PATTERN.fullmatch(code):
        raise ValueError("Access code must be five digits")
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", code.encode(), salt, 300_000)
    return f"{_encode(salt)}.{_encode(digest)}"


def verify_access_code(code: str, encoded: str | None) -> bool:
    if not encoded or "." not in encoded:
        return False
    try:
        salt, expected = encoded.split(".", 1)
        actual = hashlib.pbkdf2_hmac("sha256", code.encode(), _decode(salt), 300_000)
        return hmac.compare_digest(actual, _decode(expected))
    except (ValueError, UnicodeDecodeError):
        return False


def bootstrap_admin(settings: AuthSettings | None = None) -> dict[str, Any]:
    from app.db.database import get_connection
    settings = settings or get_auth_settings()
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (settings.allowed_email,)).fetchone()
        if row is None:
            identifier = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO users(id,email,name,role,status,created_at) VALUES (?,?,?,?,?,?)",
                (identifier, settings.allowed_email, "Super Admin", "admin", "active", datetime.now(UTC).isoformat()),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (identifier,)).fetchone()
        connection.execute("UPDATE papers SET owner_id = ? WHERE owner_id IS NULL", (row["id"],))
        return dict(row)


def account_for_email(email: str) -> dict[str, Any] | None:
    from app.db.database import get_connection
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    return dict(row) if row else None


def public_user(user: dict[str, Any]) -> dict[str, str]:
    return {key: user[key] for key in ("id", "email", "name", "role", "status")}
