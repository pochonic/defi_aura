import json
import logging
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config
from .models import LendingMarketSnapshot

log = logging.getLogger(__name__)


class KaminoClient:
    """Kamino REST adapter; endpoint paths remain configurable for API changes."""

    source = "Kamino official REST API"

    def __init__(self, base_url=None, opener=urlopen):
        self.base_url = (base_url or os.getenv("KAMINO_API_BASE_URL") or config.KAMINO_API_BASE_URL).rstrip("/")
        self.opener = opener
        self.last_report = {"markets": 0, "reserves": 0, "skipped": 0, "errors": 0}

    def _get_json(self, path):
        endpoint = self.base_url + path
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "defi-aura-lending/1.0"})
        try:
            with self.opener(request, timeout=config.KAMINO_REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8")), endpoint
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError(f"Kamino request failed for {endpoint}: {exc}") from exc

    def fetch_lending_markets(self):
        markets, catalog_endpoint = self._get_json(config.KAMINO_MARKETS_ENDPOINT)
        if not isinstance(markets, list):
            raise ValueError("Kamino market catalog is not a list")
        self.last_report = {"markets": len(markets), "reserves": 0, "skipped": 0, "errors": 0}
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snapshots = []
        for market in markets:
            market_id = market.get("lendingMarket") if isinstance(market, dict) else None
            market_name = market.get("name") if isinstance(market, dict) else None
            if not market_id:
                self.last_report["skipped"] += 1
                log.error("Skipping Kamino market without lendingMarket: %s", market)
                continue
            path = config.KAMINO_RESERVES_ENDPOINT.format(market_id=market_id)
            try:
                reserves, endpoint = self._get_json(path)
                if not isinstance(reserves, list):
                    raise ValueError("reserve metrics is not a list")
                self.last_report["reserves"] += len(reserves)
                for raw in reserves:
                    try:
                        snapshot = self._normalize(raw, market, market_id, market_name, observed_at, endpoint)
                        snapshot.validate()
                        snapshots.append(snapshot)
                    except (AttributeError, KeyError, TypeError, ValueError) as exc:
                        self.last_report["skipped"] += 1
                        log.error("Skipping invalid Kamino reserve in %s (%s): %s", market_id, raw, exc)
            except (RuntimeError, ValueError) as exc:
                self.last_report["errors"] += 1
                log.error("Skipping Kamino market %s: %s", market_id, exc)
        log.info("Kamino catalog fetched from %s; normalized %d reserves", catalog_endpoint, len(snapshots))
        return snapshots

    def _normalize(self, raw, market, market_id, market_name, observed_at, endpoint):
        if not isinstance(raw, dict) or not raw.get("reserve"):
            raise ValueError("reserve id is missing")

        def number(*names):
            value = next((raw.get(name) for name in names if raw.get(name) is not None), None)
            return None if value is None else float(value)

        values = {
            "supply_apy": number("supplyApy", "supplyInterestAPY"),
            "borrow_apy": number("borrowApy", "borrowInterestAPY"),
            "utilization": number("utilization", "utilizationRatio"),
            "total_supplied_usd": number("totalSupplyUsd", "depositTvl"),
            "total_borrowed_usd": number("totalBorrowUsd", "borrowTvl"),
            "available_liquidity_usd": number("availableLiquidityUsd", "availableLiquidity"),
        }
        missing = tuple(name for name, value in values.items() if value is None)
        quality_flags = []
        if not str(raw.get("liquidityToken") or "").strip():
            quality_flags.append("empty_asset_symbol")
        if not str(raw.get("liquidityTokenMint") or "").strip():
            quality_flags.append("empty_asset_mint")
        for name in ("supply_apy", "borrow_apy"):
            value = values[name]
            if value is not None and value > 1:
                quality_flags.append(f"anomalous_{name}")
        return LendingMarketSnapshot(
            protocol="Kamino", chain="Solana", market_id=market_id,
            reserve_id=str(raw["reserve"]), asset_symbol=raw.get("liquidityToken"),
            asset_mint=raw.get("liquidityTokenMint"), observed_at=observed_at,
            source=self.source, source_endpoint=endpoint, market_name=market_name,
            market_description=market.get("description"),
            market_is_primary=market.get("isPrimary"), market_is_curated=market.get("isCurated"),
            missing_fields=missing, quality_flags=tuple(quality_flags), **values,
        )
