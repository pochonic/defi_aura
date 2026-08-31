import argparse
import logging
import sys

import config
from database import Database
from raydium import scan, ScanResult
from services.clmm_analyzer import ClmmAnalyzer
from services.lp_scanner import scan_all
from services.provider_runtime import HEALTH


def configure_console():
    """Keep presentation/logging alive on Windows cp1252 consoles."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


def money(value):
    return "N/A" if value is None else f"${value:,.0f}"


def pct(value):
    return "N/A" if value is None else f"{value:.2f}%"


def ratio_pct(value):
    return "N/A" if value is None else f"{value * 100:.2f}%"


def fee_tier(value):
    return "N/A" if value is None else f"{value * 100:.2f}%"


def short_address(value):
    return value if len(value) <= 14 else f"{value[:6]}...{value[-6:]}"


def data_age(pool):
    if pool.data_state == "LIVE":
        return f"LIVE {int(pool.data_age_seconds)}s"
    return f"{pool.data_state} {int(pool.data_age_seconds // 60)}m"


def attach_clmm_data(report: ScanResult, db: Database):
    analyzer = ClmmAnalyzer()
    for pool in report.pools[:15]:
        if pool.data_state != "LIVE":
            continue
        if pool.pool_type.startswith("CLMM") or "/CLMM" in pool.pool_type:
            try:
                pool.clmm_data = analyzer.analyze(pool.pool_address, pool.tvl_usd)
                db.audit("Raydium CLMM", f"{config.API_BASE_URL}/pools/info/ids and /pools/line/liquidity", pool.clmm_data)
            except Exception as exc:
                pool.clmm_data = {"available": {}, "unavailable": [f"API error: {exc}"], "position_apr": None}
                db.audit("Raydium CLMM", f"{config.API_BASE_URL}/pools/info/ids and /pools/line/liquidity", None, ok=False, error=str(exc))


def combine_reports(reports):
    pools = [pool for report in reports for pool in report.pools]
    return ScanResult(
        sorted(pools, key=lambda pool: pool.opportunity_score, reverse=True),
        sum(report.analyzed for report in reports), sum(report.discarded_tvl for report in reports),
        sum(report.incomplete for report in reports), sum(report.discarded_volume for report in reports),
        sum(report.discarded_ratio for report in reports), sum(report.discarded_fee for report in reports),
        potential_candidates_incomplete=sum(report.potential_candidates_incomplete for report in reports),
        incomplete_below_tvl=sum(report.incomplete_below_tvl for report in reports),
        filter_audits=[audit for report in reports for audit in (report.filter_audits or [])],
        dropped_candidates=[item for report in reports for item in (report.dropped_candidates or [])],
    )


def canonical_pair(pool):
    return tuple(sorted("SOL" if token.upper() == "WSOL" else token.upper() for token in (pool.token_a, pool.token_b)))


def print_common_pair(report):
    grouped = {}
    for pool in report.pools:
        grouped.setdefault(canonical_pair(pool), {})[pool.protocol] = pool
    common = grouped.get(("SOL", "USDC"))
    if not common or len(common) < 2:
        common = next((values for values in grouped.values() if len(values) >= 2), None)
    if not common:
        print("\nSOL/USDC COMPARISON: no qualifying common pair in this run")
        return
    print("\nSOL/USDC DIRECT COMPARISON")
    for protocol in ("Raydium", "Orca", "Meteora"):
        if protocol not in common:
            continue
        pool = common[protocol]
        print(f"{protocol} {pool.pool_type} | {pool.pool_address}")
        print(f"  TVL: {money(pool.tvl_usd)} | Volume 24h: {money(pool.volume_24h)} | Volume/TVL: {pool.volume_tvl_ratio:.2f}x | Fees 24h: {money(pool.fees_24h_usd)}")
        reported_label = "Orca-reported fees APR" if pool.protocol == "Orca" else "Reported fee APR"
        reported = pool.orca_reported_fees_apr if pool.protocol == "Orca" else pool.reported_fees_apr
        print(f"  Nominal Fee APR: {pct(pool.nominal_fee_apr)} | {reported_label}: {pct(reported)} | Fee model: {pool.fee_model} | OPP SCORE: {pool.opportunity_score:.2f}")
        print(f"  Liquidity Structure Risk: {'N/A' if pool.liquidity_structure_risk is None else f'{pool.liquidity_structure_risk:.2f}'} | coverage: {(pool.liquidity_structure_risk_details or {}).get('coverage_pct', 0):.1f}%")
        print(f"  Expected fees: {money(pool.expected_fees_from_nominal_rate)} | Difference: {money(pool.fee_difference_usd)} ({ratio_pct(pool.fee_difference_pct)}) | Window: {pool.fee_window_comparability}")


def print_report(report: ScanResult):
    pools = report.pools
    print("=" * 118)
    print("CRYPTO RADAR SOLANA - LP RADAR")
    print("=" * 118)
    print(f"Pools analyzed: {report.analyzed} | Descartados por TVL: {report.discarded_tvl} | Descartados por volumen: {report.discarded_volume} | Descartados por Volume/TVL: {report.discarded_ratio} | Descartados por Fee APR: {report.discarded_fee} | Oportunidades finales: {len(pools)} | Potential candidates incomplete: {report.potential_candidates_incomplete}")
    for audit in report.filter_audits or []:
        print("\nMETEORA SOL/USDC FILTER AUDIT")
        print(f"Pool: {audit['pool']} | TVL: {money(audit['tvl_usd'])} | Volume 24h: {money(audit['volume_24h_usd'])} | Volume/TVL: {audit['volume_tvl'] if audit['volume_tvl'] is not None else 'N/A'} | Fee APR: {pct(audit['fee_apr'])}")
        threshold_by_filter = {"tvl": audit["thresholds"]["min_tvl_usd"], "volume": audit["thresholds"]["min_volume_24h_usd"], "volume_tvl": audit["thresholds"]["min_volume_tvl_ratio"], "fee_apr": audit["thresholds"]["min_pool_fee_apr"]}
        print(" | ".join(f"{name}={'PASS' if passed else 'FAIL'} (threshold={threshold_by_filter[name]})" for name, passed in audit["checks"].items()))
        print(f"Exact discard reason: {audit['reason'] or 'QUALIFIES'}")
    if report.dropped_candidates:
        print("\nDROPPED SINCE LAST RUN")
        for item in report.dropped_candidates:
            print(f"{item['pair']} | {item['protocol']} | {item['pool']} | previous rank: {item['previous_rank'] or 'N/A'} | previous OPP: {item['previous_opp']:.2f} | previous APR: {pct(item['previous_apr'])} | current: {item['current_metrics']} | reason: {item['drop_reason']}")
    if not pools:
        print("No se encontraron pools con los tokens permitidos.")
        return
    print("\nTOP 15")
    print(f"{'#':>2} {'PAIR':<20} {'PROTOCOL':<9} {'TYPE':<13} {'FEE':>7} {'ADDRESS':<16} {'OPP SCORE':>9} {'ASSET RISK':>10} {'VOL RISK':>8} {'STRUCT RISK':>11} {'RISK SCORE':>10} {'DATA AGE':>12} {'TVL':>14} {'V/TVL':>8} {'POOL FEE APR':>13} {'STATUS':<20} {'TREND'}")
    print("-" * 118)
    for index, pool in enumerate(pools[:15], 1):
        ratio = "N/A" if pool.volume_tvl_ratio is None else f"{pool.volume_tvl_ratio:.2f}x"
        risk = "N/A" if pool.risk_score is None else f"{pool.risk_score:.1f}"
        asset = "N/A" if pool.asset_risk is None else f"{pool.asset_risk:.1f}"
        vol = "N/A" if pool.volatility_risk is None else f"{pool.volatility_risk:.1f}"
        structure = "N/A" if pool.liquidity_structure_risk is None else f"{pool.liquidity_structure_risk:.1f}"
        print(f"{index:>2} {pool.pair:<20} {pool.protocol:<9} {pool.pool_type:<13} {fee_tier(pool.fee_tier):>7} {short_address(pool.pool_address):<16} {pool.opportunity_score:>9.1f} {asset:>10} {vol:>8} {structure:>11} {risk:>10} {data_age(pool):>12} {money(pool.tvl_usd):>14} {ratio:>8} {pct(pool.calculated_fee_apr):>13} {pool.status:<20} {pool.trend}")
    print("\nDETALLE TOP 10")
    for index, pool in enumerate(pools[:10], 1):
        print(f"\n{index}. {pool.pair} | {pool.protocol} | {fee_tier(pool.fee_tier)} | {pool.pool_type} | {pool.pool_address}")
        risk = "N/A" if pool.risk_score is None else f"{pool.risk_score:.2f}"
        print(f"OPP SCORE: {pool.opportunity_score:.2f} | RISK SCORE: {risk} | TOTAL RISK COVERAGE: {pool.risk_data_coverage:.1f}% | ASSET RISK COVERAGE: {pool.asset_risk_coverage:.1f}% | DATA AGE: {data_age(pool)} | TVL: {money(pool.tvl_usd)} | Volume 24h: {money(pool.volume_24h)} | Volume/TVL: {pool.volume_tvl_ratio:.2f}x" if pool.volume_tvl_ratio is not None else f"OPP SCORE: {pool.opportunity_score:.2f} | RISK SCORE: {risk} | TOTAL RISK COVERAGE: {pool.risk_data_coverage:.1f}% | ASSET RISK COVERAGE: {pool.asset_risk_coverage:.1f}% | DATA AGE: {data_age(pool)} | TVL: {money(pool.tvl_usd)} | Volume 24h: {money(pool.volume_24h)} | Volume/TVL: N/A")
        print(f"Volume 24h: {money(pool.volume_24h)} | Nominal fee rate: {fee_tier(pool.fee_tier)} | Expected fees from nominal rate: {money(pool.expected_fees_from_nominal_rate)}")
        print(f"Fees 24h: {money(pool.fees_24h_usd)} | Fee difference: {money(pool.fee_difference_usd)} ({ratio_pct(pool.fee_difference_pct)}) | Fee-window comparability: {pool.fee_window_comparability}")
        reported_label = "Orca-reported fees APR" if pool.protocol == "Orca" else "Reported fee APR"
        reported = pool.orca_reported_fees_apr if pool.protocol == "Orca" else pool.reported_fees_apr
        print(f"Nominal Fee APR: {pct(pool.nominal_fee_apr)} | {reported_label}: {pct(reported)} | Fee model: {pool.fee_model}")
        print(f"Asset Risk: {'N/A' if pool.asset_risk is None else f'{pool.asset_risk:.2f}'} | Asset Risk coverage: {pool.asset_risk_coverage:.1f}%")
        vol = pool.volatility_risk_details or {}
        print("VOLATILITY RISK")
        print(f"  Score: {'N/A' if pool.volatility_risk is None else f'{pool.volatility_risk:.2f}'} | Metric coverage: {vol.get('metric_coverage_pct', vol.get('coverage_pct', 0)):.1f}% | 24h window coverage: {pct(vol.get('window_coverage_24h_pct'))}")
        print(f"  Pair: {vol.get('pair', 'N/A')} | Canonical pair: {vol.get('pair', 'N/A')} | Source: {vol.get('source', 'N/A')} | Window: {vol.get('window', 'N/A')}")
        audit24 = (vol.get('source_stats') or {}).get('last_24h_audit') or {}
        print(f"  Realized vol 24h: {pct((vol.get('realized_vol_24h') or 0) * 100) if vol.get('realized_vol_24h') is not None else 'N/A'} | observations last 24h: {audit24.get('observations_last_24h', 'N/A')} | returns: {audit24.get('returns_last_24h', 'N/A')} | expected slots: {audit24.get('expected_hourly_slots', 'N/A')} | missing slots: {audit24.get('missing_slots', 'N/A')} | largest gap: {audit24.get('largest_gap_hours', 'N/A')}h | 7d: {pct((vol.get('realized_vol_7d') or 0) * 100) if vol.get('realized_vol_7d') is not None else 'N/A'} | 30d: {pct((vol.get('realized_vol_30d') or 0) * 100) if vol.get('realized_vol_30d') is not None else 'N/A'}")
        print(f"  Max drawdown 7d: {pct((vol.get('max_drawdown_7d') or 0) * 100) if vol.get('max_drawdown_7d') is not None else 'N/A'} | Max hourly move 24h: {pct((vol.get('max_price_move_24h') or 0) * 100) if vol.get('max_price_move_24h') is not None else 'N/A'} | Max 24h move 7d: {pct((vol.get('max_abs_24h_move_7d') or 0) * 100) if vol.get('max_abs_24h_move_7d') is not None else 'N/A'} | Observations: {vol.get('observations', 0)}")
        print("  Breakdown:")
        for component, detail in (vol.get('breakdown') or {}).items():
            raw = detail.get('raw_metric')
            raw_display = pct(raw * 100) if raw is not None else 'N/A'
            print(f"    {component}: raw={raw_display} | score={detail.get('score', 'N/A')} | effective weight={detail.get('effective_weight', 0) * 100:.2f}% | contribution={detail.get('weighted_contribution', 'N/A')}")
        source_stats = vol.get('source_stats') or {}
        print(f"  Source ranges: EXTERNAL {source_stats.get('external_range', {}).get('first') or 'N/A'} -> {source_stats.get('external_range', {}).get('last') or 'N/A'} | LOCAL {source_stats.get('local_range', {}).get('first') or 'N/A'} -> {source_stats.get('local_range', {}).get('last') or 'N/A'}")
        for warning in vol.get('warnings') or []:
            print(f"  warning: {warning}")
        structure = pool.liquidity_structure_risk_details or {}
        print("LIQUIDITY STRUCTURE RISK")
        structure_score = 'N/A' if pool.liquidity_structure_risk is None else f'{pool.liquidity_structure_risk:.2f}'
        structure_state = "PROVISIONAL" if structure.get("provisional") else ("EVALUABLE" if pool.liquidity_structure_risk is not None else "N/A")
        print(f"  Structure: {structure.get('structure_type', 'UNKNOWN')} | Score: {structure_score} | Score state: {structure.get('score_state', structure_state)} | Provisional: {'YES' if structure.get('provisional') else 'NO'} | Confidence: {structure.get('confidence', 'N/A')} | Metric coverage: {structure.get('metric_coverage_pct', structure.get('coverage_pct', 0)):.1f}% | Distribution state: {structure.get('distribution_state', 'N/A')} | Distribution coverage: {pct(structure.get('distribution_coverage_pct'))}")
        for component, detail in (structure.get('components') or {}).items():
            print(f"  {component}: raw={detail.get('raw_metric', 'N/A')} | score={detail.get('score', 'N/A')} | weight={detail.get('effective_weight', 0) * 100:.2f}% | contribution={detail.get('weighted_contribution', 'N/A')} | source={detail.get('source') or 'N/A'}")
        print(f"  Missing: {', '.join(structure.get('missing_components') or []) or 'none'}")
        for warning in structure.get('warnings') or []:
            print(f"  warning: {warning}")
        if pool.asset_risk_details:
            for side in ("token_a", "token_b"):
                token = pool.asset_risk_details.get(side) or {}
                name = token.get('token_symbol', pool.token_a if side == 'token_a' else pool.token_b)
                data = token.get('data') or {}
                print(f"  {side}: {name} [{data.get('asset_class', 'UNKNOWN')}] Asset Risk {token.get('score', 'N/A')} | coverage {token.get('coverage_pct', 0)}%")
                print(f"    market_asset_risk: {token.get('market_asset_risk', 'N/A')} | structural_asset_risk: {token.get('structural_asset_risk', 'N/A')}")
                if data.get('underlying_asset') or data.get('issuer') or data.get('wrapper_type'):
                    print(f"    underlying: {data.get('underlying_asset') or 'N/A'} | issuer: {data.get('issuer') or 'N/A'} | wrapper: {data.get('wrapper_type') or 'N/A'}")
                if token.get("data"):
                    data = token["data"]
                    print(f"    raw data: market_cap={money(data.get('market_cap_usd'))} | fdv={money(data.get('fully_diluted_valuation'))} | holders={data.get('holder_count') or 'N/A'} | aggregate liquidity={money(data.get('aggregate_liquidity_usd'))} | verified={data.get('token_verified')}")
                    print(f"    authorities: mint_active={data.get('mint_authority_active')} | freeze_active={data.get('freeze_authority_active')} | standard={data.get('token_standard')} | source_stablecoin_flag={data.get('source_stablecoin_flag', 'N/A')} | normalized_class={data.get('normalized_asset_class', data.get('asset_class'))}")
                    print(f"    mandatory components complete: {data.get('mandatory_components_complete', False)} | missing: {', '.join(data.get('missing_mandatory_components') or []) or 'none'}")
                for component, detail in (token.get("components") or {}).items():
                    print(f"    {component}: {detail.get('score', 'N/A')} | coverage={detail.get('coverage', False)} | source={detail.get('source') or 'N/A'}")
                for warning in token.get("warnings") or []:
                    print(f"    warning: {warning}")
        print(f"Pool Fee APR estimado: {pct(pool.calculated_fee_apr)} | Reported APR: {pct(pool.reported_apr)} | Reward APR: {pct(pool.reward_apr)}")
        print(f"Status: {pool.status} | Opportunity trend: {pool.trend}")
        changes = pool.changes or {}
        print(f"Changes: APR {pct(changes.get('apr_change'))} | Volume/TVL {pct(changes.get('volume_tvl_change'))} | TVL {pct(changes.get('tvl_change'))}")
        stats = pool.history_stats or {}
        print("Local history audit:")
        print(f"  snapshots: {stats.get('snapshot_count', 0)} | first: {stats.get('first_snapshot') or 'N/A'} | last: {stats.get('last_snapshot') or 'N/A'}")
        print(f"  duration: {stats.get('duration_hours', 0):.2f}h | current Fee APR: {pct(pool.calculated_fee_apr)} | average: {pct(stats.get('fee_apr_avg'))} | min: {pct(stats.get('fee_apr_min'))} | max: {pct(stats.get('fee_apr_max'))}")
        print("Score breakdown (componente: puntos, peso efectivo):")
        for key, value in pool.score_breakdown.items():
            print(f"  {key:<15} {value:>6.2f} pts  x {pool.effective_weights[key] * 100:>5.2f}%")
        if pool.status in {"INSUFFICIENT_HISTORY", "OBSERVING"}:
            print("  Persistence excluded - insufficient local history")
        if pool.clmm_data is not None:
            heading = "CLMM DATA" if pool.protocol == "Raydium" else ("WHIRLPOOL DATA" if pool.protocol == "Orca" else "DLMM DATA")
            print(heading)
            for key, value in pool.clmm_data.get("available", {}).items():
                if key in {"liquidity_history", "bins", "dlmm_distribution", "dlmm_state", "liquidity_distribution"}:
                    continue
                print(f"  {key}: {value}")
            for value in pool.clmm_data.get("unavailable", []):
                print(f"  unavailable: {value}")
            if pool.protocol == "Meteora":
                state = (pool.protocol_data or {}).get("dlmm_state") or {}
                print("  DLMM DISTRIBUTION SUMMARY")
                print(f"    Pool TVL: {money(state.get('pool_tvl_usd', pool.tvl_usd))} | Observed value: {money(state.get('observed_window_value_usd'))} | Current-price observed value: {money(state.get('estimated_observed_window_value_usd'))} | Estimated total pool value: {money(state.get('estimated_total_pool_value_usd'))} | REST TVL: {money(state.get('rest_tvl_usd', pool.tvl_usd))}")
                print(f"    Distribution state: {state.get('distribution_state', 'N/A')} | Distribution coverage: {pct(state.get('distribution_coverage_pct'))} | Estimated vs REST: {pct(state.get('estimated_vs_rest_difference_pct'))}")
                print(f"    Active bin ID: {state.get('active_bin_id', 'N/A')} | Active bin price: {state.get('active_bin_price', 'N/A')} | Active bin USD: {money(state.get('active_bin_value_usd'))}")
                print(f"    Active bin % observed: {pct((state.get('active_bin_share_of_observed') or 0) * 100) if state.get('active_bin_share_of_observed') is not None else 'N/A'} | Active bin % pool: {pct((state.get('active_bin_share_of_pool') or 0) * 100) if state.get('active_bin_share_of_pool') is not None else 'N/A'}")
                print(f"    Bin step: {state.get('bin_step', 'N/A')} | Raw bins: {state.get('raw_bins_received', 'N/A')} | Unique bins: {state.get('unique_bins', 'N/A')} | Duplicates removed: {state.get('duplicate_bins_removed', 'N/A')} | Window: ±{state.get('requested_bin_window', 'N/A')} | SDK calls: {state.get('sdk_calls', 'N/A')}")
                for band in ("0_5", "1", "2", "5", "10"):
                    observed = state.get(f"within_{band}pct_observed_pct", state.get(f"liquidity_within_{band}pct"))
                    pool_tvl = state.get(f"within_{band}pct_pool_tvl_pct")
                    print(f"    Within ±{band.replace('_', '.')}%: {pct(observed)} of observed distribution | {pct(pool_tvl)} of pool TVL")
                print(f"    Top 1: {pct(state.get('top_1_bin_pct'))} | Top 5: {pct(state.get('top_5_bins_pct'))} | Top 10: {pct(state.get('top_10_bins_pct'))} | HHI: {state.get('hhi', 'N/A')} | Effective bins: {state.get('effective_number_of_bins', 'N/A')}")
            if pool.protocol == "Orca":
                print("  yieldOverTvl: raw only; excluded from score until its period/annualization and fee/incentive semantics are verified")
            print("  Position APR: N/A (insufficient defensible public data)")

    if pools:
        top = pools[0]
        print("\nEXPLICACIÓN DEL POOL #1")
        print(f"{top.pair} | {top.protocol} | {fee_tier(top.fee_tier)} | {top.pool_type} | {top.pool_address}")
        print("El score es la suma ponderada de los componentes disponibles; persistence se excluye si no hay histórico suficiente.")
        for key, value in top.score_breakdown.items():
            print(f"- {key}: {value:.2f} x {top.effective_weights[key] * 100:.2f}% = {value * top.effective_weights[key]:.2f}")
        print(f"Total OPP SCORE: {top.opportunity_score:.2f}/100")
        print_common_pair(report)


def main():
    configure_console()
    parser = argparse.ArgumentParser(description="Crypto Radar Solana - Raydium LP scanner")
    parser.add_argument("--page-size", type=int, default=config.PAGE_SIZE)
    parser.add_argument("--max-pages", type=int, default=config.MAX_PAGES)
    parser.add_argument("--debug", action="store_true", help="log filter discard reasons")
    args = parser.parse_args()
    if args.debug:
        logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(message)s")
    db = Database(config.DATABASE_PATH)
    try:
        reports, statuses, reports_by_protocol = scan_all(db, page_size=args.page_size, max_pages=args.max_pages)
        report = combine_reports(reports)
        live_statuses = [status for status in statuses.values() if status == "LIVE"]
        print("RADAR CYCLE STATUS: LIVE_DATA" if live_statuses else "RADAR CYCLE STATUS: NO_LIVE_DATA")
        pipeline_reports = [reports_by_protocol.get(name) for name in ("Raydium", "Orca", "Meteora")]
        if all(report is not None and report.pipeline_counts and report.pipeline_counts["qualifying_count"] == 0 for report in pipeline_reports) and all(status in {"LIVE", "LIVE_EMPTY_RESPONSE"} for status in statuses.values()):
            print("RADAR PIPELINE STATUS: EMPTY_PIPELINE")
            for name, source_report in zip(("Raydium", "Orca", "Meteora"), pipeline_reports):
                print(f"{name:<10} pipeline={source_report.pipeline_counts}")
        print("PROTOCOL STATUS")
        for protocol, status in statuses.items():
            health = HEALTH.get(protocol)
            if health and health.last_success:
                from datetime import datetime, timezone
                age = int(max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(health.last_success)).total_seconds()))
                print(f"{protocol:<10} {health.status:<12} {health.response_time_ms or 0:.0f} ms   last success {age}s   failures {health.consecutive_failures}")
            else:
                print(f"{protocol:<10} {status:<12} last error {health.last_error if health else status}")
        for protocol in ("Raydium", "Orca", "Meteora"):
            source_report = reports_by_protocol.get(protocol)
            if source_report:
                print(f"{protocol:<10} scanned={source_report.analyzed} qualifying={len(source_report.pools)} TVL={source_report.discarded_tvl} Volume={source_report.discarded_volume} Volume/TVL={source_report.discarded_ratio} Fee APR={source_report.discarded_fee} Required incomplete={source_report.required_incomplete} Potential candidates incomplete={source_report.potential_candidates_incomplete}")
                if source_report.pipeline_counts:
                    counts = source_report.pipeline_counts
                    print(f"  pipeline raw={counts['fetched_raw_count']} normalized={counts['normalized_count']} allowed={counts['allowed_token_count']} pre_filter={counts['pre_filter_count']} qualifying={counts['qualifying_count']}")
                if args.debug:
                    print(f"  Required incomplete fields: {source_report.incomplete_by_field}")
                    print(f"  Incomplete also below TVL minimum: {source_report.incomplete_below_tvl}")
                    print(f"  Optional missing: {source_report.optional_missing}")
                if protocol == "Orca":
                    fields = source_report.incomplete_by_field or {}
                    print("ORCA REQUIRED INCOMPLETE BREAKDOWN")
                    print(f"  missing TVL: {fields.get('tvl', 0)}")
                    print(f"  missing volume: {fields.get('volume', 0)}")
                    print(f"  missing fee information: {fields.get('fee data', 0)}")
                    print(f"  missing token metadata: {fields.get('token metadata', 0)}")
                    print(f"  missing address: {fields.get('address', 0)}")
                    print(f"  incomplete also below TVL $5M: {source_report.incomplete_below_tvl}")
        attach_clmm_data(report, db)
        print_report(report)
    except Exception as exc:
        db.audit("Raydium", config.API_BASE_URL + config.RAYDIUM_POOLS_ENDPOINT, None, ok=False, error=str(exc))
        print(f"Raydium ERROR: {exc}")
        raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
