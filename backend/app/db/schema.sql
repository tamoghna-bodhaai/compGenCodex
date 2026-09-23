-- PostgreSQL-oriented core schema. The local seed script mirrors the questions
-- table in SQLite so this foundation can be run without external infrastructure.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_key TEXT UNIQUE NOT NULL,
  exam TEXT NOT NULL,
  class_level TEXT,
  subject TEXT NOT NULL,
  chapter TEXT,
  topic TEXT,
  subtopic TEXT,
  primary_concept TEXT,
  secondary_concepts JSONB NOT NULL DEFAULT '[]'::jsonb,
  question_archetype TEXT,
  question_type TEXT NOT NULL CHECK (question_type IN ('single_correct_mcq', 'multiple_correct_mcq', 'numerical', 'subjective')),
  difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  question_json JSONB NOT NULL,
  answer_json JSONB,
  solution TEXT,
  expected_time_minutes INTEGER,
  marks INTEGER,
  source TEXT,
  source_reference TEXT,
  verification_status TEXT NOT NULL DEFAULT 'pending',
  embedding vector(1536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS questions_retrieval_metadata_idx
  ON questions (exam, subject, chapter, topic, question_type, difficulty);


CREATE TABLE IF NOT EXISTS papers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID,
  title TEXT NOT NULL,
  exam TEXT,
  subject TEXT,
  generation_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  branding_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  branding_template_id UUID,
  template_id UUID,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'generated', 'final')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS paper_sections (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  position INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_questions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  section_id UUID REFERENCES paper_sections(id) ON DELETE SET NULL,
  position INTEGER NOT NULL,
  question_json JSONB NOT NULL,
  answer_json JSONB,
  solution TEXT,
  question_type TEXT NOT NULL,
  difficulty INTEGER NOT NULL CHECK (difficulty BETWEEN 1 AND 5),
  locked BOOLEAN NOT NULL DEFAULT FALSE,
  generation_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS question_diagrams (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  seed_question_id UUID REFERENCES questions(id) ON DELETE CASCADE,
  paper_question_id UUID REFERENCES paper_questions(id) ON DELETE CASCADE,
  storage_path TEXT, mime_type TEXT, width INTEGER, height INTEGER, sha256 TEXT,
  provenance TEXT NOT NULL CHECK (provenance IN ('source_crop', 'generated')),
  source_page INTEGER, crop_json JSONB, description TEXT NOT NULL DEFAULT '', render_spec TEXT NOT NULL DEFAULT '',
  generation_prompt TEXT, model TEXT, validation_status TEXT NOT NULL DEFAULT 'pending', validation_notes TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK ((seed_question_id IS NOT NULL) <> (paper_question_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS generation_logs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_question_id UUID REFERENCES paper_questions(id) ON DELETE SET NULL,
  request JSONB NOT NULL,
  model TEXT,
  latency_ms INTEGER,
  token_usage JSONB,
  cost NUMERIC(12, 6),
  status TEXT NOT NULL,
  validation_status TEXT,
  failure_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
