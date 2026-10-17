# AI Question Variation & Paper Generator

CompGen combines a curated JEE question bank, variation-based paper authoring, and classroom CBT tests in one FastAPI, SQLite, and Next.js application. The first seed collection is a user-provided 50-question paper on **Definite Integrals**.

## What is implemented now

- PostgreSQL-ready schema for questions, papers, paper questions, and generation logs.
- SQLite local-development database so the seed set works without Supabase.
- Structured source-question import with taxonomy, question type, difficulty, provenance, options, and review state.
- OpenRouter-assisted ingestion for pasted questions and text-based PDF/DOCX sets, with optional teacher classification guidance.
- FastAPI endpoints to browse and filter imported questions.
- Separate structural-variation and concept-variation pipelines, each backed by its own prompt module.
- Independent validation, a configurable three-attempt retry limit, source-similarity rejection, and generation logs.
- Persisted paper builder API with sections, manual questions, question editing, question locking, and selective or unlocked-only regeneration.
- Print-ready DOCX and PDF exports with branded paper details, candidate fields, sections, options, and answer-key variants.
- Global question bank publication, individual teacher and student accounts, class rosters, immutable test versions, assignments, timed attempts, scoring, results, and teacher analytics.
- A verified visual transcription of the supplied PDF stored as structured JSON.

## JEE assessment formats and source review

Newly ingested questions keep the existing display `question_type` and also store a versioned `exam_format` and `response_spec`. The response spec records selection cardinality, numeric rules, matching lists, and the source marking instructions. Older questions receive a non-destructive default response spec when read. JEE Advanced questions without explicit section rules stay in the per-source review queue.

The question bank shows source history and a per-job report with taxonomy, format, group, diagram, candidate, and answer-mapping details. The source review can export accepted questions and an answer key as DOCX, with stem and option images. Answer keys and solutions can be pasted or uploaded with the source or attached later. Numbered entries map directly to source question numbers; unnumbered entries map by order only when the counts and question numbers are unambiguous. Ambiguous entries can be mapped manually. Direct source mapping does not use a model.

`DELETE /api/questions/{id}` removes one bank question. `DELETE /api/questions/ingestion-jobs/{id}` permanently removes a completed source and its linked bank questions, media, attachments, candidates, and mappings, retaining a compact audit row. Generated paper questions remain historical snapshots. `POST /api/questions/solutions/generate` queues missing bank solutions in a job, subject, chapter, topic, subtopic, or explicit question scope; generated content is marked pending review.

The source paper is titled *Test on Definite Integrals*. Its questions are imported under `Mathematics > Calculus > Definite Integrals`, not Indefinite Integrals. Each source item is intentionally marked `transcription_pending`: the PDF has no answer key or solutions, so no answers have been guessed.

## Local setup

Start FastAPI on port 8000:

```bash
cd backend
uv sync
uv run python ../scripts/seed_database.py
uv run fastapi dev app/main.py
```

In a second terminal, start the Next.js workspace on port 3000:

```bash
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:3000/` for Paper Studio. FastAPI documentation remains at `http://127.0.0.1:8000/docs`. The Next.js server proxies relative `/api/*` calls to FastAPI; set `BACKEND_URL` in `frontend/.env.local` only when the backend uses another address.

## Accounts and classroom tests

The account named by `AUTH_ALLOWED_EMAIL` is bootstrapped as the super admin and
continues to sign in with one of the five-digit `AUTH_ACCESS_CODES`. Existing
papers are assigned to that account during the additive database migration.
The super admin creates individual teachers in **People & global banks**.
Teachers create students in **Tests & classes**. Each teacher and student has
their own email and five-digit access code; managers can suspend accounts or
rotate codes. Rotation invalidates existing sessions.

Only the super admin can ingest and publish questions to global banks. The
admin first reviews an ingested source in the existing review flow, then
publishes its questions from **People & global banks**. Teachers can browse
published bank questions, add them to a paper, or use the existing variation
generator. The paper editor remains the test authoring surface.

Choose **Publish as CBT test** in a paper to snapshot question order, content,
answers, response specifications, marks, and diagrams. Publication reports
item-level errors for missing answers, media, or grading rules. A paper edit can
be republished as a new version; started attempts retain their original version.
In **Tests & classes**, assign the test to classes and/or individual students.
Students sign in to **My tests** to start or resume timed attempts. Answers are
saved as they work; expiry submits automatically. Results are released
immediately or at the scheduled time selected when publishing.

For local development, copy the values from `.env.example`, choose real codes
and a random `AUTH_SESSION_SECRET` of at least 32 characters, and leave
`AUTH_COOKIE_SECURE=false`. Never commit real codes or secrets.

## Railway deployment

Create two Railway services from this repository, setting each service's Root
Directory so it discovers the matching `railway.toml`:

1. Create the **API** service with Root Directory `backend`. Its Dockerfile
   includes PDF export (LaTeX/LibreOffice) and scanned-PDF OCR (Tesseract).
   Attach one Railway Volume mounted at `/data`, set the replica count to
   **one**, then set `DATABASE_URL=sqlite:////data/db/question_generator.db`
   and `EXPORT_ROOT=/data/exports`. Immutable CBT media is stored next to the
   database on this volume. SQLite runs with WAL and a busy timeout;
   do not attach this volume to multiple API replicas.
   Add `AUTH_ALLOWED_EMAIL=utils@bodhaai.tech`, `AUTH_ACCESS_CODES`, a random
   32+ character `AUTH_SESSION_SECRET`, `AUTH_SESSION_TTL_HOURS=168`, and
   `AUTH_COOKIE_SECURE=true`. Copy the existing OpenRouter variables too when
   AI generation or ingestion is needed.
2. Create the **frontend** service with Root Directory `frontend`. If the API
   service is named `api`, set `BACKEND_URL` to
   `http://${{api.RAILWAY_PRIVATE_DOMAIN}}:${{api.PORT}}`. Generate a public
   domain only for this frontend service; do not generate one for the API.
3. Confirm the API health check at `/api/health`, then open the frontend public
   URL. A backend restart must retain the database and `/data/exports` archive.

### Migrating existing data

Create a read-only transfer manifest locally (it records database row counts
and SHA-256 hashes for PDF/DOCX/TEX files):

```bash
PYTHONPATH=backend backend/.venv/bin/python scripts/inventory_railway_data.py \
  --database data/question_generator.db --exports output/exports \
  --manifest railway-import.json
```

Upload the intended live database to `/data/db/question_generator.db` and the
supported archive files to `/data/exports` with Railway Volume file tools.
Then run the following inside the API service exactly once; it is idempotent
and validates every archive checksum before registering it in the secure
**Export archive** page:

```bash
PYTHONPATH=/app python /app/scripts/import_legacy_exports.py \
  --manifest /data/railway-import.json --export-root /data/exports
```

Keep the manifest on the volume, enable Railway Volume backups, and rehearse a
restore into a staging environment before relying on production data.

The backend is intentionally private behind the frontend proxy. Keep the API
at one replica while it uses the persistent SQLite volume, and back up the
database and media together.

## Paper Studio frontend

The TypeScript Next.js application in `frontend/` lets an authorized user:

- create a medium-difficulty JEE Definite Integrals draft;
- browse seed availability through the same exam, subject, chapter, topic, and subtopic taxonomy used by paper creation;
- ingest pasted questions or PDF/DOCX question sets into reviewable seed records as super admin;
- write, edit, section, lock, delete, or regenerate paper questions;
- set institution, duration, marks, and instruction fields for exports; and
- download the student paper as DOCX or PDF.

Generation buttons use the existing validated generation pipeline and therefore require the OpenRouter configuration described below. Curating the supplied seed bank and exporting a paper works locally without model credentials.

For larger papers, generation runs in three bounded stages: candidate generation,
local structural checks, and independent model validation. The defaults allow 10
candidate calls and 10 validation calls concurrently, while keeping the cheap
local checks highly parallel. Tune `GENERATION_BURST_CONCURRENCY`,
`VALIDATION_BURST_CONCURRENCY`, and `DETERMINISTIC_VALIDATION_CONCURRENCY` in
`.env` to match your OpenRouter rate limits. Locally successful checks normally
only reject malformed drafts. Optionally set `SYMBOLIC_VERIFICATION_ENABLED=true`
to enable exact SymPy checks for supported structured Maths and formula-based
Physics families. A verified question bypasses the validation model except for
the deterministic audit sample configured by `SYMBOLIC_VERIFICATION_AUDIT_RATE`
(default `0.10`); unsupported or inconsistent specifications retain the
independent validator.

Set `GENERATION_FALLBACK_MODEL` and/or `VALIDATION_FALLBACK_MODEL` to add a
second generation phase for failed slots. Only after the primary pipeline has
exhausted `MAX_GENERATION_ATTEMPTS` does it retry those slots with the fallback
model(s); an omitted fallback keeps its corresponding primary model.

## Ingesting question sets

Open **Question bank** and choose **Ingest questions**. Upload one PDF or DOCX (up to 35 MB), or paste question text, then optionally add classification guidance such as `Treat this as JEE Mathematics, Class 12`. The upload queues a persisted ingestion job, with live extraction, classification, and saving status in the modal and dashboard. The backend extracts embedded PDF text first and uses local Tesseract OCR for scanned PDFs when needed, then asks OpenRouter to faithfully transcribe and classify each question as JEE/NEET, subject, chapter, topic/subtopic, type, and difficulty. Imported items are marked `pending_review`; missing answers or unclear text are not invented.

Set `OPENROUTER_API_KEY` plus either `CLASSIFICATION_MODEL` (recommended) or `GENERATION_MODEL` to use ingestion. `CLASSIFICATION_MODEL` lets you choose a dedicated model for ingestion/classification independently from paper generation — e.g. `CLASSIFICATION_MODEL=google/gemini-3-flash-preview` (or your preferred OpenRouter slug). Set `CLASSIFICATION_FALLBACK_MODEL` to automatically retry a single fallback model if the primary returns an error or invalid JSON (e.g. `CLASSIFICATION_FALLBACK_MODEL=z-ai/glm-5.3`). For a vision-capable OpenRouter classification model, set `CLASSIFICATION_USE_VISION=true`; image-only PDFs are then rendered page-by-page and sent directly to that model, bypassing local Tesseract OCR. Update either model freely in `.env` without code changes.

PDF text is extracted with `pypdf` first and automatically retried with `PyMuPDF` (`fitz`) when the first pass returns sparse/no text — this fixes false `No selectable text / Use an OCR-enabled PDF` errors on clean digital PDFs that use CID fonts or XObjects. True scanned/image-only PDFs still return a clear diagnostic suggesting re-export or paste.

## Diagram-aware questions

Set `DIAGRAM_GENERATION_MODEL` to an OpenRouter image-capable model (for example, your selected Gemini Nano Banana model). `DIAGRAM_ANALYSIS_MODEL` defaults to `CLASSIFICATION_MODEL` and is used to locate PDF diagram crops and validate generated images; `DIAGRAM_MAX_ATTEMPTS` defaults to `2`. Source PDF diagrams are retained under `backend/data/media/diagrams` with structured metadata. If an image model is unavailable or rejects an output, the question remains available as text-only and records the diagram fallback state.

## Seed data

`sample_data/jee_definite_integrals_questions.json` contains 50 single-correct MCQs. Every record has:

- JEE / Mathematics / Calculus / Definite Integrals taxonomy
- difficulty `3` (`Multi-step` in the PRD's internal scale; used here as the requested medium baseline)
- its source page and source question number
- LaTeX-safe question content and options
- a conservative verification status and null answer/solution fields

Run the seed script repeatedly; it upserts by a stable source key and does not duplicate questions.

## Frontend verification

```bash
cd frontend
npm run lint
npm run typecheck
npm test
npm run build
```

From the repository root, run backend and cross-runtime contracts with:

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s tests -v
```

## Generation endpoint

`POST /api/generation/questions` accepts the PRD's paper configuration fields and returns questions accepted by the independent validator or the configured exact symbolic-verification lane. It will return `503` until `OPENROUTER_API_KEY`, `GENERATION_MODEL`, and `VALIDATION_MODEL` are configured. This is intentional: no mock or unvalidated question is ever returned as generated output.

## Paper builder endpoints

Use `POST /api/papers` with the same body as the generation endpoint to create a persisted draft. The paper API then supports `POST /{paper_id}/generate`, sections, manual questions, edits, locking, `regenerate-selected`, and `regenerate-unlocked`. Locked questions are never overwritten by a bulk regeneration.

## Paper exports

`POST /api/papers/{paper_id}/export` saves a document on the persistent export
volume, records it in export history, and streams the initial download. Send
`{"format":"docx","variant":"question_paper"}` for the student paper, or
switch `format` to `pdf` and/or `variant` to `answer_key`. The authenticated
`GET /api/exports` and `GET /api/exports/{export_id}/download` endpoints power
the Export archive page, including migrated legacy PDF/DOCX/TEX files.
