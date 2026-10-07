import dataclasses
from decimal import Decimal

import pytest

from bot.models import MarketSnapshot, Position
from bot.safety import PAPER_ENDPOINT
from bot.validator import ApprovedExit, validate_exit


def snapshot(**overrides) -> MarketSnapshot:
    defaults = dict(
        market_open=True,
        equity=Decimal("100000"),
        last_equity=Decimal("100000"),
        buying_power=Decimal("200000"),
        positions=(Position("MSFT", 12, Decimal("400")),),
        open_orders=(),
        buys_submitted_today=0,
        latest_prices={"MSFT": Decimal("400")},
        trading_endpoint=PAPER_ENDPOINT,
    )
    return MarketSnapshot(**{**defaults, **overrides})


def codes(result) -> set[str]:
    return {r.split(":", 1)[0] for r in result.reasons}


@pytest.fixture
def exit_check(tmp_path):
    def run(symbol="MSFT", snap=None, environ=None):
        return validate_exit(symbol, snap or snapshot(), project_root=tmp_path, environ=environ or {})
    return run


def test_exit_approved_full_quantity(exit_check):
    r = exit_check()
    assert r.approved and r.status == "approved" and r.reasons == ()
    assert (r.exit.symbol, r.exit.quantity) == ("MSFT", 12)


def test_exit_not_held(exit_check):
    r = exit_check("AAPL")
    assert r.status == "rejected" and codes(r) == {"not_held"} and r.exit is None


def test_exit_zero_quantity_not_held(exit_check):
    r = exit_check(snap=snapshot(positions=(Position("MSFT", 0, Decimal("400")),)))
    assert codes(r) == {"not_held"}


def test_exit_kill_switch_halts(exit_check, tmp_path):
    (tmp_path / "KILL").touch()
    r = exit_check()
    assert r.status == "halted" and codes(r) == {"kill_switch"}


def test_exit_live_endpoint_halts(exit_check):
    r = exit_check(snap=snapshot(trading_endpoint="https://api.alpaca.markets"))
    assert r.status == "halted" and codes(r) == {"not_paper"}


def test_exit_market_closed(exit_check):
    assert codes(exit_check(snap=snapshot(market_open=False))) == {"market_closed"}


def test_exit_unexpected_snapshot_fails_closed(exit_check):
    r = exit_check(snap=snapshot(positions=None))
    assert r.status == "rejected" and codes(r) == {"internal_error"}


def test_approved_exit_cannot_be_forged():
    with pytest.raises(PermissionError):
        ApprovedExit("MSFT", 12)


def test_approved_exit_cannot_be_altered_with_replace(exit_check):
    with pytest.raises(PermissionError):
        dataclasses.replace(exit_check().exit, quantity=1000)
