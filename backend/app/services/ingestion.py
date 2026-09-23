from __future__ import annotations

import asyncio
import io
import base64
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from docx import Document

from pydantic import ValidationError

from app.core.settings import get_settings
from app.core.error_safety import safe_error_message
from app.prompts.ingestion import INGESTION_SYSTEM_PROMPT, build_ingestion_prompt
from app.schemas.ingestion import ClassificationResponse
from app.db.database import get_connection
from app.services.openrouter import ModelConfigurationError, OpenRouterClient, OpenRouterError
from app.services.seed_import import upsert_questions
from app.services.lifecycle import emit_event
from app.services.diagrams import crop_normalized_page, store_diagram

MAX_UPLOAD_BYTES = 35 * 1024 * 1024
# A classified question includes substantial metadata, so a 28k-character
# source chunk can exceed a provider's 8k output limit before its JSON closes.
MAX_CHUNK_CHARACTERS = 7_000
OCR_RENDER_DPI = 300
OCR_TIMEOUT_SECONDS = 30
VISION_RENDER_DPI = 120


class IngestionError(RuntimeError):
    """Raised for source extraction or classification errors safe to show to teachers."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class ExtractedSource:
    name: str
    pages: list[tuple[int | None, str]]
    vision_pages: list[tuple[int, str]] | None = None


def _extract_pdf_with_pypdf(content: bytes) -> list[tuple[int | None, str]]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return [(number, (page.extract_text() or "").strip()) for number, page in enumerate(reader.pages, 1)]


def _extract_pdf_with_pymupdf(content: bytes) -> list[tuple[int | None, str]]:
    import fitz  # PyMuPDF

    doc = fitz.open(stream=content, filetype="pdf")
    try:
        return [(number, (page.get_text() or "").strip()) for number, page in enumerate(doc, 1)]
    finally:
        doc.close()


def _render_pdf_pages_for_vision(content: bytes) -> list[tuple[int, str]]:
    """Render each PDF page as a compact PNG for a vision-capable classifier."""
    import fitz  # PyMuPDF

    scale = VISION_RENDER_DPI / 72
    document = fitz.open(stream=content, filetype="pdf")
    try:
        return [
            (number, base64.b64encode(page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")).decode("ascii"))
            for number, page in enumerate(document, 1)
        ]
    finally:
        document.close()


def _extract_pdf_with_ocr(content: bytes) -> list[tuple[int | None, str]]:
    """Render a scanned PDF page-by-page and extract text with local Tesseract.

    This deliberately runs only after the native PDF extractors produce no
    usable text. Rendering one page at a time bounds memory use for a 35 MB
    upload while retaining the source page number for the classifier.
    """
    import fitz  # PyMuPDF
    from PIL import Image
    import pytesseract

    scale = OCR_RENDER_DPI / 72
    document = fitz.open(stream=content, filetype="pdf")
    try:
        pages: list[tuple[int | None, str]] = []
        for number, page in enumerate(document, 1):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            with Image.open(io.BytesIO(pixmap.tobytes("png"))) as image:
                text = pytesseract.image_to_string(
                    image,
                    lang="eng",
                    config="--oem 1 --psm 6",
                    timeout=OCR_TIMEOUT_SECONDS,
                ).strip()
            pages.append((number, text))
        return pages
    finally:
        document.close()


def _best_pdf_pages(content: bytes, *, include_ocr: bool = True) -> list[tuple[int | None, str]]:
    """Extract PDF pages with pypdf first, falling back to PyMuPDF when text is sparse.

    Many digital PDFs use CID fonts or XObjects that pypdf renders as empty
    strings while PyMuPDF (fitz) still extracts them. We pick the extractor
    with the most total characters when the pypdf result looks empty.
    """

    pypdf_pages: list[tuple[int | None, str]] | None = None
    pypdf_error: Exception | None = None
    try:
        pypdf_pages = _extract_pdf_with_pypdf(content)
    except Exception as error:  # noqa: BLE001 - pypdf exposes multiple types
        pypdf_error = error
        pypdf_pages = []

    pypdf_chars = sum(len(text) for _, text in (pypdf_pages or []) if text)
    pypdf_non_empty = len([text for _, text in (pypdf_pages or []) if text])
    total_pages = len(pypdf_pages or [])

    should_try_mupdf = False
    if pypdf_error is not None:
        should_try_mupdf = True
    elif not pypdf_pages or pypdf_non_empty == 0:
        should_try_mupdf = True
    else:
        avg_chars = pypdf_chars / total_pages if total_pages else 0
        coverage = pypdf_non_empty / total_pages if total_pages else 0
        if avg_chars < 20 or coverage < 0.5 or pypdf_chars < 100:
            should_try_mupdf = True

    selected_pages = pypdf_pages or []
    if should_try_mupdf:
        try:
            mupdf_pages = _extract_pdf_with_pymupdf(content)
        except ImportError:
            if pypdf_error is not None:
                raise pypdf_error
            mupdf_pages = []
        except Exception:
            if pypdf_error is not None:
                raise pypdf_error
            mupdf_pages = []

        mupdf_chars = sum(len(text) for _, text in mupdf_pages if text)
        # Prefer the extractor that yielded more selectable text.
        if mupdf_chars > pypdf_chars and any(text for _, text in mupdf_pages):
            selected_pages = mupdf_pages
        elif pypdf_error is not None and any(text for _, text in mupdf_pages):
            selected_pages = mupdf_pages
        elif any(text for _, text in mupdf_pages) and not any(text for _, text in selected_pages):
            selected_pages = mupdf_pages

    if any(text for _, text in selected_pages):
        return selected_pages

    if not include_ocr:
        return selected_pages

    # Scanned PDFs have no embedded text layer. OCR is intentionally the last
    # fallback: it is slower and less exact for mathematical notation, but it
    # makes otherwise unreadable question sets available for teacher review.
    try:
        ocr_pages = _extract_pdf_with_ocr(content)
    except Exception:  # OCR must not prevent the standard extraction failure message.
        return selected_pages
    return ocr_pages if any(text for _, text in ocr_pages) else selected_pages


def extract_source(
    *, filename: str, content_type: str | None, content: bytes, source_text: str, use_vision: bool = False
) -> ExtractedSource:
    if source_text.strip():
        return ExtractedSource(filename or "pasted-question.txt", [(None, source_text.strip())])
    if not content:
        raise IngestionError("Paste question text or upload a PDF or DOCX file.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise IngestionError("Uploads must be 35 MB or smaller.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf" or content_type == "application/pdf":
        try:
            pages = _best_pdf_pages(content, include_ocr=not use_vision)
        except Exception as error:  # extraction libraries expose several exception types
            raise IngestionError("This PDF could not be read. Upload a text-based PDF or paste its question text.") from error
    elif suffix == ".docx" or content_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        try:
            document = Document(io.BytesIO(content))
            parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                parts.extend(cell.text.strip() for row in table.rows for cell in row.cells if cell.text.strip())
            pages = [(None, "\n".join(parts))]
        except Exception as error:
            raise IngestionError("This DOCX could not be read. Paste its question text instead.") from error
    else:
        raise IngestionError("Supported uploads are PDF and DOCX. For a single question, paste its text.")
    pages = [(page, text) for page, text in pages if text]
    if not pages and use_vision and (suffix == ".pdf" or content_type == "application/pdf"):
        try:
            vision_pages = _render_pdf_pages_for_vision(content)
        except Exception as error:
            raise IngestionError("This PDF could not be rendered for vision ingestion.") from error
        if vision_pages:
            return ExtractedSource(filename or "uploaded-source", [], vision_pages)
    if not pages:
        raise IngestionError(
            "No readable text was found in this PDF. The file may be scanned, use an unsupported font encoding, "
            "or contain images too unclear for OCR. Try a clearer text-based PDF, or paste the question text. "
            "(Tried pypdf, PyMuPDF, and local OCR.)"
        )
    return ExtractedSource(filename or "uploaded-source", pages)


def chunk_source(source: ExtractedSource) -> list[str]:
    chunks: list[str] = []
    buffer = ""
    for page, text in source.pages:
        prefix = f"[Source page {page}]\n" if page else ""
        remaining = f"{prefix}{text}\n"
        while remaining:
            available = MAX_CHUNK_CHARACTERS - len(buffer)
            if len(remaining) <= available:
                buffer += remaining
                break
            if buffer:
                chunks.append(buffer)
                buffer = ""
                continue
            split_at = remaining.rfind("\n", 0, MAX_CHUNK_CHARACTERS)
            if split_at <= 0:
                split_at = MAX_CHUNK_CHARACTERS
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
    if buffer:
        chunks.append(buffer)
    return chunks


class QuestionIngestionService:
    _tasks: dict[str, asyncio.Task[None]] = {}

    @classmethod
    def enqueue(cls, job_id: str) -> None:
        task = cls._tasks.get(job_id)
        if task is None or task.done():
            cls._tasks[job_id] = asyncio.create_task(cls().run_job(job_id), name=f"ingestion-{job_id}")

    @classmethod
    def enqueue_recoverable_jobs(cls) -> None:
        with get_connection() as connection:
            rows = connection.execute("SELECT id FROM ingestion_jobs WHERE state = 'queued' AND control_state = 'active'").fetchall()
        for row in rows:
            cls.enqueue(row["id"])

    @staticmethod
    def ensure_configuration() -> None:
        """Reject jobs that cannot start before promising that they were accepted."""
        settings = get_settings()
        if not settings.openrouter_api_key or not (settings.classification_model or settings.generation_model):
            raise ModelConfigurationError(
                "Question ingestion is not configured. Set OPENROUTER_API_KEY and CLASSIFICATION_MODEL (or GENERATION_MODEL) before ingesting."
            )

    @staticmethod
    def recover_interrupted_jobs() -> None:
        """Requeue interrupted work; source and checkpoints are durable."""
        now = _now()
        with get_connection() as connection:
            jobs = connection.execute("SELECT id FROM ingestion_jobs WHERE state = 'running' AND control_state = 'active'").fetchall()
            connection.execute(
                "UPDATE ingestion_jobs SET state = 'queued', phase = 'queued', message = 'Recovering after backend restart', updated_at = ? WHERE state = 'running' AND control_state = 'active'",
                (now,),
            )
        for job in jobs:
            emit_event(job_id=job["id"], paper_id=None, operation="ingestion", phase="job", outcome="requeued", failure="interrupted")

    def create_job(self, source_name: str, *, content: bytes = b"", content_type: str | None = None,
                   source_text: str = "", conversion_note: str = "") -> dict:
        job_id, now = str(uuid.uuid4()), _now()
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO ingestion_jobs (id, source_name, state, phase, message, source_content, source_content_type, source_text, conversion_note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (job_id, source_name or "pasted-question.txt", "queued", "queued", "Queued for ingestion", content, content_type, source_text, conversion_note, now, now),
            )
        emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="queue", outcome="accepted")
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise IngestionError("Ingestion job not found.")
        item = dict(row)
        # Source bytes are durable worker input, never API output.
        for internal in ("source_content", "source_text", "conversion_note", "source_content_type"):
            item.pop(internal, None)
        if item.get("error_message"):
            item["error_message"] = safe_error_message(item["error_message"])
        return item

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute("SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.get_job(row["id"]) for row in rows]

    def delete_job(self, job_id: str) -> None:
        """Remove a completed ingestion update without touching imported questions."""
        with get_connection() as connection:
            row = connection.execute("SELECT state FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise IngestionError("Ingestion job not found.")
            if row["state"] in {"queued", "running"}:
                raise IngestionError("An active ingestion update cannot be deleted.")
            connection.execute("DELETE FROM ingestion_jobs WHERE id = ?", (job_id,))

    def pause_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job["state"] not in {"queued", "running"}:
            raise IngestionError("Only active ingestion jobs can be paused.")
        self._update_job(job_id, state="queued", phase="paused", control_state="paused", message="Paused; completed chunks are retained.")
        return self.get_job(job_id)

    def resume_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job.get("control_state") != "paused":
            raise IngestionError("This ingestion job is not paused.")
        self._update_job(job_id, state="queued", phase="queued", control_state="active", message="Queued to resume from the next unfinished chunk")
        self.enqueue(job_id)
        return self.get_job(job_id)

    def cancel_job(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job["state"] not in {"queued", "running"}:
            raise IngestionError("Only active ingestion jobs can be cancelled.")
        self._update_job(job_id, state="failed", phase="cancelled", control_state="cancelled", result_status="cancelled", message="Cancelled; accepted questions and report are retained.", finished=True)
        return self.get_job(job_id)

    def report(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        with get_connection() as connection:
            chunks = [dict(row) for row in connection.execute("SELECT position, state, question_count, error_message, attempts FROM ingestion_chunks WHERE job_id = ? ORDER BY position", (job_id,)).fetchall()]
            candidates = [dict(row) for row in connection.execute("SELECT source_page, source_question_number, confidence, status, validation_notes FROM ingestion_candidates WHERE job_id = ? ORDER BY created_at", (job_id,)).fetchall()]
        return {"job": job, "summary": {"accepted": job.get("accepted_questions", 0), "review_needed": job.get("review_questions", 0), "skipped_chunks": job.get("skipped_chunks", 0), "retryable_chunks": job.get("retryable_chunks", 0)}, "chunks": chunks, "candidates": candidates}

    def _update_job(self, job_id: str, *, state: str | None = None, phase: str | None = None, message: str | None = None,
                    total_chunks: int | None = None, completed_chunks: int | None = None, ingested_questions: int | None = None,
                    error_message: str | None = None, started: bool = False, finished: bool = False,
                    control_state: str | None = None, result_status: str | None = None,
                    accepted_questions: int | None = None, review_questions: int | None = None,
                    skipped_chunks: int | None = None, retryable_chunks: int | None = None) -> None:
        updates: dict[str, Any] = {"updated_at": _now()}
        for key, value in (("state", state), ("phase", phase), ("message", message), ("total_chunks", total_chunks),
                           ("completed_chunks", completed_chunks), ("ingested_questions", ingested_questions), ("error_message", error_message), ("control_state", control_state), ("result_status", result_status), ("accepted_questions", accepted_questions), ("review_questions", review_questions), ("skipped_chunks", skipped_chunks), ("retryable_chunks", retryable_chunks)):
            if value is not None:
                updates[key] = value
        if started:
            updates["started_at"] = updates["updated_at"]
        if finished:
            updates["finished_at"] = updates["updated_at"]
        with get_connection() as connection:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(f"UPDATE ingestion_jobs SET {assignments} WHERE id = ?", [*updates.values(), job_id])

    async def run_job(self, job_id: str, *, filename: str | None = None, content_type: str | None = None,
                      content: bytes | None = None, source_text: str | None = None, conversion_note: str | None = None) -> None:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM ingestion_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise IngestionError("Ingestion job not found.")
        persisted = dict(row)
        if persisted.get("control_state") != "active":
            return
        filename = filename or persisted["source_name"]
        content_type = content_type if content_type is not None else persisted.get("source_content_type")
        content = content if content is not None else (persisted.get("source_content") or b"")
        source_text = source_text if source_text is not None else (persisted.get("source_text") or "")
        conversion_note = conversion_note if conversion_note is not None else (persisted.get("conversion_note") or "")
        self._update_job(job_id, state="running", phase="extracting", message="Extracting readable text", started=not bool(persisted.get("started_at")))
        emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="extraction", outcome="started")

        async def progress(phase: str, message: str, total_chunks: int | None = None, completed_chunks: int | None = None) -> None:
            self._update_job(job_id, phase=phase, message=message, total_chunks=total_chunks, completed_chunks=completed_chunks)

        try:
            result = await self.ingest(filename=filename, content_type=content_type, content=content, source_text=source_text,
                                       conversion_note=conversion_note, on_progress=progress, job_id=job_id)
            if self.get_job(job_id).get("control_state") != "active":
                return
            with get_connection() as connection:
                accepted = connection.execute("SELECT COUNT(*) FROM ingestion_candidates WHERE job_id = ? AND status = 'accepted'", (job_id,)).fetchone()[0]
                review = connection.execute("SELECT COUNT(*) FROM ingestion_candidates WHERE job_id = ? AND status = 'review'", (job_id,)).fetchone()[0]
                skipped = connection.execute("SELECT COUNT(*) FROM ingestion_chunks WHERE job_id = ? AND state = 'failed'", (job_id,)).fetchone()[0]
            result_status = "partial" if skipped or review else result.get("result_status", "succeeded")
            self._update_job(job_id, state="succeeded", phase="complete", result_status=result_status, message=("Partial ingestion complete; review the report." if result_status == "partial" else "Ingestion complete"), ingested_questions=accepted, accepted_questions=accepted, review_questions=review, skipped_chunks=skipped, retryable_chunks=skipped, finished=True)
            emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="job", outcome="succeeded")
        except Exception as error:  # Background work must surface a user-safe failure state.
            if self.get_job(job_id).get("control_state") == "cancelled":
                return
            self._update_job(
                job_id,
                state="failed",
                phase="failed",
                message="Ingestion needs attention",
                error_message=safe_error_message(error),
                finished=True,
            )
            emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="job", outcome="failed", failure=error)

    async def ingest(
        self,
        *,
        filename: str,
        content_type: str | None,
        content: bytes,
        source_text: str,
        conversion_note: str,
        on_progress: Callable[[str, str, int | None, int | None], Awaitable[None] | None] | None = None,
        job_id: str | None = None,
    ) -> dict:
        settings = get_settings()
        source = extract_source(
            filename=filename,
            content_type=content_type,
            content=content,
            source_text=source_text,
            use_vision=settings.classification_use_vision,
        )
        is_pdf = Path(filename).suffix.lower() == ".pdf" or content_type == "application/pdf"
        # Diagram extraction is deliberately PDF-only in v1. Rendered images
        # are also supplied to the classifier so its normalized crop refers to
        # the exact pixels retained below.
        page_images: dict[int, str] = {}
        if is_pdf and settings.diagram_analysis_model:
            try:
                page_images = dict(_render_pdf_pages_for_vision(content))
            except Exception:
                page_images = {}
        emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="extraction", outcome="completed", details={"vision": bool(source.vision_pages)})
        if source.vision_pages:
            # One page per request keeps question boundaries and page references
            # unambiguous, and avoids overwhelming a model's image context.
            chunks: list[tuple[str, list[str] | None]] = [
                (
                    f"The attached image is original source page {page_number}. "
                    "Read and classify every clearly readable question on this page.",
                    [image],
                )
                for page_number, image in source.vision_pages
            ]
        elif page_images:
            chunks = [
                (f"[Source page {page}]\n{text}", [page_images[page]] if page in page_images else None)
                for page, text in source.pages
            ]
        else:
            chunks = [(chunk, None) for chunk in chunk_source(source)]
        if job_id:
            now = _now()
            with get_connection() as connection:
                existing = connection.execute("SELECT COUNT(*) FROM ingestion_chunks WHERE job_id = ?", (job_id,)).fetchone()[0]
                if not existing:
                    for position, (chunk_text, chunk_images) in enumerate(chunks, 1):
                        connection.execute("INSERT INTO ingestion_chunks (id, job_id, position, source_text, images_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), job_id, position, chunk_text, json.dumps(chunk_images) if chunk_images else None, now, now))
        if on_progress:
            notification = on_progress("classifying", f"Classifying 0 of {len(chunks)} source chunks", len(chunks), 0)
            if notification is not None:
                await notification
        # A separately configured diagram analysis model receives page images;
        # ordinary text ingestion continues using the classification model.
        primary_model = (settings.diagram_analysis_model if page_images else None) or settings.classification_model or settings.generation_model
        fallback_model = settings.classification_fallback_model
        # Dedicated ingestion/classification models: primary is user-configurable
        # via CLASSIFICATION_MODEL (e.g. google/gemini-flash-3.5), fallback via
        # CLASSIFICATION_FALLBACK_MODEL. Generation/validation models stay separate.
        client = OpenRouterClient(settings)
        collection_id = f"ingested-{job_id[:12]}" if job_id else f"ingested-{uuid.uuid4().hex[:12]}"
        normalized: list[dict] = []
        review_questions = 0
        skipped_chunks = 0
        inserted_total = 0
        updated_total = 0
        for chunk_number, (chunk, images) in enumerate(chunks, 1):
            if job_id:
                control = self.get_job(job_id).get("control_state")
                if control != "active":
                    return {"collection_id": collection_id, "source": source.name, "inserted": 0, "updated": 0, "questions": 0, "result_status": control}
                with get_connection() as connection:
                    chunk_row = connection.execute("SELECT id, state FROM ingestion_chunks WHERE job_id = ? AND position = ?", (job_id, chunk_number)).fetchone()
                    if chunk_row and chunk_row["state"] == "succeeded":
                        continue
                    if chunk_row:
                        connection.execute("UPDATE ingestion_chunks SET state = 'running', attempts = attempts + 1, updated_at = ? WHERE id = ?", (_now(), chunk_row["id"]))
            models_to_try: list[str | None] = []
            if primary_model:
                models_to_try.append(primary_model)
            if fallback_model and fallback_model not in models_to_try:
                models_to_try.append(fallback_model)
            if not models_to_try:
                models_to_try = [None]

            classified: ClassificationResponse | None = None
            last_error: Exception | None = None
            for attempt_model in models_to_try:
                try:
                    role = "fallback" if attempt_model == fallback_model else "primary"
                    emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="classification", outcome="started", slot=chunk_number, attempt=models_to_try.index(attempt_model) + 1, model=attempt_model, model_role=role)
                    response = await client.call_llm(
                        model=attempt_model,
                        system_prompt=INGESTION_SYSTEM_PROMPT,
                        user_prompt=build_ingestion_prompt(
                            source_name=source.name, conversion_note=conversion_note.strip(), source_text=chunk
                        ),
                        response_schema=ClassificationResponse.model_json_schema(),
                        temperature=0.1,
                        max_tokens=8_000,
                        images=images,
                        cost_context={"job_id": job_id, "operation": "ingestion", "phase": "classification", "slot": chunk_number, "attempt": models_to_try.index(attempt_model) + 1},
                    )
                    classified = ClassificationResponse.model_validate(response)
                    emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="classification", outcome="accepted", slot=chunk_number, attempt=models_to_try.index(attempt_model) + 1, model=attempt_model, model_role=role)
                    break
                except ModelConfigurationError:
                    # Missing API key/model config is not retryable across fallback
                    raise
                except (OpenRouterError, ValidationError, ValueError) as error:
                    last_error = error
                    emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="classification", outcome="failed", slot=chunk_number, attempt=models_to_try.index(attempt_model) + 1, model=attempt_model, model_role=role, failure=error)
                    if attempt_model == models_to_try[-1]:
                        break
                    # Retry with next model (fallback) - transient provider or schema error
                    emit_event(job_id=job_id, paper_id=None, operation="ingestion", phase="fallback", outcome="scheduled", slot=chunk_number, model=fallback_model, model_role="fallback")
                    continue
            if classified is None:
                skipped_chunks += 1
                if job_id:
                    with get_connection() as connection:
                        connection.execute("UPDATE ingestion_chunks SET state = 'failed', error_message = ?, updated_at = ? WHERE job_id = ? AND position = ?", (safe_error_message(last_error or OpenRouterError("Classification failed with no response.")), _now(), job_id, chunk_number))
                continue
            chunk_normalized: list[dict] = []
            for question in classified.questions:
                index = len(normalized) + 1
                record = question.model_dump()
                quality_note = None
                if len(str(record.get("stem", "")).strip()) < 12 or re.search(r"\b(unreadable|illegible|\?\?\?)\b", str(record.get("stem", "")), re.IGNORECASE):
                    quality_note = "Transcription is ambiguous or too short for automatic promotion."
                if job_id:
                    with get_connection() as connection:
                        chunk_id = connection.execute("SELECT id FROM ingestion_chunks WHERE job_id = ? AND position = ?", (job_id, chunk_number)).fetchone()["id"]
                        connection.execute("INSERT INTO ingestion_candidates (id, job_id, chunk_id, source_page, source_question_number, payload_json, confidence, status, validation_notes, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(uuid.uuid4()), job_id, chunk_id, record.get("source_page"), record.get("source_question_number"), json.dumps(record), "high" if not quality_note else "review", "accepted" if not quality_note else "review", quality_note or "Passed structured schema and deterministic quality checks.", primary_model, _now(), _now()))
                if quality_note:
                    review_questions += 1
                    continue
                record.update({
                    "source_key": f"{collection_id}-q{index:04d}",
                    "source_reference": f"{source.name} - page {question.source_page or 'not stated'}, question {question.source_question_number}",
                    "question_json": {"stem": question.stem, "options": question.options},
                    "answer_json": {"correct_answer": question.correct_answer} if question.correct_answer else None,
                    "source": source.name,
                    "verification_status": "pending_review",
                    "_diagram_page": question.source_page,
                })
                for key in ("stem", "options", "correct_answer", "source_question_number", "source_page"):
                    record.pop(key, None)
                normalized.append(record)
                chunk_normalized.append(record)
            # Checkpoint accepted questions before moving to the next chunk so
            # cancellation/restart cannot discard completed useful work.
            if chunk_normalized:
                inserted_chunk, updated_chunk = upsert_questions(chunk_normalized)
                inserted_total += inserted_chunk
                updated_total += updated_chunk
                # Assets are saved only after the seed record is durable. A
                # malformed crop is non-fatal: the text question remains usable.
                for record in chunk_normalized:
                    bbox = record.pop("diagram_bbox", None)
                    required = record.pop("diagram_required", False)
                    description = record.pop("diagram_description", None)
                    render_spec = record.pop("diagram_render_spec", None)
                    page = record.pop("_diagram_page", None)
                    if not required or not bbox or not description or not page_images.get(page):
                        continue
                    with get_connection() as connection:
                        stored = connection.execute("SELECT id FROM questions WHERE source_key = ?", (record["source_key"],)).fetchone()
                    if not stored:
                        continue
                    try:
                        cropped = crop_normalized_page(page_images[page], bbox)
                        store_diagram(owner_column="seed_question_id", owner_id=stored["id"], provenance="source_crop", data=cropped,
                                      description=description, render_spec=render_spec or description, source_page=page,
                                      crop={"x": bbox[0], "y": bbox[1], "width": bbox[2], "height": bbox[3]})
                    except (ValueError, OSError):
                        pass
            if job_id:
                with get_connection() as connection:
                    connection.execute("UPDATE ingestion_chunks SET state = 'succeeded', question_count = ?, updated_at = ? WHERE job_id = ? AND position = ?", (len(classified.questions), _now(), job_id, chunk_number))
            if on_progress:
                notification = on_progress("classifying", f"Classified {chunk_number} of {len(chunks)} source chunks", len(chunks), chunk_number)
                if notification is not None:
                    await notification
        if not normalized and not skipped_chunks:
            if job_id:
                with get_connection() as connection:
                    prior = connection.execute("SELECT COUNT(*) FROM ingestion_candidates WHERE job_id = ? AND status = 'accepted'", (job_id,)).fetchone()[0]
                if prior:
                    return {"collection_id": collection_id, "source": source.name, "inserted": 0, "updated": 0, "questions": prior, "verification_status": "pending_review", "result_status": "succeeded"}
            raise IngestionError("No readable questions were found in this source.")
        if on_progress:
            notification = on_progress("saving", f"Saving {len(normalized)} classified questions", len(chunks), len(chunks))
            if notification is not None:
                await notification
        return {
            "collection_id": collection_id,
            "source": source.name,
            "inserted": inserted_total,
            "updated": updated_total,
            "questions": len(normalized),
            "verification_status": "pending_review",
            "review_questions": review_questions,
            "skipped_chunks": skipped_chunks,
            "result_status": "partial" if skipped_chunks or review_questions else "succeeded",
            "message": "Partial ingestion complete; review the report." if skipped_chunks or review_questions else "Ingestion complete",
        }
