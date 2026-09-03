import json
import logging
import os
from decimal import Decimal
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config
from .models import LendingMarketSnapshot

log = logging.getLogger(__name__)


def _number(value):
    if value is None:
        return None
    return float(str(value).replace(",", ""))


WAD = Decimal(10) ** 18


def _decimal(value):
    return Decimal(str(value).replace(",", ""))


class SaveClient:
    """Save/Solend REST adapter. Save-specific API knowledge stays here."""

    source = "Save official REST API"

    def __init__(self, base_url=None, opener=urlopen):
        self.base_url = (base_url or os.getenv("SAVE_API_BASE_URL") or config.SAVE_API_BASE_URL).rstrip("/")
        self.opener = opener
        self.last_report = {"markets": 0, "reserves": 0, "discovered": 0, "fresh": 0,
                            "stale_skipped": 0, "anomalous_skipped": 0, "snapshots_persisted": 0,
                            "skipped": 0, "errors": 0}

    def _get_json(self, path):
        endpoint = self.base_url + path
        request = Request(endpoint, headers={"Accept": "application/json", "User-Agent": "defi-aura-lending/1.0"})
        try:
            with self.opener(request, timeout=config.SAVE_REQUEST_TIMEOUT_SECONDS) as response:
                return json.loads(response.read().decode("utf-8")), endpoint
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            raise RuntimeError(f"Save request failed for {endpoint}: {exc}") from exc

    def fetch_lending_markets(self, assets=None):
        markets_payload, markets_endpoint = self._get_json(config.SAVE_MARKETS_ENDPOINT)
        markets = markets_payload.get("results", []) if isinstance(markets_payload, dict) else []
        self.last_report = {"markets": len(markets), "reserves": 0, "discovered": 0, "fresh": 0,
                            "stale_skipped": 0, "anomalous_skipped": 0, "snapshots_persisted": 0,
                            "skipped": 0, "errors": 0}
        configs = []
        for market in markets:
            try:
                payload, endpoint = self._get_json(config.SAVE_MARKET_CONFIG_ENDPOINT.format(market_id=market["address"]))
                if payload:
                    configs.append((payload[0], endpoint))
            except (KeyError, RuntimeError, ValueError) as exc:
                self.last_report["errors"] += 1
                log.error("Skipping Save market %s: %s", market, exc)

        reserve_configs = []
        for market, endpoint in configs:
            for reserve in market.get("reserves", []):
                token = reserve.get("liquidityToken") or {}
                if assets and token.get("symbol") not in set(assets):
                    continue
                if reserve.get("address"):
                    reserve_configs.append((market, reserve, endpoint))
        self.last_report["reserves"] = len(reserve_configs)
        self.last_report["discovered"] = len(reserve_configs)
        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state_by_id = {}
        state_endpoints = []
        for offset in range(0, len(reserve_configs), 100):
            batch = reserve_configs[offset:offset + 100]
            payload, endpoint = self._get_json(config.SAVE_RESERVES_ENDPOINT.format(reserve_ids=",".join(item[1]["address"] for item in batch)))
            state_endpoints.append(endpoint)
            for result in payload.get("results", []) if isinstance(payload, dict) else []:
                state = result.get("reserve") or result
                state_by_id[state.get("address") or state.get("pubkey")] = {"data": result, "endpoint": endpoint}
        mints = sorted({(reserve.get("liquidityToken") or {}).get("mint") for _, reserve, _ in reserve_configs if (reserve.get("liquidityToken") or {}).get("mint")})
        prices = {}
        price_endpoint = None
        if mints:
            payload, price_endpoint = self._get_json(config.SAVE_PRICES_ENDPOINT.format(mints=",".join(mints)))
            prices = {item.get("mint"): _number(item["price"]) for item in payload.get("results", []) if item.get("mint") and item.get("price") is not None}
        snapshots = []
        for market, reserve_config, config_endpoint in reserve_configs:
            reserve_id = reserve_config["address"]
            item = state_by_id.get(reserve_id)
            if not item:
                self.last_report["skipped"] += 1
                continue
            state = item["data"].get("reserve") or item["data"]
            # A stale reserve has not been refreshed on-chain.  Save still
            # returns a compounded rate for it (often enormous at 100%
            # utilization), but it is not a current lending observation.
            if int((state.get("lastUpdate") or {}).get("stale", 0)) != 0:
                self.last_report["stale_skipped"] += 1
                self.last_report["skipped"] += 1
                continue
            try:
                snapshot = self._normalize(market, reserve_config, item["data"], observed_at, markets_endpoint, item["endpoint"], prices, price_endpoint)
                if "anomalous_supply_apy" in snapshot.quality_flags or "anomalous_borrow_apy" in snapshot.quality_flags:
                    self.last_report["anomalous_skipped"] += 1
                    self.last_report["skipped"] += 1
                    continue
                snapshots.append(snapshot)
                self.last_report["fresh"] += 1
            except (KeyError, TypeError, ValueError) as exc:
                self.last_report["skipped"] += 1
                log.error("Skipping invalid Save reserve %s: %s", reserve_id, exc)
        log.info("Save catalog fetched from %s; normalized %d reserves", markets_endpoint, len(snapshots))
        return snapshots

    def _normalize(self, market, reserve_config, result, observed_at, markets_endpoint, state_endpoint, prices, price_endpoint):
        state = result.get("reserve") or result
        reserve_id = reserve_config["address"]
        liquidity = state.get("liquidity") or {}
        rates = result.get("rates") or {}
        token = reserve_config.get("liquidityToken") or {}
        decimals = int(liquidity.get("mintDecimals", token.get("decimals")))
        available_atomic = _decimal(liquidity["availableAmount"])
        borrowed_atomic = _decimal(liquidity["borrowedAmountWads"]) / WAD
        scale = Decimal(10) ** decimals
        supplied_atomic = available_atomic + borrowed_atomic
        available_native = available_atomic / scale
        borrowed_native = borrowed_atomic / scale
        supplied_native = supplied_atomic / scale
        utilization = float(borrowed_atomic / supplied_atomic) if supplied_atomic else None
        price = prices.get(token.get("mint"))
        supplied_usd = float(supplied_native * _decimal(price)) if price is not None else None
        borrowed_usd = float(borrowed_native * _decimal(price)) if price is not None else None
        available_usd = float(available_native * _decimal(price)) if price is not None else None
        missing = []
        for name, value in (("supply_apy", rates.get("supplyInterest")), ("borrow_apy", rates.get("borrowInterest")), ("utilization", utilization), ("total_supplied_usd", supplied_usd), ("total_borrowed_usd", borrowed_usd), ("available_liquidity_usd", available_usd)):
            if value is None:
                missing.append(name)
        metadata = {
            "market_catalog": {"source": markets_endpoint, "type": "Save/Solend REST API"},
            "reserve_state": {"source": state_endpoint, "type": "Save/Solend REST API", "last_update_slot": state.get("lastUpdate", {}).get("slot")},
            "adapter_version": config.SAVE_ADAPTER_VERSION,
            "apy_calculation_version": config.SAVE_APY_CALCULATION_VERSION,
            "metrics": {"supply_apy": "observed:Save/Solend REST API percent / 100", "borrow_apy": "observed:Save/Solend REST API percent / 100", "available_liquidity_native": "derived:availableAmount / 10^mintDecimals", "utilization": "derived:SDK formula borrowedAmountWads/WAD / (availableAmount + borrowedAmountWads/WAD)", "supplied": "derived:(availableAmount + borrowedAmountWads/WAD) / 10^mintDecimals", "price": "observed:Save/Solend price API" if price is not None else "missing"},
            "decimals": decimals, "available_amount_raw": liquidity.get("availableAmount"), "borrowed_amount_wads": liquidity.get("borrowedAmountWads"), "cumulative_borrow_rate_wads": liquidity.get("cumulativeBorrowRateWads"), "price": price, "price_source": price_endpoint,
        }
        supply_apy = _number(rates["supplyInterest"]) / 100 if rates.get("supplyInterest") is not None else None
        borrow_apy = _number(rates["borrowInterest"]) / 100 if rates.get("borrowInterest") is not None else None
        quality_flags = []
        if price is None:
            quality_flags.append("price_missing")
        if supply_apy is not None and supply_apy > 1:
            quality_flags.append("anomalous_supply_apy")
        if borrow_apy is not None and borrow_apy > 1:
            quality_flags.append("anomalous_borrow_apy")
        return LendingMarketSnapshot(
            protocol="save", chain="Solana", market_id=market["address"], market_name=market.get("name"),
            market_description=market.get("description"), market_is_primary=market.get("isPrimary"),
            reserve_id=reserve_id, asset_symbol=token.get("symbol"), asset_mint=token.get("mint"),
            supply_apy=supply_apy, borrow_apy=borrow_apy,
            utilization=utilization, total_supplied_usd=supplied_usd, total_borrowed_usd=borrowed_usd,
            available_liquidity_usd=available_usd,
            available_liquidity_native=float(available_native), available_liquidity_decimals=decimals,
            available_liquidity_source=state_endpoint, observed_at=observed_at, source=self.source,
            source_endpoint=state_endpoint, source_metadata=metadata, missing_fields=tuple(missing),
            quality_flags=tuple(quality_flags),
        )
