import unittest

from services.asset_risk import assess_token


class AssetRiskTests(unittest.TestCase):
    def test_objective_components_and_coverage(self):
        result = assess_token({
            "mint": "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN", "symbol": "TRUMP",
            "jupiter": {
                "mcap": 250_000_000, "liquidity": 50_000_000,
                "isVerified": False, "audit": {"topHoldersPercentage": 40},
            },
            "mint_authority": "active", "freeze_authority": None,
            "rpc_state": "LIVE", "sources": [],
        })
        self.assertIsNotNone(result.score)
        self.assertGreaterEqual(result.coverage_pct, 60)
        self.assertGreater(result.components["holder_concentration_risk"]["score"], 0)
        self.assertFalse(result.components["token_age_risk"]["coverage"])

    def test_missing_data_does_not_become_zero(self):
        result = assess_token({"mint": "mint", "symbol": "UNKNOWN", "jupiter": {}, "rpc_state": "UNAVAILABLE", "sources": []})
        self.assertIsNone(result.score)
        self.assertIsNone(result.components["market_cap_risk"]["score"])
        self.assertIsNone(result.components["authority_risk"]["score"])

    def test_centralized_stablecoin_separates_issuer_control(self):
        result = assess_token({
            "mint": "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "symbol": "USDC",
            "jupiter": {"mcap": 7_000_000_000, "liquidity": 400_000_000, "isVerified": True,
                        "audit": {"topHoldersPercentage": 95}},
            "mint_authority": "active", "freeze_authority": "active", "rpc_state": "LIVE", "sources": [],
        })
        self.assertEqual(result.data["asset_class"], "STABLECOIN_CENTRALIZED")
        self.assertFalse(result.components["holder_concentration_risk"]["coverage"])
        self.assertIsNotNone(result.data["issuer"])
        self.assertIsNotNone(result.market_asset_risk)
        self.assertIsNotNone(result.structural_asset_risk)
        self.assertTrue(result.data["mandatory_components_complete"])

    def test_wrapper_does_not_use_wrapper_mcap_as_underlying_mcap(self):
        result = assess_token({
            "mint": "69MPxM6bSJCuiD1v5qyZ24CMk1eoTBUdDSCmFboAKc9v", "symbol": "whETH",
            "jupiter": {"mcap": None, "liquidity": 100_000, "isVerified": None, "audit": {}},
            "mint_authority": None, "freeze_authority": None, "rpc_state": "LIVE", "sources": [],
        })
        self.assertEqual(result.data["asset_class"], "WRAPPED_BRIDGED")
        self.assertEqual(result.data["underlying_asset"], "ETH")
        self.assertFalse(result.components["underlying_market_risk"]["coverage"])
        self.assertTrue(any("Wrapper risk" in warning for warning in result.warnings))
        self.assertFalse(result.data["mandatory_components_complete"])
        self.assertIn("underlying_market_risk", result.data["missing_mandatory_components"])
        self.assertIsNone(result.score)


if __name__ == "__main__":
    unittest.main()
