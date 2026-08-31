"""Pair-level realized volatility for LP risk analysis.

This module deliberately does not estimate IL or position/range outcomes.
It only measures movement of the canonical asset ratio.
"""

import logging
import math
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import config

logger = logging.getLogger(__name__)


class HistoricalPriceProvider:
    def get_price_history(self, canonical_asset, start, end, interval="hourly"):
        raise NotImplementedError


class CoinGeckoHistoricalPriceProvider(HistoricalPriceProvider):
    """CoinGecko market candles, addressed by explicit provider asset ID."""

    def __init__(self, timeout=20):
        self.timeout = timeout

    def get_price_history(self, canonical_asset, start, end, interval="hourly"):
        url = f"{config.VOLATILITY_EXTERNAL_ENDPOINT}/{canonical_asset}/market_chart/range?" + urlencode({"vs_currency": "usd", "from": int(start.timestamp()), "to": int(end.timestamp()), "interval": interval})
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-radar-mvp/1.0"})
        with urlopen(request, timeout=self.timeout) as response:
            if response.status >= 400:
                raise RuntimeError(f"CoinGecko HTTP {response.status}")
            payload = json.loads(response.read().decode("utf-8"))
        return [(item[0], item[1]) for item in payload.get("prices", []) if isinstance(item, list) and len(item) >= 2]


@dataclass
class VolatilityAssessment:
    pair: str
    score: float | None
    coverage_pct: float
    realized_vol_24h: float | None
    realized_vol_7d: float | None
    realized_vol_30d: float | None
    max_drawdown_7d: float | None
    max_price_move_24h: float | None
    max_abs_24h_move_7d: float | None
    price_range_7d: float | None
    observations: int
    source: str
    warnings: list[str]
    breakdown: dict | None = None
    source_stats: dict | None = None
    window: str = "canonical pair price; local snapshots bucketed hourly"
    metric_coverage_pct: float | None = None
    window_coverage_24h_pct: float | None = None


def canonical_pair(token_a, token_b, mint_a=None, mint_b=None):
    """Return a stable economic orientation, mapping only native SOL/WSOL."""
    a = "SOL" if (token_a or "").upper() in {"SOL", "WSOL"} and mint_a == "So11111111111111111111111111111111111111112" else (token_a or "UNKNOWN").upper()
    b = "SOL" if (token_b or "").upper() in {"SOL", "WSOL"} and mint_b == "So11111111111111111111111111111111111111112" else (token_b or "UNKNOWN").upper()
    stable_quotes = {"USDC", "USDT", "PYUSD", "USD"}
    if b in stable_quotes and a not in stable_quotes:
        return f"{a}/{b}"
    if a in stable_quotes and b not in stable_quotes:
        return f"{b}/{a}"
    # Deterministic economic orientation for non-USD pairs; SOL/ETH should
    # not become ETH/SOL merely because of lexical ordering.
    priority = {"SOL": 0, "ETH": 1, "BTC": 2}
    ordered = sorted((a, b), key=lambda value: (priority.get(value, 10), value))
    return "/".join(ordered)


def orient_price(price_a_over_b, token_a, token_b, mint_a=None, mint_b=None):
    if price_a_over_b is None or price_a_over_b <= 0:
        return None
    pair = canonical_pair(token_a, token_b, mint_a, mint_b)
    base, quote = pair.split("/")
    normalized_a = "SOL" if (token_a or "").upper() in {"SOL", "WSOL"} and mint_a == "So11111111111111111111111111111111111111112" else (token_a or "UNKNOWN").upper()
    return float(price_a_over_b) if normalized_a == base else 1.0 / float(price_a_over_b)


def _vol_score(annualized):
    if annualized is None:
        return None
    # Continuous interpolation of the requested risk bands.
    points = [(0.0, 5.0), (0.10, 15.0), (0.30, 35.0), (0.60, 60.0), (1.00, 82.0), (2.00, 100.0)]
    if annualized <= points[0][0]:
        return points[0][1]
    for (low, low_score), (high, high_score) in zip(points, points[1:]):
        if annualized <= high:
            return low_score + (annualized - low) / (high - low) * (high_score - low_score)
    return 100.0


def _move_score(move):
    if move is None:
        return None
    return min(100.0, max(0.0, abs(move) / 0.20 * 50.0))


def _bucketed(rows, since):
    latest_by_hour = {}
    for row in rows:
        try:
            timestamp = datetime.fromisoformat(row["snapshot_time"])
            price = float(row["price_ratio"])
        except (KeyError, TypeError, ValueError):
            continue
        if price <= 0 or timestamp < since:
            continue
        key = timestamp.replace(minute=0, second=0, microsecond=0)
        if key not in latest_by_hour or timestamp > latest_by_hour[key][0]:
            latest_by_hour[key] = (timestamp, price)
    return [item[1] for key, item in sorted(latest_by_hour.items())]


def _window_audit(rows, since, expected_slots=24):
    """Return observable hourly coverage without silently filling gaps."""
    buckets = set()
    for row in rows:
        try:
            timestamp = datetime.fromisoformat(row["snapshot_time"])
            if timestamp >= since and float(row["price_ratio"]) > 0:
                buckets.add(timestamp.replace(minute=0, second=0, microsecond=0))
        except (KeyError, TypeError, ValueError):
            continue
    ordered = sorted(buckets)
    gaps = [(current - previous).total_seconds() / 3600 for previous, current in zip(ordered, ordered[1:])]
    returns = max(0, len(ordered) - 1)
    return {"observations_last_24h": len(ordered), "returns_last_24h": returns, "expected_hourly_slots": expected_slots, "missing_slots": max(0, expected_slots - returns), "largest_gap_hours": max(gaps, default=0.0)}


def _realized_vol(prices, periods_per_year=365 * 24):
    if len(prices) < 2:
        return None
    returns = [math.log(current / previous) for previous, current in zip(prices, prices[1:]) if previous > 0 and current > 0]
    if not returns:
        return None
    average = sum(returns) / len(returns)
    variance = sum((value - average) ** 2 for value in returns) / len(returns)
    return math.sqrt(variance * periods_per_year)


def _max_drawdown(prices):
    if not prices:
        return None
    peak = prices[0]
    drawdowns = []
    for price in prices:
        peak = max(peak, price)
        drawdowns.append(price / peak - 1.0)
    return abs(min(drawdowns)) if drawdowns else None


def assess(db, token_a, token_b, mint_a=None, mint_b=None):
    pair = canonical_pair(token_a, token_b, mint_a, mint_b)
    now = datetime.now(timezone.utc)
    rows = db.pair_price_history(pair, since=(now - timedelta(days=30)).isoformat())
    prices_24h = _bucketed(rows, now - timedelta(hours=24))
    prices_7d = _bucketed(rows, now - timedelta(days=7))
    prices_30d = _bucketed(rows, now - timedelta(days=30))
    min_obs = config.VOLATILITY_MIN_OBSERVATIONS
    vol24 = _realized_vol(prices_24h) if len(prices_24h) - 1 >= min_obs["24h"] else None
    effective_days_7 = len({row["snapshot_time"][:10] for row in rows if row["snapshot_time"] >= (now - timedelta(days=7)).isoformat()})
    effective_days_30 = len({row["snapshot_time"][:10] for row in rows})
    vol7 = _realized_vol(prices_7d) if len(prices_7d) - 1 >= min_obs["7d_returns"] and effective_days_7 >= min_obs["7d_days"] else None
    vol30 = _realized_vol(prices_30d) if len(prices_30d) - 1 >= min_obs["30d_returns"] and effective_days_30 >= min_obs["30d_days"] else None
    drawdown = _max_drawdown(prices_7d) if effective_days_7 >= min_obs["7d_days"] else None
    max_move = max((abs(current / previous - 1.0) for previous, current in zip(prices_24h, prices_24h[1:]) if previous > 0), default=None) if len(prices_24h) >= 2 else None
    price_range = (max(prices_7d) / min(prices_7d) - 1.0) if prices_7d and min(prices_7d) > 0 else None
    max_move_7d = max((abs(prices_7d[index] / prices_7d[index - 24] - 1.0) for index in range(24, len(prices_7d)) if prices_7d[index - 24] > 0), default=None)
    realized = vol7 if vol7 is not None else vol24
    components = {"realized_volatility": _vol_score(realized), "max_drawdown": _move_score(drawdown), "extreme_move": _move_score(max_move)}
    usable = {key: value for key, value in components.items() if value is not None}
    coverage = len(usable) / 3 * 100
    base_weights = {key: config.VOLATILITY_WEIGHTS[key] for key in usable}
    weight_total = sum(base_weights.values())
    effective_weights = {key: value / weight_total for key, value in base_weights.items()} if weight_total else {}
    raw_metrics = {"realized_volatility": realized, "max_drawdown": drawdown, "extreme_move": max_move}
    breakdown = {
        key: {"raw_metric": raw_metrics[key], "score": usable[key], "effective_weight": effective_weights[key], "weighted_contribution": usable[key] * effective_weights[key]}
        for key in usable
    }
    score = sum(item["weighted_contribution"] for item in breakdown.values()) if realized is not None and coverage >= config.RISK_MIN_COVERAGE_PCT else None
    warnings = []
    if vol24 is None:
        warnings.append(f"24h realized volatility unavailable: {len(prices_24h) - 1 if len(prices_24h) else 0} valid returns; minimum required {min_obs['24h']}")
    if not vol7:
        warnings.append("insufficient effective days for 7d metrics")
    if not vol30:
        warnings.append("insufficient effective days for 30d metrics")
    dispersions = [float(row["dispersion_pct"]) for row in rows if row["dispersion_pct"] is not None]
    if dispersions and max(dispersions) > config.VOLATILITY_MAX_DISPERSION_WARNING_PCT:
        warnings.append(f"cross-protocol price dispersion reached {max(dispersions):.2f}%")
    sources = {row["source"] for row in rows}
    source = "HYBRID" if {"LOCAL", "EXTERNAL"}.issubset(sources) else ("EXTERNAL" if "EXTERNAL" in sources else "LOCAL")
    date_range = {"first": rows[0]["snapshot_time"] if rows else None, "last": rows[-1]["snapshot_time"] if rows else None}
    if dispersions:
        latest_dispersion = dispersions[-1]
        if latest_dispersion < 0.5:
            dispersion_status = "NORMAL"
        elif latest_dispersion < 1.0:
            dispersion_status = "ELEVATED"
        elif latest_dispersion < 2.0:
            dispersion_status = "HIGH"
        else:
            dispersion_status = "EXTREME"
        warnings.append(f"latest cross-protocol dispersion: {latest_dispersion:.2f}% ({dispersion_status})")
    quality_warnings = {row["quality_warning"] for row in rows if row["quality_warning"]}
    warnings.extend(sorted(quality_warnings))
    source_stats = {"external_observations": sum(1 for row in rows if row["source"] == "EXTERNAL"), "local_observations": sum(1 for row in rows if row["source"] == "LOCAL"), "overlap_timestamps": sum(1 for row in rows if row["source"] == "HYBRID")}
    source_stats["last_24h_audit"] = _window_audit(rows, now - timedelta(hours=24))
    external_start = now - timedelta(days=30)
    source_stats["external_request"] = {
        "endpoint": config.VOLATILITY_EXTERNAL_ENDPOINT,
        "interval": "hourly",
        "from_utc": external_start.isoformat(timespec="seconds"),
        "to_utc": now.isoformat(timespec="seconds"),
        "from_unix": int(external_start.timestamp()),
        "to_unix": int(now.timestamp()),
        "cache_ttl_seconds": config.VOLATILITY_CACHE_TTL_SECONDS["historical_price_series"],
        "timestamp_bucket": "UTC hour, floor to minute/second 00",
    }
    raw_ranges = db.pair_price_source_ranges(pair)
    for source_name, key in (("EXTERNAL", "external_range"), ("LOCAL", "local_range")):
        source_stats[key] = raw_ranges.get(source_name, {"first": None, "last": None, "observations": 0})
    window_coverage = min(100.0, source_stats["last_24h_audit"]["returns_last_24h"] / source_stats["last_24h_audit"]["expected_hourly_slots"] * 100) if source_stats["last_24h_audit"]["expected_hourly_slots"] else 0.0
    result = VolatilityAssessment(
        pair, round(score, 2) if score is not None else None, round(coverage, 2),
        vol24, vol7, vol30, drawdown, max_move, max_move_7d, price_range,
        len(rows), source, warnings, breakdown if breakdown else None, source_stats,
        metric_coverage_pct=round(coverage, 2),
        window_coverage_24h_pct=round(window_coverage, 2),
    )
    result.window = f"canonical pair; hourly log returns; {date_range['first'] or 'N/A'} to {date_range['last'] or 'N/A'}"
    return result


def _external_points(db, pair, mint_a, mint_b, token_a, token_b):
    """Bootstrap an explicit pair from aligned external USD histories."""
    normalized_a = "SOL" if (token_a or "").upper() in {"SOL", "WSOL"} and mint_a == "So11111111111111111111111111111111111111112" else (token_a or "UNKNOWN").upper()
    normalized_b = "SOL" if (token_b or "").upper() in {"SOL", "WSOL"} and mint_b == "So11111111111111111111111111111111111111112" else (token_b or "UNKNOWN").upper()
    mint_by_asset = {normalized_a: mint_a, normalized_b: mint_b}
    base, quote = pair.split("/")
    ordered_mints = [mint_by_asset.get(base), mint_by_asset.get(quote)]
    ids = []
    for mint in ordered_mints:
        provider_id = config.VOLATILITY_EXTERNAL_ASSET_IDS.get(mint)
        if not provider_id:
            return []
        ids.append(provider_id)
    cache_key = f"volatility:external:{pair}"
    cached = db.get_asset_cache(cache_key)
    if cached:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached["fetched_at"])).total_seconds()
            if age <= config.VOLATILITY_CACHE_TTL_SECONDS["historical_price_series"]:
                return json.loads(cached["payload"])
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    provider = CoinGeckoHistoricalPriceProvider()
    start, end = datetime.now(timezone.utc) - timedelta(days=30), datetime.now(timezone.utc)
    try:
        left = provider.get_price_history(ids[0], start, end)
        right = provider.get_price_history(ids[1], start, end)
        logger.info("External volatility request pair=%s endpoint=%s start_utc=%s end_utc=%s interval=hourly left_points=%d right_points=%d", pair, config.VOLATILITY_EXTERNAL_ENDPOINT, start.isoformat(), end.isoformat(), len(left), len(right))
        if quote in {"USDC", "USDT", "PYUSD", "USD"}:
            right = [(timestamp, 1.0) for timestamp, _ in left]
        right_by_hour = {int(timestamp // 3_600_000): price for timestamp, price in right if price and price > 0}
        points = [(timestamp, price / right_by_hour[int(timestamp // 3_600_000)]) for timestamp, price in left if int(timestamp // 3_600_000) in right_by_hour and price and price > 0]
        db.put_asset_cache(cache_key, points)
        logger.info("External volatility aligned pair=%s points=%d first=%s last=%s; provider timestamps are floored to UTC hour without interpolation", pair, len(points), datetime.fromtimestamp(points[0][0] / 1000, timezone.utc).isoformat() if points else None, datetime.fromtimestamp(points[-1][0] / 1000, timezone.utc).isoformat() if points else None)
        return points
    except Exception as exc:
        logger.warning("External volatility history unavailable for %s: %s", pair, exc)
        return []


def record_pool_price(db, pool):
    """Persist one raw source candidate; aggregation is pair/hour level."""
    protocol_data = pool.protocol_data or {}
    raw_price = protocol_data.get("current_price")
    price_ratio = orient_price(raw_price, pool.token_a, pool.token_b, pool.token_a_mint, pool.token_b_mint)
    pair = canonical_pair(pool.token_a, pool.token_b, pool.token_a_mint, pool.token_b_mint)
    if price_ratio is not None:
        db.insert_pair_price_snapshot(pair, pool.token_a_mint, pool.token_b_mint, price_ratio, "LOCAL", pool.protocol, pool.pool_address)
    if len(db.pair_price_history(pair, since=(datetime.now(timezone.utc) - timedelta(days=30)).isoformat())) < config.VOLATILITY_MIN_OBSERVATIONS["30d_returns"]:
        external = _external_points(db, pair, pool.token_a_mint, pool.token_b_mint, pool.token_a, pool.token_b)
        if external:
            db.insert_external_pair_prices(pair, external)


def update_pool(db, pool):
    """Attach the same pair-level assessment after all source candidates exist."""
    record_pool_price(db, pool)
    result = assess(db, pool.token_a, pool.token_b, pool.token_a_mint, pool.token_b_mint)
    pool.volatility_risk = result.score
    pool.volatility_risk_details = result.__dict__
    if pool.risk_components is not None:
        pool.risk_components["volatility_risk"] = {"score": result.score, "coverage": result.score is not None, "source": result.source, "reason": "; ".join(result.warnings) or "pair-level realized volatility"}
    return result
