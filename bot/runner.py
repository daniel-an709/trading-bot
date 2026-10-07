"""One locked-down Agent SDK session: Claude reads the news and returns its raw final answer.

Lockdown, in layers:
- tools=[]: no built-in tools at all (no Bash, file editing or web).
- allowed_tools: only the read-only Alpaca tools; everything else on the server is disallowed.
- permission_mode="dontAsk": anything not pre-approved is refused without asking.
- A PreToolUse hook denies any tool not on the allow list and records every call.
- strict_mcp_config: only the Alpaca server configured here, paper mode forced.
- setting_sources=["project"]: .claude/settings.json deny rules and the repo's skills apply.

The answer is only text. Nothing here can trade; validator.py decides what happens next.
"""

import json
import shutil
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

from claude_agent_sdk import ClaudeAgentOptions, HookMatcher, ResultMessage, query

from bot.alpaca_tools import MCP_SERVER_NAME, MCP_SERVER_PACKAGE, READ_TOOLS, SERVER_TOOLS, mcp_name
from bot.config import PROJECT_ROOT, Limits, RunnerParams
from bot.models import Candidate, MarketSnapshot

SKILLS = ["strategy", "risk", "journal"]
ALLOWED = frozenset({"Skill"} | {mcp_name(t) for t in READ_TOOLS})
BUILT_IN_TOOLS = {"Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Task"}
TOOLSETS = "account,trading,stock-data,news,assets"
CENT = Decimal("0.01")
FINAL_ANSWER_LINE = (
    "This is the bot's final-answer request: use the strategy, risk and journal skills "
    "and answer with only the JSON object."
)


class RunnerError(Exception):
    pass


@dataclass(frozen=True)
class ToolCall:
    name: str
    input_summary: str
    allowed: bool


@dataclass
class AgentRun:
    raw_output: str | None
    tool_calls: list[ToolCall]
    cost_usd: float | None
    num_turns: int | None
    duration_s: float
    error: str | None


@dataclass(frozen=True)
class Capacity:
    max_position_usd: Decimal
    trades_left: int
    open_slots: int
    daily_pnl_pct: Decimal


def capacity(snapshot: MarketSnapshot, limits: Limits) -> Capacity:
    open_buys = sum(1 for o in snapshot.open_orders if o.side == "buy")
    pct = Decimal(str(limits.max_position_pct)) / 100
    pnl = (snapshot.equity - snapshot.last_equity) / snapshot.last_equity * 100
    return Capacity(
        max_position_usd=(snapshot.equity * pct).quantize(CENT, ROUND_DOWN),
        trades_left=max(0, limits.max_new_trades_per_day - snapshot.buys_submitted_today),
        open_slots=max(0, limits.max_open_positions - len(snapshot.positions) - open_buys),
        daily_pnl_pct=pnl.quantize(CENT, ROUND_HALF_UP),
    )


def _money(x: Decimal) -> str:
    return f"{x.quantize(CENT, ROUND_HALF_UP):.2f}"


def build_prompt(today: date, candidates: Sequence[Candidate], snapshot: MarketSnapshot, cap: Capacity) -> str:
    rows = []
    for c in candidates:
        latest = snapshot.latest_prices.get(c.ticker)
        rows.append(f"| {c.ticker} | {_money(c.close)} | {_money(latest) if latest is not None else 'n/a'} "
                    f"| {_money(c.sma)} | {_money(c.rsi)} | {_money(c.atr)} |")
    positions = ", ".join(f"{p.symbol} {p.qty} @ {_money(p.avg_entry_price)}" for p in snapshot.positions) or "none"
    orders = ", ".join(f"{o.symbol} {o.side} {o.qty}" for o in snapshot.open_orders) or "none"
    return "\n".join([
        f"Date: {today.isoformat()} (New York). The market is open.",
        "",
        "Candidates (computed by code from completed daily bars: close above SMA, RSI at or below the limit):",
        "| ticker | prev close | latest | SMA | RSI | ATR |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "Capacity (from the account, computed by code):",
        f"- equity: ${snapshot.equity.quantize(CENT, ROUND_HALF_UP):,.2f}",
        f"- max new position size: ${cap.max_position_usd:,.2f}",
        f"- buying power: ${snapshot.buying_power.quantize(CENT, ROUND_HALF_UP):,.2f}",
        f"- trades left today: {cap.trades_left}",
        f"- open position slots: {cap.open_slots}",
        f"- today's P&L: {cap.daily_pnl_pct:.2f}%",
        "",
        f"Current positions: {positions}",
        f"Open orders: {orders}",
        "",
        FINAL_ANSWER_LINE,
    ])


def make_gate(trace: list[ToolCall]):
    async def gate(hook_input, tool_use_id, context):
        name = hook_input.get("tool_name", "")
        summary = json.dumps(hook_input.get("tool_input") or {}, sort_keys=True, default=str)[:200]
        allowed = name in ALLOWED
        trace.append(ToolCall(name, summary, allowed))
        if allowed:
            return {}
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"{name} is not on the bot's read-only allow list",
        }}
    return gate


def _require(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "").strip()
    if not value:
        raise RunnerError(f"{key} is not set in .env")
    return value


def build_options(
    params: RunnerParams, environ: Mapping[str, str], trace: list[ToolCall], uvx: str | None = None
) -> ClaudeAgentOptions:
    alpaca_key = _require(environ, "ALPACA_API_KEY")
    alpaca_secret = _require(environ, "ALPACA_SECRET_KEY")
    anthropic_key = _require(environ, "ANTHROPIC_API_KEY")
    uvx = uvx or shutil.which("uvx")
    if not uvx:
        raise RunnerError("uvx not found on PATH; install uv (brew install uv)")

    return ClaudeAgentOptions(
        cwd=PROJECT_ROOT,
        setting_sources=["project"],
        skills=SKILLS,
        tools=[],
        allowed_tools=sorted(ALLOWED - {"Skill"}),
        disallowed_tools=sorted({mcp_name(t) for t in SERVER_TOOLS - READ_TOOLS} | BUILT_IN_TOOLS),
        permission_mode="dontAsk",
        strict_mcp_config=True,
        mcp_servers={MCP_SERVER_NAME: {
            "type": "stdio",
            "command": uvx,
            "args": [MCP_SERVER_PACKAGE],
            "env": {
                "ALPACA_API_KEY": alpaca_key,
                "ALPACA_SECRET_KEY": alpaca_secret,
                "ALPACA_PAPER_TRADE": "true",  # the server treats any other value as live
                "ALPACA_TOOLSETS": TOOLSETS,
            },
        }},
        model=params.model,
        effort=params.effort,
        max_turns=params.max_turns,
        max_budget_usd=params.max_budget_usd_per_run,
        hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_gate(trace)])]},
        env={"ANTHROPIC_API_KEY": anthropic_key},
    )


async def run_agent(prompt: str, options: ClaudeAgentOptions, trace: list[ToolCall], query_fn=query) -> AgentRun:
    """Run the session and return Claude's final text. Never raises; failures set `error`."""
    started = time.monotonic()
    final: ResultMessage | None = None
    error = None
    try:
        async for message in query_fn(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                final = message
    except Exception as e:
        error = f"{type(e).__name__}: {e}"

    if error is None and final is None:
        error = "no result message from the session"
    elif final is not None and (final.is_error or final.subtype != "success"):
        detail = "; ".join(final.errors or [])
        status = f" (API status {final.api_error_status})" if final.api_error_status else ""
        error = f"{final.subtype}{status}{': ' + detail if detail else ''}"

    return AgentRun(
        raw_output=final.result if final is not None and error is None else None,
        tool_calls=list(trace),
        cost_usd=final.total_cost_usd if final is not None else None,
        num_turns=final.num_turns if final is not None else None,
        duration_s=round(time.monotonic() - started, 3),
        error=error,
    )
