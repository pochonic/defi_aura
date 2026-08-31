import json
import math
from datetime import datetime
from statistics import median

import config
from .history import supply_apy_history

SCORE_MODEL = "lending_opportunity"
SCORE_VERSION = "1.0"


def _get(snapshot, name):
    try:
        return snapshot[name]
    except (KeyError, IndexError, TypeError):
        return getattr(snapshot, name, None)


def _status(coverage):
    if coverage < config.LENDING_HISTORY_STATUS_THRESHOLDS["insufficient"]:
        return "insufficient"
    if coverage < config.LENDING_HISTORY_STATUS_THRESHOLDS["complete"]:
        return "partial"
    return "complete"


def _clamp(value):
    return max(0.0, min(1.0, value))


def _log_scale(value, low, high):
    if value is None or value <= 0:
        return 0.0
    value = max(low, min(high, value))
    return _clamp(math.log(value / low) / math.log(high / low))


def _utilization_score(value):
    if value is None:
        return None
    if value < 0.30:
        return value / 0.30 * 0.45
    if value < 0.60:
        return 0.45 + (value - 0.30) / 0.30 * 0.35
    if value <= 0.85:
        return 0.80 + (value - 0.60) / 0.25 * 0.20
    if value <= 0.95:
        return 1.0 - (value - 0.85) / 0.10 * 0.35
    return max(0.25, 0.65 - (value - 0.95) / 0.05 * 0.40)


def _history_for(rows):
    if not rows:
        return {"current": None, "24h": {}, "7d": {}, "30d": {}}
    now = max(datetime.fromisoformat(row["observed_at"]) for row in rows)
    return supply_apy_history(rows, now=now)


def evaluate_lending_eligibility(snapshot):
    reasons = []
    if not _get(snapshot, "market_id") or not _get(snapshot, "reserve_id"):
        reasons.append("invalid_snapshot")
    apy = _get(snapshot, "supply_apy")
    supplied = _get(snapshot, "total_supplied_usd")
    if apy is None:
        reasons.append("missing_supply_apy")
    elif not isinstance(apy, (int, float)) or not math.isfinite(apy) or apy < 0:
        reasons.append("invalid_snapshot")
    if supplied is None:
        reasons.append("missing_supply")
    elif supplied <= 0:
        reasons.append("zero_supply")
    flags = json.loads(_get(snapshot, "quality_flags") or "[]") if isinstance(_get(snapshot, "quality_flags"), str) else (_get(snapshot, "quality_flags") or [])
    if any("invalid" in str(flag) for flag in flags):
        reasons.append("invalid_snapshot")
    return {"eligible": not reasons, "reasons": list(dict.fromkeys(reasons))}


def economic_relevance(total_supply_usd):
    if total_supply_usd is None:
        return "micro"
    if total_supply_usd < config.LENDING_ECONOMIC_THRESHOLDS_USD["small"]:
        return "micro" if total_supply_usd < config.LENDING_ECONOMIC_THRESHOLDS_USD["micro"] else "small"
    if total_supply_usd < config.LENDING_ECONOMIC_THRESHOLDS_USD["medium"]:
        return "medium"
    return "large"


def _component(value, status="available", reason=None, evidence=None):
    return {"value": value, "status": status, "reason": reason, "evidence": evidence or []}


def evaluate_lending_opportunity(snapshot, history_rows):
    eligibility = evaluate_lending_eligibility(snapshot)
    history = _history_for(history_rows)
    history_status = {window: _status(history[window].get("coverage_pct", 0.0)) for window in ("24h", "7d", "30d")}
    current = history["current"]
    h7 = history["7d"]
    median7 = h7.get("filtered_median")
    coverage7 = h7.get("coverage_pct", 0.0)
    historical_available = history_status["7d"] != "insufficient"

    current_norm = _log_scale(current, 0.001, config.LENDING_APY_REFERENCE)
    median_norm = _log_scale(median7, 0.001, config.LENDING_APY_REFERENCE)
    if historical_available and median7 is not None:
        yield_quality = 0.35 * current_norm + 0.50 * median_norm + 0.15 * min(1.0, coverage7 / 90.0)
        yield_evidence = ["current_apy", "filtered_7d_median", "7d_coverage"]
    else:
        yield_quality = current_norm
        yield_evidence = ["current_apy"]

    persistence = None
    if historical_available and current is not None and median7 not in (None, 0) and h7.get("samples_count", 0) >= 2:
        persistence = _clamp(1 - abs(current - median7) / max(abs(median7), 0.0001))
    stability = None
    if historical_available and h7.get("filtered_median") not in (None, 0) and h7.get("filtered_samples_count", 0) >= 2:
        values = [float(row["supply_apy"]) for row in history_rows if row["supply_apy"] is not None and "anomalous_supply_apy" not in (row["quality_flags"] or "")]
        if values:
            center = median(values)
            mad = median([abs(value - center) for value in values])
            stability = _clamp(1 - mad / abs(center)) if center else None

    capacity = _log_scale(_get(snapshot, "total_supplied_usd"), config.LENDING_CAPACITY_MIN_USD, config.LENDING_CAPACITY_MAX_USD)
    utilization = _utilization_score(_get(snapshot, "utilization"))
    borrow = _log_scale(_get(snapshot, "borrow_apy"), 0.001, 1.0) if _get(snapshot, "borrow_apy") is not None else None
    borrow_without_utilization = borrow
    values = {
        "yield_quality": yield_quality,
        "apy_persistence": persistence,
        "apy_stability": stability,
        "capacity": capacity,
        "utilization_health": utilization,
        "borrow_demand": (0.7 * borrow + 0.3 * utilization) if borrow is not None and utilization is not None else borrow if borrow is not None else None,
    }
    details = {
        "yield_quality": _component(yield_quality, evidence=yield_evidence),
        "apy_persistence": _component(persistence, "available" if persistence is not None else "unavailable", None if persistence is not None else "insufficient_7d_history", ["current_apy", "filtered_7d_median"] if persistence is not None else []),
        "apy_stability": _component(stability, "available" if stability is not None else "unavailable", None if stability is not None else "insufficient_7d_history", ["filtered_7d_apy_values"] if stability is not None else []),
        "capacity": _component(capacity, evidence=["total_supplied_usd"]),
        "utilization_health": _component(utilization, "available" if utilization is not None else "unavailable", None if utilization is not None else "missing_utilization", ["utilization"] if utilization is not None else []),
        "borrow_demand": _component(values["borrow_demand"], "available" if values["borrow_demand"] is not None else "unavailable", None if values["borrow_demand"] is not None else "missing_borrow_apy", ["borrow_apy", "utilization"] if values["borrow_demand"] is not None else []),
    }
    components = {key: values[key] for key in config.LENDING_SCORE_WEIGHTS}
    available_weight = sum(config.LENDING_SCORE_WEIGHTS[key] for key, value in components.items() if value is not None)
    missing_weight = 1.0 - available_weight
    weighted_points = sum(components[key] * weight for key, weight in config.LENDING_SCORE_WEIGHTS.items() if components[key] is not None) * 100
    provisional_score = weighted_points / available_weight if available_weight else None
    score = weighted_points if missing_weight == 0 else None
    score_status = {"insufficient": "PROVISIONAL", "partial": "PARTIAL_HISTORY", "complete": "MATURE"}[history_status["7d"]]

    flags = []
    quality_flags = json.loads(_get(snapshot, "quality_flags") or "[]") if isinstance(_get(snapshot, "quality_flags"), str) else (_get(snapshot, "quality_flags") or [])
    if current is not None and median7 is not None and current > median7 * 2:
        flags.append("apy_spike")
    if _get(snapshot, "utilization") is not None and _get(snapshot, "utilization") > 0.95:
        flags.append("extreme_utilization")
    if capacity < 0.35:
        flags.append("low_capacity")
    if history_status["7d"] != "complete":
        flags.append("insufficient_history")
    if _get(snapshot, "borrow_apy") is not None and _get(snapshot, "borrow_apy") > 0.20:
        flags.append("high_borrow_cost")
    if current == 0:
        flags.append("zero_apy")
    if quality_flags:
        flags.append("data_quality_issue")
    confidence = _clamp(0.35 * min(1.0, coverage7 / 90.0) + 0.35 * min(1.0, h7.get("samples_count", 0) / 96.0) + 0.30 * (1.0 if persistence is not None and stability is not None else 0.0))
    return {
        "eligibility": eligibility, "economic_relevance": economic_relevance(_get(snapshot, "total_supplied_usd")),
        "history": history, "history_status": history_status, "components": components, "component_details": details,
        "score_model": SCORE_MODEL, "score_version": SCORE_VERSION,
        "available_weight": available_weight, "missing_weight": missing_weight,
        "available_points_raw": weighted_points, "weighted_points": weighted_points,
        "provisional_opportunity_score": provisional_score, "borrow_demand_without_utilization": borrow_without_utilization,
        "opportunity_score": score, "score": score, "confidence": confidence, "score_status": score_status,
        "flags": sorted(set(flags)),
    }
