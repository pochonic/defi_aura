import tempfile
import unittest
from pathlib import Path

from database import Database, utc_now


class OpportunityTrendPersistenceTests(unittest.TestCase):
    def test_allowed_trends_persist_and_missing_history_stays_null(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "trend.db")
            for index, trend in enumerate(("RISING", "STABLE", "FADING", None)):
                item = (utc_now(), "Test", f"pool-{index}", "AMM", "SOL", "USDC", 0.0004,
                        None, None, None, None, None, None, "UNKNOWN", None, "FIXED", 1,
                        6_000_000, 300_000, None, None, None, 5.0, 0.06, 50.0, 50.0,
                        trend, None, f"scan-{index}", "{}", "NEW", "test")
                db.insert_snapshot(item)
            rows = db.conn.execute("SELECT opportunity_trend FROM lp_snapshots ORDER BY pool_address").fetchall()
            self.assertEqual([row[0] for row in rows], ["RISING", "STABLE", "FADING", None])
            db.close()


if __name__ == "__main__":
    unittest.main()
