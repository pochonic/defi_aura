import json
import logging
import os
import subprocess
import shutil
from urllib.parse import urlsplit, urlunsplit
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)

def _redact_rpc_url(value):
    if not value:
        return value
    try:
        parsed = urlsplit(value)
        if parsed.path.startswith("/v2/"):
            return urlunsplit((parsed.scheme, parsed.netloc, "/v2/[REDACTED]", parsed.query, parsed.fragment))
    except ValueError:
        pass
    return "[REDACTED]"


@dataclass
class EnrichmentStats:
    attempted: int = 0
    successful: int = 0
    utilization_populated: int = 0
    available_amount_populated: int = 0
    failed: int = 0
    duration_seconds: float = 0.0


def enrich_utilization_with_sdk(db, snapshots, node_path=None, rpc_url=None, debug=False):
    """Use one official SDK batch load; REST observations are never overwritten."""
    started = datetime.now(timezone.utc)
    stats = EnrichmentStats(attempted=len(snapshots))
    if not snapshots:
        return stats, []
    node_path = node_path or os.getenv("KAMINO_NODE") or getattr(config, "KAMINO_NODE", None) or config.METEORA_DLMM_NODE
    node_path = shutil.which(node_path) or ("/root/.nix-profile/bin/node" if node_path == "node" else node_path)
    rpc_url = rpc_url or os.getenv("SOLANA_RPC_URL") or config.SOLANA_RPC_ENDPOINT
    if not rpc_url:
        raise RuntimeError("SOLANA_RPC_URL is required for Kamino SDK enrichment")
    probe = Path(__file__).with_name("kamino_sdk_probe.mjs")
    market_ids = sorted({item.market_id for item in snapshots})
    completed = None
    try:
        completed = subprocess.run([node_path, str(probe), json.dumps(market_ids), rpc_url] + (["--debug"] if debug else []),
                                   capture_output=True, text=True, timeout=config.KAMINO_SDK_TIMEOUT_SECONDS, check=True,
                                   cwd=probe.parent.parent.parent)
        rows = json.loads(completed.stdout)
        by_key = {(row["market_id"], row["reserve_id"]): row for row in rows}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        debug_rows = []
        for item in snapshots:
            derived = by_key.get((item.market_id, item.reserve_id))
            if not derived:
                continue
            utilization = derived.get("utilization")
            native_raw = derived.get("available_amount_native")
            native = float(native_raw) if native_raw is not None else None
            db.conn.execute("""UPDATE lending_snapshots SET utilization=?, utilization_source_type=?, utilization_source=?,
                utilization_calculation_version=?, utilization_calculated_at=?, available_amount_native=?,
                available_amount_decimals=?, available_amount_source=?
                WHERE protocol=? AND market_id=? AND reserve_id=? AND observed_at=?""",
                (utilization, derived["source_type"], derived["source"], derived["calculation_version"], now, native,
                 derived.get("mint_decimals"), "kamino_sdk.state.liquidity.totalAvailableAmount", item.protocol,
                 item.market_id, item.reserve_id, item.observed_at))
            if utilization is not None:
                stats.utilization_populated += 1
            if native is not None:
                stats.available_amount_populated += 1
            if utilization is not None or native is not None:
                stats.successful += 1
            if debug:
                debug_rows.append({"market_id": item.market_id, "reserve_id": item.reserve_id, **derived})
        db.conn.commit()
        stats.failed = stats.attempted - stats.successful
        return stats, debug_rows
    except Exception as exc:
        stats.failed = stats.attempted
        stderr = completed.stderr.strip() if "completed" in locals() and completed.stderr else ""
        safe_stderr = stderr.replace(rpc_url, _redact_rpc_url(rpc_url)) if stderr else ""
        log.error("Kamino SDK enrichment failed (returncode=%s, RPC=%s): %s", getattr(completed, "returncode", "unknown") if completed else "not-started", _redact_rpc_url(rpc_url), safe_stderr or type(exc).__name__)
        return stats, [{"error": f"{type(exc).__name__}: {safe_stderr or 'SDK probe failed'}", "stderr": safe_stderr or None, "rpc": _redact_rpc_url(rpc_url)}]
    finally:
        stats.duration_seconds = (datetime.now(timezone.utc) - started).total_seconds()