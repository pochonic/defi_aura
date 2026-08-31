"""Risk-engine scaffolding; deliberately does not invent risk scores."""

from dataclasses import dataclass
from typing import Any

import config


RISK_COMPONENTS = (
    "asset_risk",
    "volatility_risk",
    "protocol_risk",
    "liquidity_structure_risk",
    "pool_risk",
)


@dataclass
class RiskAssessment:
    risk_score: float | None
    coverage_pct: float
    components: dict[str, dict[str, Any]]
    missing_components: list[str]
    asset_risk: Any = None
    volatility_risk: Any = None
    protocol_risk: Any = None
    liquidity_structure_risk: Any = None
    pool_risk: Any = None


def assess(pool) -> RiskAssessment:
    components = {
        name: {"score": None, "coverage": False, "source": None,
               "reason": "risk data provider not implemented"}
        for name in RISK_COMPONENTS
    }
    return RiskAssessment(
        risk_score=None,
        coverage_pct=0.0,
        components=components,
        missing_components=list(RISK_COMPONENTS),
    )


def score_if_covered(assessment: RiskAssessment) -> float | None:
    covered = [item for item in assessment.components.values() if item.get("coverage")]
    if assessment.coverage_pct < config.RISK_MIN_COVERAGE_PCT or not covered:
        return None
    scores = [item["score"] for item in covered if item.get("score") is not None]
    return sum(scores) / len(scores) if scores else None
