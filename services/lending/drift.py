import json
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import config
from .models import LendingMarketSnapshot

log = logging.getLogger(__name__)


class DriftClient:
    """Read-only Drift spot lending adapter backed by the official SDK."""

    source = "Drift official on-chain SDK"

    def __init__(self, node_path=None, rpc_url=None, runner=subprocess.run):
        self.node_path = node_path or os.getenv("DRIFT_NODE") or config.DRIFT_NODE
        self.rpc_url = rpc_url or os.getenv("SOLANA_RPC_URL") or config.SOLANA_RPC_ENDPOINT
        self.runner = runner
        self.probe = Path(__file__).with_name("drift_sdk_probe.mjs")
        self.last_report = {"markets": 0, "reserves": 0, "skipped": 0, "errors": 0}

    def fetch_lending_markets(self):
        self.last_report = {"markets": 0, "reserves": 0, "skipped": 0, "errors": 0}
        if not self.rpc_url:
            raise RuntimeError("SOLANA_RPC_URL is required for Drift SDK ingestion")
        node = shutil.which(self.node_path) or self.node_path
        try:
            completed = self.runner(
                [node, str(self.probe), self.rpc_url, config.DRIFT_ENV],
                capture_output=True, text=True, timeout=config.DRIFT_SDK_TIMEOUT_SECONDS,
                check=True, cwd=self.probe.parent.parent.parent,
            )
            rows = json.loads(completed.stdout)
            if not isinstance(rows, list):
                raise ValueError("Drift SDK output is not a list")
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            self.last_report["errors"] = 1
            raise RuntimeError(f"Drift SDK probe failed: {exc}") from exc

        observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        snapshots = []
        for raw in rows:
            self.last_report["reserves"] += 1
            try:
                snapshots.append(self._normalize(raw, observed_at))
            except (KeyError, TypeError, ValueError) as exc:
                self.last_report["skipped"] += 1
                log.error("Skipping invalid Drift spot market %s: %s", raw, exc)
        self.last_report["markets"] = len(snapshots)
        log.info("Drift spot markets fetched from %s; normalized %d reserves", self.source, len(snapshots))
        return snapshots

    def _normalize(self, raw, observed_at):
        def optional_float(name):
            value = raw.get(name)
            return None if value is None else float(value)

        decimals = int(raw["decimals"])
        price = optional_float("oracle_price_usd")
        supplied_native = optional_float("total_supplied_native")
        borrowed_native = optional_float("total_borrowed_native")
        available_native = optional_float("available_amount_native")
        values = {
            "supply_apy": optional_float("supply_apy"),
            "borrow_apy": optional_float("borrow_apy"),
            "utilization": optional_float("utilization"),
            "total_supplied_usd": supplied_native * price if supplied_native is not None and price is not None else None,
            "total_borrowed_usd": borrowed_native * price if borrowed_native is not None and price is not None else None,
            "available_liquidity_usd": available_native * price if available_native is not None and price is not None else None,
        }
        missing = tuple(name for name, value in values.items() if value is None)
        flags = []
        if price is None:
            flags.append("price_missing")
        for name in ("supply_apy", "borrow_apy"):
            if values[name] is not None and values[name] > 1:
                flags.append(f"anomalous_{name}")
        metadata = dict(raw.get("source_metadata") or {})
        metadata.update({"adapter": "drift_sdk", "environment": config.DRIFT_ENV, "decimals": decimals, "oracle_price_usd": price})
        return LendingMarketSnapshot(
            protocol="Drift", chain="Solana", market_id=str(raw["market_id"]),
            reserve_id=str(raw.get("reserve_id") or raw["market_id"]),
            asset_symbol=raw.get("asset_symbol"), asset_mint=raw.get("asset_mint"),
            observed_at=observed_at, source=self.source,
            source_endpoint=f"onchain://drift/{config.DRIFT_ENV}",
            market_name=raw.get("market_name") or raw.get("asset_symbol"),
            missing_fields=missing, quality_flags=tuple(flags),
            available_liquidity_native=available_native,
            available_liquidity_decimals=decimals,
            available_liquidity_source=self.source, source_metadata=metadata, **values,
        )
