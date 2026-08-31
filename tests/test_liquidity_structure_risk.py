import unittest

from raydium import Pool
from services.liquidity_structure_risk import assess, classify_structure


def pool(pool_type, fee_model="FIXED"):
    return Pool("pool", "SOL", "USDC", pool_type, 0.0004, 5_000_000, 250_000, None, None, None, fee_model=fee_model)


class LiquidityStructureRiskTests(unittest.TestCase):
    def test_constant_product_can_be_evaluated_without_active_liquidity(self):
        result = assess(pool("AMM", "FIXED"))
        self.assertEqual(result.structure_type, "CONSTANT_PRODUCT_AMM")
        self.assertGreaterEqual(result.coverage_pct, 60)
        self.assertIsNotNone(result.score)
        self.assertAlmostEqual(sum(item["weighted_contribution"] for item in result.components.values() if item["coverage"]), result.score, places=6)

    def test_concentrated_pool_without_distribution_is_not_scored(self):
        result = assess(pool("CLMM", "FIXED"))
        self.assertEqual(result.structure_type, "CLMM")
        self.assertIsNone(result.score)
        self.assertIn("range_dependency_risk", result.missing_components)
        self.assertIn("active_liquidity_risk", result.missing_components)
        self.assertEqual(result.confidence, "N/A")

    def test_whirlpool_without_distribution_has_unknown_confidence(self):
        result = assess(pool("Whirlpool", "FIXED"))
        self.assertIsNone(result.score)
        self.assertEqual(result.confidence, "N/A")

    def test_type_mapping_is_mechanism_based(self):
        self.assertEqual(classify_structure(pool("Whirlpool")), "WHIRLPOOL")
        self.assertEqual(classify_structure(pool("DLMM")), "DLMM")


if __name__ == "__main__":
    unittest.main()
