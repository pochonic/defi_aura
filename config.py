import os
from pathlib import Path

KAMINO_API_BASE_URL = "https://api.kamino.finance"
KAMINO_MARKETS_ENDPOINT = "/v2/kamino-market"
KAMINO_RESERVES_ENDPOINT = "/kamino-market/{market_id}/reserves/metrics"
KAMINO_REQUEST_TIMEOUT_SECONDS = 30
KAMINO_POLL_INTERVAL_SECONDS = 15 * 60
# Railway provides Node on PATH; local users may override this for a pinned runtime.
KAMINO_NODE = os.getenv("KAMINO_NODE", "node")
KAMINO_SDK_TIMEOUT_SECONDS = 60

DRIFT_ENV = os.getenv("DRIFT_ENV", "mainnet-beta")
DRIFT_NODE = os.getenv("DRIFT_NODE", "node")
DRIFT_SDK_TIMEOUT_SECONDS = 90

API_BASE_URL = "https://api-v3.raydium.io"
RAYDIUM_POOLS_ENDPOINT = "/pools/info/list"
REQUEST_TIMEOUT_SECONDS = 20
PROVIDER_RETRY_CONFIG = {"max_attempts": 3, "initial_delay_seconds": 1, "backoff_multiplier": 2}
PROVIDER_TIMEOUTS = {
    "Raydium": {"connect_timeout": 5, "read_timeout": 25},
    "Orca": {"connect_timeout": 5, "read_timeout": 25},
    "Meteora": {"connect_timeout": 5, "read_timeout": 30},
}
STALE_THRESHOLDS_SECONDS = {"recent": 10 * 60, "stale": 30 * 60}
CIRCUIT_BREAKER_FAILURES = 5
RISK_MIN_COVERAGE_PCT = 60.0
VOLATILITY_MIN_OBSERVATIONS = {"24h": 20, "7d_returns": 120, "7d_days": 5, "30d_returns": 480, "30d_days": 20}
VOLATILITY_CACHE_TTL_SECONDS = {"historical_price_series": 30 * 60, "metrics_24h": 10 * 60}
VOLATILITY_MAX_DISPERSION_WARNING_PCT = 1.0
VOLATILITY_WEIGHTS = {"realized_volatility": 0.60, "max_drawdown": 0.20, "extreme_move": 0.20}
VOLATILITY_EXCLUDE_SUSPECT_SOURCES = True
VOLATILITY_FROZEN_BUCKETS = 3
VOLATILITY_FROZEN_OTHER_MOVE_PCT = 0.5
LIQUIDITY_STRUCTURE_WEIGHTS = {
    "range_dependency_risk": 0.30,
    "active_liquidity_risk": 0.25,
    "capital_concentration_risk": 0.20,
    "rebalance_dependency_risk": 0.15,
    "fee_mechanism_complexity_risk": 0.10,
}
METEORA_DLMM_BIN_WINDOW = 100
METEORA_DLMM_ENRICHMENT_TTL_SECONDS = 300
METEORA_DLMM_SDK_FACTORY = None
METEORA_DLMM_NODE = r"C:\Users\nicol\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
METEORA_DLMM_SDK_TIMEOUT_SECONDS = 45
METEORA_DLMM_MAX_BIN_WINDOW = 800
METEORA_DLMM_TARGET_COVERAGE_PCT = 70.0
METEORA_DLMM_MIN_CONCENTRATION_COVERAGE_PCT = 30.0
VOLATILITY_EXTERNAL_ENDPOINT = "https://api.coingecko.com/api/v3/coins"
# Explicit mint -> provider ID mappings; symbols are never used as identity.
VOLATILITY_EXTERNAL_ASSET_IDS = {
    "So11111111111111111111111111111111111111112": "solana",
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "usd-coin",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "tether",
}
ASSET_RISK_WEIGHTS = {
    "market_cap_risk": 0.25,
    "holder_concentration_risk": 0.25,
    "authority_risk": 0.20,
    "token_age_risk": 0.15,
    "liquidity_risk": 0.10,
    "verification_risk": 0.05,
}
ASSET_RISK_MANDATORY_COMPONENTS = {
    "NATIVE_BASE": {"market_cap_risk", "liquidity_risk"},
    "DECENTRALIZED_TOKEN": {"market_cap_risk", "liquidity_risk", "holder_concentration_risk", "authority_risk"},
    "STABLECOIN_CENTRALIZED": {"market_cap_risk", "liquidity_risk", "issuer_control_risk", "registry_identification"},
    "WRAPPED_CUSTODIAL": {"underlying_market_risk", "wrapper_liquidity_risk", "custody_or_bridge_risk", "authority_risk"},
    "WRAPPED_BRIDGED": {"underlying_market_risk", "wrapper_liquidity_risk", "bridge_risk", "authority_risk"},
    "UNKNOWN": set(),
}
ASSET_RISK_CACHE_TTL_SECONDS = {
    "market_data": 30 * 60,
    "holder_data": 6 * 60 * 60,
    "authority_data": 24 * 60 * 60,
    "token_age": 7 * 24 * 60 * 60,
}
ASSET_RISK_JUPITER_ENDPOINT = "https://lite-api.jup.ag/tokens/v2/search"
# Runtime credentials/endpoints are supplied by the environment.  Keeping the
# value unset is intentional: SDK enrichment must never silently use another
# cluster or a local developer endpoint in production.
SOLANA_RPC_ENDPOINT = os.getenv("SOLANA_RPC_URL")
# Periodic radar cadence. Each cycle receives its own scan_id and snapshots
# remain protected by the existing protocol/pool/scan-cycle checks.
POLL_INTERVAL_SECONDS = 15 * 60
DATABASE_PATH = Path("crypto_radar.db")
DATABASE_URL = os.getenv("DATABASE_URL")

LENDING_PROTOCOLS = tuple(
    item.strip().lower()
    for item in os.getenv("LENDING_PROTOCOLS", "kamino,save").split(",")
    if item.strip()
)

ALLOWED_TOKENS = {"USDC", "USDT", "SOL", "WSOL", "JUP", "JitoSOL", "JITOSOL"}
ALLOWED_TOKENS_NORMALIZED = {token.upper() for token in ALLOWED_TOKENS}
ALLOWED_PROTOCOLS = {"Raydium", "Orca", "Meteora"}
LP_FILTERS = {
    "min_tvl_usd": 5_000_000.0,
    "min_volume_24h_usd": 250_000.0,
    "min_volume_tvl_ratio": 0.03,
    "min_pool_fee_apr": 3.0,
}
MIN_TVL_USD = LP_FILTERS["min_tvl_usd"]
MIN_SCORE = 80.0
ALERT_COOLDOWN_MINUTES = 60
PAGE_SIZE = 1000
MAX_PAGES = 20

# Lending Intelligence v1: thresholds and weights are deliberately centralized.
LENDING_ECONOMIC_THRESHOLDS_USD = {"micro": 10_000.0, "small": 100_000.0, "medium": 1_000_000.0}
LENDING_UTILIZATION_BANDS = ((0.30, 0.30), (0.60, 0.65), (0.85, 1.0), (0.95, 0.75), (1.01, 0.35))
LENDING_SCORE_WEIGHTS = {
    "yield_quality": 0.30, "apy_persistence": 0.20, "apy_stability": 0.15,
    "capacity": 0.15, "utilization_health": 0.10, "borrow_demand": 0.10,
}
SAVE_API_BASE_URL = "https://api.solend.fi"
SAVE_MARKETS_ENDPOINT = "/v1/markets?scope=all"
SAVE_MARKET_CONFIG_ENDPOINT = "/v1/markets/configs?ids={market_id}"
SAVE_RESERVES_ENDPOINT = "/v1/reserves?ids={reserve_ids}"
SAVE_PRICES_ENDPOINT = "/v1/prices?mints={mints}"
SAVE_REQUEST_TIMEOUT_SECONDS = 30
SAVE_ADAPTER_VERSION = "3.0"
SAVE_APY_CALCULATION_VERSION = "save-rest-percent-sdk-units-v3"
LENDING_CAPACITY_MIN_USD = 1_000.0
LENDING_CAPACITY_MAX_USD = 100_000_000.0
LENDING_APY_REFERENCE = 0.20
LENDING_HISTORY_STATUS_THRESHOLDS = {"insufficient": 25.0, "complete": 90.0}

# Score component caps make the score explainable and stable across runs.
SCORE_CAPS = {
    "fee_apr": 50.0,
    "volume_tvl": 3.0,
    "tvl": 100_000_000.0,
}
MIN_HISTORY_OBSERVATIONS = 3
MIN_PERSISTENCE_SNAPSHOTS = 6
