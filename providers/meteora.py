"""Official Meteora DLMM Data API provider."""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config
from raydium import Pool, audit_number


METEORA_BASE_URL = "https://dlmm.datapi.meteora.ag"
METEORA_POOLS_ENDPOINT = "/pools"


def _value(value):
    return audit_number(value)


def normalize(raw):
    token_a = raw.get("token_x") or {}
    token_b = raw.get("token_y") or {}
    symbol_a = token_a.get("symbol")
    symbol_b = token_b.get("symbol")
    if not symbol_a or not symbol_b or not (symbol_a.upper() in config.ALLOWED_TOKENS_NORMALIZED or symbol_b.upper() in config.ALLOWED_TOKENS_NORMALIZED):
        return None
    pool_config = raw.get("pool_config") or {}
    volume = (raw.get("volume") or {}).get("24h")
    fees = (raw.get("fees") or {}).get("24h")
    base_fee_pct = _value(pool_config.get("base_fee_pct"))
    dynamic_fee_pct = _value(raw.get("dynamic_fee_pct"))
    base_fee_rate = base_fee_pct / 100 if base_fee_pct is not None else None
    # apr/fee_tvl_ratio_24h are percentage points in the live response
    # (0.20669 means 0.20669%), while apy is already a percentage.
    raw_apr = _value(raw.get("apr"))
    raw_apy = _value(raw.get("apy"))
    raw_fee_tvl_ratio = _value((raw.get("fee_tvl_ratio") or {}).get("24h"))
    reported_apr = raw_apr
    farm_apr = _value(raw.get("farm_apr"))
    pool = Pool(
        pool_address=raw.get("address", ""), token_a=symbol_a.strip(), token_b=symbol_b.strip(),
        pool_type="DLMM", fee_tier=base_fee_rate, tvl_usd=_value(raw.get("tvl")),
        volume_24h=_value(volume), volume_7d=_value((raw.get("volume") or {}).get("7d")),
        reported_apr=reported_apr, reward_apr=farm_apr, meteora_api_apr=raw_apr,
        fees_24h_usd=_value(fees), nominal_fee_apr=None, orca_reported_fees_apr=None,
        fee_model="DYNAMIC", token_a_mint=token_a.get("address"), token_b_mint=token_b.get("address"),
        protocol="Meteora", reward_known="farm_apr" in raw,
    )
    pool.protocol_data = {
        "source": METEORA_BASE_URL + METEORA_POOLS_ENDPOINT,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "current_price": _value(raw.get("current_price")),
        "active_bin": None,
        "bin_step": pool_config.get("bin_step"),
        "base_fee_pct": base_fee_pct,
        "dynamic_fee_pct": dynamic_fee_pct,
        "liquidity": None,
        "token_decimals": {"token_a": token_a.get("decimals"), "token_b": token_b.get("decimals")},
        "field_mapping": {
            "tvl_usd": "tvl",
            "volume_24h_usd": "volume.24h",
            "fees_24h_usd": "fees.24h",
            "base_fee_rate": "pool_config.base_fee_pct / 100",
            "dynamic_fee_rate": "dynamic_fee_pct / 100",
            "reported_apr": "apr (raw percentage points; not used for score)",
            "reward_apr": "farm_apr (raw percentage points; not used as fee APR)",
        },
        "raw_apr": raw_apr,
        "raw_apy": raw_apy,
        "raw_fee_tvl_ratio_24h": raw_fee_tvl_ratio,
        "raw_fees_24h": _value(fees),
        "raw_volume_24h": _value(volume),
        "daily_fee_yield": (_value(fees) / _value(raw.get("tvl"))) if _value(fees) is not None and _value(raw.get("tvl")) else None,
        "annualized_fee_apr": (_value(fees) / _value(raw.get("tvl")) * 365 * 100) if _value(fees) is not None and _value(raw.get("tvl")) else None,
        "api_apy": raw_apy,
        "max_fee_pct": _value(pool_config.get("max_fee_pct")),
    }
    pool.clmm_data = {
        "available": pool.protocol_data,
        "unavailable": ["active_bin (not present in /pools response)", "liquidity (not present as a DLMM liquidity field in /pools response)"],
        "position_apr": None,
    }
    return pool


class MeteoraClient:
    source = "Meteora"
    endpoint = METEORA_BASE_URL + METEORA_POOLS_ENDPOINT

    def __init__(self, timeout=None):
        self.timeout = timeout or config.PROVIDER_TIMEOUTS["Meteora"]["read_timeout"]

    def fetch_pools(self, size=100, max_pages=20):
        raw, pages = [], 0
        # The official endpoint documents a maximum page_size of 100.
        page_size = max(1, min(int(size), 100))
        while pages < max_pages:
            page = pages + 1
            params = {"page": page, "page_size": page_size, "sort_by": "tvl:desc"}
            url = self.endpoint + "?" + urlencode(params)
            request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-radar-mvp/1.0"})
            with urlopen(request, timeout=self.timeout) as response:
                if response.status >= 400:
                    raise RuntimeError(f"Meteora HTTP {response.status}")
                payload = json.loads(response.read().decode("utf-8"))
            batch = payload.get("data") or []
            raw.extend(batch)
            pages += 1
            if not batch or page >= payload.get("pages", page):
                break
        return raw, pages
