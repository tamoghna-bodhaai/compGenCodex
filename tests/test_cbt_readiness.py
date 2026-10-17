from __future__ import annotations

import asyncio
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi import HTTPException

from tests import test_paper_service, test_test_engine
from app.api.assessments import AccommodationUpdate, AssignmentUpdate, LinkTest, PublishTest, ResponseIn, create_class, add_student, publish_paper, update_assignments, update_accommodation, start_attempt, save_response, submit_attempt, get_attempt, student_tests, result, link_test, ClassroomCreate
from app.api.auth import AccountCreate, create_teacher
from app.core.auth import current_user_context
from app.db.database import get_connection
from app.schemas.papers import PaperCreateRequest
from app.services.papers import PaperConflictError


class PaperSelectionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_paper_service.PaperServiceTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        with get_connection() as connection:
            self.source = dict(connection.execute("SELECT * FROM questions LIMIT 1").fetchone())

    def tearDown(self):
        self.fixture.tearDown()

    def request(self, variations=False):
        source = self.source
        return PaperCreateRequest.model_validate({
            "title": "Selected questions", "exam": source["exam"], "subject": source["subject"],
            "chapters": [source["chapter"]], "generation_mode": "structural_variation",
            "subtopic_plans": [{"topic": source["topic"], "subtopic": source["subtopic"] or None,
                "chapters": [source["chapter"]], "bank_question_ids": [source["id"]],
                "question_types": [{"type": "single_correct_mcq", "count": 1}] if variations else [],
                "difficulty_distribution": [{"difficulty": 3, "count": 1}] if variations else []}],
        })

    def test_bank_only_paper_preserves_source_and_does_not_need_generation(self):
        paper = self.service.create(self.request())
        self.assertEqual(paper["status"], "generated")
        self.assertEqual(paper["question_count"], 1)
        self.assertEqual(paper["requested_question_count"], 1)
        self.assertEqual(paper["remaining_generation_count"], 0)
        self.assertEqual(paper["questions"][0]["question_json"]["stem"], json.loads(self.source["question_json"])["stem"])
        self.assertEqual(paper["questions"][0]["question_json"].get("marks"), self.source["marks"])
        self.assertEqual(paper["questions"][0]["generation_metadata"]["seed_question_id"], self.source["id"])
        with self.assertRaises(PaperConflictError):
            asyncio.run(self.service.regenerate_unlocked(paper["id"]))

    def test_mixed_paper_can_queue_variations_and_preserves_bank_question(self):
        paper = self.service.create(self.request(True))
        self.assertEqual(paper["requested_question_count"], 2)
        self.assertEqual(paper["remaining_generation_count"], 1)
        self.assertEqual(self.service.queue_initial_generation(paper["id"])["total_questions"], 1)
        self.assertEqual(self.service.get(paper["id"])["questions"][0]["generation_metadata"]["origin"], "bank")

    def test_rejects_duplicate_or_out_of_scope_selection(self):
        request = self.request()
        request.subtopic_plans[0].bank_question_ids.append(self.source["id"])
        with self.assertRaises(PaperConflictError): self.service.create(request)
        request = self.request()
        request.subject = "Different subject"
        with self.assertRaises(PaperConflictError): self.service.create(request)


class ResultPrivacyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_test_engine.TestEngineTests()
        self.fixture.setUp()
        f = self.fixture
        teacher = create_teacher(AccountCreate(name="Teacher", email="teacher@example.com", access_code="22222"), f.request(f.admin))
        tr = f.request(teacher)
        self.teacher_request = tr
        classroom = create_class(ClassroomCreate(name="Class"), tr)
        self.classroom_id = classroom["id"]
        student = add_student(classroom["id"], AccountCreate(name="Student", email="student@example.com", access_code="33333"), tr)
        self.student_request = f.request(student)
        self.student_id = student["id"]
        paper_id, self.question_id = "paper-privacy", "question-privacy"
        now = datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)", (paper_id,"Quiz","JEE","Physics","{}","{}","draft",now,now,teacher["id"]))
            spec = {"mode":"select","min_selections":1,"max_selections":2,"marking":{"partial":{"A":1}}}
            connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,locked,generation_metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (self.question_id,paper_id,1,json.dumps({"stem":"Choose","options":["a","b","c"]}),json.dumps({"correct_answer":["A","B"]}),"multiple_correct_mcq",1,0,json.dumps({"response_spec":spec}),now,now))
        token = current_user_context.set(teacher)
        try:
            test = publish_paper(paper_id, PublishTest(duration_minutes=30, results_policy={"mode":"scheduled", "release_at":(datetime.now(UTC)+timedelta(days=1)).isoformat()}), tr)
        finally:
            current_user_context.reset(token)
        update_assignments(test["id"], AssignmentUpdate(class_ids=[classroom["id"]]), tr)
        self.test_id = test["id"]
        self.attempt = start_attempt(test["id"], self.student_request)

    def tearDown(self): self.fixture.tearDown()

    def test_invalid_responses_are_rejected_and_scheduled_score_is_hidden(self):
        attempt = self.attempt
        self.assertNotIn("partial", attempt["questions"][0]["response_spec"]["marking"])
        with self.assertRaises(HTTPException) as raised:
            save_response(attempt["id"], self.question_id, ResponseIn(answer=["Z"], revision=0), self.student_request)
        self.assertEqual(raised.exception.status_code, 422)
        save_response(attempt["id"], self.question_id, ResponseIn(answer=["A","B"], revision=0), self.student_request)
        self.assertIsNone(submit_attempt(attempt["id"], self.student_request)["score"])
        self.assertIsNone(get_attempt(attempt["id"], self.student_request)["score"])
        self.assertIsNone(student_tests(self.student_request)["items"][0]["latest_attempt"]["score"])
        with self.assertRaises(HTTPException) as raised: result(attempt["id"], self.student_request)
        self.assertEqual(raised.exception.status_code, 403)

    def test_concurrent_saves_accept_only_one_revision(self):
        def save(value):
            try:
                save_response(self.attempt["id"], self.question_id, ResponseIn(answer=value, revision=0), self.student_request)
                return 200
            except HTTPException as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(save, (["A"], ["B"])))
        self.assertEqual(sorted(outcomes), [200, 409])

    def test_expired_attempt_is_finalized_even_when_result_is_not_released(self):
        with get_connection() as connection:
            connection.execute("UPDATE test_attempts SET ends_at=? WHERE id=?", ((datetime.now(UTC) - timedelta(minutes=1)).isoformat(), self.attempt["id"]))
        with self.assertRaises(HTTPException) as error:
            result(self.attempt["id"], self.student_request)
        self.assertEqual(error.exception.status_code, 403)
        with get_connection() as connection:
            submitted = connection.execute("SELECT submitted_at FROM test_attempts WHERE id=?", (self.attempt["id"],)).fetchone()[0]
        self.assertIsNotNone(submitted)

    def test_extra_time_applies_to_new_attempt_without_changing_active_attempt(self):
        update_accommodation(self.test_id, self.student_id, AccommodationUpdate(extra_minutes=15), self.teacher_request)
        resumed = get_attempt(self.attempt["id"], self.student_request)
        self.assertEqual(resumed["ends_at"], self.attempt["ends_at"])
        with get_connection() as connection:
            connection.execute("DELETE FROM test_attempts WHERE id=?", (self.attempt["id"],))
        fresh = start_attempt(self.test_id, self.student_request)
        elapsed = datetime.fromisoformat(fresh["ends_at"]) - datetime.fromisoformat(fresh["started_at"])
        self.assertEqual(elapsed, timedelta(minutes=45))

    def test_main_2026_template_checks_structure_and_snapshots_rules(self):
        teacher = self.teacher_request.state.user
        now = datetime.now(UTC).isoformat()
        paper_id = "main-template-paper"
        with get_connection() as connection:
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)", (paper_id,"Main mock","JEE","Physics","{}","{}","draft",now,now,teacher["id"]))
            for subject in ("Mathematics", "Physics", "Chemistry"):
                for index in range(25):
                    numeric = index >= 20
                    kind = "numerical" if numeric else "single_correct_mcq"
                    data = {"stem":f"{subject} {index}", "options":[] if numeric else ["a", "b", "c", "d"]}
                    metadata = {"exam_format":{"exam":"JEE", "subject":subject}}
                    connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,locked,generation_metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (f"{subject}-{index}",paper_id,index+1,json.dumps(data),json.dumps({"correct_answer":"7" if numeric else "A"}),kind,2,0,json.dumps(metadata),now,now))
        token = current_user_context.set(teacher)
        try:
            test = publish_paper(paper_id, PublishTest(duration_minutes=180, template="jee_main_2026_paper1"), self.teacher_request)
        finally:
            current_user_context.reset(token)
        with get_connection() as connection:
            version = json.loads(connection.execute("SELECT content FROM test_versions WHERE test_id=?", (test["id"],)).fetchone()[0])
        self.assertEqual(version["template"], "jee_main_2026_paper1")
        self.assertEqual(len(version["questions"]), 75)
        numeric = next(item for item in version["questions"] if item["kind"] == "numerical")
        self.assertEqual(numeric["scoring"]["negative"], -1)
        self.assertTrue(numeric["response_spec"]["numeric"]["integer_only"])

    def test_linked_total_waits_until_both_results_are_released(self):
        teacher = self.teacher_request.state.user
        now = datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)", ("second-paper","Second","JEE","Physics","{}","{}","draft",now,now,teacher["id"]))
            connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,locked,generation_metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("second-question","second-paper",1,json.dumps({"stem":"Second","options":["a","b","c","d"]}),json.dumps({"correct_answer":"A"}),"single_correct_mcq",1,0,"{}",now,now))
        token = current_user_context.set(teacher)
        try:
            second = publish_paper("second-paper", PublishTest(duration_minutes=30), self.teacher_request)
        finally:
            current_user_context.reset(token)
        update_assignments(second["id"], AssignmentUpdate(class_ids=[self.classroom_id]), self.teacher_request)
        link_test(self.test_id, LinkTest(second_test_id=second["id"]), self.teacher_request)
        second_attempt = start_attempt(second["id"], self.student_request)
        save_response(second_attempt["id"], "second-question", ResponseIn(answer=["A"], revision=0), self.student_request)
        submit_attempt(second_attempt["id"], self.student_request)
        save_response(self.attempt["id"], self.question_id, ResponseIn(answer=["A","B"], revision=0), self.student_request)
        submit_attempt(self.attempt["id"], self.student_request)
        self.assertTrue(all("linked_summary" not in item for item in student_tests(self.student_request)["items"]))
        with patch("app.api.assessments.utc_now", return_value=datetime.now(UTC) + timedelta(days=2)):
            items = student_tests(self.student_request)["items"]
        self.assertEqual(items[0]["linked_summary"]["combined_score"], 8)
