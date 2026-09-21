from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.schemas.papers import AddManualQuestionRequest, PaperCreateRequest, PaperUpdateRequest, QuestionEditRequest
from app.db.database import get_connection
from app.services.branding import BrandingProfileService
from app.services.papers import PaperConflictError, PaperNotFoundError, PaperService
from app.services.seed_import import upsert_seed_questions


def paper_request() -> PaperCreateRequest:
    return PaperCreateRequest.model_validate(
        {
            "title": "Integral Revision",
            "exam": "JEE",
            "subject": "Mathematics",
            "chapters": ["Calculus"],
            "topics": ["Definite Integrals"],
            "question_types": [{"type": "single_correct_mcq", "count": 1}],
            "difficulty_distribution": [{"difficulty": 3, "count": 1}],
            "generation_mode": "structural_variation",
        }
    )


class PaperServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.previous_database_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = f"sqlite:///{Path(self.temporary_directory.name) / 'papers.db'}"
        self.service = PaperService()
        self.paper = self.service.create(paper_request())
        upsert_seed_questions(ROOT_DIR / "sample_data" / "jee_definite_integrals_questions.json")

    def tearDown(self) -> None:
        if self.previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.previous_database_url
        self.temporary_directory.cleanup()

    def test_edit_lock_and_reload_manual_question(self) -> None:
        paper_id = self.paper["id"]
        paper = self.service.add_section(paper_id, "Section A - MCQs")
        section_id = paper["sections"][0]["id"]
        paper = self.service.add_manual_question(
            paper_id,
            AddManualQuestionRequest.model_validate(
                {
                    "section_id": section_id,
                    "question_type": "single_correct_mcq",
                    "stem": "Evaluate $\\int_0^1 2x\\,dx$.",
                    "options": ["$0$", "$1$", "$2$", "$4$"],
                    "correct_answer": "B",
                    "solution": "$[x^2]_0^1=1$.",
                    "difficulty": 2,
                    "marks": 3,
                }
            ),
        )
        question_id = paper["questions"][0]["id"]
        paper = self.service.edit_question(paper_id, question_id, QuestionEditRequest(stem="Evaluate $\\int_0^1 3x^2\\,dx$."))
        self.service.set_lock(paper_id, question_id, True)
        reloaded = self.service.get(paper_id)
        self.assertEqual(reloaded["questions"][0]["question_json"]["stem"], "Evaluate $\\int_0^1 3x^2\\,dx$.")
        self.assertTrue(reloaded["questions"][0]["locked"])
        self.assertEqual(reloaded["questions"][0]["section_id"], section_id)

    def test_locked_questions_cannot_be_regenerated(self) -> None:
        paper_id = self.paper["id"]
        paper = self.service.add_manual_question(
            paper_id,
            AddManualQuestionRequest.model_validate(
                {
                    "question_type": "single_correct_mcq",
                    "stem": "Evaluate $\\int_0^1 2x\\,dx$.",
                    "options": ["$0$", "$1$", "$2$", "$4$"],
                    "difficulty": 2,
                }
            ),
        )
        question_id = paper["questions"][0]["id"]
        self.service.set_lock(paper_id, question_id, True)
        with self.assertRaises(PaperConflictError):
            asyncio.run(self.service.regenerate_questions(paper_id, [question_id]))

    def test_paper_title_update_updates_generation_config(self) -> None:
        paper = self.service.update(self.paper["id"], PaperUpdateRequest(title="Updated Integral Revision"))
        self.assertEqual(paper["title"], "Updated Integral Revision")
        self.assertEqual(paper["generation_config"]["title"], "Updated Integral Revision")

    def test_delete_removes_papers_in_every_status_and_their_related_rows(self) -> None:
        draft = self.paper
        generated = self.service.create(paper_request())
        final = self.service.create(paper_request())
        self.service.update(generated["id"], PaperUpdateRequest(status="generated"))
        self.service.update(final["id"], PaperUpdateRequest(status="final"))
        self.service.add_section(final["id"], "Section A")
        self.service.add_manual_question(
            final["id"],
            AddManualQuestionRequest.model_validate({"question_type": "single_correct_mcq", "stem": "Question", "options": ["A", "B", "C", "D"], "difficulty": 3}),
        )
        self.service.queue_initial_generation(draft["id"])

        for paper in (draft, generated, final):
            self.service.delete(paper["id"])
            with self.assertRaises(PaperNotFoundError):
                self.service.get(paper["id"])

        with get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_sections").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_questions").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM paper_generation_jobs").fetchone()[0], 0)

    def test_subtopic_plans_create_one_section_each(self) -> None:
        paper = self.service.create(
            PaperCreateRequest.model_validate(
                {
                    "title": "Mixed",
                    "exam": "JEE",
                    "subject": "Mathematics",
                    "chapters": ["Calculus"],
                    "topics": ["Definite Integrals"],
                    "subtopics": ["Properties", "Area"],
                    "question_types": [{"type": "single_correct_mcq", "count": 3}],
                    "difficulty_distribution": [{"difficulty": 3, "count": 2}, {"difficulty": 1, "count": 1}],
                    "generation_mode": "structural_variation",
                    "subtopic_plans": [
                        {
                            "topic": "Definite Integrals", "subtopic": "Properties",
                            "section_title": "Section A · Properties",
                            "question_types": [{"type": "single_correct_mcq", "count": 2}],
                            "difficulty_distribution": [{"difficulty": 3, "count": 2}],
                        },
                        {
                            "topic": "Definite Integrals", "subtopic": "Area",
                            "question_types": [{"type": "single_correct_mcq", "count": 1}],
                            "difficulty_distribution": [{"difficulty": 1, "count": 1}],
                        },
                    ],
                }
            )
        )
        self.assertEqual([section["title"] for section in paper["sections"]], ["Section A · Properties", "Definite Integrals › Area"])
        self.assertEqual(paper["requested_question_count"], 3)

    def test_queued_generation_job_is_visible_on_paper_and_dashboard_summary(self) -> None:
        job = self.service.queue_initial_generation(self.paper["id"])
        self.assertEqual(job["state"], "queued")
        self.assertEqual(job["total_questions"], 1)
        self.assertEqual(self.service.get(self.paper["id"])["generation_job"]["id"], job["id"])
        self.assertEqual(self.service.list()[0]["generation_job"]["state"], "queued")

    def test_generation_job_can_pause_resume_and_cancel(self) -> None:
        job = self.service.queue_initial_generation(self.paper["id"])
        paused = self.service.pause_generation(self.paper["id"])
        self.assertEqual(paused["id"], job["id"])
        self.assertEqual(paused["control_state"], "paused")
        self.assertEqual(paused["state"], "queued")

        resumed = self.service.resume_generation(self.paper["id"])
        self.assertEqual(resumed["control_state"], "active")

        cancelled = self.service.cancel_generation(self.paper["id"])
        self.assertEqual(cancelled["state"], "failed")
        self.assertEqual(cancelled["control_state"], "cancelled")
        self.assertIn("Partial work is retained", cancelled["message"])
        self.assertEqual(self.service.queue_initial_generation(self.paper["id"])["state"], "queued")

    def test_restart_recovery_marks_unfinished_jobs_as_cancelled(self) -> None:
        self.service.queue_initial_generation(self.paper["id"])
        PaperService.recover_interrupted_generation_jobs()
        job = self.service.get(self.paper["id"])["generation_job"]
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["control_state"], "cancelled")
        self.assertIn("backend restarted", job["message"])

    def test_queued_solution_job_tracks_questions_without_solutions(self) -> None:
        self.service.add_manual_question(
            self.paper["id"],
            AddManualQuestionRequest.model_validate(
                {
                    "question_type": "single_correct_mcq",
                    "stem": "Evaluate the integral.",
                    "options": ["A", "B", "C", "D"],
                    "difficulty": 3,
                }
            ),
        )
        job = self.service.queue_solution_generation(self.paper["id"])
        self.assertEqual(job["operation"], "solutions")
        self.assertEqual(job["total_questions"], 1)

    def test_branding_profiles_can_be_saved_and_reused(self) -> None:
        profile = BrandingProfileService().save("Apex Academy", {"institution_name": "Apex Academy", "footer_text": "Practice"})
        self.assertEqual(profile["branding_config"]["footer_text"], "Practice")
        self.assertEqual(BrandingProfileService().list()[0]["name"], "Apex Academy")

    def test_branding_templates_can_be_updated_duplicated_and_deleted(self) -> None:
        service = BrandingProfileService()
        profile = service.save("Coaching", {"layout": {"preset": "coaching"}})
        updated = service.update(profile["id"], "Coaching standard", {"layout": {"preset": "custom", "header_left": "Marks"}})
        self.assertEqual(updated["name"], "Coaching standard")
        copied = service.duplicate(profile["id"])
        self.assertIsNotNone(copied)
        self.assertNotEqual(copied["id"], profile["id"])
        self.assertEqual(service.resolve(profile["id"], {"footer_text": "Page footer"})["footer_text"], "Page footer")
        self.assertTrue(service.delete(profile["id"]))

    def test_paper_can_select_and_clear_a_branding_template(self) -> None:
        template = BrandingProfileService().save("Export template", {"header_text": "Template header"})
        selected = self.service.update(self.paper["id"], PaperUpdateRequest(branding_template_id=template["id"]))
        self.assertEqual(selected["branding_template_id"], template["id"])
        cleared = self.service.update(self.paper["id"], PaperUpdateRequest(branding_template_id=None))
        self.assertIsNone(cleared["branding_template_id"])

    def test_question_can_be_moved_back_to_unsectioned(self) -> None:
        paper_id = self.paper["id"]
        paper = self.service.add_section(paper_id, "Part A")
        section_id = paper["sections"][0]["id"]
        paper = self.service.add_manual_question(
            paper_id,
            AddManualQuestionRequest.model_validate(
                {
                    "section_id": section_id,
                    "question_type": "single_correct_mcq",
                    "stem": "Evaluate the integral.",
                    "options": ["A", "B", "C", "D"],
                    "difficulty": 3,
                }
            ),
        )
        question_id = paper["questions"][0]["id"]

        updated = self.service.edit_question(paper_id, question_id, QuestionEditRequest(section_id=None))

        self.assertIsNone(updated["questions"][0]["section_id"])

    def test_section_assignment_moves_question_to_the_end_of_that_section(self) -> None:
        paper_id = self.paper["id"]
        section = self.service.add_section(paper_id, "Section A")["sections"][0]
        first = self.service.add_manual_question(
            paper_id,
            AddManualQuestionRequest.model_validate({"section_id": section["id"], "question_type": "single_correct_mcq", "stem": "First", "options": ["A", "B", "C", "D"], "difficulty": 3}),
        )
        second = self.service.add_manual_question(
            paper_id,
            AddManualQuestionRequest.model_validate({"question_type": "single_correct_mcq", "stem": "Second", "options": ["A", "B", "C", "D"], "difficulty": 3}),
        )
        second_id = next(question["id"] for question in second["questions"] if question["question_json"]["stem"] == "Second")
        updated = self.service.edit_question(paper_id, second_id, QuestionEditRequest(section_id=section["id"]))
        questions = [question for question in updated["questions"] if question["section_id"] == section["id"]]
        self.assertEqual([question["question_json"]["stem"] for question in questions], ["First", "Second"])
        self.assertEqual(updated["requested_question_count"], 1)

    def test_generated_question_resolves_recorded_seeds_in_retrieval_order(self) -> None:
        paper = self.service.add_manual_question(
            self.paper["id"],
            AddManualQuestionRequest.model_validate({"question_type": "single_correct_mcq", "stem": "Generated", "options": ["A", "B", "C", "D"], "difficulty": 3}),
        )
        question_id = paper["questions"][0]["id"]
        with get_connection() as connection:
            seed_ids = [row["id"] for row in connection.execute("SELECT id FROM questions ORDER BY source_key LIMIT 2").fetchall()]
            connection.execute(
                "UPDATE paper_questions SET generation_metadata = ? WHERE id = ?",
                ('{"origin":"generated","seed_question_ids":["%s","%s"],"similarity_score":0.42}' % tuple(reversed(seed_ids)), question_id),
            )
        result = self.service.get_question_seeds(self.paper["id"], question_id)
        self.assertEqual([seed["id"] for seed in result["seeds"]], list(reversed(seed_ids)))
        self.assertEqual(result["generation_metadata"]["similarity_score"], 0.42)
        self.assertEqual(result["missing_seed_question_ids"], [])

    def test_seed_comparison_rejects_manual_questions_and_handles_missing_seed_records(self) -> None:
        paper = self.service.add_manual_question(
            self.paper["id"],
            AddManualQuestionRequest.model_validate({"question_type": "single_correct_mcq", "stem": "Manual", "options": ["A", "B", "C", "D"], "difficulty": 3}),
        )
        question_id = paper["questions"][0]["id"]
        with self.assertRaises(PaperConflictError):
            self.service.get_question_seeds(self.paper["id"], question_id)
        with get_connection() as connection:
            seed_id = connection.execute("SELECT id FROM questions ORDER BY source_key LIMIT 1").fetchone()["id"]
            connection.execute(
                "UPDATE paper_questions SET generation_metadata = ? WHERE id = ?",
                ('{"origin":"generated","seed_question_ids":["missing-seed","%s"]}' % seed_id, question_id),
            )
        result = self.service.get_question_seeds(self.paper["id"], question_id)
        self.assertEqual(result["missing_seed_question_ids"], ["missing-seed"])
        self.assertEqual([seed["id"] for seed in result["seeds"]], [seed_id])

    def test_seed_comparison_requires_question_to_belong_to_paper(self) -> None:
        other_paper = self.service.create(paper_request())
        question = self.service.add_manual_question(
            self.paper["id"],
            AddManualQuestionRequest.model_validate({"question_type": "single_correct_mcq", "stem": "Question", "options": ["A", "B", "C", "D"], "difficulty": 3}),
        )["questions"][0]
        with self.assertRaises(Exception) as raised:
            self.service.get_question_seeds(other_paper["id"], question["id"])
        self.assertIn("not found", str(raised.exception).lower())


if __name__ == "__main__":
    unittest.main()
