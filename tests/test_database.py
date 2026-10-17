from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.db.database import get_connection, initialize_database


class DatabaseLifecycleTests(unittest.TestCase):
    def test_normal_connection_does_not_run_schema_initialization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "database.db"
            initialize_database(path)

            with patch("app.db.database.initialize_database", side_effect=AssertionError("request-time migration")):
                with get_connection(path) as connection:
                    self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
