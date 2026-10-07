import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D

import pytest

from bot.broker import BrokerError
from bot.config import load_limits, load_runner, load_strategy
from bot.journal import append_record
from bot.main import main, merge_env, run_once
from bot.models import Bar, MarketSnapshot
from bot.runner import AgentRun, RunnerError, ToolCall
from bot.safety import PAPER_ENDPOINT

NOW = datetime(2026, 10, 7, 14, 0, tzinfo=UTC)
LIMITS, STRATEGY, RUNNER = load_limits(), load_strategy(), load_runner()


def pullback_bars():
    closes = [100 + i for i in range(215)] + [309, 304, 299, 294, 289]
    return [Bar(date(2025, 1, 1) + timedelta(days=i), D(c) + 1, D(c) - 1, D(c)) for i, c in enumerate(closes)]


class FakeBroker:
    def __init__(self, endpoint=PAPER_ENDPOINT, market_open=True, bars=None, fail=None):
        self.trading_endpoint = endpoint
        self.market_open = market_open
        self.bars = {"MSFT": pullback_bars()} if bars is None else bars
        self.fail = fail
        self.calls = []

    def snapshot(self, tickers, now):
        self.calls.append("snapshot")
        if self.fail == "snapshot":
            raise BrokerError("orders: RuntimeError: boom")
        return MarketSnapshot(
            market_open=self.market_open, equity=D("100000"), last_equity=D("100000"), buying_power=D("200000"),
            positions=(), open_orders=(), buys_submitted_today=0, latest_prices={"MSFT": D("289")},
            trading_endpoint=self.trading_endpoint)

    def daily_bars(self, tickers, count, now):
        self.calls.append("daily_bars")
        return {t: self.bars.get(t, []) for t in tickers}


GOOD = ('{"decision":"trade","ticker":"MSFT","side":"buy","quantity":17,"order_type":"limit",'
        '"limit_price":289.00,"reasoning":"Setup: x","confidence":0.6}')


class FakeAgent:
    def __init__(self, raw=GOOD, error=None, raises=None):
        self.raw, self.error, self.raises = raw, error, raises
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        if self.raises:
            raise self.raises
        calls = [ToolCall("Skill", '{"skill": "strategy"}', True), ToolCall("mcp__alpaca__get_news", '{}', True)]
        return AgentRun(None if self.error else self.raw, calls, 0.42, 5, 12.5, self.error)


def run(tmp_path, broker=None, agent=None, environ=None, broker_factory=None):
    broker = broker or FakeBroker()
    agent = agent or FakeAgent()
    factory = broker_factory or (lambda: broker)
    record, code = run_once(
        broker_factory=factory, agent=agent, limits=LIMITS, strategy=STRATEGY, runner_params=RUNNER,
        now=NOW, project_root=tmp_path, environ=environ or {}, log_dir=tmp_path / "logs")
    return record, code, broker, agent


def logged(tmp_path):
    return [json.loads(line) for line in (tmp_path / "logs/runs.jsonl").read_text().splitlines()]


def test_good_run_logs_would_submit(tmp_path):
    record, code, _, agent = run(tmp_path)
    assert code == 0 and record["outcome"] == "would_submit" and record["mode"] == "dry_run"
    assert [c["ticker"] for c in record["candidates"]] == ["MSFT"] and record["rules_only_pick"] == "MSFT"
    assert record["agent"]["skills_loaded"] == ["strategy"]
    assert record["agent"]["tool_calls"][1]["name"] == "mcp__alpaca__get_news"
    assert record["validation"] == {"status": "approved", "reasons": []}
    assert record["order"]["status"] == "would_submit" and record["order"]["quantity"] == 17
    assert "| MSFT |" in agent.prompts[0]
    assert logged(tmp_path)[0]["run_id"] == record["run_id"]


def test_kill_switch_stops_before_broker(tmp_path):
    (tmp_path / "KILL").touch()
    called = []
    record, code, _, agent = run(tmp_path, broker_factory=lambda: called.append(1))
    assert code == 0 and record["outcome"] == "halted" and "KILL file" in record["kill_switch"]
    assert called == [] and agent.prompts == []


def test_live_endpoint_stops(tmp_path):
    record, code, broker, agent = run(tmp_path, broker=FakeBroker(endpoint="https://api.alpaca.markets"))
    assert code == 1 and record["errors"][0]["stage"] == "paper_check"
    assert broker.calls == [] and agent.prompts == []


def test_market_closed_no_agent_call(tmp_path):
    record, code, _, agent = run(tmp_path, broker=FakeBroker(market_open=False))
    assert code == 0 and record["outcome"] == "market_closed" and agent.prompts == []


def test_no_candidates_no_agent_call(tmp_path):
    record, code, _, agent = run(tmp_path, broker=FakeBroker(bars={}))
    assert code == 0 and record["outcome"] == "no_candidates" and agent.prompts == []


def test_monthly_budget_reached_no_agent_call(tmp_path):
    append_record({"timestamp_utc": "2026-10-02T14:00:00+00:00", "agent": {"cost_usd": 27.0}}, tmp_path / "logs")
    record, code, _, agent = run(tmp_path)
    assert code == 0 and record["outcome"] == "budget_exhausted" and agent.prompts == []
    assert record["budget"] == {"spent_this_month_usd": "27.0", "monthly_budget_usd": 27.0}


def test_rejected_proposal_logged_with_reasons(tmp_path):
    record, code, _, _ = run(tmp_path, agent=FakeAgent(raw=GOOD.replace('"quantity":17', '"quantity":100')))
    assert code == 0 and record["outcome"] == "rejected" and record["order"] is None
    assert any(r.startswith("position_too_large") for r in record["validation"]["reasons"])


def test_no_trade_outcome(tmp_path):
    record, code, _, _ = run(tmp_path, agent=FakeAgent(raw='{"decision":"no_trade","reasoning":"Skipped: x"}'))
    assert code == 0 and record["outcome"] == "no_trade" and record["proposal"]["decision"] == "no_trade"


def test_agent_error_exit_1_no_validation(tmp_path):
    record, code, _, _ = run(tmp_path, agent=FakeAgent(error="error_max_budget_usd"))
    assert code == 1 and record["outcome"] == "error" and record["validation"] is None
    assert record["errors"] == [{"stage": "agent", "message": "error_max_budget_usd"}]
    assert record["agent"]["cost_usd"] == 0.42


def test_agent_setup_error_exit_1(tmp_path):
    record, code, _, _ = run(tmp_path, agent=FakeAgent(raises=RunnerError("ANTHROPIC_API_KEY is not set in .env")))
    assert code == 1 and record["errors"][0] == {"stage": "agent", "message": "ANTHROPIC_API_KEY is not set in .env"}


def test_broker_error_logged_with_stage_exit_1(tmp_path):
    record, code, _, agent = run(tmp_path, broker=FakeBroker(fail="snapshot"))
    assert code == 1 and record["errors"][0]["stage"] == "snapshot" and agent.prompts == []


def test_broker_factory_error_exit_1(tmp_path):
    def factory():
        raise BrokerError("ALPACA_API_KEY is not set in .env")
    record, code, _, _ = run(tmp_path, broker_factory=factory)
    assert code == 1 and record["errors"][0] == {"stage": "broker", "message": "ALPACA_API_KEY is not set in .env"}


@pytest.mark.parametrize("scenario", [
    dict(), dict(broker=FakeBroker(market_open=False)), dict(broker=FakeBroker(bars={})),
    dict(broker=FakeBroker(fail="snapshot")), dict(agent=FakeAgent(error="x")), dict(environ={"KILL_SWITCH": "1"}),
])
def test_every_path_writes_exactly_one_record(tmp_path, scenario):
    run(tmp_path, **scenario)
    assert len(logged(tmp_path)) == 1


def test_submit_flag_refused(capsys):
    assert main(["--submit"]) == 2
    assert "step 6" in capsys.readouterr().out


@pytest.mark.parametrize("dotenv,os_env,on", [
    ({"KILL_SWITCH": ""}, {"KILL_SWITCH": "1"}, True),
    ({"KILL_SWITCH": "1"}, {"KILL_SWITCH": "0"}, True),
    ({"KILL_SWITCH": ""}, {}, False),
    ({}, {"KILL_SWITCH": " 0 "}, False),
])
def test_merge_env_kill_switch_on_from_either_source(dotenv, os_env, on):
    value = merge_env(dotenv, os_env).get("KILL_SWITCH", "").strip()
    assert (value not in ("", "0")) == on


def test_merge_env_os_overrides_other_keys_and_skips_empty():
    merged = merge_env({"ALPACA_API_KEY": "file", "X": None}, {"ALPACA_API_KEY": "shell"})
    assert merged["ALPACA_API_KEY"] == "shell" and "X" not in merged
