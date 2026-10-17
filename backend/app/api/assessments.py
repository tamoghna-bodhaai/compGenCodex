"""Native classroom and computer-based assessment API."""
from __future__ import annotations

import json
import csv
import io
import sqlite3
import uuid
import shutil
import copy
from collections import Counter
from typing import Literal
from pathlib import Path
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, model_validator

from app.api.auth import AccountCreate, AccountUpdate, _create_account, update_managed_account
from app.db.database import get_connection
from app.db.database import database_path
from app.services.diagrams import asset_path, get_diagram
from app.services.assessment import finalize_expired, iso, parse_time, score_version, snapshot_item, utc_now
from app.schemas.assessment import ResponseSpec, validate_answer
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


def audit(connection: sqlite3.Connection, user: dict, action: str, resource_id: str, details: dict | None = None) -> None:
    connection.execute("INSERT INTO assessment_audit(id,actor_id,action,resource_id,created_at,details) VALUES (?,?,?,?,?,?)", (str(uuid.uuid4()), user["id"], action, resource_id, iso(utc_now()), json.dumps(details or {})))


@router.get("/admin/health")
def assessment_health(request: Request) -> dict:
    actor(request, "admin")
    with get_connection() as connection:
        status = connection.execute("PRAGMA quick_check").fetchone()[0]
        active = connection.execute("SELECT COUNT(*) FROM test_attempts WHERE submitted_at IS NULL AND ends_at>?", (iso(utc_now()),)).fetchone()[0]
        failed = connection.execute("SELECT COUNT(*) FROM paper_generation_jobs WHERE state='failed'").fetchone()[0]
        recent = [dict(row) for row in connection.execute("SELECT action,resource_id,created_at FROM assessment_audit ORDER BY created_at DESC LIMIT 20")]
    return {"database":status, "active_attempts":active, "failed_generation_jobs":failed, "recent_events":recent}


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


@router.delete("/classes/{class_id}/members/{student_id}", status_code=204)
def remove_member(class_id: str, student_id: str, request: Request) -> None:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        if not connection.execute("SELECT 1 FROM classrooms WHERE id=? AND owner_id=?", (class_id, user["id"])).fetchone():
            raise HTTPException(404, "Class not found")
        connection.execute("DELETE FROM class_memberships WHERE class_id=? AND student_id=?", (class_id, student_id))


class PublishTest(BaseModel):
    duration_minutes: int = Field(ge=1, le=360)
    opens_at: datetime | None = None
    closes_at: datetime | None = None
    max_attempts: int = Field(default=1, ge=1, le=10)
    results_policy: dict = Field(default_factory=lambda: {"mode": "immediate"})
    template: Literal["jee_main_2026_paper1"] | None = None

    @model_validator(mode="after")
    def check(self):
        if self.opens_at and self.closes_at and self.closes_at <= self.opens_at:
            raise ValueError("Closing time must follow opening time")
        if self.template == "jee_main_2026_paper1" and self.duration_minutes != 180:
            raise ValueError("JEE Main 2026 Paper 1 requires 180 minutes")
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
    template_counts: Counter[tuple[str, str]] = Counter()
    for position, question in enumerate(paper["questions"], start=1):
        try:
            question = copy.deepcopy(question)
            metadata = question.get("generation_metadata") or {}
            exam_format = metadata.get("exam_format") or {}
            subject = str(exam_format.get("subject") or paper["subject"])
            if payload.template == "jee_main_2026_paper1":
                kind = question["question_type"]
                if subject not in {"Physics", "Chemistry", "Mathematics"} or kind not in {"single_correct_mcq", "numerical"}:
                    raise ValueError("JEE Main Paper 1 requires Physics, Chemistry, or Mathematics single-choice or numerical questions")
                spec = ResponseSpec.model_validate(metadata.get("response_spec") or {"mode":"numeric" if kind == "numerical" else "select", "min_selections":1 if kind != "numerical" else None, "max_selections":1 if kind != "numerical" else None})
                if kind == "single_correct_mcq" and (spec.mode != "select" or len(question["question_json"].get("options") or []) != 4 or spec.max_selections not in {None, 1}):
                    raise ValueError("JEE Main MCQs need four options and one answer")
                if kind == "single_correct_mcq":
                    spec.min_selections = spec.max_selections = 1
                if kind == "numerical":
                    if spec.mode != "numeric": raise ValueError("JEE Main numerical questions need a numeric response")
                    from app.schemas.assessment import NumericRule
                    spec.numeric = NumericRule(integer_only=True)
                spec.marking.full, spec.marking.negative, spec.marking.zero = 4, -1, 0
                spec.marking.partial = {}
                metadata["response_spec"] = spec.model_dump()
                metadata["exam_format"] = {**exam_format, "exam":"JEE Main", "year":2026, "paper":"Paper 1", "subject":subject}
                question["generation_metadata"] = metadata
                template_counts[(subject, kind)] += 1
            item = snapshot_item(question, sections.get(question["section_id"]),paper["exam"])
            item["subject"] = subject
            items.append(item)
        except ValueError as error:
            errors.append({"position": position, "question_id": question["id"], "reason": str(error)})
    if errors:
        raise HTTPException(422, {"message": "Fix questions before publishing", "questions": errors})
    if payload.template == "jee_main_2026_paper1" and any(template_counts[(subject, kind)] != expected for subject in ("Mathematics", "Physics", "Chemistry") for kind, expected in (("single_correct_mcq",20), ("numerical",5))):
        raise HTTPException(422, "JEE Main 2026 Paper 1 needs exactly 20 single-choice and 5 numerical questions in each of Mathematics, Physics, and Chemistry.")
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
        version_content = {"questions":items,"duration_seconds":payload.duration_minutes*60,"results_policy":payload.results_policy, "template":payload.template}
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
        audit(connection, user, "test_published", test_id, {"version":ordinal})
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
        versions = [dict(row) for row in connection.execute("SELECT id,ordinal,published_at FROM test_versions WHERE test_id=? ORDER BY ordinal DESC", (test_id,))]
        accommodations = [dict(row) for row in connection.execute("SELECT student_id,extra_seconds FROM test_accommodations WHERE test_id=?", (test_id,))]
        pair = connection.execute("SELECT * FROM linked_test_pairs WHERE first_test_id=? OR second_test_id=?", (test_id, test_id)).fetchone()
        return {**test_view(connection,test), "questions": json.loads(version["content"])["questions"], "assignments": assignments, "versions": versions, "accommodations": accommodations, "linked_pair": dict(pair) if pair else None}


@router.get("/tests/{test_id}/versions/{version_id}")
def get_test_version(test_id: str, version_id: str, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        owned_test(connection, test_id, user)
        row = connection.execute("SELECT id,ordinal,published_at,content FROM test_versions WHERE test_id=? AND id=?", (test_id, version_id)).fetchone()
        if not row:
            raise HTTPException(404, "Test version not found")
        return {**dict(row), "content": json.loads(row["content"])}


class LinkTest(BaseModel):
    second_test_id: str


@router.post("/tests/{test_id}/linked-paper", status_code=201)
def link_test(test_id: str, payload: LinkTest, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    if test_id == payload.second_test_id:
        raise HTTPException(422, "Choose a different second paper")
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        first, second = owned_test(connection, test_id, user), owned_test(connection, payload.second_test_id, user)
        if first["owner_id"] != second["owner_id"] or first["status"] != "published" or second["status"] != "published":
            raise HTTPException(422, "Both papers must be published by the same teacher")
        if connection.execute("SELECT 1 FROM linked_test_pairs WHERE first_test_id IN (?,?) OR second_test_id IN (?,?)", (test_id, payload.second_test_id, test_id, payload.second_test_id)).fetchone():
            raise HTTPException(409, "A selected test is already linked")
        pair_id = str(uuid.uuid4())
        connection.execute("INSERT INTO linked_test_pairs(id,owner_id,first_test_id,second_test_id,created_at) VALUES (?,?,?,?,?)", (pair_id, first["owner_id"], test_id, payload.second_test_id, iso(utc_now())))
        audit(connection, user, "tests_linked", pair_id)
        return {"id":pair_id, "first_test_id":test_id, "second_test_id":payload.second_test_id}


@router.delete("/tests/{test_id}/linked-paper", status_code=204)
def unlink_test(test_id: str, request: Request) -> None:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        test = owned_test(connection, test_id, user)
        connection.execute("DELETE FROM linked_test_pairs WHERE owner_id=? AND (first_test_id=? OR second_test_id=?)", (test["owner_id"], test_id, test_id))
        audit(connection, user, "tests_unlinked", test_id)


class AccommodationUpdate(BaseModel):
    extra_minutes: int = Field(ge=0, le=360)


@router.put("/tests/{test_id}/accommodations/{student_id}")
def update_accommodation(test_id: str, student_id: str, payload: AccommodationUpdate, request: Request) -> dict:
    user = actor(request, "admin", "teacher")
    with get_connection() as connection:
        owned_test(connection, test_id, user)
        if not connection.execute("SELECT 1 FROM class_memberships m JOIN classrooms c ON c.id=m.class_id WHERE m.student_id=? AND c.owner_id=?", (student_id, user["id"])).fetchone():
            raise HTTPException(404, "Student not found in your classes")
        connection.execute("INSERT INTO test_accommodations(test_id,student_id,extra_seconds) VALUES (?,?,?) ON CONFLICT(test_id,student_id) DO UPDATE SET extra_seconds=excluded.extra_seconds", (test_id, student_id, payload.extra_minutes * 60))
        audit(connection, user, "accommodation_updated", test_id, {"student_id":student_id, "extra_minutes":payload.extra_minutes})
    return {"student_id": student_id, "extra_seconds": payload.extra_minutes * 60}


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
        audit(connection, user, "assignments_updated", test_id, {"classes":len(class_ids), "students":len(student_ids)})
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
        question_stats: dict[tuple[str, str], dict] = {}
        for student in students:
            attempts = connection.execute("SELECT * FROM test_attempts WHERE test_id=? AND student_id=? ORDER BY started_at DESC", (test_id,student["id"])).fetchall()
            attempts = [finalize_expired(connection,row) for row in attempts]
            latest = attempts[0] if attempts else None
            rows.append({"student":dict(student), "status":"not_started" if not latest else "submitted" if latest["submitted_at"] else "in_progress", "score": latest["score"] if latest else None, "attempt_count":len(attempts)})
            if latest and latest["submitted_at"]:
                version = connection.execute("SELECT ordinal,content FROM test_versions WHERE id=?", (latest["version_id"],)).fetchone()
                responses = {entry["question_id"]: json.loads(entry["answer"]) if entry["answer"] else None for entry in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (latest["id"],))}
                _, scored = score_version(json.loads(version["content"]), responses)
                for item in scored:
                    key = (latest["version_id"], item["id"])
                    stats = question_stats.setdefault(key, {"version":version["ordinal"], "question_id":item["id"], "section":item["section"], "stem":item["stem"], "responses":0, "correct":0, "points_total":0.0})
                    stats["responses"] += 1
                    stats["correct"] += int(item["correct"] is True)
                    stats["points_total"] += item["points"]
        return {"students": rows, "questions": list(question_stats.values()), "summary": {"assigned":len(rows), "started":sum(row["attempt_count"]>0 for row in rows), "submitted":sum(row["status"]=="submitted" for row in rows)}}


@router.get("/tests/{test_id}/analytics.csv")
def analytics_csv(test_id: str, request: Request) -> Response:
    data = analytics(test_id, request)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Student", "Email", "Status", "Score", "Attempts"])
    def safe(value: object) -> str:
        value = "" if value is None else str(value)
        return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value
    for row in data["students"]:
        writer.writerow([safe(row["student"]["name"]), safe(row["student"]["email"]), row["status"], row["score"] if row["score"] is not None else "", row["attempt_count"]])
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="test-results.csv"'})


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
            latest = attempts[0] if attempts else None
            if latest and not result_released(connection, latest):
                latest = {**latest, "score": None}
            items.append({**test_view(connection,test),"availability":availability,"attempts_remaining":max(0,test["max_attempts"]-len(attempts)) if currently_assigned else 0,"latest_attempt":latest})
        item_by_id = {item["id"]: item for item in items}
        for pair in connection.execute("SELECT * FROM linked_test_pairs"):
            first, second = item_by_id.get(pair["first_test_id"]), item_by_id.get(pair["second_test_id"])
            if not first or not second:
                continue
            attempts = [first["latest_attempt"], second["latest_attempt"]]
            if all(attempt and attempt["submitted_at"] and attempt["score"] is not None for attempt in attempts):
                summary = {"first_title":first["title"], "second_title":second["title"], "combined_score":sum(attempt["score"] for attempt in attempts)}
                first["linked_summary"] = summary
                second["linked_summary"] = summary
        return {"items":items}


def result_released(connection: sqlite3.Connection, attempt: dict) -> bool:
    version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
    policy = version.get("results_policy") or {"mode": "immediate"}
    return policy.get("mode") != "scheduled" or utc_now() >= parse_time(policy["release_at"])


def attempt_view(connection: sqlite3.Connection, attempt: dict) -> dict:
    version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
    answers = {row["question_id"]: {"answer":json.loads(row["answer"]) if row["answer"] else None,"marked_for_review":bool(row["marked_for_review"])} for row in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (attempt["id"],))}
    questions = []
    for item in version["questions"]:
        public_item = {key:value for key,value in item.items() if key not in {"answer","solution","source"}}
        public_item["scoring"] = {key:value for key,value in item["scoring"].items() if key != "partial"}
        public_item["response_spec"] = {**item["response_spec"], "marking": {key:value for key,value in (item["response_spec"].get("marking") or {}).items() if key not in {"partial", "source_instructions"}}}
        questions.append(public_item)
    return {**attempt, "score": attempt.get("score") if result_released(connection, attempt) else None, "server_time": iso(utc_now()), "questions":questions,"responses":answers}


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
        accommodation = connection.execute("SELECT extra_seconds FROM test_accommodations WHERE test_id=? AND student_id=?", (test_id, user["id"])).fetchone()
        end = now + timedelta(seconds=test["duration_seconds"] + (accommodation["extra_seconds"] if accommodation else 0))
        if test["closes_at"]:
            end = min(end,parse_time(test["closes_at"]))
        identifier = str(uuid.uuid4())
        connection.execute("INSERT INTO test_attempts(id,test_id,version_id,student_id,started_at,ends_at) VALUES (?,?,?,?,?,?)", (identifier,test_id,test["published_version_id"],user["id"],iso(now),iso(end)))
        audit(connection, user, "attempt_started", identifier, {"test_id":test_id})
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
            # Persist any timeout finalization before returning the conflict.
            connection.commit()
            raise HTTPException(409, "Attempt is closed")
        if payload.revision != attempt["revision"]:
            raise HTTPException(409, "Attempt changed in another session")
        version = json.loads(connection.execute("SELECT content FROM test_versions WHERE id=?", (attempt["version_id"],)).fetchone()["content"])
        item = next((item for item in version["questions"] if item["id"] == question_id), None)
        if item is None:
            raise HTTPException(422, "Question is not in this attempt")
        answer = payload.answer
        if answer in ("", [], {}):
            answer = None
        spec = ResponseSpec.model_validate(item["response_spec"])
        if answer is not None:
            if spec.mode == "matching" and isinstance(answer, dict):
                valid = set(answer).issubset(spec.list_i) and all(value in spec.list_ii for value in answer.values())
            else:
                valid = validate_answer(spec, answer, len(item["options"]))
            if not valid:
                raise HTTPException(422, "Answer does not match this question's response rules")
        connection.execute("INSERT INTO attempt_responses(attempt_id,question_id,answer,marked_for_review) VALUES (?,?,?,?) ON CONFLICT(attempt_id,question_id) DO UPDATE SET answer=excluded.answer,marked_for_review=excluded.marked_for_review",
          (attempt_id,question_id,json.dumps(answer) if answer is not None else None,int(payload.marked_for_review)))
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
            audit(connection, user, "attempt_submitted", attempt_id)
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
            # student_attempt may have finalized the attempt on this request.
            connection.commit()
            raise HTTPException(403, "Results have not been released")
        responses = {row["question_id"]:json.loads(row["answer"]) if row["answer"] else None for row in connection.execute("SELECT * FROM attempt_responses WHERE attempt_id=?", (attempt_id,))}
        _, questions = score_version(version,responses)
        return {"attempt":attempt,"questions":questions}
