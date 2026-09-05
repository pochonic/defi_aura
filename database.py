import json
import os
import sqlite3
from statistics import median
from datetime import datetime, timezone
from pathlib import Path
import config


class _CompatRow(dict):
    """A psycopg row compatible with the sqlite3.Row access used by the app."""
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class _PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.rowcount = cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        return _CompatRow(row) if row is not None else None

    def fetchall(self):
        return [_CompatRow(row) for row in self._cursor.fetchall()]


class _PostgresConnection:
    """Small DB-API adapter so the existing repository code stays shared."""
    is_postgres = True

    def __init__(self, url):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("DATABASE_URL requires the psycopg PostgreSQL driver") from exc
        self._conn = psycopg.connect(url, row_factory=dict_row)

    @staticmethod
    def _translate(query):
        query = query.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        query = query.replace("datetime('now', ?)", "(CURRENT_TIMESTAMP + ?::interval)")
        if "INSERT OR REPLACE INTO api_status" in query:
            query = query.replace("INSERT OR REPLACE INTO api_status", "INSERT INTO api_status")
            query += " ON CONFLICT(source) DO UPDATE SET checked_at=EXCLUDED.checked_at, ok=EXCLUDED.ok, error=EXCLUDED.error"
        elif "INSERT OR REPLACE INTO asset_risk_cache" in query:
            query = query.replace("INSERT OR REPLACE INTO asset_risk_cache", "INSERT INTO asset_risk_cache")
            query += " ON CONFLICT(cache_key) DO UPDATE SET fetched_at=EXCLUDED.fetched_at, payload=EXCLUDED.payload"
        elif "INSERT INTO" in query and "ON CONFLICT" not in query and query.strip().upper().startswith("INSERT"):
            query += " ON CONFLICT DO NOTHING"
        return query.replace("?", "%s")

    def execute(self, query, params=()):
        return _PostgresCursor(self._conn.execute(self._translate(query), params))

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str | None = None):
        database_url = os.getenv("DATABASE_URL") or getattr(config, "DATABASE_URL", None)
        self.backend = "postgresql" if database_url else "sqlite"
        self.path = database_url or str(path or config.DATABASE_PATH)
        if database_url:
            self.conn = _PostgresConnection(database_url)
        else:
            self.conn = sqlite3.connect(self.path)
            self.conn.row_factory = sqlite3.Row
        schema = """
        CREATE TABLE IF NOT EXISTS lp_snapshots (
            id INTEGER PRIMARY KEY,
            snapshot_time TEXT NOT NULL,
            protocol TEXT NOT NULL,
            pool_address TEXT NOT NULL,
            pool_type TEXT,
            token_a TEXT,
            token_b TEXT,
            fee_tier REAL,
            fees_24h_usd REAL,
            nominal_fee_apr REAL,
            real_fee_apr REAL,
            orca_reported_fees_apr REAL,
            expected_fees_from_nominal_rate REAL,
            fee_difference_usd REAL,
            fee_difference_pct REAL,
            fee_window_comparability TEXT,
            yield_over_tvl REAL,
            fee_model TEXT,
            reward_known INTEGER,
            tvl_usd REAL,
            volume_24h REAL,
            volume_7d REAL,
            reported_apr REAL,
            reward_apr REAL,
            calculated_fee_apr REAL,
            volume_tvl_ratio REAL,
            score REAL,
            opportunity_score REAL,
            opportunity_trend TEXT,
            risk_score REAL,
            scan_id TEXT,
            score_breakdown TEXT,
            status TEXT,
            source TEXT NOT NULL,
            UNIQUE(pool_address, snapshot_time)
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY,
            alert_key TEXT NOT NULL,
            alert_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            score REAL NOT NULL,
            message TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS source_audit (
            id INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            raw_value TEXT,
            ok INTEGER NOT NULL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS api_status (
            source TEXT PRIMARY KEY,
            checked_at TEXT NOT NULL,
            ok INTEGER NOT NULL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS lending_markets (
            id INTEGER PRIMARY KEY,
            protocol TEXT NOT NULL,
            chain TEXT NOT NULL,
            market_id TEXT NOT NULL,
            reserve_id TEXT NOT NULL,
            asset_symbol TEXT,
            asset_mint TEXT,
            market_name TEXT,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            source TEXT NOT NULL,
            UNIQUE(protocol, chain, market_id, reserve_id)
        );
        CREATE TABLE IF NOT EXISTS lending_snapshots (
            id INTEGER PRIMARY KEY,
            protocol TEXT NOT NULL,
            chain TEXT NOT NULL,
            market_id TEXT NOT NULL,
            reserve_id TEXT NOT NULL,
            asset_symbol TEXT,
            asset_mint TEXT,
            supply_apy REAL,
            borrow_apy REAL,
            utilization REAL,
            total_supplied_usd REAL,
            total_borrowed_usd REAL,
            available_liquidity_usd REAL,
            observed_at TEXT NOT NULL,
            source TEXT NOT NULL,
            source_endpoint TEXT NOT NULL,
            missing_fields TEXT,
            quality_flags TEXT,
            utilization_source_type TEXT,
            utilization_source TEXT,
            utilization_calculation_version TEXT,
            utilization_calculated_at TEXT,
            available_amount_native REAL,
            available_amount_decimals INTEGER,
            available_amount_source TEXT,
            source_metadata TEXT,
            UNIQUE(protocol, chain, market_id, reserve_id, observed_at)
        );
        CREATE TABLE IF NOT EXISTS lending_evaluations (
            id INTEGER PRIMARY KEY,
            protocol TEXT NOT NULL,
            chain TEXT NOT NULL,
            market_id TEXT NOT NULL,
            reserve_id TEXT NOT NULL,
            asset_symbol TEXT,
            observed_at TEXT NOT NULL,
            eligible INTEGER NOT NULL,
            eligibility_reasons TEXT NOT NULL,
            economic_relevance TEXT NOT NULL,
            opportunity_score REAL,
            provisional_opportunity_score REAL,
            available_points_raw REAL,
            available_weight REAL NOT NULL DEFAULT 0,
            missing_weight REAL NOT NULL DEFAULT 1,
            score_model TEXT NOT NULL DEFAULT 'lending_opportunity',
            score_version TEXT NOT NULL DEFAULT '1.0',
            score_confidence REAL NOT NULL,
            score_status TEXT NOT NULL,
            history_status TEXT NOT NULL,
            components TEXT NOT NULL,
            component_details TEXT NOT NULL DEFAULT '{}',
            flags TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(protocol, chain, market_id, reserve_id, observed_at)
        );
        CREATE TABLE IF NOT EXISTS asset_risk_cache (
            cache_key TEXT PRIMARY KEY,
            fetched_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pair_price_candidates (
            id INTEGER PRIMARY KEY,
            timestamp_bucket TEXT NOT NULL,
            canonical_pair TEXT NOT NULL,
            token_a_mint TEXT,
            token_b_mint TEXT,
            price REAL NOT NULL,
            source TEXT NOT NULL,
            protocol TEXT NOT NULL,
            pool_address TEXT NOT NULL,
            freshness TEXT NOT NULL DEFAULT 'UNKNOWN',
            retrieved_at TEXT,
            source_timestamp TEXT,
            UNIQUE(timestamp_bucket, canonical_pair, protocol, pool_address, source)
        );
        CREATE TABLE IF NOT EXISTS pair_price_snapshots (
            id INTEGER PRIMARY KEY,
            timestamp_bucket TEXT NOT NULL,
            canonical_pair TEXT NOT NULL,
            price REAL NOT NULL,
            source TEXT NOT NULL,
            source_count INTEGER NOT NULL,
            min_price REAL NOT NULL,
            max_price REAL NOT NULL,
            dispersion_pct REAL NOT NULL,
            quality_warning TEXT,
            excluded_count INTEGER NOT NULL DEFAULT 0,
            UNIQUE(canonical_pair, timestamp_bucket)
        );
        """
        if self.backend == "postgresql":
            schema = schema.replace("INTEGER PRIMARY KEY", "BIGSERIAL PRIMARY KEY")
            for statement in schema.split(";"):
                if statement.strip():
                    self.conn.execute(statement)
        else:
            self.conn.executescript(schema)
        pair_columns = self._table_columns("pair_price_snapshots")
        if pair_columns and "timestamp_bucket" not in pair_columns:
            # Preserve the first implementation and migrate its observations
            # into the canonical bucketed model instead of deleting history.
            legacy_rows = self.conn.execute("SELECT snapshot_time, canonical_pair, token_a_mint, token_b_mint, price_ratio, source, protocol, pool_address FROM pair_price_snapshots").fetchall()
            self.conn.execute("ALTER TABLE pair_price_snapshots RENAME TO pair_price_snapshots_legacy")
            self.conn.execute("""CREATE TABLE pair_price_snapshots (
                id INTEGER PRIMARY KEY, timestamp_bucket TEXT NOT NULL, canonical_pair TEXT NOT NULL,
                price REAL NOT NULL, source TEXT NOT NULL, source_count INTEGER NOT NULL,
                min_price REAL NOT NULL, max_price REAL NOT NULL, dispersion_pct REAL NOT NULL,
                UNIQUE(canonical_pair, timestamp_bucket))""")
            for row in legacy_rows:
                bucket = row["snapshot_time"][:13] + ":00:00+00:00"
                self.conn.execute("""INSERT OR IGNORE INTO pair_price_candidates
                    (timestamp_bucket, canonical_pair, token_a_mint, token_b_mint, price, source, protocol, pool_address)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (bucket, row["canonical_pair"], row["token_a_mint"], row["token_b_mint"], row["price_ratio"], row["source"], row["protocol"], row["pool_address"]))
            for row in self.conn.execute("SELECT DISTINCT canonical_pair, timestamp_bucket FROM pair_price_candidates"):
                self._rebuild_pair_bucket(row["canonical_pair"], row["timestamp_bucket"])
        candidate_columns = self._table_columns("pair_price_candidates")
        for name, definition in (("freshness", "TEXT NOT NULL DEFAULT 'UNKNOWN'"), ("retrieved_at", "TEXT"), ("source_timestamp", "TEXT")):
            if name not in candidate_columns:
                self.conn.execute(f"ALTER TABLE pair_price_candidates ADD COLUMN {name} {definition}")
        snapshot_columns = self._table_columns("pair_price_snapshots")
        for name, definition in (("quality_warning", "TEXT"), ("excluded_count", "INTEGER NOT NULL DEFAULT 0")):
            if name not in snapshot_columns:
                self.conn.execute(f"ALTER TABLE pair_price_snapshots ADD COLUMN {name} {definition}")
        columns = self._table_columns("lp_snapshots")
        if "pool_type" not in columns:
            self.conn.execute("ALTER TABLE lp_snapshots ADD COLUMN pool_type TEXT")
        if "opportunity_score" not in columns:
            self.conn.execute("ALTER TABLE lp_snapshots ADD COLUMN opportunity_score REAL")
        if "risk_score" not in columns:
            self.conn.execute("ALTER TABLE lp_snapshots ADD COLUMN risk_score REAL")
        for name, definition in (("fees_24h_usd", "REAL"), ("nominal_fee_apr", "REAL"), ("real_fee_apr", "REAL"), ("orca_reported_fees_apr", "REAL"), ("expected_fees_from_nominal_rate", "REAL"), ("fee_difference_usd", "REAL"), ("fee_difference_pct", "REAL"), ("fee_window_comparability", "TEXT"), ("yield_over_tvl", "REAL"), ("fee_model", "TEXT"), ("reward_known", "INTEGER"), ("scan_id", "TEXT"), ("opportunity_trend", "TEXT")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE lp_snapshots ADD COLUMN {name} {definition}")
        lending_market_columns = self._table_columns("lending_markets")
        for name, definition in (("description", "TEXT"), ("is_primary", "INTEGER"), ("is_curated", "INTEGER")):
            if name not in lending_market_columns:
                self.conn.execute(f"ALTER TABLE lending_markets ADD COLUMN {name} {definition}")
        lending_snapshot_columns = self._table_columns("lending_snapshots")
        if "quality_flags" not in lending_snapshot_columns:
            self.conn.execute("ALTER TABLE lending_snapshots ADD COLUMN quality_flags TEXT")
        for name, definition in (("utilization_source_type", "TEXT"), ("utilization_source", "TEXT"), ("utilization_calculation_version", "TEXT"), ("utilization_calculated_at", "TEXT"), ("available_amount_native", "REAL"), ("available_amount_decimals", "INTEGER"), ("available_amount_source", "TEXT"), ("source_metadata", "TEXT")):
            if name not in lending_snapshot_columns:
                self.conn.execute(f"ALTER TABLE lending_snapshots ADD COLUMN {name} {definition}")
        lending_evaluation_columns = self._table_columns("lending_evaluations")
        for name, definition in (("provisional_opportunity_score", "REAL"), ("available_points_raw", "REAL"), ("available_weight", "REAL NOT NULL DEFAULT 0"), ("missing_weight", "REAL NOT NULL DEFAULT 1"), ("score_model", "TEXT NOT NULL DEFAULT 'lending_opportunity'"), ("score_version", "TEXT NOT NULL DEFAULT '1.0'")):
            if name not in lending_evaluation_columns:
                self.conn.execute(f"ALTER TABLE lending_evaluations ADD COLUMN {name} {definition}")
        if "history_status" not in lending_evaluation_columns:
            self.conn.execute("ALTER TABLE lending_evaluations ADD COLUMN history_status TEXT NOT NULL DEFAULT '{}'")
        if "component_details" not in lending_evaluation_columns:
            self.conn.execute("ALTER TABLE lending_evaluations ADD COLUMN component_details TEXT NOT NULL DEFAULT '{}'")
        self.conn.commit()

    def _table_columns(self, table):
        if self.backend == "postgresql":
            rows = self.conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,)).fetchall()
            return {row["column_name"] for row in rows}
        return {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}

    def audit(self, source, endpoint, raw_value, ok=True, error=None):
        self.conn.execute("INSERT INTO source_audit(source, endpoint, timestamp, raw_value, ok, error) VALUES (?, ?, ?, ?, ?, ?)",
                          (source, endpoint, utc_now(), json.dumps(raw_value, ensure_ascii=False) if raw_value is not None else None, int(ok), error))
        self.conn.execute("INSERT OR REPLACE INTO api_status(source, checked_at, ok, error) VALUES (?, ?, ?, ?)",
                          (source, utc_now(), int(ok), error))
        self.conn.commit()

    def latest_lending_snapshots(self, asset=None, protocol=None, limit=20):
        query = "SELECT * FROM lending_snapshots WHERE " + self._valid_lending_snapshot_sql()
        params = []
        if asset:
            query += " AND asset_symbol = ?"
            params.append(asset)
        if protocol:
            query += " AND lower(protocol) = lower(?)"
            params.append(protocol)
        query += " ORDER BY observed_at DESC, asset_symbol ASC LIMIT ?"
        params.append(int(limit))
        return self.conn.execute(query, params).fetchall()

    def lending_history(self, asset=None, protocol=None):
        query = "SELECT * FROM lending_snapshots WHERE " + self._valid_lending_snapshot_sql()
        params = []
        if asset:
            query += " AND asset_symbol = ?"
            params.append(asset)
        if protocol:
            query += " AND lower(protocol) = lower(?)"
            params.append(protocol)
        query += " ORDER BY market_id, reserve_id, observed_at"
        return self.conn.execute(query, params).fetchall()

    def latest_lending_reserves(self, asset=None, protocol=None):
        query = """SELECT s.* FROM lending_snapshots s JOIN (
            SELECT protocol, chain, market_id, reserve_id, MAX(observed_at) AS observed_at
            FROM lending_snapshots WHERE """ + self._valid_lending_snapshot_sql() + """ GROUP BY protocol, chain, market_id, reserve_id
        ) latest ON latest.protocol=s.protocol AND latest.chain=s.chain AND latest.market_id=s.market_id
            AND latest.reserve_id=s.reserve_id AND latest.observed_at=s.observed_at WHERE """ + self._valid_lending_snapshot_sql("s")
        params = []
        if asset:
            query += " AND s.asset_symbol = ?"; params.append(asset)
        if protocol:
            query += " AND lower(s.protocol) = lower(?)"; params.append(protocol)
        return self.conn.execute(query, params).fetchall()

    @staticmethod
    def _valid_lending_snapshot_sql(alias=None):
        prefix = f"{alias}." if alias else ""
        # Save v1/v2 rows are quarantined from every analytics read even
        # before the operational cleanup runs.
        return (
            f"(lower({prefix}protocol) <> 'save' OR "
            f"(COALESCE({prefix}source_metadata, '') LIKE '%save-rest-percent-sdk-units-v3%' "
            f"AND COALESCE({prefix}quality_flags, '') NOT LIKE '%anomalous_supply_apy%' "
            f"AND COALESCE({prefix}quality_flags, '') NOT LIKE '%anomalous_borrow_apy%'))"
        )

    def save_lending_evaluation(self, snapshot, evaluation):
        self.conn.execute("""INSERT INTO lending_evaluations
            (protocol, chain, market_id, reserve_id, asset_symbol, observed_at, eligible, eligibility_reasons,
             economic_relevance, opportunity_score, provisional_opportunity_score, available_points_raw, available_weight, missing_weight,
             score_model, score_version, score_confidence, score_status, history_status, components, component_details, flags, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(protocol, chain, market_id, reserve_id, observed_at) DO UPDATE SET
              eligible=excluded.eligible, eligibility_reasons=excluded.eligibility_reasons,
              economic_relevance=excluded.economic_relevance, opportunity_score=excluded.opportunity_score,
              provisional_opportunity_score=excluded.provisional_opportunity_score,
              available_points_raw=excluded.available_points_raw,
              available_weight=excluded.available_weight, missing_weight=excluded.missing_weight,
              score_model=excluded.score_model, score_version=excluded.score_version,
              score_confidence=excluded.score_confidence, score_status=excluded.score_status,
              history_status=excluded.history_status,
              components=excluded.components, component_details=excluded.component_details,
              flags=excluded.flags, created_at=excluded.created_at""",
            (snapshot["protocol"], snapshot["chain"], snapshot["market_id"], snapshot["reserve_id"], snapshot["asset_symbol"],
             snapshot["observed_at"], int(evaluation["eligibility"]["eligible"]), json.dumps(evaluation["eligibility"]["reasons"]),
             evaluation["economic_relevance"], evaluation["opportunity_score"], evaluation["provisional_opportunity_score"],
             evaluation["available_points_raw"], evaluation["available_weight"], evaluation["missing_weight"],
             evaluation["score_model"], evaluation["score_version"], evaluation["confidence"], evaluation["score_status"],
             json.dumps(evaluation["history_status"]), json.dumps(evaluation["components"]), json.dumps(evaluation["component_details"]),
             json.dumps(evaluation["flags"]), utc_now()))
        self.conn.commit()

    def get_asset_cache(self, cache_key):
        return self.conn.execute("SELECT cache_key, fetched_at, payload FROM asset_risk_cache WHERE cache_key = ?", (cache_key,)).fetchone()

    def put_asset_cache(self, cache_key, payload):
        self.conn.execute("INSERT OR REPLACE INTO asset_risk_cache(cache_key, fetched_at, payload) VALUES (?, ?, ?)",
                          (cache_key, utc_now(), json.dumps(payload, ensure_ascii=False)))
        self.conn.commit()

    def insert_pair_price_snapshot(self, canonical_pair, token_a_mint, token_b_mint, price_ratio, source, protocol, pool_address):
        timestamp = utc_now()
        bucket = timestamp[:13] + ":00:00+00:00"
        self.conn.execute("""INSERT OR IGNORE INTO pair_price_candidates
            (timestamp_bucket, canonical_pair, token_a_mint, token_b_mint, price, source, protocol, pool_address, freshness, retrieved_at, source_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'FRESH', ?, ?)""",
            (bucket, canonical_pair, token_a_mint, token_b_mint, price_ratio, source, protocol, pool_address, timestamp, timestamp))
        self._rebuild_pair_bucket(canonical_pair, bucket)
        self.conn.commit()

    def _rebuild_pair_bucket(self, canonical_pair, bucket):
        rows = self.conn.execute("SELECT * FROM pair_price_candidates WHERE canonical_pair = ? AND timestamp_bucket = ?", (canonical_pair, bucket)).fetchall()
        for row in rows:
            previous = self.conn.execute("SELECT price FROM pair_price_candidates WHERE canonical_pair = ? AND protocol = ? AND pool_address = ? AND source = ? AND timestamp_bucket < ? ORDER BY timestamp_bucket DESC LIMIT ?", (canonical_pair, row["protocol"], row["pool_address"], row["source"], bucket, config.VOLATILITY_FROZEN_BUCKETS - 1)).fetchall()
            other = self.conn.execute("SELECT price FROM pair_price_candidates WHERE canonical_pair = ? AND timestamp_bucket = ? AND NOT (protocol = ? AND pool_address = ? AND source = ?)", (canonical_pair, bucket, row["protocol"], row["pool_address"], row["source"])).fetchall()
            frozen = len(previous) >= config.VOLATILITY_FROZEN_BUCKETS - 1 and all(float(item["price"]) == float(row["price"]) for item in previous) and any(abs(float(item["price"]) / float(row["price"]) - 1) * 100 > config.VOLATILITY_FROZEN_OTHER_MOVE_PCT for item in other if row["price"])
            if frozen:
                self.conn.execute("UPDATE pair_price_candidates SET freshness = 'SUSPECT_STALE' WHERE id = ?", (row["id"],))
        rows = self.conn.execute("SELECT * FROM pair_price_candidates WHERE canonical_pair = ? AND timestamp_bucket = ?", (canonical_pair, bucket)).fetchall()
        selected_rows = [row for row in rows if row["freshness"] not in {"INVALID", "STALE_CONFIRMED"} and not (config.VOLATILITY_EXCLUDE_SUSPECT_SOURCES and row["freshness"] == "SUSPECT_STALE")]
        if not selected_rows:
            selected_rows = [row for row in rows if row["freshness"] not in {"INVALID", "STALE_CONFIRMED"}]
        prices = [float(row["price"]) for row in selected_rows if row["price"] is not None and float(row["price"]) > 0]
        if not prices:
            return
        local = [float(row["price"]) for row in selected_rows if row["source"] == "LOCAL" and row["price"] is not None and float(row["price"]) > 0]
        selected = local or prices
        value = median(selected)
        dispersion = (max(selected) - min(selected)) / value * 100 if value else 0.0
        sources = {row["source"] for row in rows}
        source = "HYBRID" if "LOCAL" in sources and "EXTERNAL" in sources else ("LOCAL" if "LOCAL" in sources else "EXTERNAL")
        all_prices = [float(row["price"]) for row in rows if row["price"] is not None and float(row["price"]) > 0]
        warning = "SUSPECT_STALE source excluded" if any(row["freshness"] == "SUSPECT_STALE" for row in rows) else None
        self.conn.execute("""INSERT INTO pair_price_snapshots
            (timestamp_bucket, canonical_pair, price, source, source_count, min_price, max_price, dispersion_pct, quality_warning, excluded_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(canonical_pair, timestamp_bucket) DO UPDATE SET
              price=excluded.price, source=excluded.source, source_count=excluded.source_count,
              min_price=excluded.min_price, max_price=excluded.max_price, dispersion_pct=excluded.dispersion_pct,
              quality_warning=excluded.quality_warning, excluded_count=excluded.excluded_count""",
            (bucket, canonical_pair, value, source, len(selected), min(all_prices), max(all_prices), (max(all_prices) - min(all_prices)) / value * 100 if value else 0.0, warning, len(rows) - len(selected)))

    def insert_external_pair_prices(self, canonical_pair, points):
        for timestamp, price in points:
            if price is None or price <= 0:
                continue
            dt = datetime.fromtimestamp(timestamp / 1000, timezone.utc) if timestamp > 10_000_000_000 else datetime.fromtimestamp(timestamp, timezone.utc)
            bucket = dt.replace(minute=0, second=0, microsecond=0).isoformat()
            self.conn.execute("""INSERT OR IGNORE INTO pair_price_candidates
                (timestamp_bucket, canonical_pair, token_a_mint, token_b_mint, price, source, protocol, pool_address, freshness, retrieved_at, source_timestamp)
                VALUES (?, ?, ?, ?, ?, 'EXTERNAL', 'External', 'external', 'FRESH', ?, ?)""", (bucket, canonical_pair, None, None, price, utc_now(), bucket))
            self._rebuild_pair_bucket(canonical_pair, bucket)
        self.conn.commit()

    def pair_price_history(self, canonical_pair, since=None):
        query = "SELECT timestamp_bucket AS snapshot_time, canonical_pair, price AS price_ratio, source, source_count, min_price, max_price, dispersion_pct, quality_warning, excluded_count FROM pair_price_snapshots WHERE canonical_pair = ?"
        params = [canonical_pair]
        if since:
            # Filter on the physical column, not the SELECT alias. PostgreSQL
            # does not allow SELECT aliases in WHERE (SQLite happens to).
            query += " AND timestamp_bucket >= ?"
            params.append(since)
        query += " ORDER BY timestamp_bucket ASC"
        return self.conn.execute(query, params).fetchall()

    def pair_price_source_ranges(self, canonical_pair):
        """Exact source ranges from raw candidates, not the merged hourly series."""
        rows = self.conn.execute("""
            SELECT source, MIN(timestamp_bucket) AS first, MAX(timestamp_bucket) AS last,
                   COUNT(*) AS observations
            FROM pair_price_candidates
            WHERE canonical_pair = ? GROUP BY source
        """, (canonical_pair,)).fetchall()
        return {row["source"]: dict(row) for row in rows}

    def history(self, pool_address, hours, protocol="Raydium"):
        return self.conn.execute("""
            SELECT calculated_fee_apr AS apr FROM lp_snapshots
            WHERE protocol = ? AND pool_address = ? AND snapshot_time >= datetime('now', ?)
              AND calculated_fee_apr IS NOT NULL
        """, (protocol, pool_address, f"-{hours} hours")).fetchall()

    def history_count(self, pool_address, hours, protocol="Raydium"):
        return len(self.history(pool_address, hours, protocol))

    def latest_snapshot(self, pool_address, protocol="Raydium"):
        return self.conn.execute("SELECT * FROM lp_snapshots WHERE protocol = ? AND pool_address = ? ORDER BY snapshot_time DESC LIMIT 1", (protocol, pool_address)).fetchone()

    def latest_snapshots_for_protocol(self, protocol):
        return self.conn.execute("""
            SELECT s.* FROM lp_snapshots s
            JOIN (SELECT pool_address, MAX(snapshot_time) AS snapshot_time
                  FROM lp_snapshots WHERE protocol = ? GROUP BY pool_address) latest
              ON latest.pool_address = s.pool_address AND latest.snapshot_time = s.snapshot_time
            WHERE s.protocol = ? AND s.opportunity_score IS NOT NULL
        """, (protocol, protocol)).fetchall()

    def snapshot_exists(self, protocol, pool_address, scan_id):
        return self.conn.execute("SELECT 1 FROM lp_snapshots WHERE protocol = ? AND pool_address = ? AND scan_id = ? LIMIT 1", (protocol, pool_address, scan_id)).fetchone() is not None

    def history_stats(self, pool_address, protocol="Raydium"):
        row = self.conn.execute("""
            SELECT COUNT(*) AS snapshot_count, MIN(snapshot_time) AS first_snapshot,
                   MAX(snapshot_time) AS last_snapshot, MIN(calculated_fee_apr) AS fee_apr_min,
                   MAX(calculated_fee_apr) AS fee_apr_max, AVG(calculated_fee_apr) AS fee_apr_avg
            FROM lp_snapshots WHERE protocol = ? AND pool_address = ? AND calculated_fee_apr IS NOT NULL
        """, (protocol, pool_address)).fetchone()
        stats = dict(row)
        stats["duration_hours"] = 0.0
        if stats["first_snapshot"] and stats["last_snapshot"]:
            from datetime import datetime
            first = datetime.fromisoformat(stats["first_snapshot"])
            last = datetime.fromisoformat(stats["last_snapshot"])
            stats["duration_hours"] = max(0.0, (last - first).total_seconds() / 3600)
        return stats

    def insert_snapshot(self, item):
        self.conn.execute("""INSERT OR IGNORE INTO lp_snapshots
            (snapshot_time, protocol, pool_address, pool_type, token_a, token_b, fee_tier, fees_24h_usd,
             nominal_fee_apr, orca_reported_fees_apr, expected_fees_from_nominal_rate,
             fee_difference_usd, fee_difference_pct, fee_window_comparability, yield_over_tvl,
             fee_model, reward_known, tvl_usd,
             volume_24h, volume_7d, reported_apr, reward_apr, calculated_fee_apr,
             volume_tvl_ratio, score, opportunity_score, opportunity_trend, risk_score, scan_id, score_breakdown, status, source)
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?
            )""", item)
        self.conn.commit()

    def recently_alerted(self, key, cooldown_minutes):
        return self.conn.execute("""SELECT 1 FROM alerts WHERE alert_key = ?
            AND created_at >= datetime('now', ?) LIMIT 1""", (key, f"-{cooldown_minutes} minutes")).fetchone() is not None

    def add_alert(self, key, alert_type, score, message):
        self.conn.execute("INSERT INTO alerts(alert_key, alert_type, created_at, score, message) VALUES (?, ?, ?, ?, ?)",
                          (key, alert_type, utc_now(), score, message))
        self.conn.commit()

    def close(self):
        self.conn.close()
