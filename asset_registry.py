"""Auditable asset classification registry keyed by Solana mint address."""

ASSET_REGISTRY = {
    # SOL/WSOL share the canonical wrapped-native mint on Solana.
    "So11111111111111111111111111111111111111112": {
        "asset_class": "NATIVE_BASE", "underlying_asset": "SOL", "issuer": None,
        "wrapper_type": "NATIVE_WRAPPER", "stablecoin_type": None,
    },
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": {
        "asset_class": "STABLECOIN_CENTRALIZED", "underlying_asset": "USD", "issuer": "Circle",
        "wrapper_type": None, "stablecoin_type": "fiat_backed",
    },
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": {
        "asset_class": "STABLECOIN_CENTRALIZED", "underlying_asset": "USD", "issuer": "Tether",
        "wrapper_type": None, "stablecoin_type": "fiat_backed",
    },
    # Exact mint identities observed in qualifying-pool source records.
    "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij": {
        "asset_class": "WRAPPED_CUSTODIAL", "underlying_asset": "BTC", "issuer": "Coinbase",
        "wrapper_type": "CUSTODIAL", "stablecoin_type": None,
        "classification_basis": "Jupiter token metadata: Coinbase Wrapped BTC",
    },
    "69MPxM6bSJCuiD1v5qyZ24CMk1eoTBUdDSCmFboAKc9v": {
        "asset_class": "WRAPPED_BRIDGED", "underlying_asset": "ETH", "issuer": "Wormhole",
        "wrapper_type": "BRIDGE", "stablecoin_type": None,
        "classification_basis": "Jupiter token metadata: Ethereum (Wormhole)",
    },
    "98sMhvDwXj1RQi5c5Mndm3vPe9cBqPrbLaufMXFNMh5g": {
        "asset_class": "DECENTRALIZED_TOKEN", "underlying_asset": None, "issuer": None,
        "wrapper_type": None, "stablecoin_type": None,
        "classification_basis": "Exact mint observed in Meteora source record; no issuer asserted",
    },
    "6p6xgHyF7AeE6TZkSmFsko444wqoP15icUSqi2jfGiPN": {
        "asset_class": "DECENTRALIZED_TOKEN", "underlying_asset": None, "issuer": None,
        "wrapper_type": None, "stablecoin_type": None,
        "classification_basis": "Exact mint observed in Meteora source record; no issuer asserted",
    },
}


def lookup(mint):
    return dict(ASSET_REGISTRY.get(mint, {}))
