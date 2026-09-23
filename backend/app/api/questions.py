from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Response, UploadFile, status

from app.db.database import decode_question_row, get_connection
from app.services.ingestion import IngestionError, MAX_UPLOAD_BYTES, QuestionIngestionService
from app.services.openrouter import ModelConfigurationError, OpenRouterError
from app.services.costs import cost_summary
from app.services.diagrams import diagrams_for

router = APIRouter(prefix="/api/questions", tags=["questions"])


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_questions(
    background_tasks: BackgroundTasks,
    source_text: str = Form(default=""),
    conversion_note: str = Form(default=""),
    file: UploadFile | None = File(default=None),
) -> dict:
    try:
        content = await file.read() if file else b""
        filename = file.filename if file else "pasted-question.txt"
        if not source_text.strip() and not content:
            raise IngestionError("Paste question text or upload a PDF or DOCX file.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise IngestionError("Uploads must be 35 MB or smaller.")
        service = QuestionIngestionService()
        service.ensure_configuration()
        job = service.create_job(filename, content=content, content_type=file.content_type if file else None, source_text=source_text, conversion_note=conversion_note)
        # Start only after the 202 response has been sent. enqueue() retains
        # the task handle so pause/cancel can stop active local processing.
        background_tasks.add_task(QuestionIngestionService.enqueue_after_response, job["id"])
        return {"job": job}
    except ModelConfigurationError as error:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    except (IngestionError, OpenRouterError, ValueError) as error:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/ingestion-jobs")
def list_ingestion_jobs(limit: int = 20) -> dict:
    return {"items": QuestionIngestionService().list_jobs(limit=max(1, min(limit, 100)))}


@router.get("/ingestion-jobs/{job_id}")
def get_ingestion_job(job_id: str) -> dict:
    try:
        return QuestionIngestionService().get_job(job_id)
    except IngestionError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.get("/ingestion-jobs/{job_id}/cost")
def get_ingestion_cost(job_id: str) -> dict:
    try:
        QuestionIngestionService().get_job(job_id)  # verifies the job exists
        return cost_summary(job_id=job_id)
    except IngestionError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.post("/ingestion-jobs/{job_id}/pause")
def pause_ingestion_job(job_id: str) -> dict:
    try:
        return {"job": QuestionIngestionService().pause_job(job_id)}
    except IngestionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/ingestion-jobs/{job_id}/resume")
async def resume_ingestion_job(job_id: str) -> dict:
    try:
        return {"job": QuestionIngestionService().resume_job(job_id)}
    except IngestionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.post("/ingestion-jobs/{job_id}/cancel")
def cancel_ingestion_job(job_id: str) -> dict:
    try:
        return {"job": QuestionIngestionService().cancel_job(job_id)}
    except IngestionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error


@router.get("/ingestion-jobs/{job_id}/report")
def ingestion_report(job_id: str) -> dict:
    try:
        return QuestionIngestionService().report(job_id)
    except IngestionError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error


@router.delete("/ingestion-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ingestion_job(job_id: str) -> Response:
    try:
        QuestionIngestionService().delete_job(job_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except IngestionError as error:
        status_code = status.HTTP_409_CONFLICT if "active" in str(error).lower() else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.get("")
def list_questions(
    exam: str | None = None,
    subject: str | None = None,
    chapter: list[str] | None = Query(default=None),
    topic: str | None = None,
    topics: list[str] | None = Query(default=None),
    subtopic: list[str] | None = Query(default=None),
    question_type: str | None = None,
    difficulty: int | None = None,
    verification_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    if difficulty is not None and not 1 <= difficulty <= 5:
        raise HTTPException(status_code=422, detail="difficulty must be between 1 and 5")
    if not 1 <= limit <= 100:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(status_code=422, detail="offset cannot be negative")
    clauses: list[str] = []
    parameters: list[object] = []
    for column, value in (
        ("exam", exam),
        ("subject", subject),
        ("topic", topic),
        ("question_type", question_type),
        ("difficulty", difficulty),
        ("verification_status", verification_status),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    def values_or_empty(value: list[str] | None) -> list[str]:
        # Direct service tests call this function without FastAPI resolving
        # Query defaults; only an actual list is meaningful in either case.
        return value if isinstance(value, list) else []

    for column, values in (("chapter", values_or_empty(chapter)), ("topic", values_or_empty(topics)), ("subtopic", values_or_empty(subtopic))):
        if values:
            clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
            parameters.extend(values)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with get_connection() as connection:
        total = connection.execute(f"SELECT COUNT(*) FROM questions {where}", parameters).fetchone()[0]
        rows = connection.execute(
            f"SELECT * FROM questions {where} ORDER BY source_key LIMIT ? OFFSET ?", [*parameters, limit, offset]
        ).fetchall()
    items = [decode_question_row(row) for row in rows]
    for item in items:
        item["diagrams"] = diagrams_for(owner_column="seed_question_id", owner_id=item["id"])
    return {"items": items, "count": len(items), "total": total, "offset": offset}


@router.get("/catalog")
def question_catalog() -> dict:
    """Return the seed-bank taxonomy and its availability without exposing question text."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT exam, subject, chapter, topic, subtopic, question_type, difficulty, COUNT(*) AS count
            FROM questions
            GROUP BY exam, subject, chapter, topic, subtopic, question_type, difficulty
            ORDER BY exam, subject, chapter, topic, subtopic, question_type, difficulty
            """
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.get("/{question_id}")
def get_question(question_id: str) -> dict:
    with get_connection() as connection:
        row = connection.execute("SELECT * FROM questions WHERE id = ?", (question_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Question not found")
    item = decode_question_row(row)
    item["diagrams"] = diagrams_for(owner_column="seed_question_id", owner_id=item["id"])
    return item
