import unittest

from raydium import calculate_metrics, interpolated_tvl_score, normalize, score_pool


RAW = {
    "id": "pool-1", "feeRate": 0.0025, "tvl": 10_000_000,
    "mintA": {"symbol": "SOL"}, "mintB": {"symbol": "USDC"},
    "day": {"volume": 20_000_000, "apr": 75, "rewardApr": [2, 1]},
    "week": {"volume": 100_000_000},
}


class RaydiumTests(unittest.TestCase):
    def test_normalize_and_metrics(self):
        pool = normalize(RAW)
        calculate_metrics(pool)
        self.assertEqual(pool.pair, "SOL / USDC")
        self.assertAlmostEqual(pool.calculated_fee_apr, 182.5)
        self.assertAlmostEqual(pool.volume_tvl_ratio, 2.0)
        self.assertEqual(pool.reward_apr, 3)

    def test_score_is_explainable_and_bounded(self):
        pool = normalize(RAW)
        calculate_metrics(pool)
        score_pool(pool)
        self.assertTrue(0 <= pool.score <= 100)
        self.assertEqual(set(pool.score_breakdown), {"fee_apr", "volume_tvl", "tvl", "organic_yield"})
        self.assertNotIn("persistence", pool.effective_weights)
        self.assertEqual(pool.status, "INSUFFICIENT_HISTORY")

    def test_tvl_score_uses_requested_non_linear_bands(self):
        self.assertAlmostEqual(interpolated_tvl_score(500_000), 10)
        self.assertAlmostEqual(interpolated_tvl_score(1_000_000), 25)
        self.assertGreater(interpolated_tvl_score(1_000_000), interpolated_tvl_score(500_000))
        self.assertAlmostEqual(interpolated_tvl_score(100_000_000), 100)

    def test_low_organic_fee_apr_is_not_100(self):
        raw = dict(RAW)
        raw["tvl"] = 100_000_000
        raw["day"] = {"volume": 100, "apr": 0.001, "rewardApr": []}
        pool = normalize(raw)
        calculate_metrics(pool)
        score_pool(pool)
        self.assertLess(pool.score_breakdown["organic_yield"], 10)

    def test_persistence_requires_six_snapshots_and_24_hours(self):
        pool = normalize(RAW)
        calculate_metrics(pool)
        score_pool(pool, avg_24h=pool.calculated_fee_apr, history_count=5, history_duration_hours=48)
        self.assertEqual(pool.status, "INSUFFICIENT_HISTORY")
        self.assertNotIn("persistence", pool.score_breakdown)
        score_pool(pool, avg_24h=pool.calculated_fee_apr, history_count=6, history_duration_hours=12)
        self.assertEqual(pool.status, "OBSERVING")
        score_pool(pool, avg_24h=pool.calculated_fee_apr, history_count=6, history_duration_hours=24)
        self.assertEqual(pool.status, "PERSISTENT_24H")

    def test_unknown_token_is_ignored(self):
        raw = dict(RAW)
        raw["mintA"] = {"symbol": "UNKNOWN"}
        raw["mintB"] = {"symbol": "OTHER"}
        self.assertIsNone(normalize(raw))


if __name__ == "__main__":
    unittest.main()
