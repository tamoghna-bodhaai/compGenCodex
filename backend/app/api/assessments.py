"""Native classroom and computer-based assessment API."""
from __future__ import annotations

import json
import sqlite3
import uuid
import shutil
from pathlib import Path
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, model_validator

from app.api.auth import AccountCreate, AccountUpdate, _create_account, update_managed_account
from app.db.database import get_connection
from app.db.database import database_path
from app.services.diagrams import asset_path, get_diagram
from app.services.assessment import finalize_expired, iso, parse_time, score_version, snapshot_item, utc_now
from app.services.papers import PaperService, PaperNotFoundError

router = APIRouter(prefix="/api/assessments", tags=["assessments"])


def test_asset_dir() -> Path:
    """Keep immutable media on the same persistent volume as the SQLite DB."""
    return database_path().resolve().parent / "media" / "test-assets"


def actor(request: Request, *roles: str) -> dict:
    user = request.state.user
    if user["role"] not in roles:
        raise HTTPException(403, "Access denied")
    return user


def owned_test(connection: sqlite3.Connection, test_id: str, user: dict) -> dict:
    row = connection.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()
    if not row or (user["role"] != "admin" and row["owner_id"] != user["id"]):
        raise HTTPException(404, "Test not found")
    return dict(row)


def assigned(connection: sqlite3.Connection, test_id: str, student_id: str) -> bool:
    return connection.execute("""SELECT 1 FROM test_assignments a WHERE a.test_id=? AND
      (a.student_id=? OR a.class_id IN (SELECT class_id FROM class_memberships WHERE student_id=?)) LIMIT 1""",
      (test_id,student_id,student_id)).fetchone() is not None


def test_view(connection: sqlite3.Connection, test: dict) -> dict:
    version = connection.execute("SELECT ordinal,content FROM test_versions WHERE id=?", (test["published_version_id"],)).fetchone() if test["published_version_id"] else None
    count = len(json.loads(version["content"])["questions"]) if version else 0
    assignment_count = connection.execute("SELECT COUNT(*) FROM test_assignments WHERE test_id=?", (test["id"],)).fetchone()[0]
    return {**test, "results_policy": json.loads(test["results_policy"]), "question_count": count,
            "version": version["ordinal"] if version else 0, "assignment_count": assignment_count}


class ClassroomCreate(BaseModel):
    name: str = Field(min_length=1, max_length=160)


@router.get("/classes")
def classes(request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM classrooms WHERE owner_id=? ORDER BY name", (user["id"],)).fetchall()
        return {"items": [{**dict(row), "students": [dict(member) for member in connection.execute("SELECT u.id,u.name,u.email,u.role,u.status FROM users u JOIN class_memberships m ON m.student_id=u.id WHERE m.class_id=? ORDER BY u.name", (row["id"],))]} for row in rows]}


@router.post("/classes", status_code=201)
def create_class(payload: ClassroomCreate, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    identifier = str(uuid.uuid4())
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM classrooms WHERE owner_id=? AND name=?", (user["id"],payload.name.strip())).fetchone():
            raise HTTPException(409, "Class name already exists")
        connection.execute("INSERT INTO classrooms(id,owner_id,name,created_at) VALUES (?,?,?,?)", (identifier,user["id"],payload.name.strip(),iso(utc_now())))
    return {"id": identifier, "name": payload.name.strip(), "students": []}


@router.post("/classes/{class_id}/students", status_code=201)
def add_student(class_id: str, payload: AccountCreate, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        classroom = connection.execute("SELECT 1 FROM classrooms WHERE id=? AND owner_id=?", (class_id,user["id"])).fetchone()
    if not classroom:
        raise HTTPException(404, "Class not found")
    student = _create_account(payload, "student", user["id"])
    with get_connection() as connection:
        connection.execute("INSERT INTO class_memberships(class_id,student_id) VALUES (?,?)", (class_id,student["id"]))
    return student


class AddExistingStudent(BaseModel):
    email: str


@router.post("/classes/{class_id}/members")
def add_member(class_id: str, payload: AddExistingStudent, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        classroom = connection.execute("SELECT 1 FROM classrooms WHERE id=? AND owner_id=?", (class_id,user["id"])).fetchone()
        student = connection.execute("SELECT id FROM users WHERE email=? AND role='student' AND status='active' AND owner_id=?", (payload.email.strip().lower(),user["id"])).fetchone()
        if not classroom or not student:
            raise HTTPException(404, "Class or student not found")
        connection.execute("INSERT OR IGNORE INTO class_memberships(class_id,student_id) VALUES (?,?)", (class_id,student["id"]))
    return {"ok": True}


@router.patch("/students/{student_id}")
def update_student(student_id: str, payload: AccountUpdate, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    return update_managed_account(student_id,payload,role="student",owner_id=None if user["role"] == "admin" else user["id"])


class PublishTest(BaseModel):
    duration_minutes: int = Field(ge=1, le=360)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    max_attempts: int = Field(default=1, ge=1, le=10)
    results_policy: dict = Field(default_factory=lambda: {"mode": "immediate"})

    @model_validator(mode="after")
    def check(self):
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValueError("Closing time must follow opening time")
        if self.results_policy.get("mode") not in {"immediate", "scheduled"}:
            raise ValueError("Result release mode must be immediate or scheduled")
        if self.results_policy.get("mode") == "scheduled" and not self.results_policy.get("release_at"):
            raise ValueError("Scheduled results need a release time")
        if self.results_policy.get("release_at"):
            parse_time(self.results_policy["release_at"])
        return self


@router.post("/papers/{paper_id}/publish", status_code=201)
def publish_paper(paper_id: str, payload: PublishTest, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    try:
        paper = PaperService().get(paper_id)
    except PaperNotFoundError as error:
        raise HTTPException(404, "Paper not found") from error
    if paper["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(404, "Paper not found")
    if not paper["questions"]:
        raise HTTPException(422, "Add at least one question before publishing")
    sections = {section["id"]: section["title"] for section in paper["sections"]}
    items, errors = [], []
    for position, question in enumerate(paper["questions"], start=1):
        try:
            items.append(snapshot_item(question, sections.get(question["section_id"]),paper["exam"]))
        except ValueError as error:
            errors.append({"position": position, "question_id": question["id"], "reason": str(error)})
    if errors:
        raise HTTPException(422, {"message": "Fix questions before publishing", "questions": errors})
    now, test_id, version_id = iso(utc_now()), str(uuid.uuid4()), str(uuid.uuid4())
    with get_connection() as connection:
        test = connection.execute("SELECT * FROM tests WHERE paper_id=?", (paper_id,)).fetchone()
        if test:
            test_id = test["id"]
            ordinal = connection.execute("SELECT COALESCE(MAX(ordinal),0)+1 FROM test_versions WHERE test_id=?", (test_id,)).fetchone()[0]
            connection.execute("UPDATE tests SET title=?,duration_seconds=?,opens_at=?,closes_at=?,max_attempts=?,results_policy=?,status='published',published_version_id=?,updated_at=? WHERE id=?",
              (paper["title"],payload.duration_minutes*60,iso(payload.opens_at) if payload.opens_at else None,iso(payload.closes_at) if payload.closes_at else None,payload.max_attempts,json.dumps(payload.results_policy),version_id,now,test_id))
        else:
            ordinal = 1
            connection.execute("INSERT INTO tests(id,paper_id,owner_id,title,status,duration_seconds,opens_at,closes_at,max_attempts,results_policy,published_version_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
              (test_id,paper_id,user["id"],paper["title"],"published",payload.duration_minutes*60,iso(payload.opens_at) if payload.opens_at else None,iso(payload.closes_at) if payload.closes_at else None,payload.max_attempts,json.dumps(payload.results_policy),version_id,now,now))
        version_content = {"questions":items,"duration_seconds":payload.duration_minutes*60,"results_policy":payload.results_policy}
        connection.execute("INSERT INTO test_versions(id,test_id,ordinal,content,published_at) VALUES (?,?,?,?,?)", (version_id,test_id,ordinal,json.dumps(version_content),now))
        asset_dir = test_asset_dir()
        for item in items:
            for media in item["diagrams"]:
                source = get_diagram(media["id"])
                path = asset_path(source) if source else None
                if path is None:
                    raise HTTPException(422, "Diagram became unavailable while publishing")
                asset_dir.mkdir(parents=True, exist_ok=True)
                asset_id = str(uuid.uuid4())
                destination = asset_dir / f"{asset_id}.png"
                shutil.copyfile(path,destination)
                connection.execute("INSERT INTO test_assets(id,version_id,storage_path,mime_type) VALUES (?,?,?,?)", (asset_id,version_id,str(destination),source.get("mime_type") or "image/png"))
                media["id"],media["url"] = asset_id,f"/api/assessments/assets/{asset_id}"
        connection.execute("UPDATE test_versions SET content=? WHERE id=?", (json.dumps(version_content),version_id))
        return test_view(connection, dict(connection.execute("SELECT * FROM tests WHERE id=?", (test_id,)).fetchone()))


@router.get("/assets/{asset_id}")
def test_asset(asset_id: str, request: Request) -> FileResponse:
    user = request.state.user
    with get_connection() as connection:
        row = connection.execute("SELECT a.*,v.test_id FROM test_assets a JOIN test_versions v ON v.id=a.version_id WHERE a.id=?", (asset_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Asset not found")
        test = connection.execute("SELECT owner_id FROM tests WHERE id=?", (row["test_id"],)).fetchone()
        allowed = user["role"] == "admin" or user["role"] == "teacher" and test["owner_id"] == user["id"] or user["role"] == "student" and connection.execute("SELECT 1 FROM test_attempts WHERE student_id=? AND version_id=?", (user["id"],row["version_id"])).fetchone()
        if not allowed:
            raise HTTPException(404, "Asset not found")
        path = Path(row["storage_path"]).resolve()
        if not path.is_relative_to(test_asset_dir()) or not path.is_file():
            raise HTTPException(404, "Asset not found")
        return FileResponse(path,media_type=row["mime_type"])


@router.get("/tests")
def tests(request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        rows = connection.execute("SELECT * FROM tests WHERE owner_id=? ORDER BY updated_at DESC", (user["id"],)).fetchall()
        return {"items": [test_view(connection,dict(row)) for row in rows]}


@router.get("/tests/{test_id}")
def get_test(test_id: str, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        test = owned_test(connection,test_id,user)
        version = connection.execute("SELECT content FROM test_versions WHERE id=?", (test["published_version_id"],)).fetchone()
        assignments = [dict(row) for row in connection.execute("SELECT student_id,class_id FROM test_assignments WHERE test_id=?", (test_id,))]
        return {**test_view(connection,test), "questions": json.loads(version["content"])["questions"], "assignments": assignments}


class AssignmentUpdate(BaseModel):
    class_ids: list[str] = Field(default_factory=list)
    student_ids: list[str] = Field(default_factory=list)


@router.put("/tests/{test_id}/assignments")
def update_assignments(test_id: str, payload: AssignmentUpdate, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        owned_test(connection,test_id,user)
        class_ids = list(dict.fromkeys(payload.class_ids))
        student_ids = list(dict.fromkeys(payload.student_ids))
        for class_id in class_ids:
            if not connection.execute("SELECT 1 FROM classrooms WHERE id=? AND owner_id=?", (class_id,user["id"])).fetchone():
                raise HTTPException(422, "Every class must belong to you")
        for student_id in student_ids:
            if not connection.execute("SELECT 1 FROM class_memberships m JOIN classrooms c ON c.id=m.class_id WHERE m.student_id=? AND c.owner_id=?", (student_id,user["id"])).fetchone():
                raise HTTPException(422, "Every student must belong to your class roster")
        connection.execute("DELETE FROM test_assignments WHERE test_id=?", (test_id,))
        connection.executemany("INSERT INTO test_assignments(test_id,class_id) VALUES (?,?)", [(test_id,value) for value in class_ids])
        connection.executemany("INSERT INTO test_assignments(test_id,student_id) VALUES (?,?)", [(test_id,value) for value in student_ids])
    return {"class_ids": class_ids, "student_ids": student_ids}


@router.get("/tests/{test_id}/analytics")
def analytics(test_id: str, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        owned_test(connection,test_id,user)
        students = connection.execute("""SELECT DISTINCT u.id,u.name,u.email FROM users u WHERE u.id IN
          (SELECT student_id FROM test_assignments WHERE test_id=? AND student_id IS NOT NULL UNION
           SELECT m.student_id FROM test_assignments a JOIN class_memberships m ON m.class_id=a.class_id WHERE a.test_id=? UNION
           SELECT student_id FROM test_attempts WHERE test_id=?) ORDER BY u.name""", (test_id,test_id,test_id)).fetchall()
        rows = []
        for student in students:
            attempts = connection.execute("SELECT * FROM test_attempts WHERE test_id=? AND student_id=? ORDER BY started_at DESC", (test_id,student["id"])).fetchall()
            attempts = [finalize_expired(connection,row) for row in attempts]
            latest = attempts[0] if attempts else None
            rows.append({"student":dict(student), "status":"not_started" if not latest else "submitted" if latest["submitted_at"] else "in_progress", "score": latest["score"] if latest else None, "attempt_count":len(attempts)})
        return {"students": rows, "summary": {"assigned":len(rows), "started":sum(row["attempt_count"]>0 for row in rows), "submitted":sum(row["status"]=="submitted" for row in rows)}}


@router.get("/student/tests")
def student_tests(request: Request) -> dict:
    user = actor(request, "student")
    now = utc_now()
    with get_connection() as connection:
        tests = [dict(row) for row in connection.execute("SELECT * FROM tests WHERE status='published' ORDER BY created_at DESC") if assigned(connection,row["id"],user["id"]) or connection.execute("SELECT 1 FROM test_attempts WHERE test_id=? AND student_id=?", (row["id"],user["id"])).fetchone()]
        items = []
        for test in tests:
            attempts = [finalize_expired(connection,row,now) for row in connection.execute("SELECT * FROM test_attempts WHERE test_id=? AND student_id=? ORDER BY started_at DESC", (test["id"],user["id"]))]
            currently_assigned = assigned(connection,test["id"],user["id"])
            availability = "closed" if not currently_assigned else "upcoming" if test["opens_at"] and now < parse_time(test["opens_at"]) else "closed" if test["closes_at"] and now >= parse_time(test["closes_at"]) else "available"
            items.append({**test_view(connection,test),"availability":availability,"attempts_remaining":max(0,test["max_attempts"]-len(attempts)) if currently_assigned else 0,"latest_attempt":attempts[0] if attempts else None})
        return {"items":items}


def attempt_view(connection: sqlite3.Connection, attempt: dict) -> dict:
    version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
    answers = {row["question_id"]: {"answer":json.loads(row["answer"]) if row["answer"] else None,"marked_for_review":bool(row["marked_for_review"])} for row in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (attempt["id"],))}
    questions = []
    for item in version["questions"]:
        public_item = {key:value for key,value in item.items() if key not in {"answer","solution","source"}}
        public_item["scoring"] = {key:value for key,value in item["scoring"].items() if key != "partial"}
        questions.append(public_item)
    return {**attempt,"questions":questions,"responses":answers}


@router.post("/student/tests/{test_id}/attempts", status_code=201)
def start_attempt(test_id: str, request: Request) -> dict:
    user = actor(request, "student")
    now = utc_now()
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        attempts = [finalize_expired(connection,row,now) for row in connection.execute("SELECT * FROM test_attempts WHERE test_id=? AND student_id=? ORDER BY started_at DESC", (test_id,user["id"]))]
        active = next((row for row in attempts if not row["submitted_at"]),None)
        if active:
            return attempt_view(connection,active)
        row = connection.execute("SELECT * FROM tests WHERE id=? AND status='published'", (test_id,)).fetchone()
        if not row or not assigned(connection,test_id,user["id"]):
            raise HTTPException(403, "You are not assigned to this test")
        test = dict(row)
        if test["opens_at"] and now < parse_time(test["opens_at"]) or test["closes_at"] and now >= parse_time(test["closes_at"]):
            raise HTTPException(403, "Test is not available")
        if len(attempts) >= test["max_attempts"]:
            raise HTTPException(409, "Attempt limit reached")
        end = now + timedelta(seconds=test["duration_seconds"])
        if test["closes_at"]:
            end = min(end,parse_time(test["closes_at"]))
        identifier = str(uuid.uuid4())
        connection.execute("INSERT INTO test_attempts(id,test_id,version_id,student_id,started_at,ends_at) VALUES (?,?,?,?,?,?)", (identifier,test_id,test["published_version_id"],user["id"],iso(now),iso(end)))
        return attempt_view(connection,dict(connection.execute("SELECT * FROM test_attempts WHERE id=?", (identifier,)).fetchone()))


def student_attempt(connection: sqlite3.Connection, attempt_id: str, user: dict) -> dict:
    row = connection.execute("SELECT * FROM test_attempts WHERE id=? AND student_id=?", (attempt_id,user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Attempt not found")
    return finalize_expired(connection,row)


@router.get("/attempts/{attempt_id}")
def get_attempt(attempt_id: str, request: Request) -> dict:
    user = actor(request, "student")
    with get_connection() as connection:
        return attempt_view(connection,student_attempt(connection,attempt_id,user))


class ResponseIn(BaseModel):
    answer: object | None = None
    marked_for_review: bool = False
    revision: int = Field(ge=0)


@router.put("/attempts/{attempt_id}/responses/{question_id}")
def save_response(attempt_id: str, question_id: str, payload: ResponseIn, request: Request) -> dict:
    user = actor(request, "student")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        attempt = student_attempt(connection,attempt_id,user)
        if attempt["submitted_at"]:
            raise HTTPException(409, "Attempt is closed")
        if payload.revision != attempt["revision"]:
            raise HTTPException(409, "Attempt changed in another session")
        version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
        if question_id not in {item["id"] for item in version["questions"]}:
            raise HTTPException(422, "Question is not in this attempt")
        connection.execute("INSERT INTO attempt_responses(attempt_id,question_id,answer,marked_for_review) VALUES (?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET answer=excluded.answer,marked_for_review=excluded.marked_for_review",
          (attempt_id,question_id,json.dumps(payload.answer) if payload.answer is not None else None,int(payload.marked_for_review)))
        connection.execute("UPDATE test_attempts SET revision=revision+1 WHERE id=?", (attempt_id,))
        return {"revision":attempt["revision"]+1,"server_time":iso(utc_now())}


@router.post("/attempts/{attempt_id}/submit")
def submit_attempt(attempt_id: str, request: Request) -> dict:
    user = actor(request, "student")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        attempt = student_attempt(connection,attempt_id,user)
        if not attempt["submitted_at"]:
            version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
            responses = {row["question_id"]:json.loads(row["answer"]) if row["answer"] else None for row in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (attempt_id,))}
            score, _ = score_version(version,responses)
            connection.execute("UPDATE test_attempts SET submitted_at=?,score=? WHERE id=? AND submitted_at IS NULL", (iso(utc_now()),score,attempt_id))
            attempt["submitted_at"],attempt["score"] = iso(utc_now()),score
        return attempt_view(connection,attempt)


@router.get("/results/{attempt_id}")
def result(attempt_id: str, request: Request) -> dict:
    user = actor(request, "student")
    with get_connection() as connection:
        attempt = student_attempt(connection,attempt_id,user)
        if not attempt["submitted_at"]:
            raise HTTPException(404, "Result is not ready")
        test = dict(connection.execute("SELECT * FROM tests WHERE id=?", (attempt["test_id"],)).fetchone())
        version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
        policy = version.get("results_policy") or json.loads(test["results_policy"])
        if policy.get("mode") == "scheduled" and utc_now() < parse_time(policy["release_at"]):
            raise HTTPException(403, "Results have not been released")
        responses = {row["question_id"]:json.loads(row["answer"]) if row["answer"] else None for row in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (attempt_id,))}
        _, questions = score_version(version,responses)
        return {"attempt":attempt,"questions":questions}
