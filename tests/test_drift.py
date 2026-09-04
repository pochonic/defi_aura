import json
import unittest
from unittest.mock import Mock

import config
from services.lending.drift import DriftClient


class DriftTests(unittest.TestCase):
    def test_normalizes_onchain_spot_market_and_derives_usd_values(self):
        payload = [{
            "market_id": "0",
            "reserve_id": "mint-usdc",
            "asset_symbol": "USDC",
            "asset_mint": "mint-usdc",
            "market_name": "USDC",
            "decimals": 6,
            "total_supplied_native": 1_000_000.0,
            "total_borrowed_native": 250_000.0,
            "available_amount_native": 750_000.0,
            "utilization": 0.25,
            "supply_apy": 0.03,
            "borrow_apy": 0.08,
            "oracle_price_usd": 1.0,
            "source_metadata": {"market_index": 0},
        }]
        runner = Mock(return_value=Mock(stdout=json.dumps(payload)))
        client = DriftClient(node_path="node", rpc_url="https://rpc.test", runner=runner)

        rows = client.fetch_lending_markets()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].protocol, "Drift")
        self.assertEqual(rows[0].total_supplied_usd, 1_000_000.0)
        self.assertEqual(rows[0].available_liquidity_usd, 750_000.0)
        self.assertEqual(rows[0].missing_fields, ())
        self.assertEqual(client.last_report["errors"], 0)

    def test_requires_rpc_url(self):
        client = DriftClient(rpc_url="")
        with self.assertRaisesRegex(RuntimeError, "SOLANA_RPC_URL"):
            client.fetch_lending_markets()


if __name__ == "__main__":
    unittest.main()
