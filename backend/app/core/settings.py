from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_project_env() -> None:
    """Load local development settings without overriding explicit shell values."""
    env_path = Path(__file__).resolve().parents[3] / ".env"
    if not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, separator, value = line.partition("=")
        if not separator or not key.isidentifier():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


load_project_env()


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str | None
    generation_model: str | None
    validation_model: str | None
    classification_model: str | None
    embedding_model: str | None
    max_concurrent_generations: int
    max_generation_attempts: int
    max_seed_similarity: float
    generation_burst_concurrency: int = 10
    validation_burst_concurrency: int = 10
    deterministic_validation_concurrency: int = 25
    classification_fallback_model: str | None = None
    classification_use_vision: bool = False
    generation_fallback_model: str | None = None
    validation_fallback_model: str | None = None
    symbolic_verification_enabled: bool = False
    symbolic_verification_audit_rate: float = 0.10
    diagram_generation_model: str | None = None
    diagram_analysis_model: str | None = None
    diagram_max_attempts: int = 2

    @property
    def generation_ready(self) -> bool:
        return bool(self.openrouter_api_key and self.generation_model and self.validation_model)


def get_settings() -> Settings:
    return Settings(
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        generation_model=os.getenv("GENERATION_MODEL") or None,
        validation_model=os.getenv("VALIDATION_MODEL") or None,
        classification_model=os.getenv("CLASSIFICATION_MODEL") or None,
        classification_fallback_model=os.getenv("CLASSIFICATION_FALLBACK_MODEL") or None,
        classification_use_vision=os.getenv("CLASSIFICATION_USE_VISION", "false").lower() in {"1", "true", "yes", "on"},
        generation_fallback_model=os.getenv("GENERATION_FALLBACK_MODEL") or None,
        validation_fallback_model=os.getenv("VALIDATION_FALLBACK_MODEL") or None,
        embedding_model=os.getenv("EMBEDDING_MODEL") or None,
        max_concurrent_generations=int(os.getenv("MAX_CONCURRENT_GENERATIONS", "3")),
        max_generation_attempts=int(os.getenv("MAX_GENERATION_ATTEMPTS", "3")),
        max_seed_similarity=float(os.getenv("MAX_SEED_SIMILARITY", "0.90")),
        generation_burst_concurrency=int(os.getenv("GENERATION_BURST_CONCURRENCY", "10")),
        validation_burst_concurrency=int(os.getenv("VALIDATION_BURST_CONCURRENCY", "10")),
        deterministic_validation_concurrency=int(os.getenv("DETERMINISTIC_VALIDATION_CONCURRENCY", "25")),
        symbolic_verification_enabled=os.getenv("SYMBOLIC_VERIFICATION_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        symbolic_verification_audit_rate=float(os.getenv("SYMBOLIC_VERIFICATION_AUDIT_RATE", "0.10")),
        diagram_generation_model=os.getenv("DIAGRAM_GENERATION_MODEL") or None,
        diagram_analysis_model=os.getenv("DIAGRAM_ANALYSIS_MODEL") or os.getenv("CLASSIFICATION_MODEL") or None,
        diagram_max_attempts=max(1, int(os.getenv("DIAGRAM_MAX_ATTEMPTS", "2"))),
    )
