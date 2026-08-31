import unittest

from raydium import Pool
from services.meteora_dlmm import apply_to_pool
from services.liquidity_structure_risk import assess


class FakeSdkPool:
    def getActiveBin(self):
        return {"binId": 10, "pricePerToken": "100"}

    def getBinsAroundActiveBin(self, left, right):
        return {"activeBin": 10, "bins": [
            {"binId": 9, "pricePerToken": "99", "xAmount": "1000000000", "yAmount": "100000000", "supply": "1"},
            {"binId": 10, "pricePerToken": "100", "xAmount": "2000000000", "yAmount": "200000000", "supply": "2"},
            {"binId": 11, "pricePerToken": "101", "xAmount": "1000000000", "yAmount": "100000000", "supply": "1"},
        ]}


class MeteoraDlmmTests(unittest.TestCase):
    def test_enrichment_normalizes_bins_and_structure_uses_observable_distribution(self):
        pool = Pool("pool", "SOL", "USDC", "DLMM", 0.0004, 1_200, 100, None, None, None, fee_model="DYNAMIC", token_a_mint="sol", token_b_mint="usdc", protocol="Meteora")
        pool.protocol_data = {"token_decimals": {"token_a": 9, "token_b": 6}, "bin_step": 10}
        result = apply_to_pool(pool, FakeSdkPool())
        self.assertEqual(result.available["active_bin_id"], 10)
        self.assertEqual(result.available["bins_fetched"], 3)
        self.assertIsNotNone(result.available["hhi"])
        self.assertAlmostEqual(result.available["active_bin_share_of_observed"], 0.5, places=6)
        self.assertAlmostEqual(result.available["active_bin_share_of_pool"], result.available["active_bin_value_usd"] / 1_200, places=6)
        structure = assess(pool)
        self.assertEqual(structure.structure_type, "DLMM")
        self.assertIsNotNone(structure.components["range_dependency_risk"]["score"])
        self.assertIsNotNone(structure.components["capital_concentration_risk"]["score"])
        self.assertIsNone(structure.components["active_liquidity_risk"]["score"])
        self.assertNotIn("active bin/liquidity distribution is unavailable", structure.warnings)
        self.assertIsNotNone(structure.score)

    def test_low_distribution_coverage_is_not_final_structure_score(self):
        pool = Pool("pool", "SOL", "USDC", "DLMM", 0.0004, 10_000, 100, None, None, None, fee_model="DYNAMIC", token_a_mint="sol", token_b_mint="usdc", protocol="Meteora")
        pool.protocol_data = {"token_decimals": {"token_a": 9, "token_b": 6}, "bin_step": 10}
        apply_to_pool(pool, FakeSdkPool())
        structure = assess(pool)
        self.assertLess(structure.distribution_coverage_pct, 30)
        self.assertEqual(structure.confidence, "LOW")
        self.assertIsNone(structure.score)


if __name__ == "__main__":
    unittest.main()
