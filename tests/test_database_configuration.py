import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database import Database, _PostgresConnection


class _FakeCursor:
    rowcount = 0

    def fetchall(self):
        return []

    def fetchone(self):
        return None


class _FakePostgresConnection:
    is_postgres = True

    def __init__(self, url):
        self.url = url
        self.statements = []

    def execute(self, query, params=()):
        self.statements.append((query, params))
        return _FakeCursor()

    def commit(self):
        pass

    def close(self):
        pass


class DatabaseConfigurationTests(unittest.TestCase):
    def test_postgres_sql_translation_removes_sqlite_only_constructs(self):
        translated = _PostgresConnection._translate(
            "INSERT OR IGNORE INTO lending_snapshots (id) VALUES (?)"
        )
        self.assertEqual(translated, "INSERT INTO lending_snapshots (id) VALUES (%s) ON CONFLICT DO NOTHING")
        self.assertIn("CURRENT_TIMESTAMP + %s::interval", _PostgresConnection._translate(
            "SELECT 1 WHERE created_at >= datetime('now', ?)"
        ))

    def test_without_database_url_uses_sqlite(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            db = Database(Path(folder) / "local.db")
            try:
                self.assertEqual(db.backend, "sqlite")
                self.assertIsNotNone(db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='lending_markets'").fetchone())
            finally:
                db.close()

    def test_database_url_selects_postgres_without_requiring_sqlite(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://user:pass@db.example/radar"}), \
                patch("database._PostgresConnection", _FakePostgresConnection):
            db = Database(Path("ignored.db"))
            try:
                self.assertEqual(db.backend, "postgresql")
                self.assertEqual(db.conn.url, os.environ["DATABASE_URL"])
                statements = "\n".join(query for query, _ in db.conn.statements)
                self.assertIn("information_schema.columns", statements)
                self.assertNotIn("PRAGMA", statements)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
