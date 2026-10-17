from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from datetime import UTC, datetime
import uuid
import time

from app.core.auth import (
    SESSION_COOKIE_NAME,
    AuthenticationConfigurationError,
    create_session,
    credentials_are_valid,
    get_auth_settings,
    bootstrap_admin, account_for_email, hash_access_code, public_user, verify_access_code, authenticated_account,
)
from app.db.database import get_connection
from app.schemas.auth import LoginRequest, SessionResponse, SessionUser

router = APIRouter(prefix="/api/auth", tags=["authentication"])


def _settings_or_503():
    try:
        return get_auth_settings()
    except AuthenticationConfigurationError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Authentication is not configured.") from error


def _set_session_cookie(response: Response, token: str, max_age: int, secure: bool) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=SessionResponse)
def login(payload: LoginRequest, response: Response) -> SessionResponse:
    settings = _settings_or_503()
    bootstrap_admin(settings)
    email = payload.email.strip().lower()
    now = int(time.time())
    with get_connection() as connection:
        attempt = connection.execute("SELECT failed_count,window_start FROM auth_login_attempts WHERE email=?", (email,)).fetchone()
        if attempt and attempt["window_start"] + 300 > now and attempt["failed_count"] >= 10:
            raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    user = account_for_email(email)
    valid = bool(user and user["status"] == "active" and (
        credentials_are_valid(payload.email, payload.access_code, settings)
        if user["role"] == "admin" else verify_access_code(payload.access_code, user["access_code_hash"])
    ))
    if not valid:
        with get_connection() as connection:
            if not attempt or attempt["window_start"] + 300 <= now:
                connection.execute("INSERT INTO auth_login_attempts(email,failed_count,window_start) VALUES (?,1,?) ON CONFLICT(email) DO UPDATE SET failed_count=1,window_start=excluded.window_start", (email,now))
            else:
                connection.execute("UPDATE auth_login_attempts SET failed_count=failed_count+1 WHERE email=?", (email,))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or access code.")
    with get_connection() as connection:
        connection.execute("DELETE FROM auth_login_attempts WHERE email=?", (email,))
    _set_session_cookie(response, create_session(user["email"], settings, session_version=user["session_version"]), settings.session_ttl_seconds, settings.cookie_secure)
    return SessionResponse(authenticated=True, user=SessionUser(**public_user(user)))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    settings = _settings_or_503()
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return response


@router.get("/session", response_model=SessionResponse)
def session(request: Request) -> SessionResponse:
    settings = _settings_or_503()
    bootstrap_admin(settings)
    user = authenticated_account(request.cookies.get(SESSION_COOKIE_NAME), settings)
    if not user:
        return SessionResponse(authenticated=False)
    return SessionResponse(authenticated=True, user=SessionUser(**public_user(user)))


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=320)
    access_code: str = Field(pattern=r"^\d{5}$")


class AccountUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    access_code: str | None = Field(default=None, pattern=r"^\d{5}$")


def _actor(request: Request, role: str) -> dict:
    user = getattr(request.state, "user", None)
    if not user or user["role"] != role:
        raise HTTPException(403, "Access denied")
    return user


@router.get("/teachers")
def teachers(request: Request) -> dict:
    _actor(request, "admin")
    with get_connection() as connection:
        return {"items": [public_user(dict(row)) for row in connection.execute("SELECT * FROM users WHERE role='teacher' ORDER BY name")]}


@router.post("/teachers", status_code=201)
def create_teacher(payload: AccountCreate, request: Request) -> dict:
    actor = _actor(request, "admin")
    return _create_account(payload, "teacher", actor["id"])


def _create_account(payload: AccountCreate, role: str, owner_id: str | None = None) -> dict:
    email = payload.email.strip().lower()
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "Email already exists")
        identifier = str(uuid.uuid4())
        connection.execute("INSERT INTO users(id,email,name,role,status,access_code_hash,owner_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
                           (identifier,email,payload.name.strip(),role,"active",hash_access_code(payload.access_code),owner_id,datetime.now(UTC).isoformat()))
        row = connection.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
    return public_user(dict(row))


def update_managed_account(identifier: str, payload: AccountUpdate, *, role: str, owner_id: str | None = None) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM users WHERE id=? AND role=?", (identifier,role)).fetchone()
        if not row or (owner_id is not None and row["owner_id"] != owner_id):
            raise HTTPException(404, "Account not found")
        updates = payload.model_dump(exclude_none=True)
        if "access_code" in updates:
            updates["access_code_hash"] = hash_access_code(updates.pop("access_code"))
            updates["session_version"] = row["session_version"] + 1
        if updates.get("status") == "suspended":
            updates["session_version"] = row["session_version"] + 1
        if updates:
            connection.execute(f"UPDATE users SET {', '.join(key+'=?' for key in updates)} WHERE id=?", [*updates.values(),identifier])
        return public_user(dict(connection.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()))


@router.patch("/teachers/{teacher_id}")
def update_teacher(teacher_id: str, payload: AccountUpdate, request: Request) -> dict:
    _actor(request, "admin")
    return update_managed_account(teacher_id,payload,role="teacher")
