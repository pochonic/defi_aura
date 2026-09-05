"""Run one complete Railway data cycle for LP and Lending intelligence."""

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def run_step(label: str, command: list[str]) -> int:
    print(f"=== {label} START ===", flush=True)
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", *command],
        cwd=ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    print(f"=== {label} END exit={completed.returncode} ===", flush=True)
    return completed.returncode


def main() -> int:
    lp_exit = run_step("LP RADAR", ["app.py"])
    lending_exit = run_step("LENDING INGESTION", ["fetch_lending_markets.py", "--with-sdk-enrichment"])
    rank_exit = run_step("LENDING EVALUATION", ["fetch_lending_markets.py", "--rank", "--limit", "1000"])
    exits = {"lp": lp_exit, "lending": lending_exit, "ranking": rank_exit}
    print(f"RAILWAY CYCLE SUMMARY: {exits}", flush=True)
    return 1 if any(value != 0 for value in exits.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
