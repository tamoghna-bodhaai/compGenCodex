from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.services.ingestion import ExtractedSource
from app.services.reference import _extract_document_text, _is_docx, _is_pdf, extract_reference_questions
from app.services.reference_filter import format_filter, parse_reference_filter


CLASSIFIED_RESPONSE = {
    "questions": [{
        "source_question_number": 1,
        "source_page": 1,
        "exam": "JEE",
        "class_level": "Class 12",
        "subject": "Mathematics",
        "chapter": "Calculus",
        "topic": "Integrals",
        "subtopic": None,
        "primary_concept": "Integration",
        "secondary_concepts": [],
        "question_archetype": "evaluate_integral",
        "question_type": "single_correct_mcq",
        "difficulty": 3,
        "stem": "Evaluate $\\int_0^1 x dx$.",
        "options": ["0", "1/2", "1", "2"],
        "correct_answer": "B",
        "solution": None,
        "expected_time_minutes": 2,
        "marks": 4,
    }],
}


class ReferenceIngestionTests(unittest.IsolatedAsyncioTestCase):
    def test_reference_filter_supports_disjoint_ranges_without_renumbering(self) -> None:
        selected = parse_reference_filter("generate from qs 10-20 & 25-30")
        self.assertIsNotNone(selected)
        self.assertEqual(min(selected), 10)
        self.assertEqual(max(selected), 30)
        self.assertEqual(len(selected), 17)
        self.assertEqual(format_filter(selected), "10-20,25-30")

    def test_document_type_detection_accepts_common_browser_mime_types(self) -> None:
        self.assertTrue(_is_pdf("reference", "application/x-pdf"))
        self.assertTrue(_is_pdf("reference.pdf", "application/octet-stream"))
        self.assertTrue(_is_docx("reference", "application/vnd.ms-word.document.12"))
        self.assertTrue(_is_docx("reference.docx", "application/octet-stream"))

    def test_document_text_uses_the_shared_ingestion_extractor(self) -> None:
        with patch(
            "app.services.reference.extract_source",
            return_value=ExtractedSource("reference.pdf", [(1, "First question"), (2, "Second question")]),
        ) as extract:
            text = _extract_document_text("reference.pdf", "application/pdf", b"pdf")

        self.assertEqual(text, "[Source page 1]\nFirst question\n\n[Source page 2]\nSecond question")
        self.assertTrue(extract.call_args.kwargs["use_vision"])

    def test_document_mime_aliases_are_normalized_for_shared_extraction(self) -> None:
        with patch(
            "app.services.reference.extract_source",
            return_value=ExtractedSource("reference.docx", [(None, "Question text")]),
        ) as extract:
            _extract_document_text("reference", "application/vnd.ms-word.document.12", b"docx")

        self.assertEqual(
            extract.call_args.kwargs["content_type"],
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    async def test_pdf_reference_sends_selectable_text_and_page_images_to_classifier(self) -> None:
        with (
            patch("app.services.reference._extract_document_text", return_value="[Source page 1]\n1. Evaluate x."),
            patch("app.services.reference._encode_images_for_llm", return_value=(["data:image/png;base64,page"], ["image/png"])),
            patch("app.services.reference.OpenRouterClient.call_llm", new=AsyncMock(return_value=CLASSIFIED_RESPONSE)) as call,
        ):
            questions, images = await extract_reference_questions(
                filename="reference.pdf", content_type="application/pdf", content=b"pdf"
            )

        self.assertEqual(len(questions), 1)
        self.assertEqual(images, ["data:image/png;base64,page"])
        self.assertIn("[Source page 1]", call.await_args.kwargs["user_prompt"])
        self.assertEqual(call.await_args.kwargs["images"], images)
