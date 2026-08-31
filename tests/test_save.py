import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from database import Database
from services.lending.ingestion import IngestionStats, persist_lending_snapshots
from services.lending.models import LendingMarketSnapshot
from services.lending.save import SaveClient


class SaveAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = SaveClient(base_url="https://api.solend.fi")
        self.market = {"address": "market-save", "name": "main", "description": "", "isPrimary": True}
        self.reserve_config = {"address": "reserve-save", "liquidityToken": {"symbol": "USDC", "mint": "mint-usdc", "decimals": 6}}
        self.result = {"reserve": {"address": "reserve-save", "lastUpdate": {"slot": "123"}, "liquidity": {
            "mintDecimals": 6, "availableAmount": "5000000", "borrowedAmountWads": "2000000000000000000000000",
            "cumulativeBorrowRateWads": "1000000000000000000"}}, "rates": {"supplyInterest": "8.00", "borrowInterest": "12.00"}}

    def test_normalization_uses_decimal_apy_and_utilization(self):
        item = self.client._normalize(self.market, self.reserve_config, self.result, "2026-08-31T00:00:00+00:00", "catalog", "state", {"mint-usdc": 1.0}, "prices")
        self.assertEqual(item.protocol, "save")
        self.assertEqual(item.supply_apy, 0.08)
        self.assertEqual(item.borrow_apy, 0.12)
        self.assertAlmostEqual(item.utilization, 2.0 / 7.0)
        self.assertEqual(item.available_liquidity_native, 5.0)
        self.assertEqual(item.available_liquidity_decimals, 6)
        self.assertEqual(item.total_supplied_usd, 7.0)
        self.assertEqual(item.source_metadata["metrics"]["utilization"].split(":")[0], "derived")

    def test_missing_price_keeps_native_data_and_marks_missing(self):
        item = self.client._normalize(self.market, self.reserve_config, self.result, "2026-08-31T00:00:00+00:00", "catalog", "state", {}, None)
        self.assertIsNone(item.total_supplied_usd)
        self.assertEqual(item.available_liquidity_native, 5.0)
        self.assertIn("total_supplied_usd", item.missing_fields)
        self.assertIn("price_missing", item.quality_flags)

    def test_save_and_kamino_share_persistence_table(self):
        save_item = self.client._normalize(self.market, self.reserve_config, self.result, "2026-08-31T00:00:00+00:00", "catalog", "state", {"mint-usdc": 1.0}, "prices")
        kamino_item = LendingMarketSnapshot(protocol="Kamino", chain="Solana", market_id="market-kamino", reserve_id="reserve-kamino", asset_symbol="USDC", asset_mint="mint-usdc", supply_apy=0.05, borrow_apy=0.08, utilization=0.5, total_supplied_usd=100.0, total_borrowed_usd=50.0, available_liquidity_usd=None, observed_at="2026-08-31T00:00:00+00:00", source="test", source_endpoint="test")
        with TemporaryDirectory() as folder:
            db = Database(Path(folder) / "test.db")
            stats = IngestionStats()
            persist_lending_snapshots(db, [save_item, kamino_item], stats)
            rows = db.conn.execute("SELECT protocol FROM lending_snapshots ORDER BY protocol").fetchall()
            self.assertEqual([row[0] for row in rows], ["Kamino", "save"])
            self.assertIsNotNone(db.conn.execute("SELECT source_metadata FROM lending_snapshots WHERE protocol='save'").fetchone()[0])
            db.close()


if __name__ == "__main__":
    unittest.main()
