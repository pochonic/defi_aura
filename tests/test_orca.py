import unittest

from providers.orca import normalize


class OrcaTests(unittest.TestCase):
    def test_normalizes_official_whirlpool_shape(self):
        raw = {
            "address": "orca-pool", "feeRate": 400, "tickSpacing": 4,
            "tickCurrentIndex": -22502, "liquidity": "123456",
            "tokenMintA": "So111", "tokenMintB": "EPj",
            "tokenA": {"symbol": "SOL", "decimals": 9},
            "tokenB": {"symbol": "USDC", "decimals": 6},
            "price": "105.4", "tvlUsdc": "10000000",
            "stats": {"24h": {"volume": "1000000", "fees": "10", "rewards": "0"}},
            "rewards": [],
        }
        pool = normalize(raw)
        self.assertEqual(pool.protocol, "Orca")
        self.assertEqual(pool.pool_type, "Whirlpool")
        self.assertAlmostEqual(pool.fee_tier, 0.0004)
        self.assertEqual(pool.token_a_mint, "So111")
        self.assertEqual(pool.protocol_data["current_tick"], -22502)
        self.assertEqual(pool.reward_apr, 0.0)
        self.assertTrue(pool.reward_known)
        self.assertEqual(pool.fees_24h_usd, 10.0)

    def test_unknown_rewards_are_not_assumed_zero(self):
        raw = {"address": "orca-pool", "tokenA": {"symbol": "SOL"}, "tokenB": {"symbol": "USDC"},
               "tvlUsdc": "10000000", "feeRate": 400, "stats": {"24h": {"volume": "1000000", "fees": "10", "rewards": None}},
               "rewards": [{"active": True}]}
        pool = normalize(raw)
        self.assertIsNone(pool.reward_apr)
        self.assertFalse(pool.reward_known)


if __name__ == "__main__":
    unittest.main()
