"""Objective token-risk evidence collection and explainable scoring."""

import base64
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config
from asset_registry import lookup as registry_lookup

logger = logging.getLogger(__name__)


@dataclass
class UnderlyingAssetAssessment:
    """Market evidence for the non-Solana asset behind a wrapper.

    The underlying market cap must come from an underlying-asset market-data
    source, never from the SPL representation. The provider is intentionally
    not queried until its identity mapping and freshness policy are finalized.
    """
    symbol: str
    market_cap: float | None
    liquidity: float | None
    volatility: float | None
    market_risk: float | None
    source: str | None
    reason: str | None = None


@dataclass
class AssetRiskAssessment:
    token_mint: str
    token_symbol: str
    score: float | None
    coverage_pct: float
    components: dict
    sources: list[dict]
    warnings: list[str]
    data: dict | None = None
    market_asset_risk: float | None = None
    structural_asset_risk: float | None = None


def _num(value):
    try:
        value = float(value)
        return value if value == value and abs(value) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _get_json(url, payload=None, timeout=20):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-radar-mvp/1.0"}, method="POST" if payload else "GET")
    if payload is not None:
        request.data = json.dumps(payload).encode("utf-8")
        request.add_header("Content-Type", "application/json")
    with urlopen(request, timeout=timeout) as response:
        if response.status >= 400:
            raise RuntimeError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def _cached(db, key, ttl):
    row = db.get_asset_cache(key)
    if not row:
        return None
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(row["fetched_at"])).total_seconds()
    return json.loads(row["payload"]) if age <= ttl else None


def _fetch_cached(db, key, url, payload, ttl):
    cached = _cached(db, key, ttl)
    if cached is not None:
        return cached, "CACHE"
    try:
        value = _get_json(url, payload)
        db.put_asset_cache(key, value)
        return value, "LIVE"
    except Exception as exc:
        logger.warning("Asset data unavailable for %s: %s", key, exc)
        return None, "UNAVAILABLE"


def _authority_from_mint(mint_data):
    """Decode standard SPL Mint authority options from 82-byte account data."""
    if not isinstance(mint_data, dict):
        return None, None
    try:
        encoded = mint_data["value"]["data"][0]
        raw = base64.b64decode(encoded)
        if len(raw) < 82:
            return None, None
        mint_authority = raw[4:36].hex() if int.from_bytes(raw[0:4], "little") else None
        freeze_authority = raw[50:82].hex() if int.from_bytes(raw[46:50], "little") else None
        return mint_authority, freeze_authority
    except (KeyError, TypeError, ValueError, IndexError):
        return None, None


def fetch_token_evidence(db, mint, symbol):
    jupiter_url = config.ASSET_RISK_JUPITER_ENDPOINT + "?" + urlencode({"query": mint})
    jupiter, state = _fetch_cached(db, f"jupiter:{mint}", jupiter_url, None, config.ASSET_RISK_CACHE_TTL_SECONDS["market_data"])
    token = next((item for item in (jupiter or []) if item.get("id") == mint), None)
    rpc_url = config.SOLANA_RPC_ENDPOINT
    rpc_payload = {"jsonrpc": "2.0", "id": 1, "method": "getAccountInfo", "params": [mint, {"encoding": "base64", "commitment": "finalized"}]}
    rpc, rpc_state = _fetch_cached(db, f"rpc:{mint}", rpc_url, rpc_payload, config.ASSET_RISK_CACHE_TTL_SECONDS["authority_data"])
    mint_authority, freeze_authority = _authority_from_mint((rpc or {}).get("result")) if rpc else (None, None)
    return {
        "mint": mint, "symbol": symbol, "jupiter": token, "mint_authority": mint_authority,
        "freeze_authority": freeze_authority, "jupiter_state": state, "rpc_state": rpc_state,
        "sources": [
            {"source": "Jupiter Tokens API", "endpoint": jupiter_url, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "confidence": "medium"},
            {"source": "Solana JSON-RPC", "endpoint": rpc_url, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "confidence": "high"},
        ],
    }


def _interpolate(value, points):
    if value is None:
        return None
    points = sorted(points)
    if value <= points[0][0]: return points[0][1]
    if value >= points[-1][0]: return points[-1][1]
    for (low, low_score), (high, high_score) in zip(points, points[1:]):
        if value <= high:
            return low_score + (value - low) / (high - low) * (high_score - low_score)


def _underlying_asset_assessment(symbol):
    """Return an explicit unavailable assessment until underlying data is wired.

    BTC/ETH wrapper risk must use BTC/ETH market data, not the SPL wrapper's
    mcap or liquidity. No synthetic value is assigned here.
    """
    return UnderlyingAssetAssessment(
        symbol=symbol or "UNKNOWN", market_cap=None, liquidity=None,
        volatility=None, market_risk=None, source=None,
        reason="underlying-asset market data provider not implemented; wrapper metrics are not substitutes",
    )


def assess_token(evidence):
    token = evidence.get("jupiter") or {}
    audit = token.get("audit") or {}
    registry = registry_lookup(evidence["mint"])
    asset_class = registry.get("asset_class", "UNKNOWN")
    registry_identified = bool(registry.get("asset_class"))
    market_cap = _num(token.get("mcap"))
    top10 = _num(audit.get("topHoldersPercentage"))
    liquidity = _num(token.get("liquidity"))
    market_cap_risk = _interpolate(market_cap, [(0, 100), (50_000_000, 90), (250_000_000, 70), (1_000_000_000, 45), (10_000_000_000, 10)])
    holder_risk = _interpolate(top10, [(0, 10), (15, 20), (30, 45), (50, 75), (100, 95)])
    authority_values = [evidence.get("mint_authority") is not None, evidence.get("freeze_authority") is not None]
    authority_risk = (10 if not any(authority_values) else 50 if not all(authority_values) else 80) if evidence.get("rpc_state") != "UNAVAILABLE" else None
    liquidity_risk = _interpolate(liquidity, [(0, 95), (10_000_000, 75), (100_000_000, 45), (1_000_000_000, 15)])
    verified = token.get("isVerified")
    verification_risk = 15 if verified is True else 55 if verified is False else None
    common = {
        "market_cap_risk": {"score": market_cap_risk, "coverage": market_cap_risk is not None, "source": "Jupiter Tokens API", "value": market_cap},
        "liquidity_risk": {"score": liquidity_risk, "coverage": liquidity_risk is not None, "source": "Jupiter Tokens API liquidity", "value": liquidity},
        "token_age_risk": {"score": None, "coverage": False, "source": None, "reason": "defensible mint creation timestamp not available; firstPool.createdAt is not used"},
        "verification_risk": {"score": verification_risk, "coverage": verification_risk is not None, "source": "Jupiter Tokens API isVerified", "value": verified},
    }
    market_asset_risk = None
    structural_asset_risk = None
    underlying = None
    if asset_class == "STABLECOIN_CENTRALIZED":
        components = {
            "market_cap_risk": common["market_cap_risk"], "liquidity_risk": common["liquidity_risk"],
            "issuer_control_risk": {"score": authority_risk, "coverage": authority_risk is not None, "source": "Solana JSON-RPC + registry issuer", "issuer": registry.get("issuer"), "mint_capability": authority_values[0], "freeze_capability": authority_values[1], "censorship_dependency": True},
            "holder_concentration_risk": {"score": None, "coverage": False, "source": None, "value": top10, "reason": "not reliable without classifying custodial/program accounts for major stablecoins"},
            "token_age_risk": common["token_age_risk"], "verification_risk": common["verification_risk"],
            "registry_identification": {"score": None, "coverage": registry_identified, "source": "auditable mint registry", "value": registry_identified},
        }
        weights = {"market_cap_risk": .35, "liquidity_risk": .25, "issuer_control_risk": .25, "verification_risk": .15}
        market_asset_risk = _weighted_score(components, {"market_cap_risk": .60, "liquidity_risk": .40})
        structural_asset_risk = _weighted_score(components, {"issuer_control_risk": .65, "verification_risk": .35})
    elif asset_class == "NATIVE_BASE":
        components = {"market_cap_risk": common["market_cap_risk"], "liquidity_risk": common["liquidity_risk"], "token_age_risk": common["token_age_risk"]}
        weights = {"market_cap_risk": .45, "liquidity_risk": .35, "token_age_risk": .20}
        market_asset_risk = _weighted_score(components, {"market_cap_risk": .55, "liquidity_risk": .45})
    elif asset_class in {"WRAPPED_CUSTODIAL", "WRAPPED_BRIDGED"}:
        underlying = _underlying_asset_assessment(registry.get("underlying_asset"))
        bridge_key = "custody_or_bridge_risk" if asset_class == "WRAPPED_CUSTODIAL" else "bridge_risk"
        components = {
            "wrapper_adoption_risk": {"score": market_cap_risk, "coverage": market_cap_risk is not None, "source": "Jupiter Tokens API", "value": market_cap, "reason": "wrapper adoption only; not underlying market cap"},
            "wrapper_liquidity_risk": {"score": liquidity_risk, "coverage": liquidity_risk is not None, "source": "Jupiter Tokens API liquidity", "value": liquidity},
            "authority_risk": {"score": authority_risk, "coverage": authority_risk is not None, "source": "Solana JSON-RPC getAccountInfo"},
            "underlying_market_risk": {"score": underlying.market_risk, "coverage": underlying.market_risk is not None, "source": underlying.source, "reason": underlying.reason, "underlying": underlying.symbol},
            bridge_key: {"score": None, "coverage": False, "source": None, "reason": "custodian/bridge methodology not yet defined", "issuer_or_bridge": registry.get("issuer")},
        }
        weights = {"wrapper_adoption_risk": .20, "wrapper_liquidity_risk": .30, "authority_risk": .20, "underlying_market_risk": .15, bridge_key: .15}
        market_asset_risk = _weighted_score(components, {"wrapper_adoption_risk": .45, "wrapper_liquidity_risk": .55})
        structural_asset_risk = _weighted_score(components, {"authority_risk": .50, bridge_key: .50})
    elif asset_class == "DECENTRALIZED_TOKEN":
        components = {**common, "holder_concentration_risk": {"score": holder_risk, "coverage": holder_risk is not None, "source": "Jupiter audit.topHoldersPercentage", "value": top10, "warning": "provider exclusion of LP/program accounts is not independently verified" if holder_risk is not None else None}, "authority_risk": {"score": authority_risk, "coverage": authority_risk is not None, "source": "Solana JSON-RPC getAccountInfo"}}
        weights = config.ASSET_RISK_WEIGHTS
        market_asset_risk = _weighted_score(components, {"market_cap_risk": .35, "liquidity_risk": .25, "holder_concentration_risk": .25, "token_age_risk": .15})
        structural_asset_risk = _weighted_score(components, {"authority_risk": .70, "verification_risk": .30})
    else:
        components = {name: {"score": None, "coverage": False, "source": None, "reason": "asset class not established by mint registry or verifiable metadata"} for name in config.ASSET_RISK_WEIGHTS}
        weights = config.ASSET_RISK_WEIGHTS
    mandatory_components = sorted(config.ASSET_RISK_MANDATORY_COMPONENTS.get(asset_class, set()))
    # Coverage describes evidence availability, including non-numeric
    # identification evidence such as the stablecoin registry match.
    covered = [item for item in components.values() if item["coverage"]]
    coverage = len(covered) / len(components) * 100
    weights = {key: value for key, value in weights.items() if key in components and components[key]["coverage"] and components[key]["score"] is not None}
    total_weight = sum(weights.values())
    missing_mandatory = [key for key in mandatory_components if not components.get(key, {}).get("coverage")]
    mandatory_complete = asset_class != "UNKNOWN" and not missing_mandatory
    score = sum(components[key]["score"] * value / total_weight for key, value in weights.items()) if coverage >= config.RISK_MIN_COVERAGE_PCT and mandatory_complete else None
    warnings = []
    if asset_class in {"WRAPPED_CUSTODIAL", "WRAPPED_BRIDGED"}:
        warnings.append("Wrapper risk: underlying market and custody/bridge model not yet evaluated")
    if asset_class == "STABLECOIN_CENTRALIZED":
        warnings.append(f"Issuer control is modeled explicitly ({registry.get('issuer') or 'unknown issuer'}); not treated as speculative-token authority risk")
    if asset_class == "DECENTRALIZED_TOKEN" and top10 is not None and top10 >= 30:
        warnings.append("high holder concentration")
    if score is None:
        warnings.append("Asset Risk unavailable: mandatory components incomplete" if not mandatory_complete else "Asset Risk coverage below minimum")
    symbol = evidence["symbol"]
    classification = asset_class
    return AssetRiskAssessment(
        evidence["mint"], symbol, round(score, 2) if score is not None else None, round(coverage, 2), components, evidence["sources"], warnings,
        data={
            "market_cap_usd": market_cap, "fully_diluted_valuation": _num(token.get("fdv")),
            "token_age": None, "total_supply": _num(token.get("totalSupply")), "circulating_supply": _num(token.get("circSupply")),
            "holder_count": _num(token.get("holderCount")), "top_10_holder_pct": top10, "top_1_holder_pct": None,
            "mint_authority_active": evidence.get("mint_authority") is not None, "freeze_authority_active": evidence.get("freeze_authority") is not None,
            "token_verified": verified, "token_standard": token.get("tokenProgram"), "aggregate_liquidity_usd": liquidity,
            "source_stablecoin_flag": token.get("isStablecoin"),
            "normalized_asset_class": classification,
            "asset_class": classification,
            "underlying_asset": registry.get("underlying_asset"), "issuer": registry.get("issuer"),
            "wrapper_type": registry.get("wrapper_type"), "classification_basis": registry.get("classification_basis"),
            "mandatory_components": mandatory_components,
            "mandatory_components_complete": mandatory_complete,
            "missing_mandatory_components": missing_mandatory,
            "underlying_assessment": underlying.__dict__ if underlying else None,
        },
        market_asset_risk=round(market_asset_risk, 2) if market_asset_risk is not None else None,
        structural_asset_risk=round(structural_asset_risk, 2) if structural_asset_risk is not None else None,
    )


def _weighted_score(components, weights):
    usable = [(key, weight) for key, weight in weights.items() if components.get(key, {}).get("coverage") and components[key].get("score") is not None]
    total = sum(weight for _, weight in usable)
    return sum(components[key]["score"] * weight / total for key, weight in usable) if total else None


def assess_pool_assets(db, pool):
    assessments = [assess_token(fetch_token_evidence(db, mint, symbol)) for mint, symbol in ((pool.token_a_mint, pool.token_a), (pool.token_b_mint, pool.token_b)) if mint]
    if len(assessments) != 2 or any(item.score is None for item in assessments):
        pool.asset_risk = None
    else:
        pool.asset_risk = round(max(assessments[0].score, assessments[1].score) * 0.70 + min(assessments[0].score, assessments[1].score) * 0.30, 2)
    pool.asset_risk_coverage = round(sum(item.coverage_pct for item in assessments) / len(assessments), 2) if assessments else 0.0
    pool.asset_risk_details = {"token_a": assessments[0].__dict__ if len(assessments) > 0 else None, "token_b": assessments[1].__dict__ if len(assessments) > 1 else None, "pool_formula": "max(asset_a, asset_b)*0.70 + min(asset_a, asset_b)*0.30"}
