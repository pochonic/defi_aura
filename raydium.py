import json
import logging
import math
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config
from database import Database, utc_now

logger = logging.getLogger(__name__)


def number(value):
    return float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else None


def audit_number(value):
    """Parse provider JSON scalars for completeness auditing only."""
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def reward_sum(value):
    if not isinstance(value, list) or not value:
        return None
    values = [number(x) for x in value]
    return sum(values) if all(x is not None for x in values) else None


def pool_type(raw):
    values = raw.get("pooltype") or []
    if not values and raw.get("type"):
        values = [raw["type"]]
    names = {"Clmm": "CLMM", "Amm": "AMM", "Cpmm": "CPMM"}
    return "/".join(names.get(str(value), str(value)) for value in values) or "N/A"


@dataclass
class Pool:
    pool_address: str
    token_a: str
    token_b: str
    pool_type: str
    fee_tier: float | None
    tvl_usd: float | None
    volume_24h: float | None
    volume_7d: float | None
    reported_apr: float | None
    reward_apr: float | None
    meteora_api_apr: float | None = None
    fees_24h_usd: float | None = None
    nominal_fee_apr: float | None = None
    orca_reported_fees_apr: float | None = None
    reported_fees_apr: float | None = None
    expected_fees_from_nominal_rate: float | None = None
    fee_difference_usd: float | None = None
    fee_difference_pct: float | None = None
    fee_window_comparability: str = "UNKNOWN"
    yield_over_tvl: float | None = None
    fee_model: str = "UNKNOWN"
    reward_known: bool = False
    token_a_mint: str | None = None
    token_b_mint: str | None = None
    protocol: str = "Raydium"
    calculated_fee_apr: float | None = None
    volume_tvl_ratio: float | None = None
    opportunity_score: float = 0.0
    risk_score: float | None = None
    risk_components: dict[str, Any] | None = None
    risk_data_coverage: float = 0.0
    volatility_risk: float | None = None
    volatility_risk_details: dict[str, Any] | None = None
    liquidity_structure_risk: float | None = None
    liquidity_structure_risk_details: dict[str, Any] | None = None
    asset_risk: float | None = None
    asset_risk_coverage: float = 0.0
    asset_risk_details: dict[str, Any] | None = None
    score_breakdown: dict[str, float] | None = None
    effective_weights: dict[str, float] | None = None
    status: str = "INSUFFICIENT_HISTORY"
    history_stats: dict[str, Any] | None = None
    clmm_data: dict[str, Any] | None = None
    protocol_data: dict[str, Any] | None = None
    trend: str = "N/A"
    changes: dict[str, float | None] | None = None
    data_state: str = "LIVE"
    data_age_seconds: float = 0.0

    @property
    def score(self):
        """Compatibility accessor; new code should use opportunity_score."""
        return self.opportunity_score

    @property
    def pair(self):
        return f"{self.token_a} / {self.token_b}"


@dataclass
class ScanResult:
    pools: list[Pool]
    analyzed: int
    discarded_tvl: int
    incomplete: int
    discarded_volume: int = 0
    discarded_ratio: int = 0
    discarded_fee: int = 0
    protocol_status: str = "OK"
    required_incomplete: int = 0
    incomplete_by_field: dict[str, int] | None = None
    optional_missing: dict[str, int] | None = None
    potential_candidates_incomplete: int = 0
    incomplete_below_tvl: int = 0
    filter_audits: list[dict[str, Any]] | None = None
    dropped_candidates: list[dict[str, Any]] | None = None
    pipeline_counts: dict[str, int] | None = None


class RaydiumClient:
    def __init__(self, session=None, timeout=None):
        self.session = session
        self.timeout = timeout or config.PROVIDER_TIMEOUTS["Raydium"]["read_timeout"]
        self.endpoint = config.API_BASE_URL + config.RAYDIUM_POOLS_ENDPOINT

    def fetch_pools(self, page_size=config.PAGE_SIZE, max_pages=config.MAX_PAGES):
        pools, pages = [], 0
        for page in range(1, max_pages + 1):
            params = {"poolType": "all", "poolSortField": "liquidity", "sortType": "desc", "pageSize": page_size, "page": page}
            url = self.endpoint + "?" + urlencode(params)
            if self.session is not None:
                response = self.session.get(self.endpoint, params=params, timeout=(config.PROVIDER_TIMEOUTS["Raydium"]["connect_timeout"], self.timeout))
                response.raise_for_status()
                payload = response.json()
            else:
                request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-radar-mvp/1.0"})
                with urlopen(request, timeout=self.timeout) as response:
                    if response.status >= 400:
                        raise RuntimeError(f"Raydium HTTP {response.status}")
                    payload = json.loads(response.read().decode("utf-8"))
            if payload.get("success") is not True:
                raise RuntimeError(payload.get("msg", "Raydium API returned failure"))
            data = payload.get("data") or {}
            batch = data.get("data") or []
            pools.extend(batch)
            pages += 1
            if not data.get("hasNextPage") or not batch:
                break
        return pools, pages


def normalize(raw: dict[str, Any]) -> Pool | None:
    a, b = raw.get("mintA") or {}, raw.get("mintB") or {}
    token_a = a.get("symbol").strip() if isinstance(a.get("symbol"), str) else None
    token_b = b.get("symbol").strip() if isinstance(b.get("symbol"), str) else None
    if not token_a or not token_b or not (token_a in config.ALLOWED_TOKENS or token_b in config.ALLOWED_TOKENS):
        return None
    day, week = raw.get("day") or {}, raw.get("week") or {}
    rewards = raw.get("day", {}).get("rewardApr") if isinstance(raw.get("day"), dict) else None
    reward_known = isinstance(rewards, list)
    reward_value = reward_sum(rewards)
    if reward_known and reward_value is None:
        reward_value = 0.0
    pool = Pool(raw.get("id", ""), token_a, token_b, pool_type(raw), number(raw.get("feeRate")), number(raw.get("tvl")),
                number(day.get("volume")), number(week.get("volume")), number(day.get("apr")),
                reward_apr=reward_value, fees_24h_usd=number(day.get("volumeFee")),
                token_a_mint=a.get("address"), token_b_mint=b.get("address"),
                fee_model="ADAPTIVE" if raw.get("hasDynamicFee") is True else ("FIXED" if raw.get("hasDynamicFee") is False else "UNKNOWN"),
                reward_known=reward_known)
    pool.protocol_data = {"current_price": number(raw.get("price")), "source": "Raydium pool response", "price_orientation": "token_a/token_b if exposed"}
    return pool


def calculate_metrics(pool: Pool):
    if pool.volume_24h is not None and pool.fee_tier is not None and pool.tvl_usd and pool.tvl_usd > 0:
        # Pool Fee APR is pool-level fee generation, not the return of a specific
        # CLMM position; active range, liquidity and time in-range affect that return.
        pool.nominal_fee_apr = pool.volume_24h * pool.fee_tier * 365 / pool.tvl_usd * 100
    if pool.fees_24h_usd is not None and pool.tvl_usd and pool.tvl_usd > 0:
        fee_apr = pool.fees_24h_usd / pool.tvl_usd * 365 * 100
        pool.orca_reported_fees_apr = fee_apr if pool.protocol == "Orca" else None
        pool.reported_fees_apr = fee_apr
    if pool.volume_24h is not None and pool.fee_tier is not None:
        pool.expected_fees_from_nominal_rate = pool.volume_24h * pool.fee_tier
        if pool.fees_24h_usd is not None:
            pool.fee_difference_usd = pool.fees_24h_usd - pool.expected_fees_from_nominal_rate
            if pool.expected_fees_from_nominal_rate > 0:
                pool.fee_difference_pct = pool.fees_24h_usd / pool.expected_fees_from_nominal_rate - 1
    # Orca's fees field is retained as an audit metric until its time-window
    # and LP-fee semantics are independently verified. Its opportunity score
    # therefore uses the nominal rate; Raydium keeps the historical reported
    # fee behavior only when such a value is explicitly available upstream.
    # Meteora publishes a 24h pool-fee amount, so this is the defensible
    # scoring metric. It is not inferred from volume × base fee because DLMM
    # dynamic fees can differ from the base rate.
    pool.calculated_fee_apr = pool.reported_fees_apr if pool.protocol == "Meteora" and pool.reported_fees_apr is not None else pool.nominal_fee_apr
    if pool.volume_24h is not None and pool.tvl_usd and pool.tvl_usd > 0:
        pool.volume_tvl_ratio = pool.volume_24h / pool.tvl_usd
    prepare_risk_data(pool)


def prepare_risk_data(pool: Pool):
    """Create risk evidence slots without assigning subjective risk points."""
    if pool.risk_components is not None:
        return
    from services.risk_engine import assess
    assessment = assess(pool)
    pool.risk_components = assessment.components
    pool.risk_data_coverage = assessment.coverage_pct
    pool.risk_score = assessment.risk_score


def interpolated_tvl_score(value):
    """Continuous TVL score matching the requested liquidity bands."""
    if value is None or value <= 0:
        return 0.0
    points = [(0, 0), (500_000, 10), (1_000_000, 25), (3_000_000, 45), (5_000_000, 60),
              (10_000_000, 75), (25_000_000, 90), (100_000_000, 100)]
    for (low_value, low_score), (high_value, high_score) in zip(points, points[1:]):
        if value <= high_value:
            return low_score + (value - low_value) / (high_value - low_value) * (high_score - low_score)
    return 100.0


def component(value, cap):
    return max(0.0, min(100.0, value / cap * 100.0)) if value is not None else None


def score_pool(pool: Pool, avg_24h=None, avg_7d=None, history_count=0, history_duration_hours=0):
    fee_score = component(pool.calculated_fee_apr, config.SCORE_CAPS["fee_apr"])
    volume_score = component(pool.volume_tvl_ratio, config.SCORE_CAPS["volume_tvl"])
    tvl_score = interpolated_tvl_score(pool.tvl_usd)
    # Organic yield combines fee share with absolute fee generation. A tiny
    # fee APR cannot score highly just because its reward APR is absent.
    total_apr = pool.reported_apr if pool.reported_apr is not None else ((pool.calculated_fee_apr or 0) + (pool.reward_apr or 0))
    fee_share = pool.calculated_fee_apr / total_apr if pool.reward_known and total_apr and pool.calculated_fee_apr is not None else None
    absolute_fee_score = component(pool.calculated_fee_apr, config.SCORE_CAPS["fee_apr"])
    organic_score = math.sqrt(max(0.0, min(1.0, fee_share)) * absolute_fee_score * 100) if fee_share is not None and absolute_fee_score is not None else None

    enough_history = history_count >= config.MIN_PERSISTENCE_SNAPSHOTS and history_duration_hours >= 24 and avg_24h is not None
    if enough_history:
        use_7d = history_duration_hours >= 24 * 7 and avg_7d is not None
        reference_avg = avg_7d if use_7d else avg_24h
        ratio_reference = (pool.calculated_fee_apr or 0) / max(reference_avg, 0.01)
        persistence_score = max(0.0, min(100.0, ratio_reference * 70.0))
        if pool.calculated_fee_apr < reference_avg * .85:
            pool.status = "FADING"
        elif pool.calculated_fee_apr > reference_avg * 1.15:
            pool.status = "RISING"
        elif history_duration_hours >= 24 * 7:
            pool.status = "PERSISTENT_7D"
        else:
            pool.status = "PERSISTENT_24H"
    else:
        persistence_score = None
        pool.status = "OBSERVING" if history_count >= config.MIN_PERSISTENCE_SNAPSHOTS else "INSUFFICIENT_HISTORY"

    available = {"fee_apr": fee_score, "volume_tvl": volume_score, "tvl": tvl_score, "persistence": persistence_score, "organic_yield": organic_score}
    base_weights = {"fee_apr": .30, "volume_tvl": .25, "tvl": .20, "persistence": .15, "organic_yield": .10}
    usable = {key: value for key, value in available.items() if value is not None}
    total_weight = sum(base_weights[key] for key in usable)
    pool.effective_weights = {key: round(base_weights[key] / total_weight, 4) for key in usable}
    pool.score_breakdown = {key: round(value, 2) for key, value in available.items() if value is not None}
    pool.opportunity_score = round(sum(pool.score_breakdown[key] * pool.effective_weights[key] for key in usable), 2) if usable else 0.0


def percent_change(current, previous):
    if current is None or previous in (None, 0):
        return None
    return (current - previous) / abs(previous) * 100


def calculate_trend(pool: Pool, previous, history_stats):
    if not previous or history_stats.get("snapshot_count", 0) < config.MIN_PERSISTENCE_SNAPSHOTS or history_stats.get("duration_hours", 0) < 24:
        pool.trend = "N/A"
        pool.changes = {"apr_change": None, "volume_tvl_change": None, "tvl_change": None}
        return
    pool.changes = {
        "apr_change": percent_change(pool.calculated_fee_apr, previous["calculated_fee_apr"]),
        "volume_tvl_change": percent_change(pool.volume_tvl_ratio, previous["volume_tvl_ratio"]),
        "tvl_change": percent_change(pool.tvl_usd, previous["tvl_usd"]),
    }
    previous_score = previous["opportunity_score"] or previous["score"]
    score_change = percent_change(pool.opportunity_score, previous_score)
    if score_change is None:
        pool.trend = "N/A"
    elif score_change > 5:
        pool.trend = "RISING"
    elif score_change < -5:
        pool.trend = "FADING"
    else:
        pool.trend = "STABLE"


def mean(rows):
    values = [r["apr"] for r in rows if r["apr"] is not None]
    return sum(values) / len(values) if values else None


def scan_source(db: Database, raw, endpoint, normalizer=normalize, protocol="Raydium", scan_id=None):
    """Apply one shared filter/metric/score pipeline to any LP provider."""
    raw = list(raw or [])
    scan_id = scan_id or utc_now()
    results, discarded_tvl, discarded_volume, discarded_ratio, discarded_fee, incomplete = [], 0, 0, 0, 0, 0
    potential_candidates_incomplete = 0
    incomplete_below_tvl = 0
    missing_required = {"tvl": 0, "volume": 0, "fee data": 0, "token metadata": 0, "address": 0}
    missing_optional = {"reported_apr": 0, "reward_apr": 0}
    filter_audits = []
    dropped_candidates = []
    seen_addresses = set()
    allowed_token_count = 0
    normalized_count = 0
    previous = {}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    for row in db.latest_snapshots_for_protocol(protocol):
        try:
            if datetime.fromisoformat(row["snapshot_time"]) >= cutoff:
                previous[row["pool_address"]] = row
        except (KeyError, TypeError, ValueError):
                continue
    previous_rank = {
        row["pool_address"]: index
        for index, row in enumerate(sorted(previous.values(), key=lambda item: item["opportunity_score"] or item["score"] or 0, reverse=True), 1)
    }
    for item in raw:
        # Count raw provider completeness before token normalization. This
        # makes the audit explain the provider universe, not only survivors.
        if protocol == "Orca":
            raw_tvl = audit_number(item.get("tvlUsdc"))
            raw_day = (item.get("stats") or {}).get("24h") or {}
            raw_volume = audit_number(raw_day.get("volume"))
            raw_fee = audit_number(item.get("feeRate"))
            raw_address = item.get("address")
            raw_tokens = item.get("tokenA"), item.get("tokenB")
            raw_token_metadata_ok = all(isinstance(token, dict) and token.get("symbol") for token in raw_tokens) and bool(item.get("tokenMintA")) and bool(item.get("tokenMintB"))
        elif protocol == "Raydium":
            raw_tvl = audit_number(item.get("tvl"))
            raw_day = item.get("day") or {}
            raw_volume = audit_number(raw_day.get("volume"))
            raw_fee = audit_number(item.get("feeRate"))
            raw_address = item.get("id")
            raw_tokens = item.get("mintA"), item.get("mintB")
            raw_token_metadata_ok = all(isinstance(token, dict) and token.get("symbol") and token.get("address") for token in raw_tokens)
        else:
            raw_tvl = audit_number(item.get("tvl"))
            raw_volume = audit_number((item.get("volume") or {}).get("24h"))
            raw_fee = audit_number((item.get("pool_config") or {}).get("base_fee_pct"))
            raw_address = item.get("address")
            raw_tokens = item.get("token_x"), item.get("token_y")
            raw_token_metadata_ok = all(isinstance(token, dict) and token.get("symbol") and token.get("address") for token in raw_tokens)
        symbols = [token.get("symbol") if isinstance(token, dict) else None for token in raw_tokens]
        if any(symbol in config.ALLOWED_TOKENS for symbol in symbols):
            allowed_token_count += 1
        raw_missing = set()
        if raw_tvl is None: raw_missing.add("tvl")
        if raw_volume is None: raw_missing.add("volume")
        if raw_fee is None: raw_missing.add("fee data")
        if not raw_token_metadata_ok:
            raw_missing.add("token metadata")
        if not raw_address: raw_missing.add("address")
        for field in raw_missing: missing_required[field] += 1
        if raw_missing:
            incomplete += 1
            if raw_tvl is not None and raw_tvl < config.LP_FILTERS["min_tvl_usd"]:
                incomplete_below_tvl += 1
            elif raw_address and all(isinstance(token, dict) and token.get("symbol") for token in raw_tokens):
                potential_candidates_incomplete += 1
        pool = normalizer(item)
        if not pool or not pool.pool_address:
            continue
        normalized_count += 1
        seen_addresses.add(pool.pool_address)
        calculate_metrics(pool)
        pool.data_state = "LIVE"
        pool.data_age_seconds = 0.0
        # reported_apr is informational and may legitimately be absent (e.g. Orca);
        # incomplete means a field required by the common eligibility pipeline is missing.
        missing = set()
        if pool.tvl_usd is None: missing.add("tvl")
        if pool.volume_24h is None: missing.add("volume")
        if pool.calculated_fee_apr is None: missing.add("fee data")
        if not pool.token_a_mint or not pool.token_b_mint: missing.add("token metadata")
        if not pool.pool_address: missing.add("address")
        if pool.reported_apr is None: missing_optional["reported_apr"] += 1
        if not pool.reward_known: missing_optional["reward_apr"] += 1
        checks = {
            "tvl": pool.tvl_usd is not None and pool.tvl_usd >= config.LP_FILTERS["min_tvl_usd"],
            "volume": pool.volume_24h is not None and pool.volume_24h >= config.LP_FILTERS["min_volume_24h_usd"],
            "volume_tvl": pool.volume_tvl_ratio is not None and pool.volume_tvl_ratio >= config.LP_FILTERS["min_volume_tvl_ratio"],
            "fee_apr": pool.calculated_fee_apr is not None and pool.calculated_fee_apr >= config.LP_FILTERS["min_pool_fee_apr"],
        }
        failed_filter = next((name for name, passed in checks.items() if not passed), None)
        audit = {"pool": pool.pool_address, "pair": pool.pair, "protocol": protocol, "tvl_usd": pool.tvl_usd, "volume_24h_usd": pool.volume_24h, "volume_tvl": pool.volume_tvl_ratio, "fee_apr": pool.calculated_fee_apr, "thresholds": config.LP_FILTERS.copy(), "checks": checks, "reason": failed_filter}
        if pool.pool_address == "5rCf1DM8LjKTw4YqhnoLcngyZYeNnQqztScTogYHAS6":
            filter_audits.append(audit)
            logger.info("Meteora SOL/USDC filter audit: %s", audit)
        def record_drop(reason):
            prior = previous.get(pool.pool_address)
            if prior:
                dropped_candidates.append({
                    "pool": pool.pool_address,
                    "pair": pool.pair,
                    "protocol": protocol,
                    "previous_rank": previous_rank.get(pool.pool_address),
                    "previous_opp": prior["opportunity_score"] or prior["score"],
                    "previous_apr": prior["calculated_fee_apr"],
                    "current_metrics": {"tvl_usd": pool.tvl_usd, "volume_24h_usd": pool.volume_24h, "volume_tvl": pool.volume_tvl_ratio, "fee_apr": pool.calculated_fee_apr},
                    "drop_reason": reason,
                })
        if not checks["tvl"]:
            record_drop("TVL below minimum" if pool.tvl_usd is not None else "TVL unavailable")
            discarded_tvl += 1
            logger.debug("Filtered %s (%s): TVL", pool.pool_address, pool.tvl_usd)
            continue
        if not checks["volume"]:
            record_drop("Volume 24h below minimum" if pool.volume_24h is not None else "Volume 24h unavailable")
            discarded_volume += 1
            logger.debug("Filtered %s (%s): volume_24h", pool.pool_address, pool.volume_24h)
            continue
        if not checks["volume_tvl"]:
            record_drop("Volume/TVL below minimum" if pool.volume_tvl_ratio is not None else "Volume/TVL unavailable")
            discarded_ratio += 1
            logger.debug("Filtered %s (%s): volume_tvl_ratio", pool.pool_address, pool.volume_tvl_ratio)
            continue
        if not checks["fee_apr"]:
            record_drop("Pool Fee APR below minimum" if pool.calculated_fee_apr is not None else "Pool Fee APR unavailable")
            discarded_fee += 1
            logger.debug("Filtered %s (%s): pool_fee_apr", pool.pool_address, pool.calculated_fee_apr)
            continue
        from services.volatility_risk import record_pool_price
        try:
            record_pool_price(db, pool)
        except Exception as exc:
            logger.warning("Volatility risk unavailable for %s: %s", pool.pool_address, exc)
        try:
            from services.liquidity_structure_risk import update_pool as update_structure_risk
            if protocol == "Meteora":
                from services.meteora_dlmm import apply_to_pool
                sdk_pool = config.METEORA_DLMM_SDK_FACTORY(pool) if callable(config.METEORA_DLMM_SDK_FACTORY) else None
                enrichment = apply_to_pool(pool, sdk_pool=sdk_pool, db=db)
                if enrichment.available or enrichment.unavailable:
                    db.audit("Meteora DLMM state", endpoint, {"pool_address": pool.pool_address, "available": enrichment.available, "unavailable": enrichment.unavailable, "warnings": enrichment.warnings}, ok=not enrichment.unavailable, error="; ".join(enrichment.unavailable) if enrichment.unavailable else None)
            update_structure_risk(pool)
        except Exception as exc:
            logger.warning("Liquidity structure risk unavailable for %s: %s", pool.pool_address, exc)
        if protocol in {"Raydium", "Orca", "Meteora"}:
            try:
                from services.asset_risk import assess_pool_assets
                assess_pool_assets(db, pool)
            except Exception as exc:
                logger.warning("Asset risk unavailable for %s: %s", pool.pool_address, exc)
        if protocol in {"Orca", "Meteora"}:
            db.audit(f"{protocol} raw qualifying", endpoint, item)
        local_stats = db.history_stats(pool.pool_address, protocol)
        pool.history_stats = local_stats
        avg24 = mean(db.history(pool.pool_address, 24, protocol))
        avg7 = mean(db.history(pool.pool_address, 24 * 7, protocol))
        count = local_stats["snapshot_count"]
        score_pool(pool, avg24, avg7, count, local_stats["duration_hours"])
        calculate_trend(pool, db.latest_snapshot(pool.pool_address, protocol), local_stats)
        snapshot = (utc_now(), protocol, pool.pool_address, pool.pool_type, pool.token_a, pool.token_b, pool.fee_tier,
                    pool.fees_24h_usd, pool.nominal_fee_apr, pool.orca_reported_fees_apr,
                    pool.expected_fees_from_nominal_rate, pool.fee_difference_usd, pool.fee_difference_pct,
                    pool.fee_window_comparability, pool.yield_over_tvl, pool.fee_model, int(pool.reward_known),
                    pool.tvl_usd, pool.volume_24h, pool.volume_7d, pool.reported_apr, pool.reward_apr,
                    pool.calculated_fee_apr, pool.volume_tvl_ratio, pool.opportunity_score, pool.opportunity_score, pool.trend, pool.risk_score,
                    scan_id, json.dumps({"components": pool.score_breakdown, "effective_weights": pool.effective_weights,
                                        "risk_components": pool.risk_components, "risk_data_coverage": pool.risk_data_coverage,
                                        "asset_risk": pool.asset_risk, "asset_risk_coverage": pool.asset_risk_coverage,
                                        "volatility_risk": pool.volatility_risk, "volatility_risk_details": pool.volatility_risk_details,
                                        "liquidity_structure_risk": pool.liquidity_structure_risk, "liquidity_structure_risk_details": pool.liquidity_structure_risk_details,
                                        "asset_risk_details": pool.asset_risk_details}), pool.status, endpoint)
        if not db.snapshot_exists(protocol, pool.pool_address, scan_id):
            db.insert_snapshot(snapshot)
        pool.history_stats = db.history_stats(pool.pool_address, protocol)
        assert pool.history_stats["fee_apr_min"] <= pool.calculated_fee_apr <= pool.history_stats["fee_apr_max"]
        if pool.opportunity_score >= config.MIN_SCORE and not db.recently_alerted(protocol + ":" + pool.pool_address + ":score", config.ALERT_COOLDOWN_MINUTES):
            db.add_alert(protocol + ":" + pool.pool_address + ":score", "high_score", pool.opportunity_score, f"{pool.pair} {pool.pool_type} score {pool.opportunity_score}")
        results.append(pool)
    for address, prior in previous.items():
        if address not in seen_addresses:
            dropped_candidates.append({
                "pool": address,
                "pair": f"{prior['token_a']} / {prior['token_b']}",
                "protocol": protocol,
                "previous_rank": previous_rank.get(address),
                "previous_opp": prior["opportunity_score"] or prior["score"],
                "previous_apr": prior["calculated_fee_apr"],
                "current_metrics": {"tvl_usd": None, "volume_24h_usd": None, "volume_tvl": None, "fee_apr": None},
                "drop_reason": "not returned by provider",
            })
    # analyzed counts pools that passed token normalization, including those
    # intentionally discarded by the TVL filter.
    return ScanResult(sorted(results, key=lambda x: x.opportunity_score, reverse=True), normalized_count, discarded_tvl, incomplete, discarded_volume, discarded_ratio, discarded_fee, required_incomplete=incomplete, incomplete_by_field=missing_required, optional_missing=missing_optional, potential_candidates_incomplete=potential_candidates_incomplete, incomplete_below_tvl=incomplete_below_tvl, filter_audits=filter_audits, dropped_candidates=dropped_candidates, pipeline_counts={"fetched_raw_count": len(raw), "normalized_count": normalized_count, "allowed_token_count": allowed_token_count, "pre_filter_count": normalized_count, "qualifying_count": len(results)})


def scan(db: Database, client=None, page_size=config.PAGE_SIZE, max_pages=config.MAX_PAGES):
    client = client or RaydiumClient()
    raw, pages = client.fetch_pools(page_size, max_pages)
    db.audit("Raydium", client.endpoint, {"pages": pages, "pool_count": len(raw)})
    return scan_source(db, raw, client.endpoint, normalize, "Raydium", utc_now())
