from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

ROOT_DIR = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_PATH = ROOT_DIR / "backend" / "data" / "question_generator.db"

SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    source_key TEXT UNIQUE NOT NULL,
    exam TEXT NOT NULL,
    class_level TEXT,
    subject TEXT NOT NULL,
    chapter TEXT,
    topic TEXT,
    subtopic TEXT,
    primary_concept TEXT,
    secondary_concepts TEXT NOT NULL,
    question_archetype TEXT,
    question_type TEXT NOT NULL,
    difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    question_json TEXT NOT NULL,
    answer_json TEXT,
    solution TEXT,
    expected_time_minutes INTEGER,
    marks INTEGER,
    source TEXT,
    source_reference TEXT,
    verification_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS questions_retrieval_metadata_idx
    ON questions (exam, subject, chapter, topic, question_type, difficulty);
CREATE TABLE IF NOT EXISTS generation_logs (
    id TEXT PRIMARY KEY,
    request_json TEXT NOT NULL,
    generation_mode TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL,
    failure_reason TEXT,
    seed_question_ids TEXT NOT NULL,
    validation_result_json TEXT,
    similarity_score REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lifecycle_events (
    id TEXT PRIMARY KEY,
    job_id TEXT,
    paper_id TEXT,
    operation TEXT NOT NULL,
    phase TEXT NOT NULL,
    outcome TEXT NOT NULL,
    slot INTEGER,
    attempt INTEGER,
    model TEXT,
    model_role TEXT,
    failure_code TEXT,
    duration_ms INTEGER,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lifecycle_events_job_created_idx ON lifecycle_events (job_id, created_at);
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    exam TEXT NOT NULL,
    subject TEXT NOT NULL,
    generation_config TEXT NOT NULL,
    branding_config TEXT NOT NULL DEFAULT '{}',
    branding_template_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('draft', 'generated', 'final')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_generation_jobs (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
    control_state TEXT NOT NULL DEFAULT 'active',
    total_questions INTEGER NOT NULL,
    completed_questions INTEGER NOT NULL DEFAULT 0,
    message TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paper_generation_jobs_paper_idx
    ON paper_generation_jobs (paper_id, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS paper_generation_jobs_active_paper_idx
    ON paper_generation_jobs (paper_id)
    WHERE state IN ('queued', 'running');
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'succeeded', 'failed')),
    phase TEXT NOT NULL,
    message TEXT,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    completed_chunks INTEGER NOT NULL DEFAULT 0,
    ingested_questions INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingestion_jobs_created_idx ON ingestion_jobs (created_at DESC);
CREATE TABLE IF NOT EXISTS ingestion_chunks (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    source_text TEXT NOT NULL,
    images_json TEXT,
    state TEXT NOT NULL DEFAULT 'queued',
    question_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_id, position)
);
CREATE INDEX IF NOT EXISTS ingestion_chunks_job_idx ON ingestion_chunks (job_id, position);
CREATE TABLE IF NOT EXISTS ingestion_candidates (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL REFERENCES ingestion_chunks(id) ON DELETE CASCADE,
    source_page INTEGER,
    source_question_number INTEGER,
    payload_json TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL,
    validation_notes TEXT NOT NULL DEFAULT '',
    promoted_question_id TEXT,
    model TEXT,
    provider TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ingestion_candidates_job_idx ON ingestion_candidates (job_id, status);
CREATE TABLE IF NOT EXISTS branding_profiles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    branding_config TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_exports (
    id TEXT PRIMARY KEY,
    paper_id TEXT REFERENCES papers(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('paper', 'legacy')),
    filename TEXT NOT NULL,
    relative_path TEXT NOT NULL UNIQUE,
    media_type TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paper_exports_paper_created_idx
    ON paper_exports (paper_id, created_at DESC);
CREATE INDEX IF NOT EXISTS paper_exports_kind_created_idx
    ON paper_exports (kind, created_at DESC);
CREATE TABLE IF NOT EXISTS paper_sections (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_questions (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES paper_sections(id) ON DELETE SET NULL,
    position INTEGER NOT NULL,
    question_json TEXT NOT NULL,
    answer_json TEXT,
    solution TEXT,
    question_type TEXT NOT NULL,
    difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
    locked INTEGER NOT NULL DEFAULT 0,
    generation_metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS paper_questions_position_idx ON paper_questions (paper_id, section_id, position);
CREATE TABLE IF NOT EXISTS app_migrations (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def database_path() -> Path:
    configured = os.getenv("DATABASE_URL", "")
    if configured.startswith("sqlite:///"):
        return Path(configured.removeprefix("sqlite:///"))
    return DEFAULT_DATABASE_PATH


def _repair_persisted_latex(connection: sqlite3.Connection) -> None:
    """Repair legacy model output once, including nested question payloads."""
    from app.services.openrouter import repair_decoded_latex_escapes

    for table in ("questions", "paper_questions"):
        rows = connection.execute(f"SELECT id, question_json, solution FROM {table}").fetchall()
        for identifier, question_json, solution in rows:
            payload = json.loads(question_json)
            repaired_payload = repair_decoded_latex_escapes(payload)
            repaired_solution = repair_decoded_latex_escapes(solution) if solution is not None else None
            if repaired_payload != payload or repaired_solution != solution:
                connection.execute(
                    f"UPDATE {table} SET question_json = ?, solution = ? WHERE id = ?",
                    (json.dumps(repaired_payload), repaired_solution, identifier),
                )


def initialize_database(path: Path | None = None) -> Path:
    target = path or database_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        # Railway mounts one durable volume on a single API replica. WAL keeps
        # concurrent reads responsive while the short busy timeout absorbs
        # brief write contention from background jobs.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.executescript(SQLITE_SCHEMA)
        # SQLite cannot add a CHECK constraint to an existing table without a
        # rebuild.  This small, backwards-compatible migration adds the
        # persisted pause/cancel control to databases created before jobs were
        # controllable.
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(paper_generation_jobs)")}
        if "control_state" not in job_columns:
            connection.execute(
                "ALTER TABLE paper_generation_jobs ADD COLUMN control_state TEXT NOT NULL DEFAULT 'active'"
            )
        if "last_activity_at" not in job_columns:
            connection.execute("ALTER TABLE paper_generation_jobs ADD COLUMN last_activity_at TEXT")
        ingestion_columns = {row[1] for row in connection.execute("PRAGMA table_info(ingestion_jobs)")}
        for column, definition in (
            ("control_state", "TEXT NOT NULL DEFAULT 'active'"),
            ("result_status", "TEXT NOT NULL DEFAULT 'pending'"),
            ("accepted_questions", "INTEGER NOT NULL DEFAULT 0"),
            ("review_questions", "INTEGER NOT NULL DEFAULT 0"),
            ("skipped_chunks", "INTEGER NOT NULL DEFAULT 0"),
            ("retryable_chunks", "INTEGER NOT NULL DEFAULT 0"),
            ("source_content", "BLOB"),
            ("source_content_type", "TEXT"),
            ("source_text", "TEXT"),
            ("conversion_note", "TEXT"),
        ):
            if column not in ingestion_columns:
                connection.execute(f"ALTER TABLE ingestion_jobs ADD COLUMN {column} {definition}")
        paper_columns = {row[1] for row in connection.execute("PRAGMA table_info(papers)")}
        if "branding_template_id" not in paper_columns:
            connection.execute("ALTER TABLE papers ADD COLUMN branding_template_id TEXT")
        migration_name = "repair_persisted_latex_controls_v2"
        applied = connection.execute("SELECT 1 FROM app_migrations WHERE name = ?", (migration_name,)).fetchone()
        if applied is None:
            _repair_persisted_latex(connection)
            connection.execute("INSERT INTO app_migrations (name) VALUES (?)", (migration_name,))
        # Older deployments stored raw OpenRouter HTTP response bodies. Some
        # providers echo the Authorization header in those bodies, so erase
        # any historical credential-bearing job/log value on first startup.
        secret_cleanup_migration = "remove_persisted_provider_secrets_v1"
        applied = connection.execute("SELECT 1 FROM app_migrations WHERE name = ?", (secret_cleanup_migration,)).fetchone()
        if applied is None:
            replacement = "The AI provider rejected the request. Check the server-side OpenRouter configuration and try again."
            secret_like = "error_message LIKE '%sk-or-%' OR error_message LIKE '%Authorization%' OR error_message LIKE '%api_key%'"
            connection.execute(f"UPDATE paper_generation_jobs SET error_message = ? WHERE {secret_like}", (replacement,))
            connection.execute(f"UPDATE ingestion_jobs SET error_message = ? WHERE {secret_like}", (replacement,))
            connection.execute(
                "UPDATE generation_logs SET failure_reason = ? "
                "WHERE failure_reason LIKE '%sk-or-%' OR failure_reason LIKE '%Authorization%' OR failure_reason LIKE '%api_key%'",
                (replacement,),
            )
            connection.execute("INSERT INTO app_migrations (name) VALUES (?)", (secret_cleanup_migration,))
    return target


@contextmanager
def get_connection(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = initialize_database(path)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def decode_question_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    for column in ("secondary_concepts", "question_json", "answer_json"):
        if result[column] is not None:
            result[column] = json.loads(result[column])
    return result
