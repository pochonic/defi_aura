"""Orca Public API v2 provider for Solana Whirlpools."""

import json
import math
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config
from raydium import Pool, number


ORCA_BASE_URL = "https://api.orca.so/v2/solana"
ORCA_POOLS_ENDPOINT = "/pools"


def _decimal(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _reward_apr(raw, stats, tvl):
    rewards = _decimal((stats.get("24h") or {}).get("rewards"))
    if rewards is not None and tvl and tvl > 0:
        return rewards / tvl * 365 * 100, True
    if isinstance(raw.get("rewards"), list) and not raw["rewards"]:
        return 0.0, True
    return None, False


def normalize(raw):
    token_a, token_b = raw.get("tokenA") or {}, raw.get("tokenB") or {}
    symbol_a, symbol_b = token_a.get("symbol"), token_b.get("symbol")
    if not symbol_a or not symbol_b or not (symbol_a.upper() in config.ALLOWED_TOKENS_NORMALIZED or symbol_b.upper() in config.ALLOWED_TOKENS_NORMALIZED):
        return None
    stats = raw.get("stats") or {}
    day = stats.get("24h") or {}
    tvl = _decimal(raw.get("tvlUsdc"))
    # Orca feeRate is hundredths of a basis point; 400 = 4 bp = 0.04%.
    fee_rate_raw = _decimal(raw.get("feeRate"))
    fee_rate = fee_rate_raw / 1_000_000 if fee_rate_raw is not None else None
    # yieldOverTvl is not labelled APR by Orca, so it is not promoted to reported_apr.
    reward_apr, reward_known = _reward_apr(raw, stats, tvl)
    pool = Pool(
        pool_address=raw.get("address", ""), token_a=symbol_a.strip(), token_b=symbol_b.strip(),
        pool_type="Whirlpool", fee_tier=fee_rate, tvl_usd=tvl,
        volume_24h=_decimal(day.get("volume")), volume_7d=_decimal((stats.get("7d") or {}).get("volume")),
        reported_apr=None, reward_apr=reward_apr,
        fees_24h_usd=_decimal(day.get("fees")),
        nominal_fee_apr=None, orca_reported_fees_apr=None,
        fee_model="ADAPTIVE" if raw.get("adaptiveFeeEnabled") is True else ("FIXED" if raw.get("adaptiveFeeEnabled") is False else "UNKNOWN"),
        token_a_mint=raw.get("tokenMintA"), token_b_mint=raw.get("tokenMintB"), protocol="Orca",
        reward_known=reward_known,
    )
    pool.protocol_data = {
        "current_price": _decimal(raw.get("price")), "tick_spacing": raw.get("tickSpacing"),
        "current_tick": raw.get("tickCurrentIndex"), "liquidity": raw.get("liquidity"),
        "token_decimals": {"token_a": token_a.get("decimals"), "token_b": token_b.get("decimals")},
        "source": ORCA_BASE_URL + ORCA_POOLS_ENDPOINT,
        "yield_over_tvl_raw": _decimal(day.get("yieldOverTvl")),
        "fee_window_comparability": "UNKNOWN",
        "field_mapping": {
            "volume_24h_usd": "stats.24h.volume",
            "fees_24h_usd": "stats.24h.fees",
            "fee_rate": "feeRate / 1,000,000 (400 = 0.04%)",
            "fee_model": "adaptiveFeeEnabled: true=ADAPTIVE, false=FIXED, absent=UNKNOWN",
            "yield_over_tvl": "stats.24h.yieldOverTvl (raw only)",
        },
        "fee_model_basis": "explicit adaptiveFeeEnabled from official REST response",
        "fee_window_basis": "API period label is present, but same rolling 24h window cannot be independently verified",
    }
    pool.yield_over_tvl = _decimal(day.get("yieldOverTvl"))
    pool.fee_window_comparability = "UNKNOWN"
    pool.clmm_data = {"available": pool.protocol_data, "unavailable": ["reported_apr (Orca exposes yieldOverTvl, not a field labelled APR)"], "position_apr": None}
    return pool


class OrcaClient:
    source = "Orca"
    endpoint = ORCA_BASE_URL + ORCA_POOLS_ENDPOINT

    def __init__(self, timeout=None):
        self.timeout = timeout or config.PROVIDER_TIMEOUTS["Orca"]["read_timeout"]

    def fetch_pools(self, size=1000, max_pages=20):
        raw, cursor, pages = [], None, 0
        while pages < max_pages:
            params = {"sortBy": "tvl", "sortDirection": "desc", "size": size, "stats": "24h,7d"}
            if cursor:
                params["next"] = cursor
            url = self.endpoint + "?" + urlencode(params)
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-radar-mvp/1.0"})
            with urlopen(request, timeout=self.timeout) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Orca HTTP {response.status}")
                payload = json.loads(response.read().decode("utf-8"))
            batch = payload.get("data") or []
            raw.extend(batch)
            pages += 1
            cursor = ((payload.get("meta") or {}).get("cursor") or {}).get("next")
            if not cursor or not batch:
                break
        return raw, pages
