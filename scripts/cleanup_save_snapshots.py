#!/usr/bin/env python3
"""Delete legacy Save analytics rows without touching Kamino."""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import Database


def scalar(db, query, params=()):
    row = db.conn.execute(query, params).fetchone()
    return row[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="commit deletion; default is a read-only preview")
    parser.add_argument("--vacuum-full", action="store_true", help="PostgreSQL only: reclaim filesystem space after commit")
    args = parser.parse_args()
    db = Database()
    try:
        postgres = db.backend == "postgresql"
        size_query = "SELECT pg_database_size(current_database())" if postgres else None
        before_bytes = scalar(db, size_query) if postgres else os.path.getsize(db.path)
        invalid = "(lower(protocol)='save' AND (COALESCE(source_metadata, '') NOT LIKE '%save-rest-percent-sdk-units-v3%' OR COALESCE(quality_flags, '') LIKE '%anomalous_%'))"
        save_snapshots = scalar(db, f"SELECT COUNT(*) FROM lending_snapshots WHERE {invalid}")
        save_evaluations = scalar(db, "SELECT COUNT(*) FROM lending_evaluations WHERE lower(protocol)='save' AND observed_at < COALESCE((SELECT MIN(observed_at) FROM lending_snapshots WHERE lower(protocol)='save' AND COALESCE(source_metadata, '') LIKE '%save-rest-percent-sdk-units-v3%' AND COALESCE(quality_flags, '') NOT LIKE '%anomalous_%'), '9999-12-31')")
        kamino_before = scalar(db, "SELECT COUNT(*) FROM lending_snapshots WHERE lower(protocol)='kamino'")
        print(f"backend={db.backend} before_bytes={before_bytes} save_snapshots={save_snapshots} save_evaluations={save_evaluations} kamino_snapshots={kamino_before}")
        if not args.execute:
            print("preview_only=true")
            return 0
        db.conn.execute("DELETE FROM lending_evaluations WHERE lower(protocol)='save' AND observed_at < COALESCE((SELECT MIN(observed_at) FROM lending_snapshots WHERE lower(protocol)='save' AND COALESCE(source_metadata, '') LIKE '%save-rest-percent-sdk-units-v3%' AND COALESCE(quality_flags, '') NOT LIKE '%anomalous_%'), '9999-12-31')")
        db.conn.execute(f"DELETE FROM lending_snapshots WHERE {invalid}")
        db.conn.commit()
        kamino_after = scalar(db, "SELECT COUNT(*) FROM lending_snapshots WHERE lower(protocol)='kamino'")
        if kamino_after != kamino_before:
            raise RuntimeError(f"Kamino count changed: {kamino_before} -> {kamino_after}")
        if postgres and args.vacuum_full:
            db.conn.commit()
            db.conn._conn.autocommit = True
            db.conn.execute("VACUUM (FULL, ANALYZE) lending_snapshots")
            db.conn.execute("VACUUM (FULL, ANALYZE) lending_evaluations")
            db.conn.execute("VACUUM (FULL, ANALYZE) lending_markets")
        elif not postgres:
            db.conn.execute("VACUUM")
        after_bytes = scalar(db, size_query) if postgres else os.path.getsize(db.path)
        remaining = scalar(db, "SELECT COUNT(*) FROM lending_snapshots WHERE lower(protocol)='save'")
        print(f"after_bytes={after_bytes} removed_snapshots={save_snapshots} remaining_save_snapshots={remaining} kamino_snapshots={kamino_after}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
