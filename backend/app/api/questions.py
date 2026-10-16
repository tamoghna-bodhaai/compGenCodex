from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Response, UploadFile, status

from app.db.database import decode_question_row, get_connection
from app.services.ingestion import IngestionError, MAX_UPLOAD_BYTES, QuestionIngestionService
from app.services.openrouter import ModelConfigurationError, OpenRouterError
from app.services.costs import cost_summary
from app.services.diagrams import diagrams_for
from app.services.source_material import add_attachment, attachment_text, delete_question as remove_bank_question, map_manually, promote_candidate
from app.services.bank_solutions import queue_solution_job, get_solution_job, run_solution_job
from pydantic import BaseModel, Field
from pathlib import Path
import tempfile
from app.services.document_renderer import PaperDocumentRenderer, DocumentRenderError
from app.schemas.papers import ExportFormat, ExportVariant

router = APIRouter(prefix="/api/questions", tags=["questions"])


class SolutionScope(BaseModel):
    source_job_id: str | None = None
    question_ids: list[str] | None = None
    subject: str | None = None
    chapter: str | None = None
    topic: str | None = None
    subtopic: str | None = None
    replace_existing: bool = False


class ManualMapping(BaseModel):
    question_id: str


class CandidateReview(BaseModel):
    changes: dict = Field(default_factory=dict)


@router.post("/ingest", status_code=status.HTTP_202_ACCEPTED)
async def ingest_questions(
    background_tasks: BackgroundTasks,
    source_text: str = Form(default=""),
    conversion_note: str = Form(default=""),
    answer_key_text: str = Form(default=""),
    solutions_text: str = Form(default=""),
    file: UploadFile | None = File(default=None),
    answer_key_file: UploadFile | None = File(default=None),
    solutions_file: UploadFile | None = File(default=None),
) -> dict:
    try:
        # Browsers can submit an unselected file input as an UploadFile with
        # an empty filename. Treat it as absent, including for optional files.
        file = file if file and file.filename else None
        content = await file.read() if file else b""
        filename = file.filename if file else "pasted-question.txt"
        if not source_text.strip() and not content:
            raise IngestionError("Paste question text or upload a PDF or DOCX file.")
        if len(content) > MAX_UPLOAD_BYTES:
            raise IngestionError("Uploads must be 35 MB or smaller.")
        service = QuestionIngestionService()
        service.ensure_configuration()
        attachments = []
        for kind, text, upload in (("answer_key", answer_key_text, answer_key_file), ("solutions", solutions_text, solutions_file)):
            upload = upload if upload and upload.filename else None
            if text.strip() or upload:
                attachment_content = await upload.read() if upload else b""
                if len(attachment_content) > MAX_UPLOAD_BYTES:
                    raise IngestionError("Attachments must be 35 MB or smaller.")
                attachment_name = upload.filename if upload else "pasted.txt"
                prepared_text = text.strip() or attachment_text(attachment_name, attachment_content)
                attachments.append((kind, attachment_name, attachment_content, prepared_text))
        job = service.create_job(filename, content=content, content_type=file.content_type if file else None, source_text=source_text, conversion_note=conversion_note)
        for kind, attachment_name, attachment_content, prepared_text in attachments:
            add_attachment(job["id"], kind, filename=attachment_name, content=attachment_content, text=prepared_text, map_now=False)
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


@router.get("/ingestion-jobs/{job_id}/export")
def export_ingested_source(job_id: str, format: ExportFormat = ExportFormat.DOCX, variant: ExportVariant = ExportVariant.QUESTION_PAPER) -> Response:
    try:
        report = QuestionIngestionService().report(job_id)
    except IngestionError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    questions = report["questions"]
    if not questions:
        raise HTTPException(status_code=422, detail="This source has no accepted questions to export.")
    for position, question in enumerate(questions, 1):
        question["section_id"] = None
        question["position"] = position
        answer = (question.get("answer_json") or {}).get("correct_answer")
        if isinstance(answer, list):
            question["answer_json"] = {"correct_answer": ", ".join(map(str, answer))}
        elif isinstance(answer, dict):
            question["answer_json"] = {"correct_answer": ", ".join(f"{key}→{value}" for key, value in answer.items())}
        question["question_json"].setdefault("marks", question.get("marks"))
        question["question_json"].setdefault("primary_concept", question.get("primary_concept"))
    paper = {"id": job_id, "title": report["job"]["source_name"], "exam": questions[0]["exam"], "subject": questions[0]["subject"],
             "branding_config": {}, "sections": [], "questions": questions}
    try:
        with tempfile.TemporaryDirectory() as directory:
            path, media_type = PaperDocumentRenderer(output_root=Path(directory)).export(paper, output_format=format, variant=variant)
            content = path.read_bytes()
            filename = path.name
    except DocumentRenderError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.post("/ingestion-jobs/{job_id}/attachments")
async def attach_source_material(job_id: str, kind: str = Form(...), text: str = Form(default=""), file: UploadFile | None = File(default=None)) -> dict:
    try:
        content = await file.read() if file else b""
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("Attachments must be 35 MB or smaller.")
        if not text.strip() and not content:
            raise ValueError("Paste or upload an answer key or solutions.")
        job = QuestionIngestionService().get_job(job_id)
        return add_attachment(job_id, kind, filename=file.filename if file else "pasted.txt", content=content, text=text, map_now=job["state"] == "succeeded")
    except (LookupError, IngestionError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/ingestion-jobs/{job_id}/mappings/{mapping_id}")
def resolve_source_mapping(job_id: str, mapping_id: str, request: ManualMapping) -> dict:
    try:
        QuestionIngestionService().get_job(job_id)
        with get_connection() as connection:
            if not connection.execute("SELECT 1 FROM source_mappings WHERE id = ? AND job_id = ?", (mapping_id, job_id)).fetchone():
                raise LookupError("Mapping not found in this source.")
        return map_manually(mapping_id, request.question_id)
    except (LookupError, IngestionError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.post("/ingestion-jobs/{job_id}/candidates/{candidate_id}/promote")
def accept_review_candidate(job_id: str, candidate_id: str, review: CandidateReview) -> dict:
    try:
        return promote_candidate(job_id, candidate_id, review.changes)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.delete("/ingestion-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ingestion_job(job_id: str) -> Response:
    try:
        QuestionIngestionService().delete_job(job_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except IngestionError as error:
        status_code = status.HTTP_409_CONFLICT if "active" in str(error).lower() else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=status_code, detail=str(error)) from error


@router.post("/solutions/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_bank_solutions(scope: SolutionScope, background_tasks: BackgroundTasks) -> dict:
    try:
        job = queue_solution_job(**scope.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    background_tasks.add_task(run_solution_job, job["id"])
    return {"job": job}


@router.get("/solution-jobs/{job_id}")
def bank_solution_job(job_id: str) -> dict:
    try:
        return get_solution_job(job_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


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
    source_job_id: str | None = None,
    paper: str | None = None,
    section: str | None = None,
    response_mode: str | None = None,
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
        ("source_job_id", source_job_id),
    ):
        if value is not None:
            clauses.append(f"{column} = ?")
            parameters.append(value)
    for path, value in (("paper", paper), ("section", section), ("mode", response_mode)):
        if value is not None:
            column = "exam_format_json" if path != "mode" else "response_spec_json"
            clauses.append(f"json_extract({column}, '$.{path}') = ?")
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
def question_catalog(supported_only: bool = False) -> dict:
    """Return the seed-bank taxonomy and its availability without exposing question text."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT exam, subject, chapter, topic, subtopic, question_type, difficulty, COUNT(*) AS count
            FROM questions
            WHERE (? = 0 OR response_spec_json IS NULL OR json_extract(response_spec_json, '$.supported_for_generation') = 1)
            GROUP BY exam, subject, chapter, topic, subtopic, question_type, difficulty
            ORDER BY exam, subject, chapter, topic, subtopic, question_type, difficulty
            """,
            (int(supported_only),),
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


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(question_id: str) -> Response:
    try:
        remove_bank_question(question_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return Response(status_code=204)
