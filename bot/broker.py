"""Read-only access to the Alpaca paper account and market data for the bot's own code.

Builds the MarketSnapshot the validator trusts and the daily bars signals.py needs.
Nothing here places, changes or cancels orders — that is executor.py's job alone.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from bot.models import Bar, MarketSnapshot, OpenOrder, Position
from bot.safety import is_paper_endpoint

NEW_YORK = ZoneInfo("America/New_York")
ORDER_LIMIT = 500  # Alpaca returns 50 orders unless asked for more


class BrokerError(Exception):
    pass


def _value(x) -> str:
    return x.value if isinstance(x, Enum) else str(x)


def _decimal(x) -> Decimal:
    return Decimal(str(x))


def _ny_midnight(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(NEW_YORK).date(), time(), tzinfo=NEW_YORK)


class Broker:
    def __init__(self, trading, data, feed: str):
        self._trading = trading
        self._data = data
        self._feed = DataFeed(feed)
        if not is_paper_endpoint(self.trading_endpoint):
            raise BrokerError(f"trading endpoint {self.trading_endpoint!r} is not the paper endpoint")

    @classmethod
    def from_env(cls, environ: Mapping[str, str], feed: str) -> "Broker":
        key = environ.get("ALPACA_API_KEY", "").strip()
        secret = environ.get("ALPACA_SECRET_KEY", "").strip()
        if not key:
            raise BrokerError("ALPACA_API_KEY is not set in .env")
        if not secret:
            raise BrokerError("ALPACA_SECRET_KEY is not set in .env")
        return cls(TradingClient(key, secret, paper=True), StockHistoricalDataClient(key, secret), feed)

    @property
    def trading_endpoint(self) -> str:
        return _value(self._trading._base_url)

    def _call(self, stage: str, fn, *args):
        try:
            return fn(*args)
        except Exception as e:
            raise BrokerError(f"{stage}: {type(e).__name__}: {e}") from e

    def snapshot(self, tickers: Sequence[str], now: datetime) -> MarketSnapshot:
        clock = self._call("clock", self._trading.get_clock)
        account = self._call("account", self._trading.get_account)
        values = {}
        for field in ("equity", "last_equity", "buying_power"):
            raw = getattr(account, field, None)
            if raw is None:
                raise BrokerError(f"account: missing {field}")
            values[field] = _decimal(raw)

        positions = []
        for p in self._call("positions", self._trading.get_all_positions):
            qty = Decimal(p.qty)
            if _value(p.side) != "long":
                raise BrokerError(f"positions: short position in {p.symbol}; the bot is long-only")
            if qty <= 0:
                raise BrokerError(f"positions: zero or negative quantity {qty} in {p.symbol}")
            if qty != qty.to_integral_value():
                raise BrokerError(f"positions: fractional position {qty} in {p.symbol}; the bot only handles whole shares")
            positions.append(Position(p.symbol, int(qty), _decimal(p.avg_entry_price)))

        raw_open = self._call("orders", self._trading.get_orders,
                              GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=ORDER_LIMIT))
        if len(raw_open) >= ORDER_LIMIT:
            raise BrokerError(f"orders: {ORDER_LIMIT} or more open orders; cannot be sure the list is complete")
        open_orders = tuple(OpenOrder(o.symbol, _value(o.side), int(Decimal(o.qty or 0))) for o in raw_open)

        midnight = _ny_midnight(now)
        today = midnight.date()
        todays = self._call("orders", self._trading.get_orders, GetOrdersRequest(
            status=QueryOrderStatus.ALL, after=midnight.astimezone(now.tzinfo), side=OrderSide.BUY, limit=ORDER_LIMIT))
        buys_today = sum(
            1 for o in todays
            if _value(o.side) == "buy" and o.submitted_at.astimezone(NEW_YORK).date() == today
        )

        prices = {}
        if tickers:
            trades = self._call("latest prices", self._data.get_stock_latest_trade,
                                StockLatestTradeRequest(symbol_or_symbols=list(tickers), feed=self._feed))
            prices = {symbol: _decimal(t.price) for symbol, t in trades.items()}

        return MarketSnapshot(
            market_open=bool(clock.is_open),
            equity=values["equity"],
            last_equity=values["last_equity"],
            buying_power=values["buying_power"],
            positions=tuple(positions),
            open_orders=open_orders,
            buys_submitted_today=buys_today,
            latest_prices=prices,
            trading_endpoint=self.trading_endpoint,
        )

    def daily_bars(self, tickers: Sequence[str], count: int, now: datetime) -> dict[str, list[Bar]]:
        """The last `count` completed daily sessions per ticker, oldest first (today excluded)."""
        request = StockBarsRequest(
            symbol_or_symbols=list(tickers),
            timeframe=TimeFrame.Day,
            start=now - timedelta(days=count * 3 // 2 + 30),
            adjustment=Adjustment.ALL,
            feed=self._feed,
        )
        result = self._call("daily bars", self._data.get_stock_bars, request)
        today = now.astimezone(NEW_YORK).date()
        out = {}
        for ticker in tickers:
            bars = [
                Bar(b.timestamp.astimezone(NEW_YORK).date(), _decimal(b.high), _decimal(b.low), _decimal(b.close))
                for b in result.data.get(ticker, [])
            ]
            out[ticker] = [b for b in bars if b.day < today][-count:]
        return out
