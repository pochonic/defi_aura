from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class LendingMarketSnapshot:
    protocol: str
    chain: str
    market_id: str
    reserve_id: str
    asset_symbol: Optional[str]
    asset_mint: Optional[str]
    supply_apy: Optional[float]
    borrow_apy: Optional[float]
    utilization: Optional[float]
    total_supplied_usd: Optional[float]
    total_borrowed_usd: Optional[float]
    available_liquidity_usd: Optional[float]
    observed_at: str
    source: str
    source_endpoint: str
    market_name: Optional[str] = None
    market_description: Optional[str] = None
    market_is_primary: Optional[bool] = None
    market_is_curated: Optional[bool] = None
    missing_fields: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    available_liquidity_native: Optional[float] = None
    available_liquidity_decimals: Optional[int] = None
    available_liquidity_source: Optional[str] = None
    source_metadata: dict = field(default_factory=dict)

    def validate(self) -> None:
        if self.protocol not in {"Kamino", "save"} or self.chain != "Solana":
            raise ValueError("lending snapshot has an unsupported protocol or chain")
        if not self.market_id or not self.reserve_id:
            raise ValueError("market_id and reserve_id are required")
        observed = datetime.fromisoformat(self.observed_at)
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must include a UTC timezone")
        if observed.astimezone(timezone.utc) != observed:
            raise ValueError("observed_at must be UTC")
        for field_name in ("supply_apy", "borrow_apy", "utilization", "total_supplied_usd", "total_borrowed_usd", "available_liquidity_usd", "available_liquidity_native"):
            value = getattr(self, field_name)
            if value is None:
                continue
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"{field_name} must be finite")
            if field_name == "utilization" and not 0 <= value <= 1:
                raise ValueError("utilization must be a decimal between 0 and 1")
            if field_name in {"supply_apy", "borrow_apy"} and value < 0:
                raise ValueError(f"{field_name} cannot be negative")
            if field_name not in {"supply_apy", "borrow_apy", "utilization"} and value < 0:
                raise ValueError(f"{field_name} cannot be negative")
