from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from bot.config import load_strategy
from bot.models import Bar, Candidate
from bot.signals import atr_wilder, find_candidates, rsi_wilder, rules_only_pick, sma

STRATEGY = load_strategy()


def bars_from_closes(closes) -> list[Bar]:
    start = date(2026, 1, 1)
    return [Bar(start + timedelta(days=i), D(c) + 1, D(c) - 1, D(c)) for i, c in enumerate(closes)]


def test_sma():
    assert sma([D(10), D(11), D(12), D(10), D(13)], 3) == D(35) / 3


def test_rsi_wilder_hand_computed():
    # changes +1 -1 +2 | +1 -1; first avg gain 1, loss 1/3; then 1, 2/9; then 2/3, 13/27 → RS 18/13
    assert abs(rsi_wilder([D(x) for x in (10, 11, 10, 12, 13, 12)], 3) - D(1800) / 31) < D("1e-9")


def test_rsi_all_gains_is_100():
    assert rsi_wilder([D(x) for x in range(10, 20)], 3) == 100


def test_atr_wilder_hand_computed():
    bars = [Bar(date(2026, 1, i + 1), D(h), D(l), D(c))
            for i, (h, l, c) in enumerate([(11, 9, 10), (12, 10, 11), (13, 11, 12), (12, 9, 10), (14, 10, 13)])]
    # TRs 2, 2, 3, 4 → first ATR 7/3 → (7/3*2 + 4)/3 = 26/9
    assert abs(atr_wilder(bars, 3) - D(26) / 9) < D("1e-9")


@pytest.mark.parametrize("call", [
    lambda: sma([D(1), D(2)], 3),
    lambda: rsi_wilder([D(1), D(2), D(3)], 3),
    lambda: atr_wilder(bars_from_closes([1, 2, 3]), 3),
])
def test_too_few_values_raise(call):
    with pytest.raises(ValueError):
        call()


def pullback_series():
    return [100 + i for i in range(215)] + [309, 304, 299, 294, 289]  # uptrend, then a five-day dip


def test_find_candidates():
    bars = {
        "MSFT": bars_from_closes(pullback_series()),            # uptrend + dip → candidate
        "AAPL": bars_from_closes([100 + i for i in range(220)]),  # only gains → RSI 100
        "XOM": bars_from_closes([400 - i for i in range(220)]),   # downtrend → below SMA
        "NVDA": bars_from_closes(pullback_series()[-100:]),     # too little history
    }
    found = find_candidates(bars, set(), STRATEGY)
    assert [c.ticker for c in found] == ["MSFT"]
    c = found[0]
    assert c.close == D(289)
    assert c.sma == D("219.05")
    assert c.rsi < 40
    assert c.atr > 0


def test_find_candidates_respects_exclude():
    assert find_candidates({"MSFT": bars_from_closes(pullback_series())}, {"MSFT"}, STRATEGY) == []


def test_find_candidates_sorted_by_ticker():
    series = bars_from_closes(pullback_series())
    assert [c.ticker for c in find_candidates({"XLK": series, "AAPL": series}, set(), STRATEGY)] == ["AAPL", "XLK"]


def cand(ticker, rsi):
    return Candidate(ticker, D(100), D(90), D(rsi), D(2))


def test_rules_only_pick_lowest_rsi_then_ticker():
    assert rules_only_pick([cand("MSFT", 35), cand("AAPL", 31), cand("XLK", 31)]) == "AAPL"


def test_rules_only_pick_empty_is_none():
    assert rules_only_pick([]) is None
