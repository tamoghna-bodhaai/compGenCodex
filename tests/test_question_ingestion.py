from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.api.questions import list_questions
from app.services.openrouter import ModelConfigurationError
from app.services.ingestion import MAX_CHUNK_CHARACTERS, MAX_UPLOAD_BYTES, IngestionError, QuestionIngestionService, _best_pdf_pages, chunk_source, extract_source


CLASSIFIED_RESPONSE = {
    "questions": [{
        "source_question_number": 1,
        "source_page": 2,
        "exam": "JEE",
        "class_level": "Class 12",
        "subject": "Mathematics",
        "chapter": "Calculus",
        "topic": "Definite Integrals",
        "subtopic": "Properties of definite integrals",
        "primary_concept": "Definite integration",
        "secondary_concepts": ["symmetry"],
        "question_archetype": "evaluate_integral",
        "question_type": "single_correct_mcq",
        "difficulty": 3,
        "stem": "Evaluate $\\int_0^1 x\\,dx$.",
        "options": ["$0$", "$1/2$", "$1$", "$2$"],
        "correct_answer": "B",
        "solution": None,
        "expected_time_minutes": 2,
        "marks": 4,
    }],
}


class QuestionIngestionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "questions.db"
        self.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite:///{self.database_path}"

    def tearDown(self) -> None:
        if self.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.previous_database_url
        self.temporary_directory.cleanup()

    async def test_pasted_question_is_classified_and_stored_as_a_seed(self) -> None:
        with patch("app.services.ingestion.OpenRouterClient.call_llm", new=AsyncMock(return_value=CLASSIFIED_RESPONSE)) as call:
            result = await QuestionIngestionService().ingest(
                filename="practice-set.txt",
                content_type="text/plain",
                content=b"",
                source_text="1. Evaluate the integral.",
                conversion_note="Classify this as JEE Mathematics.",
            )
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["verification_status"], "pending_review")
        self.assertEqual(call.await_count, 1)
        stored = list_questions(exam="JEE", subject="Mathematics", topic="Definite Integrals")
        self.assertEqual(stored["count"], 1)
        self.assertEqual(stored["items"][0]["question_json"]["options"], CLASSIFIED_RESPONSE["questions"][0]["options"])
        self.assertEqual(stored["items"][0]["verification_status"], "pending_review")

    def test_extract_source_accepts_pasted_text_and_rejects_unknown_uploads(self) -> None:
        source = extract_source(filename="pasted.txt", content_type="text/plain", content=b"", source_text="A question")
        self.assertEqual(source.pages, [(None, "A question")])
        with self.assertRaises(IngestionError):
            extract_source(filename="questions.png", content_type="image/png", content=b"png", source_text="")

    def test_upload_limit_is_35_mb(self) -> None:
        with self.assertRaisesRegex(IngestionError, "35 MB"):
            extract_source(filename="questions.pdf", content_type="application/pdf", content=b"x" * (MAX_UPLOAD_BYTES + 1), source_text="")

    def test_text_source_is_limited_to_safe_classification_chunks(self) -> None:
        source = extract_source(filename="large.txt", content_type="text/plain", content=b"", source_text="x" * (MAX_CHUNK_CHARACTERS * 2 + 1))
        chunks = chunk_source(source)
        self.assertEqual([len(chunk) for chunk in chunks], [MAX_CHUNK_CHARACTERS, MAX_CHUNK_CHARACTERS, 2])

    def test_configuration_is_checked_before_a_job_is_accepted(self) -> None:
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "", "CLASSIFICATION_MODEL": "", "GENERATION_MODEL": ""}, clear=False):
            with self.assertRaises(ModelConfigurationError):
                QuestionIngestionService.ensure_configuration()

    async def test_ingestion_job_persists_progress_and_completion(self) -> None:
        service = QuestionIngestionService()
        job = service.create_job("practice.txt")
        with patch("app.services.ingestion.OpenRouterClient.call_llm", new=AsyncMock(return_value=CLASSIFIED_RESPONSE)):
            await service.run_job(job["id"], filename="practice.txt", content_type="text/plain", content=b"", source_text="1. Evaluate the integral.", conversion_note="")
        completed = service.get_job(job["id"])
        self.assertEqual(completed["state"], "succeeded")
        self.assertEqual(completed["phase"], "complete")
        self.assertEqual(completed["total_chunks"], 1)
        self.assertEqual(completed["completed_chunks"], 1)
        self.assertEqual(completed["ingested_questions"], 1)

    async def test_cancelling_an_active_job_stops_its_tracked_worker(self) -> None:
        service = QuestionIngestionService()
        job = service.create_job("long-running.pdf", content=b"pdf", content_type="application/pdf")
        started = asyncio.Event()

        async def wait_until_cancelled(*_: object, **__: object) -> dict:
            started.set()
            await asyncio.Event().wait()
            return {}

        with patch.object(QuestionIngestionService, "ingest", new=wait_until_cancelled):
            service.enqueue(job["id"])
            await asyncio.wait_for(started.wait(), timeout=1)
            cancelled = service.cancel_job(job["id"])
            task = QuestionIngestionService._tasks[job["id"]]
            await asyncio.gather(task, return_exceptions=True)

        self.assertEqual(cancelled["control_state"], "cancelled")
        self.assertEqual(cancelled["result_status"], "cancelled")
        self.assertNotIn(job["id"], QuestionIngestionService._tasks)

    def test_completed_ingestion_update_can_be_deleted_but_active_one_cannot(self) -> None:
        service = QuestionIngestionService()
        completed = service.create_job("completed.txt")
        service._update_job(completed["id"], state="failed", phase="failed", finished=True)
        service.delete_job(completed["id"])
        with self.assertRaisesRegex(IngestionError, "not found"):
            service.get_job(completed["id"])

        active = service.create_job("active.txt")
        with self.assertRaisesRegex(IngestionError, "active"):
            service.delete_job(active["id"])

    def test_scanned_pdf_uses_ocr_after_both_text_extractors_are_empty(self) -> None:
        with (
            patch("app.services.ingestion._extract_pdf_with_pypdf", return_value=[(1, "")]),
            patch("app.services.ingestion._extract_pdf_with_pymupdf", return_value=[(1, "")]),
            patch("app.services.ingestion._extract_pdf_with_ocr", return_value=[(1, "1. OCR question text")]) as ocr,
        ):
            pages = _best_pdf_pages(b"scanned-pdf")

        self.assertEqual(pages, [(1, "1. OCR question text")])
        ocr.assert_called_once_with(b"scanned-pdf")

    async def test_vision_classification_bypasses_local_ocr_for_image_only_pdf(self) -> None:
        service = QuestionIngestionService()
        with (
            patch.dict(os.environ, {"CLASSIFICATION_USE_VISION": "true"}, clear=False),
            patch("app.services.ingestion._extract_pdf_with_pypdf", return_value=[(1, "")]),
            patch("app.services.ingestion._extract_pdf_with_pymupdf", return_value=[(1, "")]),
            patch("app.services.ingestion._extract_pdf_with_ocr") as ocr,
            patch("app.services.ingestion._render_pdf_pages_for_vision", return_value=[(1, "page-image")]),
            patch("app.services.ingestion.OpenRouterClient.call_llm", new=AsyncMock(return_value=CLASSIFIED_RESPONSE)) as call,
        ):
            result = await service.ingest(
                filename="image-only.pdf",
                content_type="application/pdf",
                content=b"image-only-pdf",
                source_text="",
                conversion_note="",
            )

        self.assertEqual(result["questions"], 1)
        ocr.assert_not_called()
        self.assertEqual(call.await_args.kwargs["images"], ["page-image"])
