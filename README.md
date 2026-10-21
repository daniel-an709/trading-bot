# AI-in-the-Loop Swing Trading Bot

An experiment: can an LLM's judgment, fenced in by deterministic hard limits, add value to a simple rules-based swing-trading strategy?

Code finds trend-pullback setups. Claude reads the recent news on each one and proposes at most one trade per day, or none. A Python validator checks every proposal against hard risk limits, and only approved orders can reach the executor.

> **Paper trading only.** The bot is hard-wired to Alpaca's paper endpoint; there is no setting or code path that reaches a live account. This is a personal research project, not financial advice.

## How a run works

```
kill switch on? ──► stop
paper endpoint? ──► refuse to start if not
snapshot        ──► account, positions, open orders, latest prices (Alpaca, read-only)
signals         ──► 200-day uptrend + RSI(14) ≤ 40 pullback across a fixed watchlist
                    no candidates → stop (Claude is not called)
Claude          ──► reads news via read-only tools, proposes one trade or no_trade (JSON)
validator       ──► every hard limit checked in code → approved order or reasons
journal         ──► one JSONL record: data, Claude's tool calls and reasoning, verdict, cost
```

Stop-loss and profit target are computed by code from ATR, never by Claude. A rules-only pick (lowest RSI) is logged on every run as a shadow trade, so the bot can later be compared against the same strategy without Claude.

## Safety design

The core idea is that the model proposes and code decides.

- **Limits live in code, not prompts.** `bot/validator.py` enforces `config/limits.yaml`: each position ≤ 5% of equity, ≤ 6 open positions, ≤ 2 new trades a day, no new entries after a 2% daily loss, whitelisted tickers only, limit price within a sane band of the last trade. Approved-order objects can only be constructed by the validator.
- **Locked-down agent.** The Claude Agent SDK session gets no built-in tools (no shell, files or web), only Skills plus an explicit allow list of read-only Alpaca tools. A deny-by-default `PreToolUse` hook blocks everything else and records every call.
- **Fail closed.** Any error or implausible data from Alpaca or Claude means log it and make no trade. A `KILL` file or `KILL_SWITCH` env var stops everything.
- **Cost caps.** $1 per run and a monthly budget, checked before Claude is called.
- **Skills stay in sync with config.** The strategy and risk instructions Claude reads (`.claude/skills/`) are tested against the YAML config, so the prompt can't silently drift from what the code enforces.

## Project layout

| Path | Purpose |
|---|---|
| `bot/signals.py` | SMA, Wilder RSI/ATR, candidate selection |
| `bot/broker.py` | Read-only Alpaca client; builds the market snapshot |
| `bot/runner.py` | Locked-down Claude Agent SDK session, tool trace, cost |
| `bot/schema.py` | Strict proposal schema (pydantic) |
| `bot/validator.py` | Hard-limit checks; the only source of approved orders |
| `bot/journal.py` | JSONL run log and monthly cost |
| `bot/main.py` | Orchestrates one run |
| `config/` | Limits, strategy parameters, model and budget |
| `.claude/skills/` | Strategy, risk and journal instructions for Claude |
| `docs/superpowers/` | Design specs and implementation plans |

## Status

Built step by step with test-driven development: spec, plan, failing tests, then code, with a code review at the end of each step.

| Step | | Status |
|---|---|---|
| 1–2 | Config, proposal schema, validator | Done |
| 3 | Strategy, risk and journal skills | Done |
| 4 | Alpaca MCP in Claude Code, write tools blocked | Done |
| 5 | Signals, broker, runner, journal, dry-run orchestration | Built, in final review on `build/step5` |
| 6 | Executor: bracket orders, stale-entry cancels, time exits | Designed (spec and plan on `build/step5`) |
| 7 | Daily scheduling; benchmark vs. rules-only and buy-and-hold SPY; backtest | Planned |

## Running it

Requires Python 3.13, an Alpaca paper account and an Anthropic API key.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -c constraints.txt
cp .env.example .env            # then fill in your own keys
.venv/bin/pytest                # run the tests (no network or keys needed)
.venv/bin/python -m bot.main    # one dry run; appends to logs/runs.jsonl, sends no orders
```

Tech: Python, Claude Agent SDK, alpaca-py, Alpaca MCP server, pydantic, pytest.
