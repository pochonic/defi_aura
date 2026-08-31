import unittest

from providers.meteora import normalize


class MeteoraTests(unittest.TestCase):
    def test_normalizes_official_dlmm_shape(self):
        raw = {
            "address": "meteora-pool", "token_x": {"address": "SOL", "symbol": "SOL", "decimals": 9},
            "token_y": {"address": "USDC", "symbol": "USDC", "decimals": 6},
            "tvl": 10_000_000, "current_price": 100, "dynamic_fee_pct": 0.02,
            "pool_config": {"base_fee_pct": 0.04, "bin_step": 10},
            "volume": {"24h": 1_000_000}, "fees": {"24h": 500},
            "apr": 0.01825, "farm_apr": 0,
        }
        pool = normalize(raw)
        self.assertEqual(pool.protocol, "Meteora")
        self.assertEqual(pool.pool_type, "DLMM")
        self.assertAlmostEqual(pool.fee_tier, 0.0004)
        self.assertEqual(pool.fee_model, "DYNAMIC")
        self.assertEqual(pool.protocol_data["bin_step"], 10)
        self.assertEqual(pool.protocol_data["active_bin"], None)
        self.assertEqual(pool.fees_24h_usd, 500)


if __name__ == "__main__":
    unittest.main()
