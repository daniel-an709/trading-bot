import dataclasses
import json
from decimal import Decimal

import pytest

from bot.config import load_limits, load_strategy
from bot.models import Candidate, MarketSnapshot, OpenOrder, Position
from bot.safety import PAPER_ENDPOINT
from bot.validator import ApprovedOrder, validate_proposal

LIMITS = load_limits()
STRATEGY = load_strategy()
MSFT = Candidate("MSFT", Decimal("400"), Decimal("380"), Decimal("35"), Decimal("8"))


def snapshot(**overrides) -> MarketSnapshot:
    defaults = dict(
        market_open=True,
        equity=Decimal("100000"),
        last_equity=Decimal("100000"),
        buying_power=Decimal("200000"),
        positions=(),
        open_orders=(),
        buys_submitted_today=0,
        latest_prices={"MSFT": Decimal("400"), "AAPL": Decimal("200")},
        trading_endpoint=PAPER_ENDPOINT,
    )
    return MarketSnapshot(**{**defaults, **overrides})


def proposal(**overrides) -> str:
    data = {"decision": "trade", "ticker": "MSFT", "side": "buy", "quantity": 12,
            "order_type": "limit", "limit_price": 400.00, "reasoning": "r", "confidence": 0.7}
    data.update(overrides)
    return json.dumps({k: v for k, v in data.items() if v is not _DROP})


_DROP = object()


def codes(result) -> set[str]:
    return {r.split(":", 1)[0] for r in result.reasons}


@pytest.fixture
def validate(tmp_path):
    def run(raw, snap=None, cands=(MSFT,), environ=None):
        return validate_proposal(
            raw, snap or snapshot(), list(cands), LIMITS, STRATEGY,
            project_root=tmp_path, environ=environ or {},
        )
    return run


def held(symbol, qty=10):
    return Position(symbol, qty, Decimal("100"))


# --- Approval ---------------------------------------------------------------

def test_good_proposal_approved(validate):
    r = validate(proposal())
    assert r.approved and r.status == "approved" and r.reasons == ()
    assert (r.order.ticker, r.order.quantity, r.order.limit_price) == ("MSFT", 12, Decimal("400.00"))
    assert r.order.stop_price == Decimal("384.00") and r.order.take_profit_price == Decimal("424.00")


def test_stop_and_target_round_half_up_to_cents(validate):
    cand = Candidate("MSFT", Decimal("400"), Decimal("380"), Decimal("35"), Decimal("1.2345"))
    r = validate(proposal(), cands=(cand,))
    assert r.order.stop_price == Decimal("397.53") and r.order.take_profit_price == Decimal("403.70")


def test_no_trade(validate):
    r = validate('{"decision":"no_trade","reasoning":"r"}')
    assert r.status == "no_trade" and not r.approved and r.order is None and r.reasons == ()


# --- Spec §13 deliberately bad proposals -----------------------------------

def test_oversized(validate):
    assert "position_too_large" in codes(validate(proposal(quantity=13)))  # 5200 > 5000


def test_not_whitelisted(validate):
    r = validate(proposal(ticker="GME"), snapshot(latest_prices={"GME": Decimal("400")}))
    assert "not_whitelisted" in codes(r) and not r.approved


def test_daily_trade_cap(validate):
    assert "daily_trade_cap" in codes(validate(proposal(), snapshot(buys_submitted_today=2)))


def test_malformed_json(validate):
    r = validate("{oops")
    assert r.status == "rejected" and codes(r) == {"malformed"} and r.order is None


def test_kill_file(validate, tmp_path):
    (tmp_path / "KILL").touch()
    r = validate(proposal())
    assert r.status == "halted" and codes(r) == {"kill_switch"}


def test_kill_env(validate):
    assert validate(proposal(), environ={"KILL_SWITCH": "1"}).status == "halted"


def test_kill_switch_beats_malformed(validate):
    assert validate("{oops", environ={"KILL_SWITCH": "1"}).status == "halted"


def test_live_endpoint(validate):
    r = validate(proposal(), snapshot(trading_endpoint="https://api.alpaca.markets"))
    assert r.status == "halted" and codes(r) == {"not_paper"}


def test_not_candidate(validate):
    r = validate(proposal(ticker="AAPL", limit_price=200, quantity=10))
    assert codes(r) == {"not_candidate"}


def test_chasing_limit(validate):
    assert "limit_out_of_band" in codes(validate(proposal(limit_price=404.01)))


def test_lowball_limit(validate):
    assert "limit_out_of_band" in codes(validate(proposal(limit_price=359.99)))


def test_position_cap(validate):
    snap = snapshot(positions=tuple(held(s) for s in ["AAPL", "NVDA", "AMZN", "GOOGL", "META"]),
                    open_orders=(OpenOrder("JPM", "buy", 5),))
    assert "too_many_positions" in codes(validate(proposal(), snap))


def test_open_sell_orders_do_not_count_toward_position_cap(validate):
    snap = snapshot(positions=tuple(held(s) for s in ["AAPL", "NVDA", "AMZN", "GOOGL", "META"]),
                    open_orders=(OpenOrder("AAPL", "sell", 10),))
    assert validate(proposal(), snap).approved


def test_daily_loss_halt(validate):
    assert "daily_loss_halt" in codes(validate(proposal(), snapshot(equity=Decimal("97000"))))


# --- Other checks -----------------------------------------------------------

def test_market_closed(validate):
    assert "market_closed" in codes(validate(proposal(), snapshot(market_open=False)))


def test_sell_rejected(validate):
    assert "not_buy" in codes(validate(proposal(side="sell")))


def test_market_order_rejected(validate):
    r = validate(proposal(order_type="market", limit_price=_DROP))
    assert codes(r) == {"not_limit"}


def test_already_held(validate):
    assert "already_held" in codes(validate(proposal(), snapshot(positions=(held("MSFT"),))))


def test_open_order_exists(validate):
    snap = snapshot(open_orders=(OpenOrder("MSFT", "buy", 5),))
    assert "open_order_exists" in codes(validate(proposal(), snap))


def test_insufficient_buying_power(validate):
    assert "insufficient_buying_power" in codes(validate(proposal(), snapshot(buying_power=Decimal("4799.99"))))


def test_no_price(validate):
    r = validate(proposal(), snapshot(latest_prices={}))
    assert "no_price" in codes(r) and "limit_out_of_band" not in codes(r)


def test_bad_stop(validate):
    cand = Candidate("MSFT", Decimal("400"), Decimal("380"), Decimal("35"), Decimal("300"))
    assert "bad_stop" in codes(validate(proposal(), cands=(cand,)))


@pytest.mark.parametrize("field", ["equity", "last_equity"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_bad_account_data_rejected_not_crash(validate, field, value):
    r = validate(proposal(), snapshot(**{field: Decimal(value)}))
    assert codes(r) == {"bad_account_data"}


def test_unexpected_snapshot_shape_fails_closed(validate):
    r = validate(proposal(), snapshot(positions=None))
    assert r.status == "rejected" and codes(r) == {"internal_error"}


# --- Boundaries: inclusive limits pass, one cent over fails -----------------

def test_size_exactly_5pct_ok(validate):
    snap = snapshot(equity=Decimal("80000"), last_equity=Decimal("80000"))
    assert validate(proposal(quantity=10), snap).approved  # 4000 == 4000


def test_limit_exactly_101pct_ok(validate):
    assert validate(proposal(limit_price=404.00, quantity=12)).approved


def test_limit_exactly_90pct_ok(validate):
    assert validate(proposal(limit_price=360.00)).approved


def test_daily_loss_exactly_2pct_halts(validate):
    assert "daily_loss_halt" in codes(validate(proposal(), snapshot(equity=Decimal("98000"))))


def test_daily_loss_just_under_ok(validate):
    assert validate(proposal(), snapshot(equity=Decimal("98000.01"))).approved


def test_trade_cap_one_below_ok(validate):
    assert validate(proposal(), snapshot(buys_submitted_today=1)).approved


def test_buying_power_exact_ok(validate):
    assert validate(proposal(), snapshot(buying_power=Decimal("4800"))).approved


# --- Reason collection and forgery ------------------------------------------

def test_collects_all_reasons(validate):
    r = validate(proposal(ticker="GME", quantity=100),
                 snapshot(buys_submitted_today=2, latest_prices={"GME": Decimal("400")}))
    assert {"not_whitelisted", "not_candidate", "position_too_large", "daily_trade_cap"} <= codes(r)


def test_lowercase_ticker_is_normalised_and_approved(validate):
    assert validate(proposal(ticker=" msft ")).approved


def test_approved_order_cannot_be_forged():
    with pytest.raises(PermissionError):
        ApprovedOrder("MSFT", 1, Decimal("1"), Decimal("1"), Decimal("1"))


def test_approved_order_cannot_be_altered_with_replace(validate):
    order = validate(proposal()).order
    with pytest.raises(PermissionError):
        dataclasses.replace(order, quantity=1000)
