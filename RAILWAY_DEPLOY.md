# Railway: LP + Lending Intelligence collector

## Variables

Configure these variables on the Railway service (do not commit their values):

- `DATABASE_URL`: PostgreSQL connection URL supplied by Railway PostgreSQL.
- `SOLANA_RPC_URL`: Alchemy Solana RPC URL, including its API key.
- `KAMINO_API_BASE_URL`: `https://api.kamino.finance` (or the approved API endpoint).
- `LENDING_PROTOCOLS`: `kamino,save` by default; add `drift` after validating the Drift adapter.

`DATABASE_URL` selects PostgreSQL. When it is absent, local development continues
to use `crypto_radar.db` through SQLite.

## Service configuration

Install the Python and SDK dependencies during the build. The collector is a
one-shot process; configure Railway Cron with:

```text
*/15 * * * *
```

The repository includes `railway.toml` to install both `requirements.txt` and
the Node SDK dependencies used by `--with-sdk-enrichment`, and to set the
one-shot start command. Each cron execution now runs the LP scanner, Lending
ingestion with SDK enrichment, and Lending evaluation against the same
PostgreSQL database. Set the cron schedule in the Railway service settings.

Start command:

```bash
python railway_cycle.py
```

The repository also includes a read-only Drift spot-lending adapter. It uses
the same `SOLANA_RPC_URL` and the pinned `@drift-labs/sdk` dependency; no wallet
or signing key is required. Activate it with:

```text
LENDING_PROTOCOLS=kamino,save,drift
```

Do not add `--interval` to the Railway command. The process exits after one
ingestion; a global failure returns a non-zero exit code. Reserve-level errors
are logged and counted while the remaining reserves continue.

## Validation

Run one manual execution from the Railway service shell or a one-off run, then
inspect stdout/stderr for the adapter totals, saved snapshots, and any reserve
errors. Confirm PostgreSQL contains rows in `lending_markets` and
`lending_snapshots`; if ranking is needed, run:

```bash
python fetch_lending_markets.py --rank --limit 10
```

The command uses the same `DATABASE_URL`, so it validates the deployed database
rather than the local SQLite file. The cycle exits with a non-zero code if LP,
Lending ingestion, or Lending evaluation fails.
