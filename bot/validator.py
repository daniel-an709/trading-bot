"""Deterministic checks between Claude's proposal and the broker.

Pure functions: no network, no clock, no file access beyond the kill-switch check.
Every number comes from the code-fetched MarketSnapshot, never from Claude.
Only this module can create ApprovedOrder / ApprovedExit, and executor.py accepts
nothing else.
"""

from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal

from bot.config import Limits, StrategyParams
from bot.models import Candidate, MarketSnapshot
from bot.safety import is_paper_endpoint, kill_switch_reason
from bot.schema import MalformedProposal, NoTrade, TradeProposal, parse_proposal

CENT = Decimal("0.01")
LIMIT_BAND_LOW = Decimal("0.90")
LIMIT_BAND_HIGH = Decimal("1.01")

# Set only while this module is constructing an approval. dataclasses.replace() and
# direct construction elsewhere run __post_init__ with it unset and are refused.
_issuing: ContextVar[bool] = ContextVar("_issuing", default=False)


class _Issued:
    def __post_init__(self):
        if not _issuing.get():
            raise PermissionError(f"{type(self).__name__} can only be created by bot.validator")


def _issue(cls, **fields):
    token = _issuing.set(True)
    try:
        return cls(**fields)
    finally:
        _issuing.reset(token)


@dataclass(frozen=True)
class ApprovedOrder(_Issued):
    ticker: str
    quantity: int
    limit_price: Decimal
    stop_price: Decimal
    take_profit_price: Decimal


@dataclass(frozen=True)
class ApprovedExit(_Issued):
    symbol: str
    quantity: int


@dataclass(frozen=True)
class ExitResult:
    status: Literal["approved", "rejected", "halted"]
    reasons: tuple[str, ...]
    exit: ApprovedExit | None = None

    @property
    def approved(self) -> bool:
        return self.status == "approved"


@dataclass(frozen=True)
class ValidationResult:
    status: Literal["approved", "rejected", "no_trade", "halted"]
    reasons: tuple[str, ...]
    proposal: TradeProposal | NoTrade | None
    order: ApprovedOrder | None = field(default=None)

    @property
    def approved(self) -> bool:
        return self.status == "approved"


def _pct(value: float) -> Decimal:
    return Decimal(str(value)) / 100


def _halt_reason(snapshot: MarketSnapshot, project_root: Path, environ: Mapping[str, str]) -> str | None:
    killed = kill_switch_reason(project_root, environ)
    if killed:
        return f"kill_switch: {killed}"
    if not is_paper_endpoint(snapshot.trading_endpoint):
        return f"not_paper: endpoint {snapshot.trading_endpoint!r} is not the paper endpoint"
    return None


def validate_proposal(
    raw: str,
    snapshot: MarketSnapshot,
    candidates: Sequence[Candidate],
    limits: Limits,
    strategy: StrategyParams,
    *,
    project_root: Path,
    environ: Mapping[str, str],
) -> ValidationResult:
    """Approve or reject Claude's raw output. Never raises; any unexpected error rejects."""
    try:
        return _validate_proposal(raw, snapshot, candidates, limits, strategy, project_root, environ)
    except Exception as e:  # fail closed
        return ValidationResult("rejected", (f"internal_error: {type(e).__name__}: {e}",), None)


def _validate_proposal(raw, snapshot, candidates, limits, strategy, project_root, environ) -> ValidationResult:
    halt = _halt_reason(snapshot, project_root, environ)
    if halt:
        return ValidationResult("halted", (halt,), None)

    try:
        proposal = parse_proposal(raw)
    except MalformedProposal as e:
        return ValidationResult("rejected", (f"malformed: {e}",), None)

    if isinstance(proposal, NoTrade):
        return ValidationResult("no_trade", (), proposal)

    if snapshot.equity <= 0 or snapshot.last_equity <= 0:
        return ValidationResult(
            "rejected",
            (f"bad_account_data: equity={snapshot.equity} last_equity={snapshot.last_equity}",),
            proposal,
        )

    p = proposal
    reasons: list[str] = []
    candidate = next((c for c in candidates if c.ticker == p.ticker), None)
    latest = snapshot.latest_prices.get(p.ticker)
    limit = p.limit_price  # None for market orders, which check 6 rejects
    notional = p.quantity * limit if limit is not None else None

    # 5
    if not snapshot.market_open:
        reasons.append("market_closed: market is not open")
    # 6
    if p.side != "buy":
        reasons.append(f"not_buy: side {p.side!r}; bot is long-only")
    if p.order_type != "limit":
        reasons.append(f"not_limit: order type {p.order_type!r}; only limit orders allowed")
    # 7
    if p.ticker not in limits.allowed_tickers:
        reasons.append(f"not_whitelisted: {p.ticker}")
    if any(pos.symbol == p.ticker for pos in snapshot.positions):
        reasons.append(f"already_held: {p.ticker}")
    if any(o.symbol == p.ticker for o in snapshot.open_orders):
        reasons.append(f"open_order_exists: {p.ticker}")
    # 8
    if candidate is None:
        reasons.append(f"not_candidate: {p.ticker} is not in today's signals")
    # 9
    if latest is None:
        reasons.append(f"no_price: no latest price for {p.ticker}")
    elif limit is not None:
        low, high = LIMIT_BAND_LOW * latest, LIMIT_BAND_HIGH * latest
        if not low <= limit <= high:
            reasons.append(f"limit_out_of_band: {limit} outside {low:.2f}-{high:.2f} (latest {latest})")
    # 10
    if notional is not None:
        max_notional = _pct(limits.max_position_pct) * snapshot.equity
        if notional > max_notional:
            reasons.append(f"position_too_large: {notional} > {max_notional:.2f}")
    # 11
    open_buys = sum(1 for o in snapshot.open_orders if o.side == "buy")
    if len(snapshot.positions) + open_buys >= limits.max_open_positions:
        reasons.append(
            f"too_many_positions: {len(snapshot.positions)} held + {open_buys} pending"
            f" >= {limits.max_open_positions}"
        )
    # 12
    if snapshot.buys_submitted_today >= limits.max_new_trades_per_day:
        reasons.append(
            f"daily_trade_cap: {snapshot.buys_submitted_today} >= {limits.max_new_trades_per_day}"
        )
    # 13
    change = (snapshot.equity - snapshot.last_equity) / snapshot.last_equity
    if change <= -_pct(limits.max_daily_loss_pct):
        reasons.append(f"daily_loss_halt: equity change {change:.2%} vs -{limits.max_daily_loss_pct}% limit")
    # 14
    if notional is not None and notional > snapshot.buying_power:
        reasons.append(f"insufficient_buying_power: {notional} > {snapshot.buying_power}")

    stop = target = None
    if candidate is not None and limit is not None:
        atr = candidate.atr
        stop = (limit - Decimal(str(strategy.stop_atr_multiple)) * atr).quantize(CENT, ROUND_HALF_UP)
        target = (limit + Decimal(str(strategy.target_atr_multiple)) * atr).quantize(CENT, ROUND_HALF_UP)
        if atr <= 0 or stop <= 0 or not stop < limit < target:
            reasons.append(f"bad_stop: atr {atr} gives stop {stop} / target {target} around limit {limit}")

    if reasons:
        return ValidationResult("rejected", tuple(reasons), proposal)

    order = _issue(
        ApprovedOrder,
        ticker=p.ticker,
        quantity=p.quantity,
        limit_price=limit,
        stop_price=stop,
        take_profit_price=target,
    )
    return ValidationResult("approved", (), proposal, order)


def validate_exit(
    symbol: str,
    snapshot: MarketSnapshot,
    *,
    project_root: Path,
    environ: Mapping[str, str],
) -> ExitResult:
    """Approve closing the full quantity of an existing long position. Never raises."""
    try:
        halt = _halt_reason(snapshot, project_root, environ)
        if halt:
            return ExitResult("halted", (halt,))

        reasons: list[str] = []
        if not snapshot.market_open:
            reasons.append("market_closed: market is not open")
        position = next((p for p in snapshot.positions if p.symbol == symbol), None)
        if position is None or position.qty <= 0:
            reasons.append(f"not_held: no long position in {symbol}")
        if reasons:
            return ExitResult("rejected", tuple(reasons))

        return ExitResult("approved", (), _issue(ApprovedExit, symbol=symbol, quantity=position.qty))
    except Exception as e:  # fail closed
        return ExitResult("rejected", (f"internal_error: {type(e).__name__}: {e}",))
