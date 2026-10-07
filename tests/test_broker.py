from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from types import SimpleNamespace as NS

import pytest

from bot.broker import Broker, BrokerError
from bot.models import OpenOrder, Position
from bot.safety import PAPER_ENDPOINT

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)  # 10:00 New York


def order(symbol, side, qty, submitted_at=NOW):
    return NS(symbol=symbol, side=side, qty=qty, submitted_at=submitted_at)


class FakeTrading:
    def __init__(self, base_url=PAPER_ENDPOINT, positions=None, open_orders=None, all_orders=None,
                 account=None, fail_on=None):
        self._base_url = base_url
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.all_orders = all_orders or []
        self.account = account or NS(equity="100000.50", last_equity="100400", buying_power="200000")
        self.fail_on = fail_on
        self.order_filters = []

    def _maybe_fail(self, name):
        if self.fail_on == name:
            raise RuntimeError("boom")

    def get_clock(self):
        self._maybe_fail("clock")
        return NS(is_open=True)

    def get_account(self):
        self._maybe_fail("account")
        return self.account

    def get_all_positions(self):
        self._maybe_fail("positions")
        return self.positions

    def get_orders(self, filter=None):
        self._maybe_fail("orders")
        self.order_filters.append(filter)
        return self.open_orders if filter.status.value == "open" else self.all_orders


class FakeData:
    def __init__(self, bars=None, prices=None):
        self.bars = bars or {}
        self.prices = prices or {}
        self.requests = []

    def get_stock_bars(self, request_params):
        self.requests.append(request_params)
        return NS(data=self.bars)

    def get_stock_latest_trade(self, request_params):
        self.requests.append(request_params)
        return {s: NS(price=p) for s, p in self.prices.items()}


def broker(trading=None, data=None):
    return Broker(trading or FakeTrading(), data or FakeData(), feed="iex")


def test_snapshot_maps_account_positions_orders_prices():
    trading = FakeTrading(
        positions=[NS(symbol="AAPL", qty="10", side="long", avg_entry_price="190.5")],
        open_orders=[order("MSFT", "buy", "5"), order("AAPL", "sell", "10")],
        all_orders=[order("MSFT", "buy", "5"), order("AAPL", "sell", "10")],
    )
    snap = broker(trading, FakeData(prices={"MSFT": 400.1})).snapshot(["MSFT", "AAPL"], NOW)
    assert snap.market_open is True
    assert (snap.equity, snap.last_equity, snap.buying_power) == (D("100000.50"), D("100400"), D("200000"))
    assert snap.positions == (Position("AAPL", 10, D("190.5")),)
    assert snap.open_orders == (OpenOrder("MSFT", "buy", 5), OpenOrder("AAPL", "sell", 10))
    assert snap.buys_submitted_today == 1
    assert snap.latest_prices == {"MSFT": D("400.1")}
    assert snap.trading_endpoint == PAPER_ENDPOINT


def test_buys_today_uses_new_york_date():
    late_yesterday = datetime(2026, 10, 7, 3, 30, tzinfo=UTC)   # 23:30 on Oct 6 in New York
    early_today = datetime(2026, 10, 7, 4, 30, tzinfo=UTC)      # 00:30 on Oct 7 in New York
    trading = FakeTrading(all_orders=[order("MSFT", "buy", "1", late_yesterday), order("XLK", "buy", "1", early_today)])
    assert broker(trading).snapshot([], NOW).buys_submitted_today == 1
    after = trading.order_filters[-1].after
    assert after == datetime(2026, 10, 7, 4, 0, tzinfo=UTC)  # New York midnight (EDT)


def test_live_endpoint_refused():
    with pytest.raises(BrokerError, match="paper"):
        broker(FakeTrading(base_url="https://api.alpaca.markets"))


def test_from_env_requires_keys():
    with pytest.raises(BrokerError, match="ALPACA_API_KEY"):
        Broker.from_env({"ALPACA_SECRET_KEY": "s"}, feed="iex")
    with pytest.raises(BrokerError, match="ALPACA_SECRET_KEY"):
        Broker.from_env({"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": " "}, feed="iex")


def test_from_env_builds_paper_client():
    b = Broker.from_env({"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}, feed="iex")
    assert b.trading_endpoint == PAPER_ENDPOINT


def test_short_position_raises():
    trading = FakeTrading(positions=[NS(symbol="AAPL", qty="-10", side="short", avg_entry_price="190")])
    with pytest.raises(BrokerError, match="short"):
        broker(trading).snapshot([], NOW)


@pytest.mark.parametrize("field", ["equity", "last_equity", "buying_power"])
def test_missing_account_value_raises(field):
    account = NS(equity="1", last_equity="1", buying_power="1")
    setattr(account, field, None)
    with pytest.raises(BrokerError, match=field):
        broker(FakeTrading(account=account)).snapshot([], NOW)


@pytest.mark.parametrize("stage", ["clock", "account", "positions", "orders"])
def test_client_error_becomes_broker_error(stage):
    with pytest.raises(BrokerError, match=stage):
        broker(FakeTrading(fail_on=stage)).snapshot([], NOW)


def bar(day: date, close: float):
    # Alpaca daily bars are timestamped at New York midnight, i.e. 04:00 UTC in EDT
    return NS(timestamp=datetime(day.year, day.month, day.day, 4, tzinfo=UTC), high=close + 1, low=close - 1, close=close)


def test_daily_bars_drop_today_and_keep_last_count():
    days = [date(2026, 10, 1) + timedelta(days=i) for i in range(7)]  # Oct 1..7; Oct 7 is today
    data = FakeData(bars={"MSFT": [bar(d, 100 + i) for i, d in enumerate(days)]})
    result = broker(data=data).daily_bars(["MSFT"], 3, NOW)
    assert [b.day for b in result["MSFT"]] == [date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6)]
    assert result["MSFT"][-1].close == D("105")
    request = data.requests[-1]
    assert request.adjustment.value == "all" and request.feed.value == "iex"
    start = request.start if request.start.tzinfo else request.start.replace(tzinfo=UTC)  # alpaca-py stores naive UTC
    assert start <= NOW - timedelta(days=3)


def test_daily_bars_missing_ticker_is_empty():
    assert broker().daily_bars(["MSFT"], 3, NOW) == {"MSFT": []}
