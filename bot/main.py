"""Run the bot once: python -m bot.main

Dry run only in this build step: an approved proposal is logged as "would_submit" and no
order is sent. Every run appends exactly one record to logs/runs.jsonl, and any error or
implausible data ends the run with no trade (exit code 1).
"""

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from dotenv import dotenv_values

from bot.broker import NEW_YORK, Broker, BrokerError
from bot.config import PROJECT_ROOT, Limits, RunnerParams, StrategyParams, load_limits, load_runner, load_strategy
from bot.journal import append_record, month_cost_usd
from bot.runner import AgentRun, RunnerError, build_options, build_prompt, capacity, run_agent
from bot.safety import is_paper_endpoint, kill_switch_reason
from bot.signals import find_candidates, rules_only_pick
from bot.validator import validate_proposal

OUTCOME_EXIT = {"error": 1}


def merge_env(dotenv: Mapping[str, str | None], os_env: Mapping[str, str]) -> dict[str, str]:
    """.env values overlaid by the shell environment; the kill switch is on if either source turns it on."""
    merged = {k: v for k, v in dotenv.items() if v is not None}
    merged.update(os_env)
    for source in (os_env, dotenv):
        value = (source.get("KILL_SWITCH") or "").strip()
        if value not in ("", "0"):
            merged["KILL_SWITCH"] = value
            break
    return merged


def _skills_loaded(run: AgentRun) -> list[str]:
    skills = []
    for call in run.tool_calls:
        if call.name == "Skill":
            try:
                skills.append(json.loads(call.input_summary).get("skill"))
            except ValueError:
                skills.append(call.input_summary)
    return skills


def run_once(
    *,
    broker_factory: Callable[[], Broker],
    agent: Callable[[str], AgentRun],
    limits: Limits,
    strategy: StrategyParams,
    runner_params: RunnerParams,
    now: datetime,
    project_root: Path,
    environ: Mapping[str, str],
    log_dir: Path,
) -> tuple[dict, int]:
    record = {
        "run_id": uuid.uuid4().hex,
        "timestamp_utc": now.astimezone(UTC).isoformat(),
        "mode": "dry_run",
        "outcome": None,
        "kill_switch": None,
        "account": None,
        "positions": None,
        "open_orders": None,
        "candidates": [],
        "rules_only_pick": None,
        "budget": None,
        "agent": None,
        "proposal": None,
        "validation": None,
        "order": None,
        "time_exits": [],
        "errors": [],
    }

    def finish(outcome: str, stage: str | None = None, message: str | None = None) -> tuple[dict, int]:
        record["outcome"] = outcome
        if stage:
            record["errors"].append({"stage": stage, "message": message})
        append_record(record, log_dir)
        return record, OUTCOME_EXIT.get(outcome, 0)

    stage = "kill_switch"
    try:
        killed = kill_switch_reason(project_root, environ)
        if killed:
            record["kill_switch"] = killed
            return finish("halted")

        stage = "broker"
        broker = broker_factory()

        stage = "paper_check"
        if not is_paper_endpoint(broker.trading_endpoint):
            return finish("error", stage, f"trading endpoint {broker.trading_endpoint!r} is not the paper endpoint")

        stage = "snapshot"
        tickers = sorted(limits.allowed_tickers)
        snap = broker.snapshot(tickers, now)
        record["account"] = {"equity": snap.equity, "last_equity": snap.last_equity,
                             "buying_power": snap.buying_power, "market_open": snap.market_open}
        record["positions"] = [dataclasses.asdict(p) for p in snap.positions]
        record["open_orders"] = [dataclasses.asdict(o) for o in snap.open_orders]
        if not snap.market_open:
            return finish("market_closed")

        stage = "bars"
        bars = broker.daily_bars(tickers, strategy.min_bars, now)

        stage = "signals"
        exclude = {p.symbol for p in snap.positions} | {o.symbol for o in snap.open_orders}
        candidates = find_candidates(bars, exclude, strategy)
        record["candidates"] = [dataclasses.asdict(c) for c in candidates]
        record["rules_only_pick"] = rules_only_pick(candidates)
        if not candidates:
            return finish("no_candidates")

        stage = "budget"
        spent = month_cost_usd(log_dir, now)
        if spent >= Decimal(str(runner_params.monthly_budget_usd)):
            record["budget"] = {"spent_this_month_usd": str(spent), "monthly_budget_usd": runner_params.monthly_budget_usd}
            return finish("budget_exhausted")

        stage = "agent"
        prompt = build_prompt(now.astimezone(NEW_YORK).date(), candidates, snap, capacity(snap, limits))
        run = agent(prompt)
        record["agent"] = {**dataclasses.asdict(run), "skills_loaded": _skills_loaded(run)}
        if run.error:
            return finish("error", stage, run.error)

        stage = "validation"
        result = validate_proposal(run.raw_output, snap, candidates, limits, strategy,
                                   project_root=project_root, environ=environ)
        record["proposal"] = result.proposal.model_dump(mode="json") if result.proposal else None
        record["validation"] = {"status": result.status, "reasons": list(result.reasons)}
        if result.approved:
            record["order"] = {"status": "would_submit", **dataclasses.asdict(result.order)}
            return finish("would_submit")
        return finish(result.status)
    except Exception as e:  # fail closed: log where it broke and trade nothing
        message = str(e) if isinstance(e, (BrokerError, RunnerError)) else f"{type(e).__name__}: {e}"
        return finish("error", stage, message)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m bot.main", description="Run the trading bot once (dry run).")
    parser.add_argument("--submit", action="store_true", help="send approved orders to the paper account")
    args = parser.parse_args(argv)
    if args.submit:
        print("Order submission is not connected until build step 6. Run without --submit for a dry run.")
        return 2

    environ = merge_env(dotenv_values(PROJECT_ROOT / ".env"), os.environ)
    limits, strategy, runner_params = load_limits(), load_strategy(), load_runner()

    def agent(prompt: str) -> AgentRun:
        trace = []
        options = build_options(runner_params, environ, trace)
        return asyncio.run(run_agent(prompt, options, trace))

    record, code = run_once(
        broker_factory=lambda: Broker.from_env(environ, runner_params.data_feed),
        agent=agent,
        limits=limits,
        strategy=strategy,
        runner_params=runner_params,
        now=datetime.now(UTC),
        project_root=PROJECT_ROOT,
        environ=environ,
        log_dir=PROJECT_ROOT / "logs",
    )
    summary = f"{record['outcome']}"
    if record["validation"]:
        summary += f" ({record['validation']['status']}: {'; '.join(record['validation']['reasons']) or 'ok'})"
    for err in record["errors"]:
        summary += f" [{err['stage']}] {err['message']}"
    print(f"Run {record['run_id']}: {summary}")
    return code


if __name__ == "__main__":
    sys.exit(main())
