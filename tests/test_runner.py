import asyncio
import re
from datetime import date
from decimal import Decimal as D

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

from bot.alpaca_tools import BLOCKED_TOOLS, MCP_SERVER_PACKAGE, READ_TOOLS, SERVER_TOOLS, mcp_name
from bot.config import PROJECT_ROOT, load_limits, load_runner
from bot.models import Candidate, MarketSnapshot, OpenOrder, Position
from bot.runner import (
    ALLOWED, SKILLS, Capacity, RunnerError, ToolCall, build_options, build_prompt, capacity, make_gate, run_agent,
)
from bot.safety import PAPER_ENDPOINT

WRITE_VERB = re.compile(r"(place|replace|cancel|close|exercise|update|create|delete|add|remove)_")
ENV = {"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s", "ANTHROPIC_API_KEY": "a"}
LIMITS = load_limits()
PARAMS = load_runner()


def snapshot(**overrides):
    defaults = dict(market_open=True, equity=D("100000"), last_equity=D("100400"), buying_power=D("200000"),
                    positions=(), open_orders=(), buys_submitted_today=0,
                    latest_prices={"MSFT": D("399.85")}, trading_endpoint=PAPER_ENDPOINT)
    return MarketSnapshot(**{**defaults, **overrides})


MSFT = Candidate("MSFT", D("401.2"), D("381.4"), D("34.1"), D("8.2"))


# --- Lockdown ---------------------------------------------------------------

def test_options_lockdown():
    o = build_options(PARAMS, ENV, [], uvx="/opt/homebrew/bin/uvx")
    assert o.cwd == PROJECT_ROOT and o.setting_sources == ["project"] and o.skills == SKILLS
    assert o.tools == [] and o.permission_mode == "dontAsk" and o.strict_mcp_config is True
    assert set(o.allowed_tools) == {mcp_name(t) for t in READ_TOOLS}
    assert not [t for t in o.allowed_tools if WRITE_VERB.search(t)]
    assert {mcp_name(t) for t in SERVER_TOOLS - READ_TOOLS} <= set(o.disallowed_tools)
    assert {mcp_name(t) for t in BLOCKED_TOOLS} <= set(o.disallowed_tools)
    assert {"Bash", "Write", "Edit", "WebFetch", "WebSearch"} <= set(o.disallowed_tools)
    server = o.mcp_servers["alpaca"]
    assert server["command"] == "/opt/homebrew/bin/uvx" and server["args"] == [MCP_SERVER_PACKAGE]
    assert server["env"]["ALPACA_PAPER_TRADE"] == "true"
    assert server["env"]["ALPACA_TOOLSETS"] == "account,trading,stock-data,news,assets"
    assert (server["env"]["ALPACA_API_KEY"], server["env"]["ALPACA_SECRET_KEY"]) == ("k", "s")
    assert o.env["ANTHROPIC_API_KEY"] == "a"
    assert (o.model, o.effort, o.max_turns, o.max_budget_usd) == ("claude-opus-5-5", "medium", 12, 1.0)
    assert o.hooks["PreToolUse"][0].matcher is None


@pytest.mark.parametrize("missing", ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ANTHROPIC_API_KEY"])
def test_build_options_requires_keys(missing):
    with pytest.raises(RunnerError, match=missing):
        build_options(PARAMS, {**ENV, missing: ""}, [], uvx="/x/uvx")


def test_build_options_requires_uvx(monkeypatch):
    monkeypatch.setattr("bot.runner.shutil.which", lambda _: None)
    with pytest.raises(RunnerError, match="uvx"):
        build_options(PARAMS, ENV, [])


# --- Gate -------------------------------------------------------------------

def call_gate(name, tool_input=None):
    trace: list[ToolCall] = []
    gate = make_gate(trace)
    out = asyncio.run(gate({"hook_event_name": "PreToolUse", "tool_name": name,
                            "tool_input": tool_input or {}, "tool_use_id": "t1"}, "t1", {"signal": None}))
    return out, trace


@pytest.mark.parametrize("name", ["Skill", mcp_name("get_news"), mcp_name("get_clock")])
def test_gate_allows_read_tool_and_skill(name):
    out, trace = call_gate(name, {"symbols": "MSFT"})
    assert out == {} and trace == [ToolCall(name, '{"symbols": "MSFT"}', True)]


@pytest.mark.parametrize("name", [mcp_name("place_stock_order"), mcp_name("close_all_positions"),
                                  mcp_name("get_stock_bars"), "Bash", "mcp__other__thing"])
def test_gate_denies_everything_else(name):
    out, trace = call_gate(name)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert trace[0].allowed is False


def test_gate_truncates_long_input():
    _, trace = call_gate("Skill", {"x": "y" * 1000})
    assert len(trace[0].input_summary) <= 200


def test_allowed_set():
    assert ALLOWED == {"Skill"} | {mcp_name(t) for t in READ_TOOLS}


# --- Capacity and prompt ----------------------------------------------------

def test_capacity():
    snap = snapshot(buys_submitted_today=1, positions=(Position("AAPL", 10, D("190")), Position("XLK", 5, D("230"))),
                    open_orders=(OpenOrder("JPM", "buy", 5), OpenOrder("AAPL", "sell", 10)))
    assert capacity(snap, LIMITS) == Capacity(D("5000.00"), 1, 3, D("-0.40"))


def test_capacity_floors_at_zero():
    snap = snapshot(buys_submitted_today=5, positions=tuple(Position(t, 1, D(1)) for t in "ABCDEFGH"))
    cap = capacity(snap, LIMITS)
    assert (cap.trades_left, cap.open_slots) == (0, 0)


def test_prompt_contains_candidates_capacity_and_final_answer_line():
    snap = snapshot(positions=(Position("AAPL", 10, D("190.5")),), open_orders=(OpenOrder("JPM", "buy", 5),))
    text = build_prompt(date(2026, 10, 7), [MSFT], snap, capacity(snap, LIMITS))
    for fragment in ["2026-10-07", "| MSFT | 401.20 | 399.85 | 381.40 | 34.10 | 8.20 |", "$5,000.00",
                     "trades left today: 2", "open position slots: 4", "-0.40%", "AAPL 10 @ 190.50",
                     "JPM buy 5", "only the JSON object"]:
        assert fragment in text, fragment


def test_prompt_marks_missing_latest_price():
    text = build_prompt(date(2026, 10, 7), [MSFT], snapshot(latest_prices={}), capacity(snapshot(), LIMITS))
    assert "| MSFT | 401.20 | n/a |" in text


def test_prompt_is_deterministic():
    snap = snapshot()
    args = (date(2026, 10, 7), [MSFT], snap, capacity(snap, LIMITS))
    assert build_prompt(*args) == build_prompt(*args)


# --- run_agent --------------------------------------------------------------

def result(**overrides):
    defaults = dict(subtype="success", duration_ms=1000, duration_api_ms=900, is_error=False, num_turns=4,
                    session_id="s", total_cost_usd=0.4, result='{"decision":"no_trade","reasoning":"r"}')
    return ResultMessage(**{**defaults, **overrides})


def fake_query(*messages, raise_exc=None):
    async def gen(*, prompt, options):
        for m in messages:
            yield m
        if raise_exc:
            raise raise_exc
    return gen


def run(query_fn):
    return asyncio.run(run_agent("prompt", build_options(PARAMS, ENV, [], uvx="/x/uvx"), [], query_fn=query_fn))


def test_run_agent_success():
    r = run(fake_query(AssistantMessage([TextBlock("thinking out loud")], "claude-opus-5-5"), result()))
    assert r.raw_output == '{"decision":"no_trade","reasoning":"r"}' and r.error is None
    assert (r.cost_usd, r.num_turns) == (0.4, 4) and r.duration_s >= 0


def test_run_agent_budget_exceeded_is_error():
    r = run(fake_query(result(subtype="error_max_budget_usd", is_error=True, result=None)))
    assert r.raw_output is None and "error_max_budget_usd" in r.error and r.cost_usd == 0.4


def test_run_agent_api_error_is_error():
    r = run(fake_query(result(is_error=True, api_error_status=529)))
    assert r.raw_output is None and "529" in r.error


def test_run_agent_exception_is_error():
    r = run(fake_query(raise_exc=ConnectionError("network down")))
    assert r.raw_output is None and "network down" in r.error


def test_run_agent_no_result_is_error():
    r = run(fake_query(AssistantMessage([TextBlock("hi")], "claude-opus-5-5")))
    assert r.raw_output is None and "no result" in r.error
