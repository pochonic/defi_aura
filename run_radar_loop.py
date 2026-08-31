"""Run the local LP radar periodically while preserving the SQLite history."""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import config
from services.network_diagnostics import diagnose


def main():
    parser = argparse.ArgumentParser(description="Run Crypto Radar on a fixed polling cadence")
    parser.add_argument("--duration-hours", type=float, default=None, help="optional automatic stop; omit for manual stop")
    parser.add_argument("--interval-seconds", type=int, default=config.POLL_INTERVAL_SECONDS)
    parser.add_argument("--log", default="radar_loop.log")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    app = root / "app.py"
    end = None if args.duration_hours is None else time.monotonic() + max(0.0, args.duration_hours) * 3600
    log_path = root / args.log
    cycle = 0
    stats = {"cycles_attempted": 0, "cycles_live": 0, "cycles_stale": 0, "cycles_unavailable": 0, "cycles_failed": 0, "snapshots_persisted": 0}
    previous_snapshot_count = None
    with log_path.open("a", encoding="utf-8") as log:
        try:
            while end is None or time.monotonic() < end:
                cycle += 1
                stats["cycles_attempted"] += 1
                started = datetime.now(timezone.utc).isoformat(timespec="seconds")
                log.write(f"\n=== RADAR CYCLE {cycle} START {started} ===\n")
                log.flush()
                try:
                    before = sqlite3.connect(str(root / "crypto_radar.db")).execute("SELECT COUNT(*) FROM lp_snapshots").fetchone()[0]
                except sqlite3.Error:
                    before = previous_snapshot_count or 0
                completed = subprocess.run([sys.executable, "-X", "utf8", str(app)], cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONIOENCODING": "utf-8"}, check=False)
                log.write(completed.stdout)
                if completed.returncode != 0:
                    stats["cycles_failed"] += 1
                elif "RADAR CYCLE STATUS: LIVE_DATA" in completed.stdout:
                    stats["cycles_live"] += 1
                    if " STALE" in completed.stdout or "STALE " in completed.stdout:
                        stats["cycles_stale"] += 1
                elif "STALE" in completed.stdout:
                    stats["cycles_stale"] += 1
                else:
                    stats["cycles_unavailable"] += 1
                if completed.stdout.count("WinError 10013") >= 3:
                    diagnostic = diagnose()
                    log.write("SYSTEM NETWORK STATUS: LOCAL_NETWORK_ERROR\n")
                    log.write(json.dumps(diagnostic, ensure_ascii=True) + "\n")
                try:
                    after = sqlite3.connect(str(root / "crypto_radar.db")).execute("SELECT COUNT(*) FROM lp_snapshots").fetchone()[0]
                    stats["snapshots_persisted"] += max(0, after - before)
                    previous_snapshot_count = after
                except sqlite3.Error:
                    pass
                finished = datetime.now(timezone.utc).isoformat(timespec="seconds")
                log.write(f"=== RADAR CYCLE {cycle} END {finished} EXIT {completed.returncode} ===\n")
                log.flush()
                remaining = None if end is None else end - time.monotonic()
                if remaining is not None and remaining <= 0:
                    break
                time.sleep(max(1, args.interval_seconds) if remaining is None else min(max(1, args.interval_seconds), remaining))
        except KeyboardInterrupt:
            log.write("=== RADAR LOOP STOPPED MANUALLY ===\n")
        finally:
            log.write("FINAL RUNNER STATS: " + json.dumps(stats, ensure_ascii=True) + "\n")
            log.flush()


if __name__ == "__main__":
    main()
