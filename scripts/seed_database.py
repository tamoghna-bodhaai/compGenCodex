from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "backend"))

from app.services.seed_import import upsert_seed_questions  # noqa: E402
from app.db.database import initialize_database  # noqa: E402


if __name__ == "__main__":
    initialize_database()
    seed_file = ROOT_DIR / "sample_data" / "jee_definite_integrals_questions.json"
    inserted, updated = upsert_seed_questions(seed_file)
    print(f"Seed import complete: {inserted} inserted, {updated} updated.")
