import json
import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

@dataclass
class IngestionStats:
    reserves: int = 0
    saved: int = 0
    skipped: int = 0
    missing: int = 0
    errors: int = 0


def persist_lending_snapshots(db, snapshots, stats=None):
    saved = 0
    stats = stats or IngestionStats()
    for item in snapshots:
        item.validate()
        if item.missing_fields:
            stats.missing += 1
        db.conn.execute("""INSERT INTO lending_markets
            (protocol, chain, market_id, reserve_id, asset_symbol, asset_mint, market_name, first_seen_at, last_seen_at, source, description, is_primary, is_curated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(protocol, chain, market_id, reserve_id) DO UPDATE SET
              asset_symbol=excluded.asset_symbol, asset_mint=excluded.asset_mint,
              market_name=excluded.market_name, last_seen_at=excluded.last_seen_at, source=excluded.source,
              description=excluded.description, is_primary=excluded.is_primary, is_curated=excluded.is_curated""",
            (item.protocol, item.chain, item.market_id, item.reserve_id, item.asset_symbol, item.asset_mint,
             item.market_name, item.observed_at, item.observed_at, item.source, item.market_description,
             int(item.market_is_primary) if item.market_is_primary is not None else None,
             int(item.market_is_curated) if item.market_is_curated is not None else None))
        cursor = db.conn.execute("""INSERT OR IGNORE INTO lending_snapshots
            (protocol, chain, market_id, reserve_id, asset_symbol, asset_mint, supply_apy, borrow_apy,
             utilization, total_supplied_usd, total_borrowed_usd, available_liquidity_usd, observed_at,
             source, source_endpoint, missing_fields, quality_flags, available_amount_native, available_amount_decimals,
             available_amount_source, source_metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (item.protocol, item.chain, item.market_id, item.reserve_id, item.asset_symbol, item.asset_mint,
             item.supply_apy, item.borrow_apy, item.utilization, item.total_supplied_usd, item.total_borrowed_usd,
             item.available_liquidity_usd, item.observed_at, item.source, item.source_endpoint,
             json.dumps(item.missing_fields), json.dumps(item.quality_flags), item.available_liquidity_native,
             item.available_liquidity_decimals, item.available_liquidity_source,
             json.dumps(item.source_metadata, ensure_ascii=False)))
        saved += cursor.rowcount
    db.conn.commit()
    stats.saved += saved
    return saved


def fetch_and_persist(db, client):
    snapshots = client.fetch_lending_markets()
    stats = IngestionStats(reserves=client.last_report["reserves"], skipped=client.last_report["skipped"], errors=client.last_report["errors"])
    persist_lending_snapshots(db, snapshots, stats)
    return snapshots, stats
