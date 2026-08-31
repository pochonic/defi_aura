import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from database import Database
from services.volatility_risk import _realized_vol, assess, canonical_pair, orient_price


class FakeHistoryDb:
    def __init__(self, rows):
        self.rows = rows

    def pair_price_history(self, pair, since=None):
        return self.rows

    def pair_price_source_ranges(self, pair):
        return {"LOCAL": {"first": self.rows[0]["snapshot_time"], "last": self.rows[-1]["snapshot_time"], "observations": len(self.rows)}}


class VolatilityRiskTests(unittest.TestCase):
    def test_native_wrappers_share_canonical_pair_and_orientation(self):
        sol = "So11111111111111111111111111111111111111112"
        self.assertEqual(canonical_pair("WSOL", "USDC", sol, "usdc"), "SOL/USDC")
        self.assertEqual(orient_price(100.0, "USDC", "WSOL", "usdc", sol), 0.01)
        self.assertEqual(canonical_pair("SOL", "ETH", "sol", "eth"), "SOL/ETH")

    def test_realized_vol_uses_log_returns(self):
        result = _realized_vol([100.0, 110.0, 100.0])
        self.assertIsNotNone(result)
        self.assertGreater(result, 0)

    def test_pair_snapshots_are_deduplicated_per_timestamp_and_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            db.insert_pair_price_snapshot("SOL/USDC", "a", "b", 100, "LOCAL", "Raydium", "pool-1")
            db.insert_pair_price_snapshot("SOL/USDC", "a", "b", 101, "LOCAL", "Orca", "pool-2")
            rows = db.pair_price_history("SOL/USDC")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_count"], 2)
            self.assertEqual(rows[0]["price_ratio"], 100.5)
            db.close()

    def test_24h_window_is_separate_from_metric_coverage(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        rows = [{"snapshot_time": (now - timedelta(hours=24 - i)).isoformat(), "price_ratio": 100 + i, "source": "LOCAL", "dispersion_pct": 0, "quality_warning": None} for i in range(25)]
        result = assess(FakeHistoryDb(rows), "SOL", "USDC", "So11111111111111111111111111111111111111112", "usdc")
        self.assertGreaterEqual(result.window_coverage_24h_pct, 95.0)
        self.assertEqual(result.metric_coverage_pct, result.coverage_pct)
        self.assertIsNotNone(result.realized_vol_24h)

    def test_24h_under_minimum_is_not_called_complete(self):
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        rows = [{"snapshot_time": (now - timedelta(hours=24 - i * 2)).isoformat(), "price_ratio": 100 + i, "source": "LOCAL", "dispersion_pct": 0, "quality_warning": None} for i in range(12)]
        result = assess(FakeHistoryDb(rows), "SOL", "USDC", "So11111111111111111111111111111111111111112", "usdc")
        self.assertIsNone(result.realized_vol_24h)
        self.assertLess(result.window_coverage_24h_pct, 100.0)
        self.assertIn("24h realized volatility unavailable", " ".join(result.warnings))


if __name__ == "__main__":
    unittest.main()
