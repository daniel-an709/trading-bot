"""Pure indicator maths and candidate selection from completed daily bars.

Claude never computes these; the validator only trusts candidates produced here.
"""

from collections.abc import Mapping, Sequence, Set
from decimal import Decimal

from bot.config import StrategyParams
from bot.models import Bar, Candidate


def sma(closes: Sequence[Decimal], period: int) -> Decimal:
    if len(closes) < period:
        raise ValueError(f"need {period} closes for SMA, got {len(closes)}")
    return sum(closes[-period:], Decimal(0)) / period


def _wilder(values: Sequence[Decimal], period: int) -> Decimal:
    """Simple mean of the first `period` values, then Wilder smoothing over the rest."""
    avg = sum(values[:period], Decimal(0)) / period
    for v in values[period:]:
        avg = (avg * (period - 1) + v) / period
    return avg


def rsi_wilder(closes: Sequence[Decimal], period: int) -> Decimal:
    if len(closes) < period + 1:
        raise ValueError(f"need {period + 1} closes for RSI, got {len(closes)}")
    changes = [b - a for a, b in zip(closes, closes[1:])]
    avg_gain = _wilder([max(c, Decimal(0)) for c in changes], period)
    avg_loss = _wilder([max(-c, Decimal(0)) for c in changes], period)
    if avg_loss == 0:
        return Decimal(100)
    return 100 - 100 / (1 + avg_gain / avg_loss)


def atr_wilder(bars: Sequence[Bar], period: int) -> Decimal:
    if len(bars) < period + 1:
        raise ValueError(f"need {period + 1} bars for ATR, got {len(bars)}")
    true_ranges = [
        max(b.high - b.low, abs(b.high - prev.close), abs(b.low - prev.close))
        for prev, b in zip(bars, bars[1:])
    ]
    return _wilder(true_ranges, period)


def find_candidates(
    bars: Mapping[str, Sequence[Bar]], exclude: Set[str], params: StrategyParams
) -> list[Candidate]:
    """Tickers in an uptrend (close above SMA) with a pullback (RSI at or below rsi_max)."""
    found = []
    for ticker in sorted(bars):
        history = bars[ticker]
        if ticker in exclude or len(history) < params.min_bars:
            continue
        closes = [b.close for b in history]
        average = sma(closes, params.sma_period)
        rsi = rsi_wilder(closes, params.rsi_period)
        if closes[-1] > average and rsi <= Decimal(str(params.rsi_max)):
            found.append(Candidate(ticker, closes[-1], average, rsi, atr_wilder(history, params.atr_period)))
    return found


def rules_only_pick(candidates: Sequence[Candidate]) -> str | None:
    """What the plain rules would buy: the deepest pullback (lowest RSI), ties by ticker."""
    if not candidates:
        return None
    return min(candidates, key=lambda c: (c.rsi, c.ticker)).ticker
