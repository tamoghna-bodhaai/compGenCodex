from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.api.questions import list_questions, question_catalog
from app.db.database import get_connection
from app.schemas.generation import GenerationRequest
from app.services.retrieval import MetadataFirstRetriever, RetrievalError
from app.services.seed_import import upsert_seed_questions


class QuestionCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "questions.db"
        self.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite:///{self.database_path}"
        upsert_seed_questions(ROOT_DIR / "sample_data" / "jee_definite_integrals_questions.json", self.database_path)

    def tearDown(self) -> None:
        if self.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.previous_database_url
        self.temporary_directory.cleanup()

    def request(self, *, question_type: str = "single_correct_mcq", difficulty: int = 3) -> GenerationRequest:
        return GenerationRequest.model_validate({
            "title": "Catalog request", "exam": "JEE", "subject": "Mathematics",
            "chapters": ["Calculus"], "topics": ["Definite Integrals"],
            "question_types": [{"type": question_type, "count": 1}],
            "difficulty_distribution": [{"difficulty": difficulty, "count": 1}],
            "generation_mode": "structural_variation",
        })

    def test_catalog_aggregates_seed_taxonomy_and_list_filters(self) -> None:
        catalog = question_catalog()["items"]
        self.assertEqual(len(catalog), 1)
        self.assertEqual(catalog[0]["exam"], "JEE")
        self.assertEqual(catalog[0]["topic"], "Definite Integrals")
        self.assertEqual(catalog[0]["count"], 50)
        result = list_questions(exam="JEE", subject="Mathematics", chapter=["Calculus"], topics=["Definite Integrals"], subtopic=[], question_type="single_correct_mcq")
        self.assertEqual(result["count"], 50)
        self.assertEqual(result["total"], 50)

    def test_question_list_supports_pagination(self) -> None:
        first_page = list_questions(exam="JEE", limit=10, offset=0)
        second_page = list_questions(exam="JEE", limit=10, offset=10)
        self.assertEqual(first_page["total"], 50)
        self.assertEqual(first_page["count"], 10)
        self.assertEqual(second_page["offset"], 10)
        self.assertNotEqual(first_page["items"][0]["id"], second_page["items"][0]["id"])

    def test_retrieval_requires_exact_question_type_and_difficulty_band(self) -> None:
        retriever = MetadataFirstRetriever()
        medium = self.request()
        seeds = retriever.retrieve(medium, medium.build_slots()[0])
        self.assertGreaterEqual(len(seeds), 3)
        numerical = self.request(question_type="numerical")
        with self.assertRaises(RetrievalError):
            retriever.retrieve(numerical, numerical.build_slots()[0])
        hard = self.request(difficulty=5)
        with self.assertRaises(RetrievalError):
            retriever.retrieve(hard, hard.build_slots()[0])

    def test_retrieval_accepts_a_single_compatible_seed(self) -> None:
        request = self.request()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id FROM questions WHERE exam = ? AND subject = ? AND question_type = ? AND difficulty = ? LIMIT 1",
                ("JEE", "Mathematics", "single_correct_mcq", 3),
            ).fetchone()
            connection.execute("DELETE FROM questions WHERE id != ?", (row["id"],))
        seeds = MetadataFirstRetriever().retrieve(request, request.build_slots()[0])
        self.assertEqual(len(seeds), 1)


if __name__ == "__main__":
    unittest.main()
