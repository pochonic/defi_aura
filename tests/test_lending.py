import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

from database import Database
from services.lending.ingestion import persist_lending_snapshots
from services.lending.kamino import KaminoClient
from services.lending.history import supply_apy_history


class _Response:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class LendingTests(unittest.TestCase):
    def test_normalizes_documented_decimal_fields_and_leaves_derived_fields_null(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{
                "reserve": "reserve-1", "liquidityToken": "USDC", "liquidityTokenMint": "mint-1",
                "supplyApy": "0.0942", "borrowApy": "0.117", "totalSupplyUsd": "21800000",
                "totalBorrowUsd": "16175600",
            }]),
        ])
        rows = KaminoClient(base_url="https://example.test", opener=opener).fetch_lending_markets()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].supply_apy, 0.0942)
        self.assertEqual(rows[0].borrow_apy, 0.117)
        self.assertTrue(rows[0].observed_at.endswith("+00:00"))
        self.assertIsNone(rows[0].utilization)
        self.assertIsNone(rows[0].available_liquidity_usd)
        self.assertIn("utilization", rows[0].missing_fields)
        self.assertIn("available_liquidity_usd", rows[0].missing_fields)

    def test_invalid_utilization_is_skipped(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{"reserve": "reserve-1", "liquidityToken": "USDC", "utilization": "1.2"}]),
        ])
        rows = KaminoClient(opener=opener).fetch_lending_markets()
        self.assertEqual(rows, [])

    def test_persistence_is_idempotent_for_same_snapshot(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{"reserve": "reserve-1", "liquidityToken": "USDC", "supplyApy": "0.1"}]),
        ])
        snapshot = KaminoClient(opener=opener).fetch_lending_markets()[0]
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "lending.db")
            try:
                self.assertEqual(persist_lending_snapshots(db, [snapshot]), 1)
                self.assertEqual(persist_lending_snapshots(db, [snapshot]), 0)
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM lending_snapshots").fetchone()[0], 1)
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM lending_markets").fetchone()[0], 1)
            finally:
                db.close()

    def test_same_asset_in_multiple_markets_remains_distinct(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Market A", "lendingMarket": "market-a"}, {"name": "Market B", "lendingMarket": "market-b"}]),
            _Response([{"reserve": "reserve-a", "liquidityToken": "USDC", "liquidityTokenMint": "mint"}]),
            _Response([{"reserve": "reserve-b", "liquidityToken": "USDC", "liquidityTokenMint": "mint"}]),
        ])
        rows = KaminoClient(opener=opener).fetch_lending_markets()
        self.assertEqual({(row.market_id, row.reserve_id) for row in rows}, {("market-a", "reserve-a"), ("market-b", "reserve-b")})

    def test_consecutive_snapshots_are_preserved(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{"reserve": "reserve-1", "liquidityToken": "USDC", "supplyApy": "0.1"}]),
        ])
        snapshot = KaminoClient(opener=opener).fetch_lending_markets()[0]
        later = replace(snapshot, observed_at="2026-08-30T12:15:00+00:00")
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "history.db")
            try:
                persist_lending_snapshots(db, [snapshot, later])
                self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM lending_snapshots").fetchone()[0], 2)
            finally:
                db.close()

    def test_nan_infinity_and_non_utc_timestamps_are_rejected(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{"reserve": "reserve-1", "liquidityToken": "USDC", "supplyApy": "NaN"}]),
        ])
        self.assertEqual(KaminoClient(opener=opener).fetch_lending_markets(), [])

    def test_finite_extreme_apy_is_kept_and_marked_anomalous(self):
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{"reserve": "reserve-1", "liquidityToken": "USDC", "supplyApy": "20.33", "borrowApy": "10.13"}]),
        ])
        rows = KaminoClient(opener=opener).fetch_lending_markets()
        self.assertEqual(len(rows), 1)
        self.assertIn("anomalous_supply_apy", rows[0].quality_flags)
        self.assertIn("anomalous_borrow_apy", rows[0].quality_flags)

    def test_history_metrics_use_real_timestamp_coverage_and_filter_only_marked_anomaly(self):
        rows = [
            {"observed_at": "2026-08-30T11:00:00+00:00", "supply_apy": 0.08, "quality_flags": None},
            {"observed_at": "2026-08-30T11:15:00+00:00", "supply_apy": 1.6, "quality_flags": '["anomalous_supply_apy"]'},
            {"observed_at": "2026-08-30T12:00:00+00:00", "supply_apy": 0.10, "quality_flags": None},
        ]
        metrics = supply_apy_history(rows, now=__import__("datetime").datetime.fromisoformat("2026-08-30T12:00:00+00:00"))
        self.assertEqual(metrics["24h"]["samples_count"], 3)
        self.assertAlmostEqual(metrics["24h"]["coverage_pct"], 4.1667, places=3)
        self.assertAlmostEqual(metrics["24h"]["raw_avg"], (0.08 + 1.6 + 0.10) / 3)
        self.assertAlmostEqual(metrics["24h"]["filtered_avg"], 0.09)
        self.assertEqual(metrics["7d"]["samples_count"], 3)
        opener = Mock(side_effect=[
            _Response([{"name": "Main", "lendingMarket": "market-1"}]),
            _Response([{"reserve": "reserve-1", "liquidityToken": "USDC", "supplyApy": "Infinity"}]),
        ])
        self.assertEqual(KaminoClient(opener=opener).fetch_lending_markets(), [])


if __name__ == "__main__":
    unittest.main()
