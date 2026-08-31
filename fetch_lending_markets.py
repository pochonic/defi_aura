import argparse
import logging
import os
import sys
import time
from time import perf_counter

import config
from database import Database
from services.lending.ingestion import IngestionStats, persist_lending_snapshots
from services.lending.kamino import KaminoClient
from services.lending.save import SaveClient
from services.lending.derived import enrich_utilization_with_sdk
from services.lending.history import supply_apy_history
from services.lending.scoring import evaluate_lending_opportunity


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Fetch and persist Kamino lending market snapshots")
    parser.add_argument("--interval", type=int, default=0, help="repeat every N seconds; 0 runs once")
    parser.add_argument("--asset", action="append", help="only print these symbols after ingestion (repeatable)")
    parser.add_argument("--protocol", choices=("kamino", "save", "Kamino", "Save"), help="protocol filter; omitted runs all active adapters")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--show-latest", action="store_true", help="show latest persisted snapshots and exit")
    parser.add_argument("--show-history", action="store_true", help="show APY history for persisted reserves and exit")
    parser.add_argument("--with-sdk-enrichment", action="store_true", help="derive utilization and native liquidity using Kamino SDK/RPC")
    parser.add_argument("--debug-enrichment", action="store_true", help="print isolated SDK state diagnostics")
    parser.add_argument("--rank", action="store_true", help="rank latest persisted Kamino reserves")
    parser.add_argument("--explain", help="explain one reserve_id from the latest persisted data")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)
    if args.with_sdk_enrichment and not os.getenv("SOLANA_RPC_URL"):
        logging.getLogger(__name__).error("SOLANA_RPC_URL is required with --with-sdk-enrichment")
        return 2
    db = Database(config.DATABASE_PATH)
    protocol = args.protocol.lower() if args.protocol else None
    clients = {"kamino": KaminoClient(), "save": SaveClient()}
    try:
        if args.show_latest:
            rows = db.latest_lending_snapshots(asset=args.asset[0] if args.asset else None, protocol=protocol, limit=args.limit)
            print("Lending latest snapshots")
            print("Protocol | Asset | Market | Reserve | Supply APY | Borrow APY | Supply USD | Borrow USD | Utilization | Available | Missing")
            for row in rows:
                print(f"{row['protocol']} | {row['asset_symbol'] or 'N/A'} | {row['market_id']} | {row['reserve_id']} | "
                      f"{format_pct(row['supply_apy'])} | {format_pct(row['borrow_apy'])} | "
                      f"{format_money(row['total_supplied_usd'])} | {format_money(row['total_borrowed_usd'])} | "
                      f"{format_ratio(row['utilization'])} | {format_money(row['available_liquidity_usd'])} | {row['missing_fields'] or '[]'}")
            return
        if args.show_history:
            rows = db.lending_history(asset=args.asset[0] if args.asset else None, protocol=protocol)
            groups = {}
            for row in rows:
                groups.setdefault((row["market_id"], row["reserve_id"], row["asset_symbol"]), []).append(row)
            for (market_id, reserve_id, asset), group in list(groups.items())[:args.limit]:
                metrics = supply_apy_history(group)
                latest = group[-1]
                print(f"{asset or 'N/A'} — {protocol or 'all'}\nMarket: {market_id}\nReserve: {reserve_id}")
                print(f"Current APY: {format_pct(metrics['current'])}")
                for label, key in (("24h", "24h"), ("7d", "7d"), ("30d", "30d")):
                    value = metrics[key]
                    print(f"{label} avg filtered/raw: {format_pct(value['filtered_avg'])} / {format_pct(value['raw_avg'])} | min/max: {format_pct(value['min'])} / {format_pct(value['max'])} | stddev: {format_pct(value['stddev'])} | samples: {value['samples_count']} | coverage: {value['coverage_pct']:.1f}%")
                print(f"Utilization: {format_ratio(latest['utilization'])} | source: {latest['utilization_source'] or 'N/A'} | calculated at: {latest['utilization_calculated_at'] or 'N/A'}")
                print()
            return
        if args.rank or args.explain:
            latest = db.latest_lending_reserves(asset=args.asset[0] if args.asset else None, protocol=protocol)
            history_rows = db.lending_history(asset=args.asset[0] if args.asset else None, protocol=protocol)
            grouped = {}
            for row in history_rows:
                grouped.setdefault((row["protocol"], row["market_id"], row["reserve_id"]), []).append(row)
            evaluated = []
            for row in latest:
                evaluation = evaluate_lending_opportunity(row, grouped.get((row["protocol"], row["market_id"], row["reserve_id"]), [row]))
                db.save_lending_evaluation(row, evaluation)
                evaluated.append((row, evaluation))
            if args.explain:
                match = next(((row, evaluation) for row, evaluation in evaluated if row["reserve_id"] == args.explain), None)
                if not match:
                    print(f"Reserve not found: {args.explain}")
                    return
                row, evaluation = match
                print(f"Protocol: {row['protocol']} | Market: {row['market_id']} | Reserve: {row['reserve_id']}")
                print(f"Score Model: {evaluation['score_model']} | Score Version: {evaluation['score_version']}")
                score = evaluation["opportunity_score"] if evaluation["opportunity_score"] is not None else evaluation["provisional_opportunity_score"]
                score_display = "N/A" if score is None else f"{score:.1f}/100"
                label = "Opportunity Score" if evaluation["opportunity_score"] is not None else "Provisional Opportunity Score"
                print(f"{label}: {score_display}")
                for key, detail in evaluation["component_details"].items():
                    value = detail["value"]
                    points = "N/A" if value is None else f"{value * config.LENDING_SCORE_WEIGHTS[key] * 100:.1f}/{config.LENDING_SCORE_WEIGHTS[key] * 100:.0f}"
                    suffix = "" if value is not None else f" — {detail['reason']}"
                    print(f"{key}: {points}{suffix}")
                print(f"Status: {evaluation['score_status']} | Confidence: {evaluation['confidence']:.2f}")
                print(f"Evidence available: {evaluation['available_weight'] * 100:.0f}% | Historical evidence: {evaluation['history_status']['7d']}")
                print(f"History: 24h={evaluation['history_status']['24h']} | 7d={evaluation['history_status']['7d']} | 30d={evaluation['history_status']['30d']}")
                print(f"Eligibility: {evaluation['eligibility']['eligible']} | Reasons: {', '.join(evaluation['eligibility']['reasons']) or 'none'}")
                print(f"Economic relevance: {evaluation['economic_relevance']} | Flags: {', '.join(evaluation['flags']) or 'none'}")
                return
            def display_score(evaluation):
                return evaluation["opportunity_score"] if evaluation["opportunity_score"] is not None else evaluation["provisional_opportunity_score"]
            evaluated.sort(key=lambda pair: display_score(pair[1]) if display_score(pair[1]) is not None else -1, reverse=True)
            print("Rank | Protocol | Asset | Market | APY | 7d median | Util | Supply USD | Provisional/Score | Status | Weight | Conf | Flags")
            for index, (row, evaluation) in enumerate(evaluated[:args.limit], 1):
                median7 = evaluation["history"]["7d"].get("filtered_median")
                shown_score = display_score(evaluation)
                score_text = "N/A" if shown_score is None else f"{shown_score:.1f}"
                print(f"{index} | {row['protocol']} | {row['asset_symbol'] or 'N/A'} | {row['market_id']} | {format_pct(row['supply_apy'])} | {format_pct(median7)} | {format_ratio(row['utilization'])} | {format_money(row['total_supplied_usd'])} | {score_text} | {evaluation['score_status']} | {evaluation['available_weight'] * 100:.0f}% | {evaluation['confidence']:.2f} | {','.join(evaluation['flags']) or '-'}")
            print("\nTop supply APY (comparison only)")
            for index, (row, evaluation) in enumerate(sorted(evaluated, key=lambda pair: pair[0]["supply_apy"] if pair[0]["supply_apy"] is not None else -1, reverse=True)[:args.limit], 1):
                print(f"{index} | {row['protocol']} | {row['asset_symbol'] or 'N/A'} | {row['market_id']} | {format_pct(row['supply_apy'])} | Score: {display_score(evaluation):.1f} | Status: {evaluation['score_status']} | Flags: {','.join(evaluation['flags']) or '-'}")
            return
        while True:
            started = perf_counter()
            try:
                snapshots = []
                adapter_reports = {}
                rest_durations = {}
                for name, client in clients.items():
                    if protocol and protocol != name:
                        continue
                    rest_started = perf_counter()
                    adapter_snapshots = client.fetch_lending_markets(assets=args.asset) if name == "save" else client.fetch_lending_markets()
                    rest_durations[name] = perf_counter() - rest_started
                    snapshots.extend(adapter_snapshots)
                    adapter_reports[name] = client.last_report
                db_started = perf_counter()
                stats = IngestionStats(
                    reserves=sum(report["reserves"] for report in adapter_reports.values()),
                    skipped=sum(report["skipped"] for report in adapter_reports.values()),
                    errors=sum(report["errors"] for report in adapter_reports.values()),
                )
                persist_lending_snapshots(db, snapshots, stats)
                db_duration = perf_counter() - db_started
                kamino_snapshots = [item for item in snapshots if item.protocol == "Kamino"]
                enrichment_input = kamino_snapshots[:args.limit] if args.debug_enrichment else kamino_snapshots
                enriched, enriched_debug = enrich_utilization_with_sdk(db, enrichment_input, debug=args.debug_enrichment) if (args.with_sdk_enrichment or args.debug_enrichment) and enrichment_input else (None, [])
                duration = perf_counter() - started
                print("Lending ingestion")
                for name, report in adapter_reports.items():
                    print(f"{name.title()} markets: {report['markets']} | reserves: {report['reserves']} | REST: {rest_durations[name]:.2f}s")
                print(f"Reserves: {stats.reserves}")
                print(f"Snapshots saved: {stats.saved}")
                print(f"Snapshots skipped: {stats.skipped}")
                print(f"Records with missing fields: {stats.missing}")
                print(f"Errors: {stats.errors}")
                print(f"Duration: {duration:.2f}s")
                print(f"DB duration: {db_duration:.2f}s")
                if enriched is not None:
                    print(f"SDK enrichment attempted: {enriched.attempted}")
                    print(f"SDK enrichment successful: {enriched.successful}")
                    print(f"Utilization populated: {enriched.utilization_populated}")
                    print(f"Available amount populated: {enriched.available_amount_populated}")
                    print(f"Enrichment failed: {enriched.failed}")
                    print(f"SDK/RPC duration: {enriched.duration_seconds:.2f}s")
                    if args.debug_enrichment:
                        print("Enrichment diagnostics:")
                        for row in enriched_debug:
                            print(row)
                    print("Derived fields persisted: utilization, available_amount_native | Pending: available_liquidity_usd")
                visible = snapshots if not args.asset else [item for item in snapshots if item.asset_symbol in set(args.asset)]
                print("Top supply APY (audit only)")
                print("Asset | Market | Supply APY | Borrow APY | Supply USD | Borrow USD | Missing")
                for item in sorted(visible, key=lambda value: value.supply_apy or -1, reverse=True)[:10]:
                    persisted = db.conn.execute("""SELECT utilization, available_amount_native, missing_fields
                        FROM lending_snapshots WHERE protocol=? AND market_id=? AND reserve_id=? AND observed_at=?""",
                        (item.protocol, item.market_id, item.reserve_id, item.observed_at)).fetchone()
                    missing = list(item.missing_fields)
                    if persisted and persisted["utilization"] is not None and "utilization" in missing:
                        missing.remove("utilization")
                    print(f"{item.asset_symbol or item.reserve_id} | {item.market_name or item.market_id} | "
                          f"{format_pct(item.supply_apy)} | {format_pct(item.borrow_apy)} | "
                          f"{format_money(item.total_supplied_usd)} | {format_money(item.total_borrowed_usd)} | "
                          f"{','.join(missing) or 'none'}")
            except Exception as exc:
                db.audit(protocol or "lending", "active adapters", None, ok=False, error=str(exc))
                logging.getLogger(__name__).error("Lending ingestion failed: %s", type(exc).__name__)
                if not args.interval:
                    return 1
            if not args.interval:
                break
            time.sleep(args.interval)
    finally:
        db.close()


def format_pct(value):
    return "N/A" if value is None else f"{value * 100:.2f}%"


def format_ratio(value):
    return "N/A" if value is None else f"{value * 100:.2f}%"


def format_money(value):
    return "N/A" if value is None else f"${value:,.2f}"


if __name__ == "__main__":
    raise SystemExit(main() or 0)
