"""Pool-level liquidity mechanism risk; deliberately not position risk or IL."""

from dataclasses import dataclass

import config


STRUCTURE_TYPES = {"CONSTANT_PRODUCT_AMM", "CLMM", "WHIRLPOOL", "DLMM", "UNKNOWN"}


@dataclass
class LiquidityStructureAssessment:
    score: float | None
    coverage_pct: float
    structure_type: str
    components: dict
    warnings: list[str]
    missing_components: list[str]
    source: str
    metric_coverage_pct: float = 0.0
    distribution_coverage_pct: float | None = None
    confidence: str = "LOW"
    provisional: bool = False
    score_state: str = "N/A"
    distribution_state: str = "N/A"


def classify_structure(pool):
    value = (pool.pool_type or "").upper()
    if "DLMM" in value:
        return "DLMM"
    if "WHIRLPOOL" in value:
        return "WHIRLPOOL"
    if "CLMM" in value:
        return "CLMM"
    if any(token in value for token in ("AMM", "CPMM", "OPENBOOK")):
        return "CONSTANT_PRODUCT_AMM"
    return "UNKNOWN"


def _component(score, raw_metric, source, reason=None):
    return {"raw_metric": raw_metric, "score": score, "coverage": score is not None, "source": source, "reason": reason}


def _fee_component(pool):
    model = (pool.fee_model or "UNKNOWN").upper()
    if model == "FIXED":
        return _component(10.0, "FIXED", "normalized pool fee model", "fixed fee mechanism")
    if model in {"DYNAMIC", "ADAPTIVE"}:
        return _component(35.0, model, "normalized pool fee model", "dynamic fee adds variability; not intrinsically negative")
    return _component(None, model, None, "fee mechanism unknown")


def assess(pool):
    structure = classify_structure(pool)
    protocol_data = pool.protocol_data or {}
    dlmm_state = protocol_data.get("dlmm_state") or {}
    distribution_coverage = dlmm_state.get("distribution_coverage_pct") if structure == "DLMM" else None
    distribution_state = dlmm_state.get("distribution_state") if structure == "DLMM" else "N/A"
    distribution_invalid = structure == "DLMM" and distribution_coverage is not None and distribution_coverage > 105
    components = {}
    warnings = []
    if structure == "CONSTANT_PRODUCT_AMM":
        components["range_dependency_risk"] = _component(5.0, {"range_required": False}, "pool structure classification", "constant-product liquidity has no user-selected price range")
        components["rebalance_dependency_risk"] = _component(5.0, {"position_range": "not_applicable"}, "pool structure classification", "no range rebalancing dependency at pool structure level")
    else:
        if structure == "DLMM" and not distribution_invalid and isinstance(dlmm_state.get("liquidity_within_1pct"), (int, float)) and (distribution_coverage or 0) >= config.METEORA_DLMM_MIN_CONCENTRATION_COVERAGE_PCT:
            share_1 = dlmm_state["liquidity_within_1pct"] / 100
            share_2 = (dlmm_state.get("liquidity_within_2pct") or 0) / 100
            share_5 = (dlmm_state.get("liquidity_within_5pct") or 0) / 100
            # Provisional multi-band curve; the raw bands remain visible and
            # this is not a type-based score.
            range_score = min(100.0, max(0.0, (0.50 * share_1 + 0.30 * share_2 + 0.20 * share_5) * 100))
            components["range_dependency_risk"] = _component(range_score, {"within_1pct_observed_pct": share_1 * 100, "within_2pct_observed_pct": share_2 * 100, "within_5pct_observed_pct": share_5 * 100}, "Meteora SDK bin distribution", "provisional multi-band concentration curve")
        else:
            components["range_dependency_risk"] = _component(None, protocol_data.get("liquidity_distribution"), None, "pool-level range/bin distribution is not available")
        components["rebalance_dependency_risk"] = _component(None, None, None, "no position range or defensible pool-level range dependency metric")
        if structure in {"CLMM", "WHIRLPOOL"}:
            warnings.append("concentrated-liquidity distribution is unavailable; no range risk inferred from type alone")
        if structure == "DLMM" and not (
            protocol_data.get("dlmm_state_source") == "METEORA_OFFICIAL_SDK"
            and dlmm_state.get("active_bin_id") is not None
        ):
            warnings.append("active bin/liquidity distribution is unavailable; no DLMM concentration risk inferred from type alone")
    # Active-bin share is descriptive only. It is not an active/total ratio
    # and must not be converted into active-liquidity risk in V1.
    components["active_liquidity_risk"] = _component(
        None,
        dlmm_state.get("active_bin_share_of_observed") if structure == "DLMM" else protocol_data.get("active_liquidity_ratio"),
        "Meteora SDK bin distribution" if structure == "DLMM" else None,
        "active liquidity risk suspended until active-bin semantics are validated",
    )
    concentration = protocol_data.get("liquidity_concentration")
    if structure == "DLMM" and not distribution_invalid and isinstance(dlmm_state.get("hhi"), (int, float)) and (distribution_coverage or 0) >= config.METEORA_DLMM_MIN_CONCENTRATION_COVERAGE_PCT:
        concentration = dlmm_state["hhi"]
        top1 = (dlmm_state.get("top_1_bin_pct") or 0) / 100
        top5 = (dlmm_state.get("top_5_bins_pct") or 0) / 100
        top10 = (dlmm_state.get("top_10_bins_pct") or 0) / 100
        concentration_score = min(100.0, max(0.0, (0.50 * top1 + 0.30 * top5 + 0.20 * top10) * 100))
        components["capital_concentration_risk"] = _component(concentration_score, {"hhi": concentration, "top_1_bin_pct": top1 * 100, "top_5_bins_pct": top5 * 100, "top_10_bins_pct": top10 * 100}, "Meteora SDK bin distribution", "provisional top-bin concentration curve")
    elif structure != "DLMM" and isinstance(concentration, (int, float)) and 0 <= concentration <= 1:
        components["capital_concentration_risk"] = _component(concentration * 100, concentration, "provider liquidity distribution", "concentration around active price/bin")
    else:
        components["capital_concentration_risk"] = _component(None, concentration, None, "liquidity distribution is not available")
    components["fee_mechanism_complexity_risk"] = _fee_component(pool)
    covered = [item for item in components.values() if item["coverage"]]
    coverage = len(covered) / len(components) * 100
    if structure == "DLMM":
        # Active-bin share is descriptive only, so it is deliberately not a
        # mandatory input for structure risk. Distribution-derived range and
        # concentration metrics are mandatory once coverage is sufficient.
        mandatory = {"range_dependency_risk", "capital_concentration_risk", "fee_mechanism_complexity_risk"}
        mandatory_complete = all(components[name]["coverage"] for name in mandatory)
    else:
        mandatory = {"range_dependency_risk", "active_liquidity_risk"}
        mandatory_complete = any(components[name]["coverage"] for name in mandatory)
    missing = [name for name in mandatory if not components[name]["coverage"]]
    weights = {name: config.LIQUIDITY_STRUCTURE_WEIGHTS[name] for name, item in components.items() if item["coverage"]}
    total_weight = sum(weights.values())
    effective = {name: value / total_weight for name, value in weights.items()} if total_weight else {}
    for name, item in components.items():
        if item["coverage"]:
            item["effective_weight"] = effective[name]
            item["weighted_contribution"] = item["score"] * effective[name]
    score = sum(item["weighted_contribution"] for item in components.values() if item.get("coverage")) if not distribution_invalid and coverage >= config.RISK_MIN_COVERAGE_PCT and mandatory_complete else None
    if structure == "UNKNOWN":
        warnings.append("unknown liquidity mechanism")
    if score is None and not mandatory_complete:
        warnings.append("mandatory structure risk components are unavailable")
    # Keep the unrounded sum so the printed contribution breakdown can
    # reconstruct the score without hidden rounding transformations.
    if structure != "DLMM":
        # A concentrated structure without its mandatory distribution input
        # is not high-confidence merely because type classification exists.
        confidence = "N/A" if score is None or not mandatory_complete else "HIGH"
    elif distribution_invalid:
        confidence = "N/A"
    elif (distribution_coverage or 0) >= 70 and coverage >= 60:
        confidence = "HIGH"
    elif (distribution_coverage or 0) >= 30:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    # Even high-coverage Meteora scores remain provisional until the bin
    # valuation has been reconciled against pool balances.
    provisional = structure == "DLMM" and score is not None
    if distribution_invalid:
        warnings.append("distribution coverage exceeds 105%; Structure Risk withheld as INVALID")
    if structure == "DLMM" and distribution_coverage is not None and distribution_coverage < config.METEORA_DLMM_MIN_CONCENTRATION_COVERAGE_PCT:
        warnings.append("distribution coverage below concentration minimum; concentration and range risk withheld")
    score_state = "EVALUABLE" if score is not None else "N/A"
    return LiquidityStructureAssessment(score if score is not None else None, round(coverage, 2), structure, components, warnings, missing, "normalized pool/provider data", round(coverage, 2), distribution_coverage, confidence, provisional, score_state, distribution_state)


def update_pool(pool):
    result = assess(pool)
    pool.liquidity_structure_risk = result.score
    pool.liquidity_structure_risk_details = result.__dict__
    if pool.risk_components is not None:
        pool.risk_components["liquidity_structure_risk"] = {"score": result.score, "coverage": result.score is not None, "source": result.source, "reason": "; ".join(result.warnings) or "mechanism-level structure analysis"}
    return result
