import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import config
from database import Database
from services.provider_runtime import HEALTH, fetch_with_retry, fallback_report
from services.lp_scanner import scan_all
from raydium import normalize, scan_source


class FakeClient:
    endpoint = "https://example.invalid/pools"

    def __init__(self, failures=0):
        self.failures = failures
        self.calls = 0

    def fetch_pools(self, *_):
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("simulated timeout")
        return [], 1


class AlwaysFailClient(FakeClient):
    def __init__(self):
        super().__init__(failures=99)


class ProviderRuntimeTests(unittest.TestCase):
    def test_retries_timeout_and_records_health(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            client = FakeClient(failures=2)
            with patch.dict(config.PROVIDER_RETRY_CONFIG, {"initial_delay_seconds": 0}):
                raw, pages, health = fetch_with_retry("MeteoraTest", client, 10, 1, db)
            self.assertEqual(client.calls, 3)
            self.assertEqual(raw, [])
            self.assertEqual(health.status, "LIVE_EMPTY_RESPONSE")
            db.close()

    def test_fallback_excludes_old_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            db.close()

    def test_provider_failure_uses_stale_snapshot_without_new_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "test.db")
            now = __import__("database").utc_now()
            values = (now, "Meteora", "pool-1", "DLMM", "SOL", "USDC", 0.0004, 1000,
                      10, None, None, None, None, "UNKNOWN", None, "DYNAMIC", 1,
                      6000000, 1000000, None, None, None, 10, 1, 50, 50, None, None, "{}", "INSUFFICIENT_HISTORY", "test")
            db.conn.execute("""INSERT INTO lp_snapshots
                (snapshot_time, protocol, pool_address, pool_type, token_a, token_b, fee_tier, fees_24h_usd,
                 nominal_fee_apr, orca_reported_fees_apr, expected_fees_from_nominal_rate, fee_difference_usd,
                 fee_difference_pct, fee_window_comparability, yield_over_tvl, fee_model, reward_known, tvl_usd,
                 volume_24h, volume_7d, reported_apr, reward_apr, calculated_fee_apr, volume_tvl_ratio, score,
                 opportunity_score, risk_score, scan_id, score_breakdown, status, source)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
            db.conn.commit()
            before = db.conn.execute("SELECT COUNT(*) FROM lp_snapshots").fetchone()[0]
            with patch.dict(config.PROVIDER_RETRY_CONFIG, {"initial_delay_seconds": 0}):
                reports, statuses, by_protocol = scan_all(
                    db, raydium_client=FakeClient(), orca_client=FakeClient(),
                    meteora_client=AlwaysFailClient(), page_size=1, max_pages=1)
            after = db.conn.execute("SELECT COUNT(*) FROM lp_snapshots").fetchone()[0]
            self.assertEqual(statuses["Meteora"], "STALE")
            self.assertEqual(by_protocol["Meteora"].pools[0].data_state, "STALE_RECENT")
            self.assertEqual(before, after)
            db.close()

    def test_live_empty_response_is_distinguished_from_live(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "empty.db")
            client = FakeClient()
            raw, _, health = fetch_with_retry("Raydium", client, 10, 1, db)
            self.assertEqual(raw, [])
            self.assertEqual(health.status, "LIVE_EMPTY_RESPONSE")
            db.close()

    def test_repeated_valid_cycles_keep_pipeline_counts(self):
        raw = [{"id": "pool-valid", "feeRate": 0.0025, "tvl": 10_000_000,
                "mintA": {"symbol": "SOL", "address": "sol"}, "mintB": {"symbol": "USDC", "address": "usdc"},
                "day": {"volume": 20_000_000, "apr": 75, "rewardApr": []}, "week": {"volume": 100_000_000}}]
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "cycles.db")
            first = scan_source(db, raw, "test", normalize, "Raydium", "cycle-1")
            second = scan_source(db, raw, "test", normalize, "Raydium", "cycle-2")
            for report in (first, second):
                self.assertGreater(report.pipeline_counts["fetched_raw_count"], 0)
                self.assertGreater(report.pipeline_counts["normalized_count"], 0)
                self.assertGreater(report.pipeline_counts["allowed_token_count"], 0)
                self.assertGreater(report.pipeline_counts["pre_filter_count"], 0)
                self.assertGreater(report.pipeline_counts["qualifying_count"], 0)
            db.close()


if __name__ == "__main__":
    unittest.main()
