from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.branding import router as branding_router
from app.api.exports import router as exports_router
from app.core.auth import (
    SESSION_COOKIE_NAME,
    AuthenticationConfigurationError,
    get_auth_settings,
    verify_session,
)
from app.api.generation import router as generation_router
from app.api.papers import router as papers_router
from app.api.questions import router as questions_router
from app.db.database import initialize_database
from app.services.ingestion import QuestionIngestionService
from app.services.papers import PaperService
from app.services.lifecycle import cleanup_events

app = FastAPI(
    title="Question Paper Generator API",
    version="0.1.0",
    description="Foundation API for curated question-bank ingestion and retrieval.",
)
app.include_router(auth_router)
app.include_router(questions_router)
app.include_router(generation_router)
app.include_router(papers_router)
app.include_router(branding_router)
app.include_router(exports_router)


@app.middleware("http")
async def require_authenticated_api_session(request: Request, call_next):
    """Keep every application API private while leaving login and health reachable."""
    public_paths = {"/api/health", "/api/auth/login", "/api/auth/logout", "/api/auth/session"}
    if not request.url.path.startswith("/api/") or request.url.path in public_paths:
        return await call_next(request)
    try:
        settings = get_auth_settings()
    except AuthenticationConfigurationError:
        return JSONResponse(status_code=503, content={"detail": "Authentication is not configured."})
    if verify_session(request.cookies.get(SESSION_COOKIE_NAME), settings) is None:
        return JSONResponse(status_code=401, content={"detail": "Authentication required."})
    return await call_next(request)


@app.on_event("startup")
def startup() -> None:
    initialize_database()
    PaperService.recover_interrupted_generation_jobs()
    QuestionIngestionService.recover_interrupted_jobs()
    cleanup_events()


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
