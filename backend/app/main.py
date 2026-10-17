from fastapi import FastAPI, Request
import asyncio
import logging
import uuid
from datetime import UTC, datetime
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.branding import router as branding_router
from app.api.exports import router as exports_router
from app.core.auth import (
    SESSION_COOKIE_NAME,
    AuthenticationConfigurationError,
    get_auth_settings,
    bootstrap_admin, current_user_context, authenticated_account,
)
from app.db.database import database_path, get_connection
from app.api.generation import router as generation_router
from app.api.papers import router as papers_router
from app.api.questions import router as questions_router
from app.api.diagrams import router as diagrams_router
from app.db.database import initialize_database
from app.services.ingestion import QuestionIngestionService
from app.services.papers import PaperService
from app.services.lifecycle import cleanup_events
from app.services.assessment import finalize_expired
from app.api.assessments import router as assessments_router
from app.api.banks import router as banks_router

app = FastAPI(
    title="Question Paper Generator API",
    version="0.1.0",
    description="Foundation API for curated question-bank ingestion and retrieval.",
)
logger = logging.getLogger(__name__)
app.include_router(auth_router)
app.include_router(questions_router)
app.include_router(diagrams_router)
app.include_router(generation_router)
app.include_router(papers_router)
app.include_router(branding_router)
app.include_router(exports_router)
app.include_router(assessments_router)
app.include_router(banks_router)


@app.middleware("http")
async def require_authenticated_api_session(request: Request, call_next):
    """Keep every application API private while leaving login and health reachable."""
    public_paths = {"/api/health", "/api/ready", "/api/auth/login", "/api/auth/logout", "/api/auth/session"}
    if not request.url.path.startswith("/api/") or request.url.path in public_paths:
        return await call_next(request)
    try:
        settings = get_auth_settings()
    except AuthenticationConfigurationError:
        return JSONResponse(status_code=503, content={"detail": "Authentication is not configured."})
    user = authenticated_account(request.cookies.get(SESSION_COOKIE_NAME), settings)
    if not user:
        return JSONResponse(status_code=401, content={"detail": "Authentication required."})
    path = request.url.path
    role = user["role"]
    if role == "student" and not path.startswith(("/api/assessments/student", "/api/assessments/attempts", "/api/assessments/results", "/api/assessments/assets/")):
        return JSONResponse(status_code=403, content={"detail": "Access denied."})
    if role != "admin" and path.startswith("/api/questions/") and request.method != "GET":
        return JSONResponse(status_code=403, content={"detail": "Only the super admin can manage the global question bank."})
    if role != "admin" and path.startswith(("/api/questions/ingestion-jobs", "/api/questions/solution-jobs")):
        return JSONResponse(status_code=403, content={"detail": "Access denied."})
    if role != "admin" and path in {"/api/questions/ingest", "/api/questions/solutions/generate"}:
        return JSONResponse(status_code=403, content={"detail": "Access denied."})
    if role != "admin" and path.startswith("/api/exports"):
        return JSONResponse(status_code=403, content={"detail": "Access denied."})
    if role != "admin" and path.startswith("/api/branding-profiles") and request.method != "GET":
        return JSONResponse(status_code=403, content={"detail": "Only the super admin can manage branding templates."})
    if path.startswith("/api/papers/") and path != "/api/papers/from-reference" and role != "admin":
        paper_id = path.split("/")[3]
        with get_connection() as connection:
            paper = connection.execute("SELECT owner_id FROM papers WHERE id=?", (paper_id,)).fetchone()
        if not paper or paper["owner_id"] != user["id"]:
            return JSONResponse(status_code=404, content={"detail": "Paper not found."})
    if path.startswith("/api/papers/") and request.method == "DELETE":
        paper_id = path.split("/")[3]
        with get_connection() as connection:
            if connection.execute("SELECT 1 FROM tests WHERE paper_id=?", (paper_id,)).fetchone():
                return JSONResponse(status_code=409, content={"detail": "A published test uses this paper. Keep it for the assessment record."})
    request.state.user = user
    token = current_user_context.set(user)
    try:
        return await call_next(request)
    finally:
        current_user_context.reset(token)


@app.on_event("startup")
async def startup() -> None:
    initialize_database()
    bootstrap_admin()
    PaperService.recover_interrupted_generation_jobs()
    QuestionIngestionService.recover_interrupted_jobs()
    QuestionIngestionService.enqueue_recoverable_jobs()
    cleanup_events()
    app.state.expiry_task = asyncio.create_task(_expire_attempts())


async def _expire_attempts() -> None:
    while True:
        try:
            with get_connection() as connection:
                rows = connection.execute("SELECT * FROM test_attempts WHERE submitted_at IS NULL AND ends_at<=?", (datetime.now(UTC).isoformat(),)).fetchall()
                for row in rows:
                    finalize_expired(connection,row)
        except Exception:
            # A transient SQLite failure must not stop the sweep permanently.
            logger.exception("Failed to finalize expired test attempts")
        await asyncio.sleep(30)


@app.on_event("shutdown")
async def shutdown() -> None:
    task = getattr(app.state, "expiry_task", None)
    if task:
        task.cancel()


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/ready", tags=["health"])
def readiness():
    """Confirm the database and its persistent storage can serve traffic."""
    probe = None
    try:
        with get_connection() as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("Database integrity check failed")
        storage = database_path().resolve().parent
        probe = storage / f".readiness-{uuid.uuid4().hex}"
        probe.write_bytes(b"ready")
        probe.unlink()
        return {"status": "ready"}
    except Exception:
        logger.exception("Readiness check failed")
        if probe is not None:
            try:
                probe.unlink(missing_ok=True)
            except OSError:
                pass
        return JSONResponse(status_code=503, content={"status": "not_ready"})
