from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.auth import (
    SESSION_COOKIE_NAME,
    AuthenticationConfigurationError,
    create_session,
    credentials_are_valid,
    get_auth_settings,
    verify_session,
)
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
    if not credentials_are_valid(payload.email, payload.access_code, settings):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or access code.")
    _set_session_cookie(response, create_session(settings.allowed_email, settings), settings.session_ttl_seconds, settings.cookie_secure)
    return SessionResponse(authenticated=True, user=SessionUser(email=settings.allowed_email))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> Response:
    settings = _settings_or_503()
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax")
    return response


@router.get("/session", response_model=SessionResponse)
def session(request: Request) -> SessionResponse:
    settings = _settings_or_503()
    email = verify_session(request.cookies.get(SESSION_COOKIE_NAME), settings)
    if email is None:
        return SessionResponse(authenticated=False)
    return SessionResponse(authenticated=True, user=SessionUser(email=email))
