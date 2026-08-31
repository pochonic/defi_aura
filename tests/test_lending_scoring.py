import unittest
from datetime import datetime, timedelta, timezone

from services.lending.scoring import evaluate_lending_opportunity


def rows_for(supply_apy, supplied, utilization, count=97, borrow_apy=0.12, start=None):
    start = start or datetime(2026, 8, 23, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        rows.append({"protocol": "Kamino", "chain": "Solana", "market_id": "market", "reserve_id": "reserve", "asset_symbol": "USDC",
                     "supply_apy": supply_apy, "borrow_apy": borrow_apy, "utilization": utilization,
                     "total_supplied_usd": supplied, "observed_at": (start + timedelta(minutes=15 * index)).isoformat(), "quality_flags": "[]"})
    return rows


class LendingScoringTests(unittest.TestCase):
    def test_stable_high_capacity_market_scores_well(self):
        rows = rows_for(0.095, 60_000_000, 0.75)
        result = evaluate_lending_opportunity(rows[-1], rows)
        self.assertTrue(result["eligibility"]["eligible"])
        self.assertGreater(result["provisional_opportunity_score"], 70)
        self.assertIsNone(result["components"]["apy_persistence"])
        self.assertIsNone(result["components"]["apy_stability"])
        self.assertEqual(result["score_status"], "PROVISIONAL")
        self.assertEqual(result["score_model"], "lending_opportunity")
        self.assertEqual(result["score_version"], "1.0")
        self.assertAlmostEqual(result["available_points_raw"], result["weighted_points"])
        self.assertAlmostEqual(result["available_weight"], 0.65)
        self.assertAlmostEqual(result["missing_weight"], 0.35)
        self.assertEqual(result["economic_relevance"], "large")

    def test_spike_does_not_win_on_apy_alone(self):
        rows = rows_for(0.08, 11_000, 0.90)
        rows[-1]["supply_apy"] = 1.60
        result = evaluate_lending_opportunity(rows[-1], rows)
        self.assertIn("apy_spike", result["flags"])
        self.assertIn("low_capacity", result["flags"])
        self.assertLess(result["provisional_opportunity_score"], 75)
        self.assertIsNone(result["components"]["apy_persistence"])
        self.assertIsNone(result["components"]["apy_stability"])

    def test_tiny_market_is_penalized(self):
        result = evaluate_lending_opportunity(rows_for(0.20, 800, 0.75)[-1], rows_for(0.20, 800, 0.75))
        self.assertEqual(result["economic_relevance"], "micro")
        self.assertIn("low_capacity", result["flags"])

    def test_insufficient_history_lowers_confidence(self):
        rows = rows_for(0.10, 5_000_000, 0.75, count=2, start=datetime(2026, 8, 30, tzinfo=timezone.utc))
        result = evaluate_lending_opportunity(rows[-1], rows)
        self.assertLess(result["confidence"], 0.5)
        self.assertIn("insufficient_history", result["flags"])
        self.assertEqual(result["component_details"]["apy_persistence"]["status"], "unavailable")
        self.assertEqual(result["component_details"]["apy_stability"]["reason"], "insufficient_7d_history")

    def test_history_status_transition_is_deterministic(self):
        provisional_rows = rows_for(0.10, 5_000_000, 0.75, count=2, start=datetime(2026, 8, 30, tzinfo=timezone.utc))
        partial_rows = rows_for(0.10, 5_000_000, 0.75, count=289, start=datetime(2026, 8, 27, tzinfo=timezone.utc))
        mature_rows = rows_for(0.10, 5_000_000, 0.75, count=673, start=datetime(2026, 8, 23, tzinfo=timezone.utc))
        provisional = evaluate_lending_opportunity(provisional_rows[-1], provisional_rows)
        partial = evaluate_lending_opportunity(partial_rows[-1], partial_rows)
        mature = evaluate_lending_opportunity(mature_rows[-1], mature_rows)
        self.assertEqual(provisional["score_status"], "PROVISIONAL")
        self.assertEqual(partial["score_status"], "PARTIAL_HISTORY")
        self.assertEqual(mature["score_status"], "MATURE")
        self.assertIsNone(provisional["components"]["apy_persistence"])
        self.assertIsNotNone(partial["components"]["apy_persistence"])
        self.assertIsNotNone(mature["opportunity_score"])
        self.assertIsNotNone(provisional["provisional_opportunity_score"])


if __name__ == "__main__":
    unittest.main()
