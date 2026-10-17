from __future__ import annotations

import json
import asyncio
import os
import tempfile
import unittest
import uuid
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from fastapi import Request, Response, HTTPException

from app.core.auth import account_for_email, authenticated_account, create_session, current_user_context, get_auth_settings
from app.db.database import SQLITE_SCHEMA, get_connection, initialize_database
from app.api.auth import create_teacher, delete_teacher, teachers, login, update_teacher, AccountCreate, AccountUpdate
from app.schemas.auth import LoginRequest
from app.api.assessments import create_class, add_student, publish_paper, update_assignments, student_tests, start_attempt, save_response, submit_attempt, result, get_attempt, ClassroomCreate, PublishTest, AssignmentUpdate, ResponseIn
from app.services.assessment import score_item, snapshot_item
from app.api.banks import publish_bank, BankPublish
from app.api.questions import list_questions
from app.services.seed_import import upsert_seed_questions
from app.services.papers import PaperService
from app.main import require_authenticated_api_session


class TestEngineTests(unittest.TestCase):
    def setUp(self):
        self.original = dict(os.environ)
        self.temp = tempfile.TemporaryDirectory()
        os.environ.update({"DATABASE_URL":f"sqlite:///{Path(self.temp.name)/'test.db'}",
                           "AUTH_ALLOWED_EMAIL":"admin@example.com", "AUTH_ACCESS_CODES":"12345",
                           "AUTH_SESSION_SECRET":"test-secret-that-is-longer-than-thirty-two-chars", "AUTH_COOKIE_SECURE":"false"})
        initialize_database()
        login(LoginRequest(email="admin@example.com",access_code="12345"),Response())
        self.admin = account_for_email("admin@example.com")

    def tearDown(self):
        os.environ.clear(); os.environ.update(self.original)
        self.temp.cleanup()

    def request(self, user: dict) -> Request:
        request = Request({"type":"http","method":"GET","path":"/","headers":[]})
        request.state.user = user
        return request

    def test_admin_teacher_metrics_and_removal_preserve_records(self):
        teacher = create_teacher(AccountCreate(name="Teacher",email="teacher@example.com",access_code="22222"),self.request(self.admin))
        teacher_request = self.request(teacher)
        classroom = create_class(ClassroomCreate(name="Physics"),teacher_request)
        add_student(classroom["id"],AccountCreate(name="Student",email="student@example.com",access_code="33333"),teacher_request)
        now = datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                               (str(uuid.uuid4()),"Quiz","JEE","Physics",'{}','{}','generated',now,now,teacher["id"]))
        item = teachers(self.request(self.admin))["items"][0]
        self.assertEqual(item["metrics"]["papers_generated"],1)
        self.assertEqual(item["metrics"]["classes_created"],1)
        self.assertEqual(item["metrics"]["students_enrolled"],1)
        self.assertEqual(delete_teacher(teacher["id"],self.request(self.admin)).status_code,204)
        self.assertEqual(teachers(self.request(self.admin))["items"],[])
        self.assertIsNone(authenticated_account(create_session(teacher["email"],get_auth_settings()),get_auth_settings()))
        with get_connection() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM classrooms WHERE owner_id=?",(teacher["id"],)).fetchone()[0],1)

    def test_full_classroom_test_flow_and_immutable_republish(self):
        teacher = create_teacher(AccountCreate(name="Teacher",email="teacher@example.com",access_code="22222"),self.request(self.admin))
        teacher_request = self.request(teacher)
        classroom = create_class(ClassroomCreate(name="Physics"),teacher_request)
        student = add_student(classroom["id"],AccountCreate(name="Student",email="student@example.com",access_code="33333"),teacher_request)
        student_request = self.request(student)
        paper_id, question_id, now = str(uuid.uuid4()),str(uuid.uuid4()),datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (paper_id,"Physics quiz","JEE","Physics",'{}','{}','draft',now,now,teacher["id"]))
            connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,locked,generation_metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (question_id,paper_id,1,json.dumps({"stem":"2 + 2?","options":["1","2","3","4"],"marks":4}),json.dumps({"correct_answer":"D"}),"single_correct_mcq",1,0,'{}',now,now))
        token = current_user_context.set(teacher)
        try:
            published = publish_paper(paper_id,PublishTest(duration_minutes=30),teacher_request)
        finally:
            current_user_context.reset(token)
        test_id = published["id"]
        update_assignments(test_id,AssignmentUpdate(class_ids=[classroom["id"]]),teacher_request)
        self.assertEqual(len(student_tests(student_request)["items"]),1)
        attempt = start_attempt(test_id,student_request)
        saved = save_response(attempt["id"],question_id,ResponseIn(answer=["D"],revision=0),student_request)
        self.assertEqual(saved["revision"],1)
        with self.assertRaises(HTTPException) as raised:
            save_response(attempt["id"],question_id,ResponseIn(answer=["A"],revision=0),student_request)
        self.assertEqual(raised.exception.status_code,409)
        submitted = submit_attempt(attempt["id"],student_request)
        self.assertEqual(submitted["score"],4)
        with get_connection() as connection:
            connection.execute("UPDATE paper_questions SET question_json=? WHERE id=?", (json.dumps({"stem":"Changed question","options":["1","2","3","4"]}),question_id))
        token = current_user_context.set(teacher)
        try:
            self.assertEqual(publish_paper(paper_id,PublishTest(duration_minutes=30,opens_at=datetime(2099,1,1,tzinfo=UTC)),teacher_request)["version"],2)
        finally:
            current_user_context.reset(token)
        self.assertEqual(result(attempt["id"],student_request)["questions"][0]["stem"],"2 + 2?")
        with self.assertRaises(HTTPException) as raised:
            get_attempt(attempt["id"],teacher_request)
        self.assertEqual(raised.exception.status_code,403)

    def test_active_attempt_survives_republish_and_unassignment(self):
        teacher = create_teacher(AccountCreate(name="Teacher",email="teacher@example.com",access_code="22222"),self.request(self.admin))
        teacher_request = self.request(teacher)
        classroom = create_class(ClassroomCreate(name="Physics"),teacher_request)
        student = add_student(classroom["id"],AccountCreate(name="Student",email="student@example.com",access_code="33333"),teacher_request)
        paper_id, question_id, now = str(uuid.uuid4()),str(uuid.uuid4()),datetime.now(UTC).isoformat()
        with get_connection() as connection:
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)",(paper_id,"Quiz","JEE","Physics",'{}','{}','draft',now,now,teacher["id"]))
            connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,locked,generation_metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(question_id,paper_id,1,json.dumps({"stem":"Old","options":["A","B","C","D"]}),json.dumps({"correct_answer":"A"}),"single_correct_mcq",1,0,'{}',now,now))
        token = current_user_context.set(teacher)
        try:
            test = publish_paper(paper_id,PublishTest(duration_minutes=30),teacher_request)
        finally:
            current_user_context.reset(token)
        update_assignments(test["id"],AssignmentUpdate(class_ids=[classroom["id"]]),teacher_request)
        student_request = self.request(student)
        attempt = start_attempt(test["id"],student_request)
        with get_connection() as connection:
            connection.execute("UPDATE paper_questions SET question_json=? WHERE id=?",(json.dumps({"stem":"New","options":["A","B","C","D"]}),question_id))
        token = current_user_context.set(teacher)
        try:
            publish_paper(paper_id,PublishTest(duration_minutes=30,opens_at=datetime(2099,1,1,tzinfo=UTC)),teacher_request)
        finally:
            current_user_context.reset(token)
        update_assignments(test["id"],AssignmentUpdate(),teacher_request)
        resumed = start_attempt(test["id"],student_request)
        self.assertEqual(resumed["id"],attempt["id"])
        self.assertEqual(resumed["questions"][0]["stem"],"Old")

    def test_expiry_scores_saved_answers_and_rejects_late_save(self):
        self.test_full_classroom_test_flow_and_immutable_republish()
        with get_connection() as connection:
            connection.execute("UPDATE test_attempts SET submitted_at=NULL,ends_at='2020-01-01T00:00:00+00:00' WHERE id=(SELECT id FROM test_attempts LIMIT 1)")
            attempt_id = connection.execute("SELECT id FROM test_attempts LIMIT 1").fetchone()[0]
            student = dict(connection.execute("SELECT * FROM users WHERE role='student'").fetchone())
        finished = get_attempt(attempt_id,self.request(student))
        self.assertEqual(finished["score"],4)
        with self.assertRaises(HTTPException) as raised:
            save_response(attempt_id,finished["questions"][0]["id"],ResponseIn(answer=["A"],revision=1),self.request(student))
        self.assertEqual(raised.exception.status_code,409)

    def test_partial_credit_requires_only_correct_subset(self):
        item = {"kind":"multiple_correct_mcq","options":["a","b","c","d"],"answer":["A","C","D"],
                "response_spec":{"mode":"select","min_selections":1,"max_selections":4},
                "scoring":{"full":4,"negative":-2,"zero":0,"partial":{"1":1,"2":2}}}
        self.assertEqual(score_item(item,["A","C"])[0],2)
        self.assertEqual(score_item(item,["A","B"])[0],-2)
        self.assertEqual(score_item(item,None)[0],0)
        item["scoring"]["partial"] = {"two correct": 2}
        self.assertEqual(score_item(item,["A","C"])[0],2)

    def test_numeric_and_matching_rules(self):
        numeric = {"response_spec":{"mode":"numeric","numeric":{"permitted_precision":2,"rounding_rule":"nearest","tolerance":0.01}},"answer":3.14,"scoring":{"full":4,"negative":0,"zero":0}}
        self.assertEqual(score_item(numeric,"3.144")[0],4)
        self.assertEqual(score_item(numeric,"3.20")[0],0)
        matching = {"response_spec":{"mode":"matching","list_i":["I","II"],"list_ii":["P","Q"]},"answer":{"I":"Q","II":"P"},"scoring":{"full":3,"negative":-1,"zero":0}}
        self.assertEqual(score_item(matching,{"I":"Q","II":"P"})[0],3)
        self.assertEqual(score_item(matching,{"I":"P","II":"Q"})[0],-1)

    def test_publication_rejects_missing_answer_and_unreviewed_advanced_marking(self):
        question = {"id":"q","question_type":"single_correct_mcq","question_json":{"stem":"Choose","options":["a","b","c","d"]},"answer_json":None}
        with self.assertRaisesRegex(ValueError,"reviewed answer"):
            snapshot_item(question,None,"JEE Main")
        question["answer_json"] = {"correct_answer":"A"}
        with self.assertRaisesRegex(ValueError,"reviewed full and negative"):
            snapshot_item(question,None,"JEE Advanced")
        question["generation_metadata"] = {"exam_format":{"exam":"JEE Advanced"}}
        with self.assertRaisesRegex(ValueError,"reviewed full and negative"):
            snapshot_item(question,None,"JEE")

    def test_rotating_teacher_code_invalidates_existing_session(self):
        teacher = create_teacher(AccountCreate(name="Teacher",email="teacher@example.com",access_code="22222"),self.request(self.admin))
        settings = get_auth_settings()
        cookie = create_session(teacher["email"],settings,session_version=teacher["session_version"] if "session_version" in teacher else 0)
        self.assertIsNotNone(authenticated_account(cookie,settings))
        update_teacher(teacher["id"],AccountUpdate(access_code="44444"),self.request(self.admin))
        self.assertIsNone(authenticated_account(cookie,settings))

    def test_api_role_isolation_blocks_ingestion_and_foreign_papers(self):
        teacher = create_teacher(AccountCreate(name="Teacher",email="teacher@example.com",access_code="22222"),self.request(self.admin))
        student = create_class(ClassroomCreate(name="Class"),self.request(teacher))
        student = add_student(student["id"],AccountCreate(name="Student",email="student@example.com",access_code="33333"),self.request(teacher))
        settings = get_auth_settings()
        async def call(path: str, user: dict, method: str = "GET"):
            cookie = create_session(user["email"],settings,session_version=user.get("session_version",0))
            request = Request({"type":"http","method":method,"path":path,"query_string":b"","headers":[(b"cookie",f"paper_studio_session={cookie}".encode())],"scheme":"http","server":("test",80)})
            return await require_authenticated_api_session(request,lambda request: asyncio.sleep(0,result=Response(status_code=200)))
        self.assertEqual(asyncio.run(call("/api/questions/ingestion-jobs",teacher)).status_code,403)
        self.assertEqual(asyncio.run(call("/api/branding-profiles",teacher,"POST")).status_code,403)
        self.assertEqual(asyncio.run(call("/api/papers",student)).status_code,403)
        with get_connection() as connection:
            paper_id,now = str(uuid.uuid4()),datetime.now(UTC).isoformat()
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)",(paper_id,"Admin paper","JEE","Math",'{}','{}','draft',now,now,self.admin["id"]))
        self.assertEqual(asyncio.run(call(f"/api/papers/{paper_id}",teacher)).status_code,404)

    def test_global_bank_publication_and_bank_copy_preserve_answer(self):
        source = Path(__file__).resolve().parents[1] / "sample_data" / "jee_definite_integrals_questions.json"
        upsert_seed_questions(source)
        teacher = create_teacher(AccountCreate(name="Teacher",email="teacher@example.com",access_code="22222"),self.request(self.admin))
        with get_connection() as connection:
            question_id = connection.execute("SELECT id FROM questions LIMIT 1").fetchone()["id"]
            connection.execute("UPDATE questions SET answer_json=? WHERE id=?", (json.dumps({"correct_answer":"A"}),question_id))
            paper_id, now = str(uuid.uuid4()),datetime.now(UTC).isoformat()
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at,owner_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
              (paper_id,"Bank paper","JEE","Mathematics",'{}','{}','draft',now,now,teacher["id"]))
        token = current_user_context.set(teacher)
        try:
            self.assertEqual(list_questions(limit=50,offset=0,chapter=[],topics=[],subtopic=[])["total"],0)
        finally:
            current_user_context.reset(token)
        publish_bank("legacy",BankPublish(question_ids=[question_id]),self.request(self.admin))
        token = current_user_context.set(teacher)
        try:
            self.assertEqual(list_questions(limit=50,offset=0,chapter=[],topics=[],subtopic=[])["total"],1)
            paper = PaperService().add_bank_question(paper_id,question_id)
            self.assertEqual(paper["questions"][0]["answer_json"]["correct_answer"],"A")
            self.assertEqual(paper["questions"][0]["generation_metadata"]["seed_question_id"],question_id)
            with get_connection() as connection:
                variation_id = str(uuid.uuid4())
                connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,locked,generation_metadata,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                  (variation_id,paper_id,2,json.dumps({"stem":"Generated variation","options":["1","2","3","4"]}),json.dumps({"correct_answer":"B"}),"single_correct_mcq",1,0,json.dumps({"origin":"generated","seed_question_ids":[question_id]}),now,now))
            published = publish_paper(paper_id,PublishTest(duration_minutes=20),self.request(teacher))
            self.assertEqual(published["question_count"],2)
        finally:
            current_user_context.reset(token)
        with get_connection() as connection:
            connection.execute("DELETE FROM questions WHERE id=?",(question_id,))
            version = json.loads(connection.execute("SELECT content FROM test_versions WHERE test_id=?",(published["id"],)).fetchone()[0])
            self.assertEqual(version["questions"][0]["answer"],["A"])
            self.assertEqual(version["questions"][1]["stem"],"Generated variation")

    def test_populated_legacy_database_gains_owner_without_losing_rows(self):
        path = Path(self.temp.name) / "legacy.db"
        with sqlite3.connect(path) as connection:
            connection.executescript(SQLITE_SCHEMA.split("CREATE TABLE IF NOT EXISTS users")[0])
            connection.execute("INSERT INTO papers(id,title,exam,subject,generation_config,branding_config,status,created_at,updated_at) VALUES ('old','Old paper','JEE','Math','{}','{}','draft','2020','2020')")
            connection.execute("INSERT INTO questions(id,source_key,exam,subject,secondary_concepts,question_type,difficulty,question_json,verification_status,created_at,updated_at) VALUES ('seed','seed-key','JEE','Math','[]','single_correct_mcq',2,'{}','approved','2020','2020')")
            connection.execute("INSERT INTO paper_questions(id,paper_id,position,question_json,answer_json,question_type,difficulty,created_at,updated_at) VALUES ('item','old',1,'{}','{\"correct_answer\":\"A\"}','single_correct_mcq',2,'2020','2020')")
            connection.execute("INSERT INTO question_diagrams(id,paper_question_id,provenance,validation_status,created_at,updated_at) VALUES ('diagram','item','source_crop','accepted','2020','2020')")
            connection.execute("INSERT INTO paper_exports(id,paper_id,kind,filename,relative_path,media_type,byte_size,sha256,created_at) VALUES ('export','old','paper','paper.pdf','paper.pdf','application/pdf',1,'hash','2020')")
        initialize_database(path)
        with sqlite3.connect(path) as connection:
            self.assertEqual(connection.execute("SELECT title FROM papers WHERE id='old'").fetchone()[0],"Old paper")
            self.assertIn("owner_id",[row[1] for row in connection.execute("PRAGMA table_info(papers)")])
            self.assertEqual(connection.execute("SELECT source_key FROM questions WHERE id='seed'").fetchone()[0],"seed-key")
            self.assertEqual(connection.execute("SELECT answer_json FROM paper_questions WHERE id='item'").fetchone()[0],'{"correct_answer":"A"}')
            self.assertEqual(connection.execute("SELECT id FROM question_diagrams WHERE id='diagram'").fetchone()[0],"diagram")
            self.assertEqual(connection.execute("SELECT filename FROM paper_exports WHERE id='export'").fetchone()[0],"paper.pdf")


if __name__ == "__main__": unittest.main()
