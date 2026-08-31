"""Optional Meteora DLMM state enrichment through the official SDK surface.

The REST provider remains the discovery/metrics source. This module only
accepts an SDK pool object (or an injected factory) and calls documented SDK
methods; it never fabricates a REST endpoint or bin state.
"""

import math
import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config


@dataclass
class DlmmEnrichment:
    available: dict
    unavailable: list[str]
    warnings: list[str]
    source: str


def _value(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _read(obj, *names):
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return None


def _call(obj, *names_and_args):
    for index in range(0, len(names_and_args), 2):
        name, args = names_and_args[index], names_and_args[index + 1]
        method = getattr(obj, name, None)
        if callable(method):
            return method(*args)
    return None


def _bin_record(item, decimals_x, decimals_y, price_is_ui=True):
    bin_id = _read(item, "binId", "bin_id")
    price = _value(_read(item, "pricePerToken", "price_per_token", "price"))
    x_raw = _value(_read(item, "xAmount", "x_amount"))
    y_raw = _value(_read(item, "yAmount", "y_amount"))
    supply = _read(item, "supply")
    if bin_id is None or price is None or x_raw is None or y_raw is None:
        return None
    x_amount = x_raw / (10 ** decimals_x)
    y_amount = y_raw / (10 ** decimals_y)
    return {"bin_id": int(bin_id), "price": price, "raw_x": x_raw, "raw_y": y_raw, "x_human": x_amount, "y_human": y_amount, "x_amount": x_amount, "y_amount": y_amount, "supply": supply}


def _enrich_window(pool, sdk_pool=None, window=None):
    window = int(window or config.METEORA_DLMM_BIN_WINDOW)
    if sdk_pool is None:
        try:
            probe = Path(__file__).with_name("meteora_dlmm_probe.mjs")
            completed = subprocess.run([config.METEORA_DLMM_NODE, str(probe), pool.pool_address, config.SOLANA_RPC_ENDPOINT, str(window)], capture_output=True, text=True, timeout=config.METEORA_DLMM_SDK_TIMEOUT_SECONDS, check=True, cwd=probe.parent.parent)
            sdk_pool = json.loads(completed.stdout)
        except Exception as exc:
            return DlmmEnrichment({}, [f"official Meteora DLMM SDK unavailable or failed: {exc}"], [], "UNAVAILABLE")
    decimals = (pool.protocol_data or {}).get("token_decimals") or {}
    decimals_x = int(decimals.get("token_a") or 0)
    decimals_y = int(decimals.get("token_b") or 0)
    try:
        if isinstance(sdk_pool, dict) and "activeBin" in sdk_pool:
            active = sdk_pool.get("activeBin")
            around = sdk_pool
        else:
            active = _call(sdk_pool, "getActiveBin", (), "get_active_bin", ())
            around = _call(sdk_pool, "getBinsAroundActiveBin", (window, window), "get_bins_around_active_bin", (window, window))
        active_id = _read(active, "binId", "bin_id")
        active_price = _value(_read(active, "pricePerToken", "price_per_token", "price"))
        bins = _read(around, "bins", "bin_liquidty", "bin_liquidity") or []
        if active_id is None and isinstance(around, dict):
            active_id = around.get("activeBin")
        raw_bins_received = len(bins)
        by_id = {}
        for item in bins:
            record = _bin_record(item, decimals_x, decimals_y)
            if record is not None:
                by_id[record["bin_id"]] = record
        records = list(by_id.values())
        duplicate_bins_removed = max(0, raw_bins_received - len(records))
        if not records:
            return DlmmEnrichment({"active_bin_id": active_id, "active_bin_price": active_price, "bins_fetched": 0, "raw_bins_received": raw_bins_received, "unique_bins": 0, "duplicate_bins_removed": duplicate_bins_removed}, ["SDK returned no usable bins"], [], "METEORA_OFFICIAL_SDK")
        stable_b = pool.token_b.upper() in {"USDC", "USDT", "PYUSD"}
        stable_a = pool.token_a.upper() in {"USDC", "USDT", "PYUSD"}
        current_price = _value((pool.protocol_data or {}).get("current_price"))
        values = []
        for record in records:
            # pricePerToken is the bin-implied X/Y price. It is retained as a
            # control calculation; coverage uses current-price valuation when
            # the provider exposes a current price.
            if stable_b:
                x_value_bin = record["x_amount"] * record["price"]
                y_value_bin = record["y_amount"]
                x_value_usd = record["x_amount"] * current_price if current_price is not None else None
                y_value_usd = record["y_amount"]
            elif stable_a:
                x_value_bin = record["x_amount"]
                y_value_bin = record["y_amount"] / record["price"] if record["price"] > 0 else None
                x_value_usd = record["x_amount"]
                y_value_usd = record["y_amount"] * current_price if current_price is not None else None
            else:
                x_value_bin = y_value_bin = x_value_usd = y_value_usd = None
            record["x_value_usd"] = x_value_usd
            record["y_value_usd"] = y_value_usd
            record["bin_value_usd"] = x_value_bin + y_value_bin if x_value_bin is not None and y_value_bin is not None else None
            value = x_value_usd + y_value_usd if x_value_usd is not None and y_value_usd is not None else record["bin_value_usd"]
            record["value_usd"] = value
            if value is not None and value >= 0:
                values.append(record)
        observed_value = sum(item["value_usd"] for item in values) if values else None
        distribution_coverage = observed_value / pool.tvl_usd * 100 if observed_value is not None and pool.tvl_usd and pool.tvl_usd > 0 else None
        prices = [item["price"] for item in records]
        shares = [item["value_usd"] / observed_value for item in values] if observed_value else []
        active_value = next((item["value_usd"] for item in values if item["bin_id"] == int(active_id)), None) if active_id is not None else None
        metrics = {}
        if active_price and values:
            for pct in (0.5, 1, 2, 5, 10):
                band_value = sum(item["value_usd"] for item in values if abs(item["price"] / active_price - 1) * 100 <= pct)
                key = str(pct).replace('.', '_')
                # The legacy key is retained as an observed-distribution
                # percentage; the explicit suffix prevents denominator drift.
                metrics[f"liquidity_within_{key}pct"] = band_value / observed_value * 100 if observed_value else None
                metrics[f"within_{key}pct_observed_pct"] = metrics[f"liquidity_within_{key}pct"]
                metrics[f"within_{key}pct_pool_tvl_pct"] = band_value / pool.tvl_usd * 100 if pool.tvl_usd and pool.tvl_usd > 0 else None
        ordered = sorted(shares, reverse=True)
        metrics.update({"top_1_bin_pct": (sum(ordered[:1]) * 100 if ordered else None), "top_5_bins_pct": (sum(ordered[:5]) * 100 if ordered else None), "top_10_bins_pct": (sum(ordered[:10]) * 100 if ordered else None), "hhi": (sum(value * value for value in shares) if shares else None), "effective_number_of_bins": (1 / sum(value * value for value in shares) if shares and sum(value * value for value in shares) else None), "active_bin_share_of_observed": (active_value / observed_value if active_value is not None and observed_value else None), "active_bin_share_of_pool": (active_value / pool.tvl_usd if active_value is not None and pool.tvl_usd and pool.tvl_usd > 0 else None)})
        totals = {"x": sum(item["x_human"] for item in values), "y": sum(item["y_human"] for item in values)}
        estimated_total = None
        if stable_b and current_price is not None:
            estimated_total = totals["x"] * current_price + totals["y"]
        elif stable_a and current_price is not None:
            estimated_total = totals["x"] + totals["y"] * current_price
        estimated_total_pool = estimated_total if distribution_coverage is not None and 99.5 <= distribution_coverage <= 105 else None
        estimated_difference_pct = estimated_total_pool / pool.tvl_usd * 100 - 100 if estimated_total_pool is not None and pool.tvl_usd else None
        distribution_state = "N/A" if distribution_coverage is None else "INVALID" if distribution_coverage > 105 else "HIGH_COVERAGE" if distribution_coverage >= 70 else "PARTIAL"
        available = {"active_bin_id": int(active_id) if active_id is not None else None, "active_bin_price": active_price, "bin_step": (pool.protocol_data or {}).get("bin_step"), "bins_fetched": len(records), "raw_bins_received": raw_bins_received, "unique_bins": len(records), "duplicate_bins_removed": duplicate_bins_removed, "price_min": min(prices), "price_max": max(prices), "distribution_coverage_pct": distribution_coverage, "distribution_state": distribution_state, "active_bin_value_usd": active_value, "observed_window_value_usd": observed_value, "estimated_observed_window_value_usd": estimated_total, "estimated_total_pool_value_usd": estimated_total_pool, "rest_tvl_usd": pool.tvl_usd, "estimated_vs_rest_difference_pct": estimated_difference_pct, "pool_tvl_usd": pool.tvl_usd, "valuation_price_usd": current_price, **metrics, "bins": records}
        available["active_bin_share_of_observed"] = active_value / observed_value if active_value is not None and observed_value else None
        available["active_bin_share_of_pool"] = active_value / pool.tvl_usd if active_value is not None and pool.tvl_usd and pool.tvl_usd > 0 else None
        warnings = []
        if distribution_coverage is None:
            warnings.append("distribution coverage unavailable because bin value could not be validated against TVL")
        elif distribution_coverage < 100:
            warnings.append("bin window does not cover the full pool TVL")
        elif distribution_coverage <= 105:
            warnings.append("POSSIBLE_TIMESTAMP_OR_VALUATION_DRIFT")
        return DlmmEnrichment(available, [], warnings, "METEORA_OFFICIAL_SDK")
    except Exception as exc:
        return DlmmEnrichment({}, [f"SDK enrichment failed: {exc}"], [], "METEORA_OFFICIAL_SDK")


def enrich_pool(pool, sdk_pool=None, window=None):
    """Fetch progressively wider official-SDK windows until coverage is useful."""
    initial = int(window or config.METEORA_DLMM_BIN_WINDOW)
    windows = []
    current = initial
    while current <= config.METEORA_DLMM_MAX_BIN_WINDOW:
        windows.append(current)
        current *= 2
    result = None
    calls = 0
    for candidate_window in windows:
        result = _enrich_window(pool, sdk_pool=sdk_pool, window=candidate_window)
        calls += 1
        coverage = result.available.get("distribution_coverage_pct") if result else None
        if coverage is not None and coverage >= config.METEORA_DLMM_TARGET_COVERAGE_PCT:
            break
        if result and result.source == "UNAVAILABLE":
            break
    result = result or DlmmEnrichment({}, ["no DLMM enrichment result"], [], "UNAVAILABLE")
    result.available["requested_bin_window"] = windows[min(calls, len(windows)) - 1] if windows else initial
    result.available["sdk_calls"] = calls
    return result


def apply_to_pool(pool, sdk_pool=None, window=None, db=None):
    result = enrich_pool(pool, sdk_pool, window)
    # Version the cache key so pre-audit payloads containing the old
    # active_bin_share semantics cannot be replayed as current data.
    cache_key = f"meteora:dlmm:v2:{pool.pool_address}:window:{int(window or config.METEORA_DLMM_BIN_WINDOW)}"
    if db is not None:
        cached = db.get_asset_cache(cache_key)
        if cached:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
                if age <= config.METEORA_DLMM_ENRICHMENT_TTL_SECONDS:
                    payload = json.loads(cached["payload"])
                    result = DlmmEnrichment(payload.get("available", {}), payload.get("unavailable", []), payload.get("warnings", []), payload.get("source", "METEORA_OFFICIAL_SDK"))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        elif result.available:
            db.put_asset_cache(cache_key, {"available": result.available, "unavailable": result.unavailable, "warnings": result.warnings, "source": result.source})
    protocol_data = pool.protocol_data or {}
    protocol_data["dlmm_state"] = {key: value for key, value in result.available.items() if key != "bins"}
    if "liquidity_within_1pct" in result.available:
        protocol_data["liquidity_distribution"] = result.available["liquidity_within_1pct"] / 100
    if "hhi" in result.available:
        protocol_data["liquidity_concentration"] = result.available["hhi"]
    protocol_data["dlmm_distribution"] = result.available.get("bins")
    protocol_data["dlmm_state_source"] = result.source
    pool.protocol_data = protocol_data
    return result
