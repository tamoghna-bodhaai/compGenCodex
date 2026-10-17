from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.services.seed_import import (
    load_seed_questions,
    mark_unreadable_ocr_options,
    normalize_options,
    upsert_seed_questions,
    validate_question,
)
from app.db.database import initialize_database


class SeedImportTests(unittest.TestCase):
    def test_import_is_complete_and_idempotent(self) -> None:
        seed_file = ROOT_DIR / "sample_data" / "jee_definite_integrals_questions.json"
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "questions.db"
            initialize_database(database_path)
            self.assertEqual(upsert_seed_questions(seed_file, database_path), (50, 0))
            self.assertEqual(upsert_seed_questions(seed_file, database_path), (0, 50))
            with sqlite3.connect(database_path) as connection:
                count = connection.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
                topic = connection.execute("SELECT DISTINCT topic FROM questions").fetchone()[0]
            self.assertEqual(count, 50)
            self.assertEqual(topic, "Definite Integrals")

    def test_supported_question_shapes_are_validated_by_type(self) -> None:
        base = {
            "source_key": "shape-test", "exam": "JEE", "subject": "Mathematics", "chapter": "Calculus",
            "topic": "Definite Integrals", "primary_concept": "integration", "question_archetype": "test",
            "difficulty": 3, "source": "test", "source_reference": "test", "verification_status": "verified",
        }
        validate_question({**base, "question_type": "numerical", "question_json": {"stem": "Evaluate", "options": []}})
        validate_question({**base, "question_type": "multiple_correct_mcq", "question_json": {"stem": "Select", "options": ["A", "B"]}})
        with self.assertRaises(ValueError):
            validate_question({**base, "question_type": "single_correct_mcq", "question_json": {"stem": "Choose", "options": ["A"]}})

    def test_labelled_option_objects_are_normalized_for_import(self) -> None:
        labelled_options = [
            {"id": "a", "text": "First"}, {"id": "b", "text": "Second"},
            {"id": "c", "text": "Third"}, {"id": "d", "text": "Fourth"},
        ]
        self.assertEqual(normalize_options(labelled_options), ["First", "Second", "Third", "Fourth"])

    def test_string_source_question_numbers_make_stable_seed_keys(self) -> None:
        payload = {
            "schema_version": 1,
            "collection_id": "test-collection",
            "source_document": "test.pdf",
            "defaults": {
                "exam": "JEE", "subject": "Physics", "chapter": "Thermodynamics",
                "topic": "Thermodynamics", "source": "test", "verification_status": "pending",
            },
            "questions": [{
                "source_question_number": "1", "source_page": 1,
                "question_type": "single_correct_mcq", "difficulty": 3,
                "primary_concept": "First law", "question_archetype": "test",
                "stem": "Choose.", "options": ["A", "B", "C", "D"],
            }],
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            seed_file = Path(temporary_directory) / "seed.json"
            seed_file.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(load_seed_questions(seed_file)[0]["source_key"], "test-collection-q01")

    def test_unreadable_scanned_options_are_marked_for_review(self) -> None:
        question = {
            "verification_status": "transcription_pending",
            "question_json": {"options": ["", "Readable"]},
            "transcription_note": "OCR transcription.",
        }
        mark_unreadable_ocr_options(question)
        self.assertEqual(question["question_json"]["options"][0], "[Option unreadable in source scan]")
        self.assertIn("require review", question["transcription_note"])


if __name__ == "__main__":
    unittest.main()
