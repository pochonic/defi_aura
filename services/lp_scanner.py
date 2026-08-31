"""Unified Raydium + Orca LP opportunity scanner."""

from providers.orca import OrcaClient, normalize as normalize_orca
from providers.meteora import MeteoraClient, normalize as normalize_meteora
from database import utc_now
from raydium import RaydiumClient, ScanResult, normalize, scan_source
from services.provider_runtime import HEALTH, fetch_with_retry, fallback_report


def scan_all(db, raydium_client=None, orca_client=None, meteora_client=None, page_size=1000, max_pages=20):
    results = []
    reports_by_protocol = {}
    statuses = {}
    scan_id = utc_now()
    for name, client, normalizer in [
        ("Raydium", raydium_client or RaydiumClient(), None),
        ("Orca", orca_client or OrcaClient(), normalize_orca),
        ("Meteora", meteora_client or MeteoraClient(), normalize_meteora),
    ]:
        try:
            raw, pages, health = fetch_with_retry(name, client, page_size, max_pages, db)
            if raw is None:
                fallback = fallback_report(db, name)
                reports_by_protocol[name] = fallback
                if fallback.pools:
                    latest = max((row["snapshot_time"] for row in db.latest_snapshots_for_protocol(name)), default=None)
                    if latest:
                        health.last_success = latest
                    results.append(fallback)
                    if health.status != "DEGRADED":
                        health.status = "STALE"
                    statuses[name] = health.status
                else:
                    statuses[name] = "UNAVAILABLE"
                continue
            result = scan_source(db, raw, client.endpoint, normalizer or normalize, name, scan_id)
            results.append(result)
            reports_by_protocol[name] = result
            statuses[name] = health.status
        except Exception as exc:
            db.audit(name, client.endpoint, None, ok=False, error=str(exc))
            statuses[name] = f"ERROR: {exc}"
    from services.volatility_risk import update_pool
    for result in results:
        for pool in result.pools:
            # Last-known-good data is display-only. It must not create a new
            # market observation, advance volatility history, or alter trends.
            if pool.data_state != "LIVE":
                continue
            try:
                update_pool(db, pool)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning("Volatility risk unavailable for %s: %s", pool.pool_address, exc)
    return results, statuses, reports_by_protocol
