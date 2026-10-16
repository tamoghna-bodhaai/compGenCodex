from __future__ import annotations


SYSTEM_PROMPT = """You are a meticulous JEE Mathematics solution writer. Independently solve the supplied question, then provide a concise, rigorous step-by-step solution. Return only JSON matching the supplied schema. All mathematical notation must be valid LaTeX enclosed in the required delimiters."""


def build_prompt(*, question: dict, exam: str, subject: str) -> str:
    return f"""Write the answer and worked solution for this {exam} {subject} question.

Do not assume any existing answer key is correct. Derive the answer independently and obey the response_spec in the question. For a single-choice question return one option letter; for multi-select return comma-separated letters; for numeric return a value that meets the integer, precision, rounding, and range rules. If no valid answer can be determined, return null. Write the worked solution as clear, logical steps: put each material step on its own line, and put standalone calculations or transformations in $$...$$ display delimiters on their own line. Every mathematical expression—including substitutions, equations, intervals, trigonometric terms, and the final value—must be enclosed in literal $...$ (inline) or $$...$$ (display) delimiters. Never emit bare LaTeX commands or unwrapped mathematical notation. Use valid LaTeX commands with single backslashes, escaped correctly for JSON. Keep the solution appropriate for a student answer key.

Question:
{question}
"""
