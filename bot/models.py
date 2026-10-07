"""Plain data passed from the broker and signals into the validator."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal


@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    avg_entry_price: Decimal


@dataclass(frozen=True)
class OpenOrder:
    symbol: str
    side: Literal["buy", "sell"]
    qty: int


@dataclass(frozen=True)
class Bar:
    """One completed daily session."""

    day: date
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True)
class Candidate:
    ticker: str
    close: Decimal
    sma: Decimal
    rsi: Decimal
    atr: Decimal


@dataclass(frozen=True)
class MarketSnapshot:
    """Account and market state fetched by code from Alpaca — never from Claude."""

    market_open: bool
    equity: Decimal
    last_equity: Decimal
    buying_power: Decimal
    positions: tuple[Position, ...]
    open_orders: tuple[OpenOrder, ...]
    buys_submitted_today: int
    latest_prices: Mapping[str, Decimal]
    trading_endpoint: str
