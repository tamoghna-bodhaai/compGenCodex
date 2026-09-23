from __future__ import annotations


SYSTEM_PROMPT = """You create rigorous JEE-level examination questions. Return only JSON that matches the requested schema. Mathematical content uses LaTeX inside strings."""


def build_prompt(*, seeds: list[dict], blueprint: dict, custom_instruction: str | None = None) -> str:
    return f"""Create one independent JEE question from this blueprint.

The seed questions are conceptual grounding only. Do not make a near-copy. Follow the blueprint's concept, requested question type, and difficulty; use its new archetype, setup, unknown, and reasoning path. The question must be complete, solvable, and have an answer consistent with its solution. For a supported computational Maths or formula-based Physics question, provide a `machine_check` object using only the schema's listed families and exact values; otherwise set it to null. It must exactly match the displayed question and options. Set `diagram_required` only when a diagram is necessary; when true provide an exact, answer-independent `diagram_render_spec`.

Blueprint:
{blueprint}

Custom regeneration instruction: {custom_instruction or "None"}

Seeds:
{seeds}
"""
