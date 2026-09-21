from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse

from app.schemas.papers import (
    AddManualQuestionRequest,
    LockRequest,
    PaperCreateRequest,
    PaperExportRequest,
    PaperUpdateRequest,
    QuestionEditRequest,
    RegenerateSelectedRequest,
    SectionCreateRequest,
    SectionUpdateRequest,
)
from app.services.generation import GenerationFailure
from app.services.document_renderer import DocumentRenderError, PaperDocumentRenderer
from app.services.exports import register_export
from app.services.branding import BrandingProfileService
from app.services.openrouter import ModelConfigurationError
from app.services.papers import PaperConflictError, PaperNotFoundError, PaperService
from app.services.retrieval import RetrievalError

router = APIRouter(prefix="/api/papers", tags=["papers"])


def _raise(error: Exception) -> None:
    if isinstance(error, PaperNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, PaperConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    if isinstance(error, ModelConfigurationError):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
    if isinstance(error, (GenerationFailure, RetrievalError)):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    if isinstance(error, DocumentRenderError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
    raise error


@router.get("")
def list_papers() -> dict:
    return {"items": PaperService().list()}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_paper(request: PaperCreateRequest) -> dict:
    return PaperService().create(request)


@router.post("/from-reference", status_code=status.HTTP_201_CREATED)
async def create_from_reference(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    desired_count: int = Form(default=5),
    custom_instruction: str = Form(default=""),
    variation_strength: str = Form(default="balanced"),
    exam: str = Form(default=""),
    subject: str = Form(default=""),
    reference_filter: str = Form(default=""),
) -> dict:
    try:
        # No cap per user request - allow whole paper (e.g. JEE mains 90 Qs); guard only by sane upper bound 200 to avoid runaway
        if desired_count < 1:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="desired_count must be at least 1")
        if desired_count > 200:
            # Still honor "no cap" but prevent accidental 10k request
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="desired_count too large (max 200)")
        content = await file.read()
        filename = file.filename or "reference-paper"
        content_type = file.content_type

        from app.services.reference import extract_reference_questions
        from app.services.reference_filter import format_filter, parse_reference_filter

        reference_questions, reference_images = await extract_reference_questions(
            filename=filename, content_type=content_type, content=content, custom_instruction=custom_instruction
        )

        # Support selective reference: explicit field + implicit parse from free-text custom_instruction
        # If filter detectable -> use only those source_question_numbers, else full paper
        explicit = parse_reference_filter(reference_filter)
        implicit = parse_reference_filter(custom_instruction)
        effective = explicit if explicit is not None else implicit
        raw_filter = (reference_filter or "").strip() or (custom_instruction or "").strip()
        if effective is not None:
            filtered = []
            fallback_filtered = []
            for idx, q in enumerate(reference_questions, start=1):
                num = q.get("source_question_number")
                # source_question_number may be int or None; fallback to ordinal position
                try:
                    n = int(num) if num is not None else idx
                except Exception:
                    n = idx
                if n in effective:
                    filtered.append(q)
                # Also keep fallback mapping for ordinal if LLM numbering differs
                if idx in effective:
                    fallback_filtered.append(q)
            # Prefer direct source_question_number match; if that yields empty but ordinal would match, use ordinal
            if not filtered and fallback_filtered:
                filtered = fallback_filtered
            if not filtered:
                extracted_nums = sorted({int(q.get("source_question_number")) for q in reference_questions if q.get("source_question_number") is not None})
                extracted_desc = f"extracted numbers {extracted_nums[:20]}{'...' if len(extracted_nums) > 20 else ''}" if extracted_nums else f"{len(reference_questions)} questions (numbered 1-{len(reference_questions)})"
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Filter {format_filter(effective)} matched 0 of {len(reference_questions)} extracted questions ({extracted_desc}). Adjust filter or leave empty for full paper.",
                )
            # Preserve user-visible formatting
            reference_questions = filtered

        # Allow caller to override count with actual extracted count if they sent 0? Already handled
        # Reuse STRUCTURAL mode via create_reference_paper
        paper_title = title.strip() or f"Reference — {filename[:40]}"
        paper = PaperService().create_reference_paper(
            title=paper_title,
            exam=exam.strip(),
            subject=subject.strip(),
            reference_questions=reference_questions,
            reference_images=reference_images,
            custom_instruction=custom_instruction,
            desired_count=desired_count,
            variation_strength=variation_strength,
            reference_filter_raw=raw_filter if effective is not None else "",
            reference_filter_formatted=format_filter(effective) if effective is not None else "",
        )
        job = PaperService().queue_initial_generation(paper["id"])
        background_tasks.add_task(_run_initial_generation, paper["id"], job["id"])
        # Return fresh paper with job
        return PaperService().get(paper["id"])
    except HTTPException:
        raise
    except (PaperConflictError, PaperNotFoundError) as error:
        _raise(error)
    except Exception as error:
        # Use same mapping as other generation errors
        if isinstance(error, ModelConfigurationError):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)) from error
        if isinstance(error, (GenerationFailure, RetrievalError)):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error
        # For reference extraction errors, surface as 422
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(error)) from error


@router.get("/{paper_id}")
def get_paper(paper_id: str) -> dict:
    try:
        return PaperService().get(paper_id)
    except PaperNotFoundError as error:
        _raise(error)


@router.get("/{paper_id}/comparison")
def get_paper_comparison(paper_id: str) -> dict:
    try:
        return PaperService().get_paper_comparison(paper_id)
    except (PaperNotFoundError, PaperConflictError) as error:
        _raise(error)


@router.put("/{paper_id}")
def update_paper(paper_id: str, request: PaperUpdateRequest) -> dict:
    try:
        return PaperService().update(paper_id, request)
    except PaperNotFoundError as error:
        _raise(error)


@router.delete("/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_paper(paper_id: str) -> Response:
    try:
        PaperService().delete(paper_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except PaperNotFoundError as error:
        _raise(error)


async def _run_initial_generation(paper_id: str, job_id: str) -> None:
    await PaperService().run_initial_generation_job(paper_id, job_id)


async def _run_solution_generation(paper_id: str, job_id: str) -> None:
    await PaperService().run_solution_generation_job(paper_id, job_id)


@router.post("/{paper_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_paper(paper_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        job = PaperService().queue_initial_generation(paper_id)
        background_tasks.add_task(_run_initial_generation, paper_id, job["id"])
        return {"job": job}
    except (PaperNotFoundError, PaperConflictError, ModelConfigurationError, GenerationFailure, RetrievalError) as error:
        _raise(error)


@router.post("/{paper_id}/solutions/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_solutions(paper_id: str, background_tasks: BackgroundTasks) -> dict:
    try:
        job = PaperService().queue_solution_generation(paper_id)
        background_tasks.add_task(_run_solution_generation, paper_id, job["id"])
        return {"job": job}
    except (PaperNotFoundError, PaperConflictError, ModelConfigurationError, GenerationFailure, RetrievalError) as error:
        _raise(error)


@router.post("/{paper_id}/generation/pause")
def pause_generation(paper_id: str) -> dict:
    try:
        return {"job": PaperService().pause_generation(paper_id)}
    except (PaperNotFoundError, PaperConflictError) as error:
        _raise(error)


@router.post("/{paper_id}/generation/resume")
def resume_generation(paper_id: str) -> dict:
    try:
        return {"job": PaperService().resume_generation(paper_id)}
    except (PaperNotFoundError, PaperConflictError) as error:
        _raise(error)


@router.post("/{paper_id}/generation/cancel")
def cancel_generation(paper_id: str) -> dict:
    try:
        return {"job": PaperService().cancel_generation(paper_id)}
    except (PaperNotFoundError, PaperConflictError) as error:
        _raise(error)


@router.post("/{paper_id}/sections")
def add_section(paper_id: str, request: SectionCreateRequest) -> dict:
    try:
        return PaperService().add_section(paper_id, request.title)
    except PaperNotFoundError as error:
        _raise(error)


@router.put("/{paper_id}/sections/{section_id}")
def update_section(paper_id: str, section_id: str, request: SectionUpdateRequest) -> dict:
    try:
        return PaperService().update_section(paper_id, section_id, title=request.title, position=request.position)
    except PaperNotFoundError as error:
        _raise(error)


@router.delete("/{paper_id}/sections/{section_id}")
def delete_section(paper_id: str, section_id: str) -> dict:
    try:
        return PaperService().delete_section(paper_id, section_id)
    except PaperNotFoundError as error:
        _raise(error)


@router.post("/{paper_id}/questions/manual", status_code=status.HTTP_201_CREATED)
def add_manual_question(paper_id: str, request: AddManualQuestionRequest) -> dict:
    try:
        return PaperService().add_manual_question(paper_id, request)
    except PaperNotFoundError as error:
        _raise(error)


@router.put("/{paper_id}/questions/{question_id}")
def edit_question(paper_id: str, question_id: str, request: QuestionEditRequest) -> dict:
    try:
        return PaperService().edit_question(paper_id, question_id, request)
    except (PaperNotFoundError, PaperConflictError) as error:
        _raise(error)


@router.get("/{paper_id}/questions/{question_id}/seeds")
def get_question_seeds(paper_id: str, question_id: str) -> dict:
    try:
        return PaperService().get_question_seeds(paper_id, question_id)
    except (PaperNotFoundError, PaperConflictError) as error:
        _raise(error)


@router.put("/{paper_id}/questions/{question_id}/lock")
def lock_question(paper_id: str, question_id: str, request: LockRequest) -> dict:
    try:
        return PaperService().set_lock(paper_id, question_id, request.locked)
    except PaperNotFoundError as error:
        _raise(error)


@router.delete("/{paper_id}/questions/{question_id}")
def delete_question(paper_id: str, question_id: str) -> dict:
    try:
        return PaperService().delete_question(paper_id, question_id)
    except PaperNotFoundError as error:
        _raise(error)


@router.post("/{paper_id}/regenerate-selected")
async def regenerate_selected(paper_id: str, request: RegenerateSelectedRequest) -> dict:
    try:
        return await PaperService().regenerate_questions(paper_id, request.question_ids, custom_instruction=request.custom_instruction)
    except (PaperNotFoundError, PaperConflictError, ModelConfigurationError, GenerationFailure, RetrievalError) as error:
        _raise(error)


@router.post("/{paper_id}/regenerate-unlocked")
async def regenerate_unlocked(paper_id: str) -> dict:
    try:
        return await PaperService().regenerate_unlocked(paper_id)
    except (PaperNotFoundError, PaperConflictError, ModelConfigurationError, GenerationFailure, RetrievalError) as error:
        _raise(error)


@router.post("/{paper_id}/export")
def export_paper(paper_id: str, request: PaperExportRequest) -> FileResponse:
    try:
        paper = PaperService().get(paper_id)
        template_id = request.branding_template_id if request.branding_template_id is not None else paper.get("branding_template_id")
        resolved_branding = BrandingProfileService().resolve(template_id, paper.get("branding_config"))
        paper["branding_config"] = {**resolved_branding, **(request.branding_overrides or {})}
        path, media_type = PaperDocumentRenderer().export(paper, output_format=request.format, variant=request.variant)
        register_export(path=path, media_type=media_type, paper_id=paper_id)
        return FileResponse(path, media_type=media_type, filename=path.name)
    except (PaperNotFoundError, DocumentRenderError) as error:
        _raise(error)
