"""Read-only CLMM metadata discovery for Raydium pools.

This module deliberately does not connect a wallet, read positions, or estimate
position APR. Raydium's public REST API exposes pool metadata and a liquidity
time series, but not a complete per-tick distribution suitable for that model.
"""

import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config


class ClmmAnalyzer:
    def __init__(self, timeout=None):
        self.timeout = timeout or config.REQUEST_TIMEOUT_SECONDS

    def _get(self, path, params):
        url = config.API_BASE_URL + path + "?" + urlencode(params)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "crypto-radar-mvp/1.0"})
        with urlopen(request, timeout=self.timeout) as response:
            if response.status >= 400:
                raise RuntimeError(f"Raydium HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))

    def analyze(self, pool_address, current_tvl=None):
        result = {
            "pool_address": pool_address,
            "available": {},
            "unavailable": [
                "current_tick (not exposed by Raydium REST API v3)",
                "active_liquidity (not exposed as current pool-state field by REST API v3)",
                "liquidity_distribution_by_ticks_or_ranges (not exposed)",
                "price_history (not exposed by REST API v3)",
            ],
            "position_apr": None,
            "debug": {},
        }
        info = self._get("/pools/info/ids", {"ids": pool_address})
        if info.get("success") is not True or not info.get("data"):
            raise RuntimeError(info.get("msg", "No CLMM pool data returned"))
        item = info["data"][0]
        if str(item.get("type", "")).lower() != "concentrated":
            return result
        mint_a, mint_b = item.get("mintA") or {}, item.get("mintB") or {}
        cfg = item.get("config") or {}
        result["available"] = {
            "current_pool_price": item.get("price"),
            "tick_spacing": cfg.get("tickSpacing"),
            "fee_tier": item.get("feeRate"),
            "token_decimals": {"token_a": mint_a.get("decimals"), "token_b": mint_b.get("decimals")},
            "tvl_current": current_tvl,
            "tvl_7d_ago": None,
            "tvl_7d_change_pct": None,
            "tvl_30d_ago": None,
            "tvl_30d_change_pct": None,
        }
        line = self._get("/pools/line/liquidity", {"id": pool_address})
        if line.get("success") is True:
            result["debug"]["liquidity_history"] = (line.get("data") or {}).get("line")
            result["unavailable"].extend([
                "tvl_7d_ago / tvl_7d_change_pct (endpoint exposes liquidity history, not TVL history)",
                "tvl_30d_ago / tvl_30d_change_pct (endpoint exposes liquidity history, not TVL history)",
            ])
        else:
            result["unavailable"].append(f"liquidity_history ({line.get('msg', 'API error')})")
        return result
