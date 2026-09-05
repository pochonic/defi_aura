"""Minimal read-only FastAPI facade over the existing SQLite database."""

import os
import sqlite3
import json
import sys
import ast
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware


ROOT = Path(__file__).resolve().parent.parent
DATABASE_PATH = Path(os.getenv("CRYPTO_RADAR_DB", ROOT / "crypto_radar.db"))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
try:
    import config
    LP_FILTERS = config.LP_FILTERS
except ImportError:
    LP_FILTERS = {
        "min_tvl_usd": 5_000_000.0,
        "min_volume_24h_usd": 250_000.0,
        "min_volume_tvl_ratio": 0.03,
        "min_pool_fee_apr": 3.0,
    }
app = FastAPI(title="Crypto Radar API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _connect() -> sqlite3.Connection:
    # Read-only URI prevents API requests from creating tables or modifying
    # the scanner's database while the scanner is writing snapshots.
    uri = f"file:{DATABASE_PATH.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _breakdown(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    raw = row["score_breakdown"] if isinstance(row, sqlite3.Row) else row.get("score_breakdown")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        try:
            parsed = ast.literal_eval(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, SyntaxError):
            return {}


def _json_value(value: Any, default: Any) -> Any:
    if not value:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        parsed = json.loads(value)
        return parsed
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _lending_payload(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    missing_fields = _json_value(data.get("missing_fields"), [])
    quality_flags = _json_value(data.get("quality_flags"), [])
    history_status = _json_value(data.get("history_status"), {})
    components = _json_value(data.get("components"), {})
    component_details = _json_value(data.get("component_details"), {})
    eligibility_reasons = _json_value(data.get("eligibility_reasons"), [])
    flags = _json_value(data.get("flags"), [])
    score = data.get("opportunity_score")
    provisional = data.get("provisional_opportunity_score")
    return {
        "protocol": data.get("protocol"), "chain": data.get("chain"),
        "market_id": data.get("market_id"), "reserve_id": data.get("reserve_id"),
        "asset_symbol": data.get("asset_symbol"), "asset_mint": data.get("asset_mint"),
        "market_name": data.get("market_name"), "supply_apy": data.get("supply_apy"),
        "borrow_apy": data.get("borrow_apy"), "utilization": data.get("utilization"),
        "total_supplied_usd": data.get("total_supplied_usd"),
        "total_borrowed_usd": data.get("total_borrowed_usd"),
        "available_liquidity_usd": data.get("available_liquidity_usd"),
        "available_liquidity_native": data.get("available_amount_native"),
        "available_liquidity_decimals": data.get("available_amount_decimals"),
        "observed_at": data.get("observed_at"), "source": data.get("source"),
        "missing_fields": missing_fields, "quality_flags": quality_flags,
        "opportunity_score": score, "provisional_opportunity_score": provisional,
        "display_score": score if score is not None else provisional,
        "score_confidence": data.get("score_confidence"), "score_status": data.get("score_status"),
        "eligible": bool(data["eligible"]) if data.get("eligible") is not None else None,
        "eligibility_reasons": eligibility_reasons, "economic_relevance": data.get("economic_relevance"),
        "history_status": history_status, "components": components,
        "component_details": component_details, "flags": flags,
        "utilization_source": data.get("utilization_source"),
        "utilization_calculated_at": data.get("utilization_calculated_at"),
    }


def _filter_result(data: dict[str, Any]) -> tuple[bool, list[str]]:
    checks = {
        "TVL": data.get("tvl_usd") is not None and data["tvl_usd"] >= LP_FILTERS["min_tvl_usd"],
        "volume": data.get("volume_24h") is not None and data["volume_24h"] >= LP_FILTERS["min_volume_24h_usd"],
        "volume_tvl": data.get("volume_tvl_ratio") is not None and data["volume_tvl_ratio"] >= LP_FILTERS["min_volume_tvl_ratio"],
        "fee_apr": (data.get("calculated_fee_apr", data.get("apr")) is not None and data.get("calculated_fee_apr", data.get("apr")) >= LP_FILTERS["min_pool_fee_apr"]),
    }
    return all(checks.values()), [name for name, passed in checks.items() if not passed]


def _current_eligibility(payload: dict[str, Any]) -> tuple[str, str | None]:
    snapshot_time = payload.get("snapshot_time")
    if not snapshot_time:
        return "UNKNOWN", "No persisted snapshot timestamp"
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(snapshot_time)).total_seconds()
    except (TypeError, ValueError):
        return "UNKNOWN", "Invalid persisted snapshot timestamp"
    if age > 30 * 60:
        return "UNKNOWN", "Latest persisted snapshot is stale (>30m)"
    eligible, failures = _filter_result(payload)
    if eligible:
        return "ELIGIBLE", None
    return "EXCLUDED", ", ".join(failures) + " below configured threshold"


def _pool_payload(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    breakdown = _breakdown(row)
    risk_components = breakdown.get("risk_components") or {}
    volatility_details = breakdown.get("volatility_risk_details") or {}
    structure_details = breakdown.get("liquidity_structure_risk_details") or {}
    risk_modules = {
        "asset": breakdown.get("asset_risk") is not None,
        "volatility": breakdown.get("volatility_risk") is not None,
        "structure": breakdown.get("liquidity_structure_risk") is not None,
    }
    payload = {
        "pair": f"{data.get('token_a') or 'N/A'} / {data.get('token_b') or 'N/A'}",
        "protocol": data.get("protocol"),
        "pool_address": data.get("pool_address"),
        "type": data.get("pool_type"),
        "fee_tier": data.get("fee_tier"),
        "opportunity_score": data.get("opportunity_score", data.get("score")),
        "risk_score": data.get("risk_score"),
        "risk_coverage": breakdown.get("risk_data_coverage"),
        "risk_modules": risk_modules,
        "risk_modules_available": sum(risk_modules.values()),
        "asset_risk": breakdown.get("asset_risk", risk_components.get("asset_risk", {}).get("score")),
        "asset_risk_coverage": breakdown.get("asset_risk_coverage"),
        "volatility_risk": breakdown.get("volatility_risk", risk_components.get("volatility_risk", {}).get("score")),
        "volatility_coverage": volatility_details.get("coverage_pct", risk_components.get("volatility_risk", {}).get("coverage_pct")),
        "structure_risk": breakdown.get("liquidity_structure_risk", risk_components.get("liquidity_structure_risk", {}).get("score")),
        "structure_coverage": structure_details.get("metric_coverage_pct", structure_details.get("coverage_pct", risk_components.get("liquidity_structure_risk", {}).get("coverage_pct"))),
        "apr": data.get("calculated_fee_apr"),
        "tvl_usd": data.get("tvl_usd"),
        "volume_24h": data.get("volume_24h"),
        "volume_tvl_ratio": data.get("volume_tvl_ratio"),
        "status": data.get("status"),
        "trend": data.get("opportunity_trend"),
        "snapshot_time": data.get("snapshot_time"),
    }
    eligibility, reason = _current_eligibility(payload)
    payload["current_eligibility"] = eligibility
    payload["eligibility_reason"] = reason
    payload["currently_eligible"] = eligibility == "ELIGIBLE"
    payload["hard_filter_failures"] = _filter_result(payload)[1]
    return payload


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "crypto-radar-api"}


@app.get("/api/pools")
def pools(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    protocol: str | None = None,
    pair: str | None = None,
    status: str | None = None,
    min_opportunity: float | None = Query(None, ge=0, le=100),
    eligible_only: bool = False,
) -> dict[str, Any]:
    conditions = []
    params: list[Any] = []
    if protocol:
        conditions.append("s.protocol = ?")
        params.append(protocol)
    if pair:
        conditions.append("UPPER(s.token_a || ' / ' || s.token_b) LIKE ?")
        params.append(f"%{pair.upper()}%")
    if status:
        conditions.append("s.status = ?")
        params.append(status)
    if min_opportunity is not None:
        conditions.append("COALESCE(s.opportunity_score, s.score) >= ?")
        params.append(min_opportunity)
    if eligible_only:
        conditions.extend([
            "s.tvl_usd >= ?", "s.volume_24h >= ?",
            "s.volume_tvl_ratio >= ?", "s.calculated_fee_apr >= ?",
        ])
        params.extend([
            LP_FILTERS["min_tvl_usd"], LP_FILTERS["min_volume_24h_usd"],
            LP_FILTERS["min_volume_tvl_ratio"], LP_FILTERS["min_pool_fee_apr"],
        ])
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with _connect() as db:
        total = db.execute(f"""
            SELECT COUNT(*) FROM (
                SELECT s.protocol, s.pool_address FROM lp_snapshots s
                JOIN (SELECT protocol, pool_address, MAX(snapshot_time) AS snapshot_time
                      FROM lp_snapshots GROUP BY protocol, pool_address) latest
                  ON latest.protocol = s.protocol AND latest.pool_address = s.pool_address AND latest.snapshot_time = s.snapshot_time
                {where}
            )
        """, params).fetchone()[0]
        rows = db.execute(f"""
            SELECT s.* FROM lp_snapshots s
            JOIN (SELECT protocol, pool_address, MAX(snapshot_time) AS snapshot_time
                  FROM lp_snapshots GROUP BY protocol, pool_address) latest
             ON latest.protocol = s.protocol
             AND latest.pool_address = s.pool_address
             AND latest.snapshot_time = s.snapshot_time
            {where}
            ORDER BY COALESCE(s.opportunity_score, s.score) DESC, s.snapshot_time DESC
            LIMIT ? OFFSET ?
        """, [*params, limit, offset]).fetchall()
    return {"items": [_pool_payload(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@app.get("/api/pools/{address}")
def pool_detail(address: str) -> dict[str, Any]:
    with _connect() as db:
        row = db.execute("""
            SELECT * FROM lp_snapshots
            WHERE pool_address = ?
            ORDER BY snapshot_time DESC LIMIT 1
        """, (address,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Pool not found in persisted snapshots")
    payload = _pool_payload(row)
    payload["snapshot"] = dict(row)
    payload["score_breakdown"] = _breakdown(row)
    with _connect() as db:
        history = db.execute("""
            SELECT snapshot_time, tvl_usd, volume_24h, calculated_fee_apr,
                   volume_tvl_ratio, opportunity_score, risk_score, status
            FROM lp_snapshots WHERE pool_address = ? ORDER BY snapshot_time ASC
        """, (address,)).fetchall()
    payload["history"] = [dict(item) for item in history]
    return payload


LENDING_VALID_SNAPSHOT_SQL = """(
    lower(s.protocol) <> 'save' OR
    (COALESCE(s.source_metadata, '') LIKE '%save-rest-percent-sdk-units-v3%'
     AND COALESCE(s.quality_flags, '') NOT LIKE '%anomalous_supply_apy%'
     AND COALESCE(s.quality_flags, '') NOT LIKE '%anomalous_borrow_apy%')
)"""


def _lending_query(where: str = "", params: list[Any] | None = None) -> tuple[list[dict[str, Any]], int]:
    params = params or []
    latest = f"""
        WITH latest AS (
            SELECT protocol, chain, market_id, reserve_id, MAX(observed_at) AS observed_at
            FROM lending_snapshots s WHERE {LENDING_VALID_SNAPSHOT_SQL}
            GROUP BY protocol, chain, market_id, reserve_id
        )
        SELECT s.*, e.eligible, e.eligibility_reasons, e.economic_relevance,
               e.opportunity_score, e.provisional_opportunity_score,
               e.score_confidence, e.score_status, e.history_status,
               e.components, e.component_details, e.flags
        FROM lending_snapshots s
        JOIN latest ON latest.protocol=s.protocol AND latest.chain=s.chain
          AND latest.market_id=s.market_id AND latest.reserve_id=s.reserve_id
          AND latest.observed_at=s.observed_at
        LEFT JOIN lending_evaluations e ON e.protocol=s.protocol AND e.chain=s.chain
          AND e.market_id=s.market_id AND e.reserve_id=s.reserve_id AND e.observed_at=s.observed_at
        WHERE {LENDING_VALID_SNAPSHOT_SQL.replace('s.', 's.')}
        {where}
    """
    with _connect() as db:
        total = db.execute(f"SELECT COUNT(*) FROM ({latest})", params).fetchone()[0]
        rows = db.execute(latest + " ORDER BY COALESCE(e.opportunity_score, e.provisional_opportunity_score) DESC, s.supply_apy DESC, s.observed_at DESC", params).fetchall()
    return [_lending_payload(row) for row in rows], total


@app.get("/api/lending")
def lending(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    asset: str | None = None,
    protocol: str | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
    eligible_only: bool = False,
) -> dict[str, Any]:
    conditions: list[str] = []
    params: list[Any] = []
    if asset:
        conditions.append("UPPER(s.asset_symbol) = UPPER(?)")
        params.append(asset)
    if protocol:
        conditions.append("lower(s.protocol) = lower(?)")
        params.append(protocol)
    if min_score is not None:
        conditions.append("COALESCE(e.opportunity_score, e.provisional_opportunity_score) >= ?")
        params.append(min_score)
    if eligible_only:
        conditions.append("e.eligible = 1")
    where = (" AND " + " AND ".join(conditions)) if conditions else ""
    items, total = _lending_query(where, params)
    return {"items": items[offset:offset + limit], "total": total, "limit": limit, "offset": offset}


@app.get("/api/lending/{reserve_id}")
def lending_detail(reserve_id: str) -> dict[str, Any]:
    items, _ = _lending_query("AND s.reserve_id = ?", [reserve_id])
    if not items:
        raise HTTPException(status_code=404, detail="Lending reserve not found in persisted snapshots")
    item = items[0]
    with _connect() as db:
        history = db.execute("""
            SELECT observed_at, supply_apy, borrow_apy, utilization,
                   total_supplied_usd, total_borrowed_usd, available_liquidity_usd
            FROM lending_snapshots WHERE protocol=? AND chain=? AND market_id=? AND reserve_id=?
            ORDER BY observed_at ASC
        """, (item["protocol"], item["chain"], item["market_id"], reserve_id)).fetchall()
    item["history"] = [dict(row) for row in history]
    return item


@app.get("/api/protocols/health")
def protocols_health() -> list[dict[str, Any]]:
    protocols = ("Raydium", "Orca", "Meteora")
    with _connect() as db:
        rows = {row["source"]: dict(row) for row in db.execute("SELECT * FROM api_status").fetchall()}
    return [{
        "protocol": protocol,
        "status": ("OK" if rows[protocol]["ok"] else "ERROR") if protocol in rows else "N/A",
        "checked_at": rows.get(protocol, {}).get("checked_at", "N/A"),
        "error": rows.get(protocol, {}).get("error", "N/A"),
    } for protocol in protocols]
