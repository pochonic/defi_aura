"""Small provider runtime: retries, health and last-known-good fallback."""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError

import config
from raydium import Pool, ScanResult

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    protocol: str
    status: str = "UNAVAILABLE"
    last_success: str | None = None
    last_attempt: str | None = None
    response_time_ms: float | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    poll_interval_seconds: int = config.POLL_INTERVAL_SECONDS


HEALTH: dict[str, ProviderHealth] = {}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _retryable(exc):
    if isinstance(exc, HTTPError):
        return exc.code >= 500
    if isinstance(exc, (TimeoutError, ConnectionError, URLError)):
        return True
    message = str(exc).lower()
    return any(value in message for value in ("timed out", "timeout", "connection reset", "connection aborted", "temporarily unavailable"))


def fetch_with_retry(protocol, client, page_size, max_pages, db):
    health = HEALTH.setdefault(protocol, ProviderHealth(protocol))
    retry = config.PROVIDER_RETRY_CONFIG
    timeout = config.PROVIDER_TIMEOUTS.get(protocol, {})
    if hasattr(client, "timeout"):
        client.timeout = timeout.get("read_timeout", config.REQUEST_TIMEOUT_SECONDS)
    attempts = int(retry["max_attempts"])
    last_exc = None
    for attempt in range(1, attempts + 1):
        health.last_attempt = _now()
        started = time.perf_counter()
        logger.info("%s provider attempt %d/%d (connect_timeout=%ss read_timeout=%ss)", protocol, attempt, attempts, timeout.get("connect_timeout"), timeout.get("read_timeout"))
        try:
            raw, pages = client.fetch_pools(page_size, max_pages)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            health.status = "LIVE_EMPTY_RESPONSE" if not raw else "LIVE"
            health.last_success = _now()
            health.response_time_ms = elapsed
            health.consecutive_failures = 0
            health.last_error = None
            health.poll_interval_seconds = config.POLL_INTERVAL_SECONDS
            db.audit(protocol, client.endpoint, {"pages": pages, "pool_count": len(raw), "response_time_ms": elapsed, "http_status": 200, "attempt": attempt, "success": True})
            return raw, pages, health
        except Exception as exc:
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            last_exc = exc
            retryable = _retryable(exc)
            db.audit(protocol, client.endpoint, {"response_time_ms": elapsed, "http_status": getattr(exc, "code", None), "attempt": attempt, "success": False}, ok=False, error=str(exc))
            logger.warning("%s provider attempt %d failed after %.0f ms: %s (retryable=%s)", protocol, attempt, elapsed, exc, retryable)
            if not retryable or attempt >= attempts:
                break
            time.sleep(float(retry["initial_delay_seconds"]) * float(retry["backoff_multiplier"]) ** (attempt - 1))
    health.consecutive_failures += 1
    health.last_error = str(last_exc)
    health.status = "DEGRADED" if health.consecutive_failures >= config.CIRCUIT_BREAKER_FAILURES else "UNAVAILABLE"
    health.poll_interval_seconds = config.POLL_INTERVAL_SECONDS * 2 if health.status == "DEGRADED" else config.POLL_INTERVAL_SECONDS
    return None, 0, health


def _age_seconds(snapshot_time):
    value = datetime.fromisoformat(snapshot_time)
    return max(0.0, (datetime.now(timezone.utc) - value).total_seconds())


def stale_state(age_seconds):
    if age_seconds < config.STALE_THRESHOLDS_SECONDS["recent"]:
        return "STALE_RECENT"
    if age_seconds <= config.STALE_THRESHOLDS_SECONDS["stale"]:
        return "STALE"
    return "UNAVAILABLE"


def fallback_report(db, protocol, normalizer=None):
    rows = db.latest_snapshots_for_protocol(protocol)
    pools = []
    last_state = "UNAVAILABLE"
    for row in rows:
        age = _age_seconds(row["snapshot_time"])
        state = stale_state(age)
        last_state = state
        if state == "UNAVAILABLE":
            continue
        breakdown = __import__("json").loads(row["score_breakdown"] or "{}")
        pool = Pool(
            pool_address=row["pool_address"], token_a=row["token_a"], token_b=row["token_b"], pool_type=row["pool_type"],
            fee_tier=row["fee_tier"], tvl_usd=row["tvl_usd"], volume_24h=row["volume_24h"], volume_7d=row["volume_7d"],
            reported_apr=row["reported_apr"], reward_apr=row["reward_apr"], fees_24h_usd=row["fees_24h_usd"],
            nominal_fee_apr=row["nominal_fee_apr"], orca_reported_fees_apr=row["orca_reported_fees_apr"],
            fee_model=row["fee_model"] or "UNKNOWN", protocol=protocol, calculated_fee_apr=row["calculated_fee_apr"], trend=row["opportunity_trend"] or "N/A",
            volume_tvl_ratio=row["volume_tvl_ratio"], opportunity_score=row["opportunity_score"] or row["score"] or 0,
            risk_score=row["risk_score"], status=row["status"] or "INSUFFICIENT_HISTORY",
            score_breakdown=breakdown.get("components", {}), effective_weights=breakdown.get("effective_weights", {}),
            risk_components=breakdown.get("risk_components"), risk_data_coverage=breakdown.get("risk_data_coverage", 0.0),
            asset_risk=breakdown.get("asset_risk"), asset_risk_coverage=breakdown.get("asset_risk_coverage", 0.0),
            asset_risk_details=breakdown.get("asset_risk_details"),
            volatility_risk=breakdown.get("volatility_risk"), volatility_risk_details=breakdown.get("volatility_risk_details"),
            liquidity_structure_risk=breakdown.get("liquidity_structure_risk"), liquidity_structure_risk_details=breakdown.get("liquidity_structure_risk_details"),
            data_state=state, data_age_seconds=age,
        )
        pools.append(pool)
    return ScanResult(sorted(pools, key=lambda item: item.opportunity_score, reverse=True), len(pools), 0, 0, protocol_status=last_state if pools else "UNAVAILABLE")
