from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from app.db.database import decode_question_row, get_connection
from app.core.error_safety import safe_error_message
from app.schemas.generation import GeneratedSlotResult, GenerationRequest, GenerationSlot
from app.schemas.papers import AddManualQuestionRequest, PaperCreateRequest, PaperQuestionInput, PaperUpdateRequest, QuestionEditRequest
from app.services.generation import GenerationService


class PaperNotFoundError(RuntimeError):
    pass


class PaperConflictError(RuntimeError):
    pass


class GenerationCancelled(RuntimeError):
    """Internal signal used to stop queued work while retaining partial output."""

    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _decode(row: Any) -> dict:
    item = dict(row)
    for key in ("generation_config", "branding_config", "question_json", "answer_json", "generation_metadata"):
        if key in item and item[key] is not None:
            item[key] = json.loads(item[key])
    if "locked" in item:
        item["locked"] = bool(item["locked"])
    if item.get("error_message"):
        item["error_message"] = safe_error_message(item["error_message"])
    return item


class PaperService:
    # Jobs run in this FastAPI process. Keeping the task references lets a
    # cancel request interrupt an in-flight model call instead of merely
    # preventing the next queued slot from starting.
    _active_generation_tasks: ClassVar[dict[str, asyncio.Task[Any]]] = {}

    @staticmethod
    def recover_interrupted_generation_jobs() -> None:
        """Make jobs from a previous server process safe to continue.

        Background tasks are in-process, so a server restart cannot resume an
        old worker. Marking it cancelled is truthful and keeps completed
        questions/solutions available for a fresh continuation job.
        """
        now = _now()
        with get_connection() as connection:
            jobs = connection.execute(
                "SELECT id, paper_id, operation FROM paper_generation_jobs "
                "WHERE state IN ('queued', 'running') "
                "OR (state = 'failed' AND control_state = 'cancelled' "
                "AND message LIKE 'Generation stopped because the backend restarted%')"
            ).fetchall()
            for job in jobs:
                if job["operation"] == "initial":
                    completed = connection.execute(
                        "SELECT COUNT(*) FROM paper_questions WHERE paper_id = ? "
                        "AND generation_metadata LIKE '%\"generation_slot\"%'",
                        (job["paper_id"],),
                    ).fetchone()[0]
                else:
                    completed = connection.execute(
                        "SELECT COUNT(*) FROM paper_questions WHERE paper_id = ? "
                        "AND generation_metadata LIKE '%\"solution_origin\": \"generated\"%'",
                        (job["paper_id"],),
                    ).fetchone()[0]
                connection.execute(
                    "UPDATE paper_generation_jobs SET state = 'failed', control_state = 'cancelled', completed_questions = ?, "
                    "message = 'Generation stopped because the backend restarted. Partial work is retained.', "
                    "error_message = '', finished_at = ?, updated_at = ? WHERE id = ?",
                    (completed, now, now, job["id"]),
                )

    def create(self, request: PaperCreateRequest) -> dict:
        paper_id = str(uuid.uuid4())
        now = _now()
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO papers (id, title, exam, subject, generation_config, branding_config, branding_template_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (paper_id, request.title, request.exam, request.subject, request.model_dump_json(), "{}", None, "draft", now, now),
            )
        if request.subtopic_plans:
            self._ensure_plan_sections(paper_id, request)
        return self.get(paper_id)

    def list(self) -> list[dict]:
        with get_connection() as connection:
            rows = connection.execute("SELECT * FROM papers ORDER BY updated_at DESC").fetchall()
            return [self._summary(_decode(row), connection) for row in rows]

    def get(self, paper_id: str) -> dict:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if row is None:
                raise PaperNotFoundError("Paper not found")
            paper = _decode(row)
            sections = [_decode(section) for section in connection.execute(
                "SELECT * FROM paper_sections WHERE paper_id = ? ORDER BY position", (paper_id,)
            ).fetchall()]
            questions = [_decode(question) for question in connection.execute(
                "SELECT paper_questions.* FROM paper_questions LEFT JOIN paper_sections ON paper_questions.section_id = paper_sections.id WHERE paper_questions.paper_id = ? ORDER BY CASE WHEN paper_questions.section_id IS NULL THEN 1 ELSE 0 END, paper_sections.position, paper_questions.position",
                (paper_id,),
            ).fetchall()]
        paper["sections"] = sections
        paper["questions"] = questions
        paper["question_count"] = len(questions)
        paper["requested_question_count"] = self._requested_total(paper["generation_config"])
        paper["generation_job"] = self._latest_generation_job(paper_id)
        return paper

    def update(self, paper_id: str, request: PaperUpdateRequest) -> dict:
        updates = request.model_dump(exclude_none=True)
        if "branding_template_id" in request.model_fields_set:
            updates["branding_template_id"] = request.branding_template_id
        if not updates:
            return self.get(paper_id)
        now = _now()
        if "branding_config" in updates:
            updates["branding_config"] = json.dumps(updates["branding_config"])
        if "title" in updates:
            # Keep the generation request's title in sync for logs and later generation calls.
            paper = self.get(paper_id)
            config = paper["generation_config"]
            config["title"] = updates["title"]
            updates["generation_config"] = json.dumps(config)
        updates["updated_at"] = now
        with get_connection() as connection:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            cursor = connection.execute(f"UPDATE papers SET {assignments} WHERE id = ?", [*updates.values(), paper_id])
            if cursor.rowcount != 1:
                raise PaperNotFoundError("Paper not found")
        return self.get(paper_id)

    def delete(self, paper_id: str) -> None:
        # Generation tasks run in-process. Cancel any active work before the
        # database cascade removes its job and paper rows.
        with get_connection() as connection:
            exists = connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone()
            if exists is None:
                raise PaperNotFoundError("Paper not found")
            job_ids = [row["id"] for row in connection.execute(
                "SELECT id FROM paper_generation_jobs WHERE paper_id = ? AND state IN ('queued', 'running')",
                (paper_id,),
            ).fetchall()]
        for job_id in job_ids:
            task = self._active_generation_tasks.get(job_id)
            if task and not task.done():
                task.cancel()
        with get_connection() as connection:
            cursor = connection.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
            if cursor.rowcount != 1:
                raise PaperNotFoundError("Paper not found")

    def add_section(self, paper_id: str, title: str) -> dict:
        self._ensure_paper(paper_id)
        section_id, now = str(uuid.uuid4()), _now()
        with get_connection() as connection:
            position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) + 1 FROM paper_sections WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO paper_sections (id, paper_id, title, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (section_id, paper_id, title, position, now, now),
            )
        return self.get(paper_id)

    def update_section(self, paper_id: str, section_id: str, *, title: str | None, position: int | None) -> dict:
        self._ensure_section(paper_id, section_id)
        if position is not None:
            self._move_section(paper_id, section_id, position)
        if title is not None:
            with get_connection() as connection:
                connection.execute("UPDATE paper_sections SET title = ?, updated_at = ? WHERE id = ?", (title, _now(), section_id))
        return self.get(paper_id)

    def delete_section(self, paper_id: str, section_id: str) -> dict:
        self._ensure_section(paper_id, section_id)
        with get_connection() as connection:
            connection.execute("UPDATE paper_questions SET section_id = NULL WHERE paper_id = ? AND section_id = ?", (paper_id, section_id))
            connection.execute("DELETE FROM paper_sections WHERE id = ?", (section_id,))
        return self.get(paper_id)

    def add_manual_question(self, paper_id: str, request: AddManualQuestionRequest) -> dict:
        self._ensure_paper(paper_id)
        if request.section_id:
            self._ensure_section(paper_id, request.section_id)
        question_id, now = str(uuid.uuid4()), _now()
        with get_connection() as connection:
            position = self._next_question_position(connection, paper_id, request.section_id)
            connection.execute(
                "INSERT INTO paper_questions (id, paper_id, section_id, position, question_json, answer_json, solution, question_type, difficulty, locked, generation_metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    question_id, paper_id, request.section_id, position, json.dumps(request.question_json()),
                    json.dumps({"correct_answer": request.correct_answer}) if request.correct_answer else None,
                    request.solution, request.question_type.value, request.difficulty,
                    json.dumps({"origin": "manual"}), now, now,
                ),
            )
        return self.get(paper_id)

    def edit_question(self, paper_id: str, question_id: str, request: QuestionEditRequest) -> dict:
        current = self._ensure_question(paper_id, question_id)
        # Pydantic represents both an omitted field and an explicit JSON null as
        # None. The latter means "move this question back to unsectioned".
        section_requested = "section_id" in request.model_fields_set
        if section_requested and request.section_id is not None:
            self._ensure_section(paper_id, request.section_id)
        question_json = current["question_json"]
        for key in ("stem", "options", "marks"):
            value = getattr(request, key)
            if value is not None:
                question_json[key] = value
        if current["question_type"] == "single_correct_mcq" and len(question_json.get("options", [])) != 4:
            raise PaperConflictError("Single-correct MCQs must retain exactly four options.")
        updates: dict[str, Any] = {"question_json": json.dumps(question_json), "updated_at": _now()}
        if request.correct_answer is not None:
            updates["answer_json"] = json.dumps({"correct_answer": request.correct_answer})
        if request.solution is not None:
            updates["solution"] = request.solution
        if request.difficulty is not None:
            updates["difficulty"] = request.difficulty
        if section_requested and request.position is None and request.section_id != current["section_id"]:
            with get_connection() as connection:
                destination = self._next_question_position(connection, paper_id, request.section_id)
            self._move_question(paper_id, question_id, request.section_id, destination)
        if section_requested:
            updates["section_id"] = request.section_id
        if request.position is not None:
            destination_section = request.section_id if section_requested else current["section_id"]
            self._move_question(paper_id, question_id, destination_section, request.position)
        with get_connection() as connection:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            connection.execute(f"UPDATE paper_questions SET {assignments} WHERE id = ?", [*updates.values(), question_id])
        return self.get(paper_id)

    def set_lock(self, paper_id: str, question_id: str, locked: bool) -> dict:
        self._ensure_question(paper_id, question_id)
        with get_connection() as connection:
            connection.execute("UPDATE paper_questions SET locked = ?, updated_at = ? WHERE id = ?", (int(locked), _now(), question_id))
        return self.get(paper_id)

    def delete_question(self, paper_id: str, question_id: str) -> dict:
        self._ensure_question(paper_id, question_id)
        with get_connection() as connection:
            connection.execute("DELETE FROM paper_questions WHERE id = ?", (question_id,))
        return self.get(paper_id)

    def get_question_seeds(self, paper_id: str, question_id: str) -> dict:
        """Return the current seed-bank records used to create one question.

        Seed IDs are stored with the generated question, rather than copied, so
        review always reflects the current canonical seed bank.
        """
        question = self._ensure_question(paper_id, question_id)
        metadata = question.get("generation_metadata") or {}
        reference_comparison = self._reference_comparison_for_question(paper_id, question, metadata)
        if reference_comparison is not None:
            return reference_comparison
        seed_ids = metadata.get("seed_question_ids")
        if metadata.get("origin") != "generated" or not isinstance(seed_ids, list) or not seed_ids:
            raise PaperConflictError("This question was not generated from recorded seed questions.")

        ordered_ids = [seed_id for seed_id in seed_ids if isinstance(seed_id, str) and seed_id]
        if not ordered_ids:
            raise PaperConflictError("This question has no usable recorded seed questions.")
        placeholders = ", ".join("?" for _ in ordered_ids)
        with get_connection() as connection:
            rows = connection.execute(f"SELECT * FROM questions WHERE id IN ({placeholders})", ordered_ids).fetchall()
        by_id = {row["id"]: decode_question_row(row) for row in rows}
        return {
            "comparison_mode": "seed_bank",
            "question": question,
            "generation_metadata": metadata,
            "seeds": [by_id[seed_id] for seed_id in ordered_ids if seed_id in by_id],
            "missing_seed_question_ids": [seed_id for seed_id in ordered_ids if seed_id not in by_id],
        }

    @staticmethod
    def _reference_snapshot(config: dict, reference_index: int | None, reference_id: str | None = None) -> dict | None:
        references = config.get("reference_questions")
        if not isinstance(references, list):
            return None
        selected: dict | None = None
        if reference_index is not None and 0 <= reference_index < len(references):
            candidate = references[reference_index]
            if isinstance(candidate, dict):
                selected = candidate
        if selected is None and reference_id:
            selected = next((item for item in references if isinstance(item, dict) and item.get("reference_question_id") == reference_id), None)
        return dict(selected) if selected is not None else None

    def _reference_comparison_for_question(self, paper_id: str, question: dict, metadata: dict) -> dict | None:
        mapping = metadata.get("reference_mapping")
        if not isinstance(mapping, dict):
            return None
        paper = self.get(paper_id)
        reference = self._reference_snapshot(
            paper.get("generation_config") or {},
            mapping.get("reference_question_index"),
            mapping.get("reference_question_id"),
        )
        return {
            "comparison_mode": "reference",
            "question": question,
            "generation_metadata": metadata,
            "reference": reference,
            "reference_mapping": mapping,
            "seeds": [],
            "missing_seed_question_ids": [],
        }

    def get_paper_comparison(self, paper_id: str) -> dict:
        paper = self.get(paper_id)
        config = paper.get("generation_config") or {}
        references = config.get("reference_questions")
        if not isinstance(references, list) or not references:
            raise PaperConflictError("This paper was not generated from an uploaded reference paper.")

        items: list[dict] = []
        for generated_position, question in enumerate(paper.get("questions") or [], start=1):
            metadata = question.get("generation_metadata") or {}
            if metadata.get("origin") != "generated":
                continue
            mapping = metadata.get("reference_mapping")
            if not isinstance(mapping, dict):
                items.append({
                    "generated_question": question,
                    "generated_position": generated_position,
                    "reference": None,
                    "reference_mapping": None,
                    "mapping_status": "unavailable",
                })
                continue
            reference = self._reference_snapshot(
                config,
                mapping.get("reference_question_index"),
                mapping.get("reference_question_id"),
            )
            status = "unavailable" if reference is None else ("reused" if mapping.get("reference_reused") else "matched")
            items.append({
                "generated_question": question,
                "generated_position": generated_position,
                "reference": reference,
                "reference_mapping": mapping,
                "mapping_status": status,
            })

        return {
            "comparison_mode": "reference",
            "paper_id": paper_id,
            "title": paper["title"],
            "reference_filter": config.get("reference_filter_formatted") or None,
            "reference_count": len(references),
            "generated_count": len(items),
            "items": items,
        }

    @staticmethod
    def _core_generation_request(config: dict) -> GenerationRequest:
        core = {k: v for k, v in config.items() if k in GenerationRequest.model_fields}
        return GenerationRequest.model_validate(core)

    async def generate_initial(self, paper_id: str, generation_service: GenerationService | None = None) -> dict:
        paper = self.get(paper_id)
        if paper["questions"]:
            raise PaperConflictError("This paper already has questions. Use selected or unlocked regeneration instead.")
        request = self._core_generation_request(paper["generation_config"])
        section_ids = self._ensure_plan_sections(paper_id, request)
        # Reference-image mode: stored inside generation_config
        ref_questions = paper["generation_config"].get("reference_questions")
        ref_images = paper["generation_config"].get("reference_images")
        custom_instruction = paper["generation_config"].get("reference_custom_instruction")
        if ref_questions:
            generated = await (generation_service or GenerationService()).generate_from_reference(
                request, ref_questions, ref_images, custom_instruction
            )
        else:
            generated = await (generation_service or GenerationService()).generate(request)
        for result in generated.slots:
            self._store_generated_result(paper_id, result, section_id=section_ids.get(result.slot.section_title or ""))
        self.update(paper_id, PaperUpdateRequest(status="generated"))
        return self.get(paper_id)

    def queue_initial_generation(self, paper_id: str) -> dict:
        paper = self.get(paper_id)
        current_job = paper.get("generation_job")
        if current_job and current_job["state"] in {"queued", "running"}:
            raise PaperConflictError("This paper already has a generation job in progress.")
        request = self._core_generation_request(paper["generation_config"])
        slots = request.build_slots()
        completed_slots = self._completed_initial_slots(paper) & {slot.slot for slot in slots}
        if paper["questions"] and len(completed_slots) != len(paper["questions"]):
            raise PaperConflictError("This paper contains manually curated questions. Use selected or unlocked regeneration instead.")
        missing_slots = [slot for slot in slots if slot.slot not in completed_slots]
        if not missing_slots:
            raise PaperConflictError("All requested questions have already been generated.")
        job_id, now = str(uuid.uuid4()), _now()
        try:
            with get_connection() as connection:
                connection.execute(
                    "INSERT INTO paper_generation_jobs (id, paper_id, operation, state, total_questions, completed_questions, message, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, paper_id, "initial", "queued", len(slots), len(completed_slots), "Queued for generation", now, now),
                )
                connection.execute("UPDATE papers SET updated_at = ? WHERE id = ?", (now, paper_id))
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise PaperConflictError("This paper already has a generation job in progress.") from error
            raise
        return self._generation_job(job_id)

    def create_reference_paper(
        self,
        title: str,
        exam: str,
        subject: str,
        reference_questions: list[dict],
        reference_images: list[str],
        custom_instruction: str | None,
        desired_count: int,
        generation_mode: str = "structural_variation",
        variation_strength: str = "balanced",
        reference_filter_raw: str = "",
        reference_filter_formatted: str = "",
    ) -> dict:
        """Create a paper backed by a reference image or paper.

        Supports the standard structural and concept variation modes. No cap on desired_count.
        Stores reference_questions/images inside generation_config for background job.
        """
        from app.schemas.generation import GenerationMode, QuestionType, VariationStrength

        if not reference_questions:
            raise PaperConflictError("No reference questions could be extracted from the upload.")

        # Snapshot the selected originals before generation. Preserve source
        # numbering even when the selection starts at Q10 or contains gaps.
        normalized_references: list[dict] = []
        for selection_index, original in enumerate(reference_questions):
            reference = dict(original)
            source_number = reference.get("source_question_number")
            try:
                source_number = int(source_number) if source_number is not None else None
            except (TypeError, ValueError):
                source_number = None
            reference["source_question_number"] = source_number
            reference["reference_question_id"] = reference.get("reference_question_id") or (
                f"reference-{selection_index + 1}-q-{source_number or selection_index + 1}"
            )
            reference["reference_selection_index"] = selection_index
            reference["reference_source_position"] = selection_index + 1
            normalized_references.append(reference)
        reference_questions = normalized_references

        # If exam/subject not provided, infer from most common reference
        if not exam:
            exams = [q.get("exam") for q in reference_questions if q.get("exam")]
            exam = max(set(exams), key=exams.count) if exams else "JEE"
        if not subject:
            subjects = [q.get("subject") for q in reference_questions if q.get("subject")]
            subject = max(set(subjects), key=subjects.count) if subjects else "Physics"

        # Derive per-slot types/difficulties by cycling reference
        slots_meta: list[tuple[str, int]] = []
        for i in range(desired_count):
            ref = reference_questions[i % len(reference_questions)]
            qtype = ref.get("question_type") or "single_correct_mcq"
            # Normalize to valid enum
            try:
                QuestionType(qtype)
            except Exception:
                qtype = "single_correct_mcq"
            diff = ref.get("difficulty") or 3
            try:
                diff = int(diff)
                if not 1 <= diff <= 5:
                    diff = 3
            except Exception:
                diff = 3
            slots_meta.append((qtype, diff))

        # Build aggregated counts for GenerationRequest validation
        from collections import Counter

        type_counter = Counter(t for t, _ in slots_meta)
        diff_counter = Counter(d for _, d in slots_meta)
        # Map difficulty to bands for legacy GenerationRequest difficulty_distribution
        # Use actual ints 1-5 as stored
        question_types = [{"type": k, "count": v} for k, v in type_counter.items()]
        difficulty_distribution = [{"difficulty": k, "count": v} for k, v in diff_counter.items()]

        # Validate generation settings.
        try:
            generation_mode = GenerationMode(generation_mode).value
        except Exception:
            generation_mode = GenerationMode.STRUCTURAL.value
        try:
            VariationStrength(variation_strength)
        except Exception:
            variation_strength = "balanced"

        generation_config: dict = {
            "title": title,
            "exam": exam,
            "subject": subject,
            "chapters": list({q.get("chapter") for q in reference_questions if q.get("chapter")}),
            "topics": list({q.get("topic") for q in reference_questions if q.get("topic")}),
            "subtopics": list({q.get("subtopic") for q in reference_questions if q.get("subtopic")}),
            "concepts": [],
            "question_types": question_types,
            "difficulty_distribution": difficulty_distribution,
            "generation_mode": generation_mode,
            "variation_strength": variation_strength,
            "subtopic_plans": None,
            # Reference mode extension (preserved for job, not part of schema validation via extra="allow"?)
            "reference_questions": reference_questions,
            "reference_images": reference_images,
            "reference_custom_instruction": (custom_instruction or "").strip() or "Take this paper as reference & generate a structural variation around this based on the paper",
            "reference_source_name": f"reference-{title}",
            "reference_filter_raw": reference_filter_raw,
            "reference_filter_formatted": reference_filter_formatted,
        }

        # Validate core fields via GenerationRequest but allow extra keys
        # GenerationRequest will ignore extra keys if we validate then re-add them
        core = {k: v for k, v in generation_config.items() if k in GenerationRequest.model_fields}
        GenerationRequest.model_validate(core)  # raise if invalid

        paper_id, now = str(uuid.uuid4()), _now()
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO papers (id, title, exam, subject, generation_config, branding_config, branding_template_id, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (paper_id, title, exam, subject, json.dumps(generation_config), "{}", None, "draft", now, now),
            )
        return self.get(paper_id)

    async def run_initial_generation_job(self, paper_id: str, job_id: str) -> None:
        self._register_job_task(job_id)
        try:
            await self._wait_until_job_can_continue(job_id)
            paper = self.get(paper_id)
            ref_filter_fmt = paper["generation_config"].get("reference_filter_formatted") if paper.get("generation_config") else None
            start_msg = "Generating and independently validating questions"
            if ref_filter_fmt:
                # Show selective reference in live logs
                total_ref = len(paper["generation_config"].get("reference_questions") or [])
                start_msg = f"Generating from reference {ref_filter_fmt} ({total_ref} selected) — validating questions"
            self._update_generation_job(job_id, state="running", message=start_msg, started=True)
            request = self._core_generation_request(paper["generation_config"])
            all_slots = request.build_slots()
            completed_slots = self._completed_initial_slots(paper) & {slot.slot for slot in all_slots}
            if paper["questions"] and len(completed_slots) != len(paper["questions"]):
                raise PaperConflictError("This paper contains manually curated questions. Use selected or unlocked regeneration instead.")
            missing_slots = [slot for slot in all_slots if slot.slot not in completed_slots]
            section_ids = self._ensure_plan_sections(paper_id, request)

            async def report_progress(result: GeneratedSlotResult) -> None:
                await self._wait_until_job_can_continue(job_id)
                # Persist each completed slot immediately. The editor can then
                # show a useful partial paper while the remaining slots run.
                # Sectioned plans keep each subtopic's questions grouped under
                # its own heading; position stays global so ordering is stable.
                self._store_generated_result(
                    paper_id, result, section_id=section_ids.get(result.slot.section_title or ""), position=result.slot.slot,
                )
                job = self._generation_job(job_id)
                completed = min(job["completed_questions"] + 1, job["total_questions"])
                self._update_generation_job(
                    job_id,
                    completed_questions=completed,
                    message=f"Validated {completed} of {job['total_questions']} questions",
                )

            ref_questions = paper["generation_config"].get("reference_questions")
            ref_images = paper["generation_config"].get("reference_images")
            ref_instruction = paper["generation_config"].get("reference_custom_instruction")
            if ref_questions:
                await GenerationService().generate_from_reference(
                    request,
                    reference_questions=ref_questions,
                    reference_images=ref_images,
                    custom_instruction=ref_instruction,
                    on_slot_complete=report_progress,
                    slots=missing_slots,
                    before_slot=lambda _: self._wait_until_job_can_continue(job_id),
                )
            else:
                await GenerationService().generate(
                    request,
                    on_slot_complete=report_progress,
                    slots=missing_slots,
                    before_slot=lambda _: self._wait_until_job_can_continue(job_id),
                )
            self.update(paper_id, PaperUpdateRequest(status="generated"))
            final_msg = f"Generated and validated {len(all_slots)} questions"
            if ref_filter_fmt:
                final_msg = f"Generated {len(all_slots)} variations from reference {ref_filter_fmt}"
            self._update_generation_job(
                job_id,
                state="succeeded",
                completed_questions=len(all_slots),
                message=final_msg,
                finished=True,
            )
        except asyncio.CancelledError:
            try:
                job = self._generation_job(job_id)
            except PaperNotFoundError:
                return
            if job.get("control_state") != "cancelled":
                self._update_generation_job(job_id, state="failed", message="Generation interrupted", error_message="Generation was interrupted.", finished=True)
            return
        except (GenerationCancelled, PaperNotFoundError):
            # A deleted paper cascades its job rows. That is a normal terminal
            # state for an in-flight task, not a generation failure.
            return
        except Exception as error:
            self._update_generation_job(
                job_id,
                state="failed",
                message="Generation needs attention",
                error_message=safe_error_message(error),
                finished=True,
            )
        finally:
            self._unregister_job_task(job_id)

    def queue_solution_generation(self, paper_id: str) -> dict:
        paper = self.get(paper_id)
        missing_solutions = [question for question in paper["questions"] if not question.get("solution")]
        if not missing_solutions:
            raise PaperConflictError("Every question already has a solution.")
        current_job = paper.get("generation_job")
        if current_job and current_job["state"] in {"queued", "running"}:
            raise PaperConflictError("This paper already has a generation job in progress.")
        job_id, now = str(uuid.uuid4()), _now()
        try:
            with get_connection() as connection:
                connection.execute(
                    "INSERT INTO paper_generation_jobs (id, paper_id, operation, state, total_questions, completed_questions, message, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, paper_id, "solutions", "queued", len(missing_solutions), 0, "Queued to generate missing solutions", now, now),
                )
                connection.execute("UPDATE papers SET updated_at = ? WHERE id = ?", (now, paper_id))
        except Exception as error:
            if "UNIQUE constraint failed" in str(error):
                raise PaperConflictError("This paper already has a generation job in progress.") from error
            raise
        return self._generation_job(job_id)

    async def run_solution_generation_job(self, paper_id: str, job_id: str) -> None:
        self._register_job_task(job_id)
        try:
            await self._wait_until_job_can_continue(job_id)
            self._update_generation_job(job_id, state="running", message="Generating worked solutions", started=True)
            paper = self.get(paper_id)
            questions = [question for question in paper["questions"] if not question.get("solution")]
            if not questions:
                raise PaperConflictError("Every question already has a solution.")
            service = GenerationService()
            semaphore = asyncio.Semaphore(service.settings.max_concurrent_generations)

            async def solve(question: dict) -> tuple[dict, Any | None, Exception | None]:
                try:
                    await self._wait_until_job_can_continue(job_id)
                    async with semaphore:
                        await self._wait_until_job_can_continue(job_id)
                        result = await service.generate_solution(
                            question["question_json"], exam=paper["exam"], subject=paper["subject"]
                        )
                        return question, result, None
                except (asyncio.CancelledError, GenerationCancelled):
                    raise
                except Exception as error:
                    # Return the failed question with its error instead of
                    # propagating it to the coordinator. Other slots can
                    # therefore keep using the available worker capacity.
                    return question, None, error

            tasks = [asyncio.create_task(solve(question)) for question in questions]
            failures: list[tuple[dict, str]] = []
            try:
                for task in asyncio.as_completed(tasks):
                    question, result, error = await task
                    if error is not None:
                        failures.append((question, safe_error_message(error)))
                        continue
                    assert result is not None
                    self._store_solution_result(question, result)
                    job = self._generation_job(job_id)
                    completed = min(job["completed_questions"] + 1, job["total_questions"])
                    self._update_generation_job(
                        job_id,
                        completed_questions=completed,
                        message=f"Generated {completed} of {job['total_questions']} solutions",
                    )
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

            completed = self._generation_job(job_id)["completed_questions"]
            if failures:
                failure_details = "; ".join(
                    f"Q{question['position']}: {reason}" for question, reason in failures
                )
                self._update_generation_job(
                    job_id,
                    state="failed",
                    completed_questions=completed,
                    message=(
                        f"Generated {completed} of {len(questions)} worked solutions; "
                        f"{len(failures)} failed. The remaining questions continued."
                    ),
                    error_message=failure_details,
                    finished=True,
                )
                return
            self._update_generation_job(
                job_id,
                state="succeeded",
                completed_questions=completed,
                message=f"Generated {completed} worked solutions",
                finished=True,
            )
        except asyncio.CancelledError:
            try:
                job = self._generation_job(job_id)
            except PaperNotFoundError:
                return
            if job.get("control_state") != "cancelled":
                self._update_generation_job(job_id, state="failed", message="Solution generation interrupted", error_message="Solution generation was interrupted.", finished=True)
            return
        except (GenerationCancelled, PaperNotFoundError):
            # See run_initial_generation_job.
            return
        except Exception as error:
            self._update_generation_job(
                job_id,
                state="failed",
                message="Solution generation needs attention",
                error_message=safe_error_message(error),
                finished=True,
            )
        finally:
            self._unregister_job_task(job_id)

    def pause_generation(self, paper_id: str) -> dict:
        job = self._active_job_for_control(paper_id)
        if job.get("control_state") == "paused":
            return job
        self._update_generation_job(
            job["id"], control_state="paused",
            message=f"Paused at {job['completed_questions']} of {job['total_questions']}. Completed work is available in the editor.",
        )
        return self._generation_job(job["id"])

    def resume_generation(self, paper_id: str) -> dict:
        job = self._active_job_for_control(paper_id)
        if job.get("control_state") != "paused":
            raise PaperConflictError("This generation job is not paused.")
        self._update_generation_job(job["id"], control_state="active", message="Resuming generation")
        return self._generation_job(job["id"])

    def cancel_generation(self, paper_id: str) -> dict:
        job = self._active_job_for_control(paper_id)
        self._update_generation_job(
            job["id"], state="failed", control_state="cancelled",
            message=f"Generation cancelled after {job['completed_questions']} of {job['total_questions']}. Partial work is retained.",
            error_message="", finished=True,
        )
        task = self._active_generation_tasks.get(job["id"])
        if task and not task.done():
            task.cancel()
        return self._generation_job(job["id"])

    @staticmethod
    def _completed_initial_slots(paper: dict) -> set[int]:
        slots = set()
        for question in paper["questions"]:
            slot = (question.get("generation_metadata") or {}).get("generation_slot")
            if isinstance(slot, int):
                slots.add(slot)
        return slots

    def _active_job_for_control(self, paper_id: str) -> dict:
        self._ensure_paper(paper_id)
        job = self._latest_generation_job(paper_id)
        if not job or job["state"] not in {"queued", "running"}:
            raise PaperConflictError("There is no active generation job for this paper.")
        return job

    def _register_job_task(self, job_id: str) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._active_generation_tasks[job_id] = task

    def _unregister_job_task(self, job_id: str) -> None:
        current = asyncio.current_task()
        if self._active_generation_tasks.get(job_id) is current:
            self._active_generation_tasks.pop(job_id, None)

    async def _wait_until_job_can_continue(self, job_id: str) -> None:
        while True:
            job = self._generation_job(job_id)
            if job.get("control_state") == "cancelled":
                raise GenerationCancelled("Generation was cancelled.")
            if job.get("control_state") != "paused":
                return
            await asyncio.sleep(0.2)

    def _generation_job(self, job_id: str) -> dict:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM paper_generation_jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise PaperNotFoundError("Generation job not found")
        return _decode(row)

    def _latest_generation_job(self, paper_id: str) -> dict | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM paper_generation_jobs WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1", (paper_id,)
            ).fetchone()
        return _decode(row) if row else None

    def _update_generation_job(
        self,
        job_id: str,
        *,
        state: str | None = None,
        completed_questions: int | None = None,
        message: str | None = None,
        error_message: str | None = None,
        control_state: str | None = None,
        started: bool = False,
        finished: bool = False,
    ) -> None:
        now = _now()
        updates: dict[str, Any] = {"updated_at": now}
        if state is not None:
            updates["state"] = state
        if completed_questions is not None:
            updates["completed_questions"] = completed_questions
        if message is not None:
            updates["message"] = message
        if error_message is not None:
            updates["error_message"] = error_message
        if control_state is not None:
            updates["control_state"] = control_state
        if started:
            updates["started_at"] = now
        if finished:
            updates["finished_at"] = now
        with get_connection() as connection:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            row = connection.execute("SELECT paper_id FROM paper_generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise PaperNotFoundError("Generation job not found")
            connection.execute(f"UPDATE paper_generation_jobs SET {assignments} WHERE id = ?", [*updates.values(), job_id])
            connection.execute("UPDATE papers SET updated_at = ? WHERE id = ?", (now, row["paper_id"]))

    async def regenerate_questions(self, paper_id: str, question_ids: list[str], generation_service: GenerationService | None = None,
                                   custom_instruction: str | None = None) -> dict:
        paper = self.get(paper_id)
        current_by_id = {question["id"]: question for question in paper["questions"]}
        missing = set(question_ids) - set(current_by_id)
        if missing:
            raise PaperNotFoundError("One or more selected questions were not found in this paper.")
        locked = [question_id for question_id in question_ids if current_by_id[question_id]["locked"]]
        if locked:
            raise PaperConflictError("Locked questions cannot be regenerated. Unlock them first.")
        request = GenerationRequest.model_validate(paper["generation_config"])
        service = generation_service or GenerationService()
        for question_id in question_ids:
            current = current_by_id[question_id]
            slot = GenerationSlot(slot=current["position"], question_type=current["question_type"], difficulty=current["difficulty"])
            reference_questions = paper["generation_config"].get("reference_questions")
            if reference_questions:
                response = await service.generate_from_reference(
                    request,
                    reference_questions=reference_questions,
                    reference_images=paper["generation_config"].get("reference_images"),
                    custom_instruction=(custom_instruction or "").strip() or paper["generation_config"].get("reference_custom_instruction") or None,
                    slots=[slot],
                )
                result = response.slots[0]
            else:
                result = await service.generate_slot(
                    request,
                    slot,
                    custom_instruction=(custom_instruction or "").strip() or None,
                )
            self._store_generated_result(paper_id, result, replace_question_id=question_id, section_id=current["section_id"], position=current["position"])
        return self.get(paper_id)

    async def regenerate_unlocked(self, paper_id: str, generation_service: GenerationService | None = None) -> dict:
        paper = self.get(paper_id)
        question_ids = [question["id"] for question in paper["questions"] if not question["locked"]]
        if not question_ids:
            raise PaperConflictError("There are no unlocked questions to regenerate.")
        return await self.regenerate_questions(paper_id, question_ids, generation_service)

    @staticmethod
    def _requested_total(config: dict) -> int:
        plans = config.get("subtopic_plans")
        if plans:
            return sum(sum(item.get("count", 0) for item in plan.get("question_types", [])) for plan in plans)
        return sum(item.get("count", 0) for item in config.get("question_types", []))

    def _ensure_plan_sections(self, paper_id: str, request: GenerationRequest) -> dict[str, str]:
        """Create one section per subtopic plan and return title -> section id."""
        if not request.subtopic_plans:
            return {}
        self._ensure_paper(paper_id)
        with get_connection() as connection:
            existing = {
                row["title"]: row["id"]
                for row in connection.execute("SELECT id, title FROM paper_sections WHERE paper_id = ?", (paper_id,)).fetchall()
            }
            position = connection.execute(
                "SELECT COALESCE(MAX(position), 0) FROM paper_sections WHERE paper_id = ?", (paper_id,)
            ).fetchone()[0] or 0
            mapping = dict(existing)
            for plan in request.subtopic_plans:
                title = plan.resolved_section_title()
                if title in mapping:
                    continue
                section_id, now = str(uuid.uuid4()), _now()
                position += 1
                connection.execute(
                    "INSERT INTO paper_sections (id, paper_id, title, position, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (section_id, paper_id, title, position, now, now),
                )
                mapping[title] = section_id
        return mapping

    def _store_generated_result(
        self,
        paper_id: str,
        result: GeneratedSlotResult,
        *,
        replace_question_id: str | None = None,
        section_id: str | None = None,
        position: int | None = None,
    ) -> None:
        now = _now()
        question = result.question
        question_json = {
            "stem": question.stem,
            "options": question.options,
            "primary_concept": question.primary_concept,
            "secondary_concepts": question.secondary_concepts,
            "estimated_time_minutes": question.estimated_time_minutes,
            "marks": question.marks,
        }
        answer_json = {"correct_answer": question.correct_answer}
        metadata = {
            "origin": "generated",
            "generation_slot": result.slot.slot,
            "seed_question_ids": result.seed_question_ids,
            "similarity_score": result.similarity_score,
            "generation_attempt": result.generation_attempt,
            "validation": result.validation.model_dump(),
        }
        if result.reference_question_id is not None:
            metadata["reference_mapping"] = {
                "reference_question_id": result.reference_question_id,
                "reference_question_index": result.reference_question_index,
                "source_question_number": result.reference_question_number,
                "reference_reused": result.reference_reused,
            }
        with get_connection() as connection:
            if replace_question_id:
                connection.execute(
                    "UPDATE paper_questions SET question_json = ?, answer_json = ?, solution = ?, question_type = ?, difficulty = ?, generation_metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(question_json), json.dumps(answer_json), question.solution, question.question_type.value,
                     question.difficulty, json.dumps(metadata), now, replace_question_id),
                )
                return
            stored_position = position or self._next_question_position(connection, paper_id, section_id)
            connection.execute(
                "INSERT INTO paper_questions (id, paper_id, section_id, position, question_json, answer_json, solution, question_type, difficulty, locked, generation_metadata, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (str(uuid.uuid4()), paper_id, section_id, stored_position, json.dumps(question_json), json.dumps(answer_json),
                 question.solution, question.question_type.value, question.difficulty, json.dumps(metadata), now, now),
            )

    @staticmethod
    def _store_solution_result(question: dict, result: Any) -> None:
        answer = question.get("answer_json") or {}
        if result.correct_answer:
            answer = {"correct_answer": result.correct_answer}
        metadata = question.get("generation_metadata") or {}
        metadata["solution_origin"] = "generated"
        with get_connection() as connection:
            connection.execute(
                "UPDATE paper_questions SET answer_json = ?, solution = ?, generation_metadata = ?, updated_at = ? WHERE id = ?",
                (json.dumps(answer) if answer else None, result.solution, json.dumps(metadata), _now(), question["id"]),
            )

    def _ensure_paper(self, paper_id: str) -> None:
        with get_connection() as connection:
            if connection.execute("SELECT 1 FROM papers WHERE id = ?", (paper_id,)).fetchone() is None:
                raise PaperNotFoundError("Paper not found")

    def _ensure_section(self, paper_id: str, section_id: str) -> None:
        with get_connection() as connection:
            if connection.execute("SELECT 1 FROM paper_sections WHERE id = ? AND paper_id = ?", (section_id, paper_id)).fetchone() is None:
                raise PaperNotFoundError("Section not found")

    def _ensure_question(self, paper_id: str, question_id: str) -> dict:
        with get_connection() as connection:
            row = connection.execute("SELECT * FROM paper_questions WHERE id = ? AND paper_id = ?", (question_id, paper_id)).fetchone()
        if row is None:
            raise PaperNotFoundError("Question not found")
        return _decode(row)

    @staticmethod
    def _next_question_position(connection: Any, paper_id: str, section_id: str | None) -> int:
        return connection.execute(
            "SELECT COALESCE(MAX(position), 0) + 1 FROM paper_questions WHERE paper_id = ? AND section_id IS ?", (paper_id, section_id)
        ).fetchone()[0]

    def _move_section(self, paper_id: str, section_id: str, destination: int) -> None:
        with get_connection() as connection:
            rows = connection.execute("SELECT id FROM paper_sections WHERE paper_id = ? ORDER BY position", (paper_id,)).fetchall()
            ids = [row["id"] for row in rows if row["id"] != section_id]
            ids.insert(min(destination - 1, len(ids)), section_id)
            for position, current_id in enumerate(ids, start=1):
                connection.execute("UPDATE paper_sections SET position = ?, updated_at = ? WHERE id = ?", (position, _now(), current_id))

    def _move_question(self, paper_id: str, question_id: str, section_id: str | None, destination: int) -> None:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT id FROM paper_questions WHERE paper_id = ? AND section_id IS ? ORDER BY position", (paper_id, section_id)
            ).fetchall()
            ids = [row["id"] for row in rows if row["id"] != question_id]
            ids.insert(min(destination - 1, len(ids)), question_id)
            for position, current_id in enumerate(ids, start=1):
                connection.execute("UPDATE paper_questions SET position = ?, section_id = ?, updated_at = ? WHERE id = ?", (position, section_id, _now(), current_id))

    @staticmethod
    def _summary(paper: dict, connection: Any) -> dict:
        count = connection.execute("SELECT COUNT(*) FROM paper_questions WHERE paper_id = ?", (paper["id"],)).fetchone()[0]
        job = connection.execute(
            "SELECT * FROM paper_generation_jobs WHERE paper_id = ? ORDER BY created_at DESC LIMIT 1", (paper["id"],)
        ).fetchone()
        return {key: value for key, value in paper.items() if key not in {"generation_config", "branding_config"}} | {
            "question_count": count,
            "requested_question_count": PaperService._requested_total(paper["generation_config"]),
            "generation_job": _decode(job) if job else None,
        }
