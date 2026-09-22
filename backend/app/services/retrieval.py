from __future__ import annotations

import re
from dataclasses import dataclass

from app.db.database import decode_question_row, get_connection
from app.schemas.generation import GenerationRequest, GenerationSlot


class RetrievalError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetrievalCandidate:
    id: str
    source_key: str
    primary_concept: str
    question_archetype: str
    difficulty: int
    question_json: dict
    score: float

    def prompt_payload(self) -> dict:
        return {
            "source_key": self.source_key,
            "primary_concept": self.primary_concept,
            "question_archetype": self.question_archetype,
            "difficulty": self.difficulty,
            "stem": self.question_json["stem"],
            "options": self.question_json.get("options", []),
        }


def _terms(value: str) -> set[str]:
    return {term for term in re.findall(r"[a-z]{3,}", value.lower()) if term not in {"with", "then", "from", "that"}}


def _fallback_semantic_score(candidate: dict, query_terms: set[str]) -> float:
    candidate_terms = _terms(
        " ".join(
            [candidate.get("primary_concept") or "", candidate.get("question_archetype") or "", candidate["question_json"]["stem"]]
        )
    )
    if not query_terms or not candidate_terms:
        return 0.0
    return len(query_terms & candidate_terms) / len(query_terms | candidate_terms)


class MetadataFirstRetriever:
    """Metadata filter -> semantic fallback score -> archetype-diverse seed selection."""

    def retrieve(self, request: GenerationRequest, slot: GenerationSlot, *, candidate_limit: int = 30, seed_count: int = 5) -> list[RetrievalCandidate]:
        if slot.selected_seed_question_ids or slot.selected_seed_question_id:
            return self._retrieve_selected_seeds(request, slot)
        clauses = ["exam = ?", "subject = ?", "question_type = ?"]
        parameters: list[object] = [request.exam, request.subject, slot.question_type.value]
        slot_chapters = slot.chapters or request.chapters
        slot_topics = [slot.topic] if slot.topic else request.topics
        slot_subtopics = [slot.subtopic] if slot.subtopic else request.subtopics
        for column, values in (("chapter", slot_chapters), ("topic", slot_topics), ("subtopic", slot_subtopics)):
            if values:
                clauses.append(f"{column} IN ({','.join('?' for _ in values)})")
                parameters.extend(values)
        difficulty_values = _difficulty_band_values(slot.difficulty)
        clauses.append(f"difficulty IN ({','.join('?' for _ in difficulty_values)})")
        parameters.extend(difficulty_values)
        sql = f"SELECT * FROM questions WHERE {' AND '.join(clauses)} LIMIT ?"
        with get_connection() as connection:
            rows = [decode_question_row(row) for row in connection.execute(sql, [*parameters, candidate_limit]).fetchall()]
        if not rows:
            raise RetrievalError(
                "No compatible seed questions found. Select a taxonomy, question type, and difficulty with at least one labeled seed."
            )

        query_terms = _terms(" ".join([*request.concepts, *slot_chapters, *slot_topics, *slot_subtopics]))
        candidates = []
        for row in rows:
            metadata_score = 1 - abs(row["difficulty"] - slot.difficulty) / 5
            semantic_score = _fallback_semantic_score(row, query_terms)
            candidates.append(
                RetrievalCandidate(
                    id=row["id"],
                    source_key=row["source_key"],
                    primary_concept=row["primary_concept"],
                    question_archetype=row["question_archetype"],
                    difficulty=row["difficulty"],
                    question_json=row["question_json"],
                    score=round(metadata_score * 0.7 + semantic_score * 0.3, 4),
                )
            )
        return self._select_diverse(candidates, seed_count)

    @staticmethod
    def _retrieve_selected_seeds(request: GenerationRequest, slot: GenerationSlot) -> list[RetrievalCandidate]:
        """Resolve every teacher-picked source in selection order."""
        selected_ids = slot.selected_seed_question_ids or ([slot.selected_seed_question_id] if slot.selected_seed_question_id else [])
        placeholders = ",".join("?" for _ in selected_ids)
        with get_connection() as connection:
            rows = connection.execute(f"SELECT * FROM questions WHERE id IN ({placeholders})", selected_ids).fetchall()
        by_id = {row["id"]: decode_question_row(row) for row in rows}
        if len(by_id) != len(selected_ids):
            raise RetrievalError("A selected source question is no longer available in the question bank.")
        candidates = []
        for selected_id in selected_ids:
            question = by_id[selected_id]
            if question["exam"] != request.exam or question["subject"] != request.subject:
                raise RetrievalError("A selected source question does not match this paper's exam and subject.")
            if slot.chapters and question.get("chapter") not in slot.chapters:
                raise RetrievalError("A selected source question is outside this section's chapter selection.")
            if slot.topic and question.get("topic") != slot.topic:
                raise RetrievalError("A selected source question is outside this section's topic.")
            if slot.subtopic and question.get("subtopic") != slot.subtopic:
                raise RetrievalError("A selected source question is outside this section's subtopic.")
            candidates.append(RetrievalCandidate(
                id=question["id"], source_key=question["source_key"], primary_concept=question["primary_concept"],
                question_archetype=question["question_archetype"], difficulty=question["difficulty"],
                question_json=question["question_json"], score=1.0,
            ))
        return candidates

    @staticmethod
    def _select_diverse(candidates: list[RetrievalCandidate], seed_count: int) -> list[RetrievalCandidate]:
        ranked = sorted(candidates, key=lambda item: item.score, reverse=True)
        selected: list[RetrievalCandidate] = []
        used_archetypes: set[str] = set()
        # First pass guarantees one best candidate per archetype where possible.
        for candidate in ranked:
            if candidate.question_archetype not in used_archetypes:
                selected.append(candidate)
                used_archetypes.add(candidate.question_archetype)
                if len(selected) == seed_count:
                    break
        # A narrow source set can have fewer archetypes than requested seeds.
        for candidate in ranked:
            if len(selected) == seed_count:
                break
            if candidate not in selected:
                selected.append(candidate)
        return selected


def _difficulty_band_values(difficulty: int) -> tuple[int, ...]:
    """Creation UI uses 1/3/5 as Easy/Medium/Hard band representatives."""
    if difficulty in {1, 2}:
        return (1, 2)
    if difficulty == 3:
        return (3,)
    return (4, 5)
