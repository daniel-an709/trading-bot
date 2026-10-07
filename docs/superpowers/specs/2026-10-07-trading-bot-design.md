# AI-in-the-Loop Trading Bot — Design Spec

Date: 2026-10-07
Status: Draft for review

## 1. Purpose and success criteria

An experiment: can Claude's judgment, fenced in by deterministic hard limits, add value to a simple rules-based swing strategy? Paper trading only.

Success means:
- The bot runs unattended on a schedule, once per trading day.
- No order ever violates a hard limit (enforced in code, proven by tests).
- Every run leaves a complete, readable log record.
- After a couple of months, the benchmark script gives an honest comparison of **bot vs. rules-only vs. buy-and-hold SPY**.

## 2. Scope

In scope: long-only US stocks and ETFs from a fixed whitelist, daily-bar swing trades, Alpaca paper account.

Not in scope: live trading, shorting, options, margin, intraday trading, multiple accounts, notifications beyond the log.

## 3. Strategy: trend pullback with a news check

All rule parameters live in `config/strategy.yaml`.

| Rule | Definition |
|---|---|
| Data | Daily bars up to the last completed session; at least 220 bars per ticker |
| Uptrend | Last close > 200-day simple moving average of closes |
| Pullback | RSI(14), Wilder smoothing, ≤ 40 |
| Candidate | Whitelisted ticker meeting uptrend + pullback, not already held, no open order |
| Claude's role | Read the last 72 hours of news for each candidate. Reject candidates whose drop has a thesis-breaking cause (guidance cut, investigation/lawsuit, earnings miss, pending deal). Propose at most one trade per run — the strongest remaining candidate — or `no_trade`. |
| Entry | Limit buy, guided by the strategy skill to be at or below the previous close |
| Stop | entry − 2 × ATR(14), Wilder smoothing (computed by code) |
| Target | entry + 3 × ATR(14) (computed by code) |
| Time exit | Close any position whose entry filled ≥ 10 trading days ago |
| Rules-only pick | Candidate with the lowest RSI(14) — logged as a shadow trade for comparison, never submitted |

## 4. Hard limits (`config/limits.yaml`)

```yaml
max_position_pct: 5
max_open_positions: 6
max_new_trades_per_day: 2
max_daily_loss_pct: 2
whitelist:
  etfs:   [SPY, QQQ, IWM, XLK, XLF, XLV, XLE, XLI, XLY, XLP, XLU]
  stocks: [AAPL, MSFT, NVDA, AMZN, GOOGL, META, AVGO, JPM, V, LLY, XOM, COST, WMT, HD, UNH]
```

Hard-coded, not configurable: paper endpoint only, kill switch, long-only, limit orders only.

Definitions:
- **Daily loss**: `(equity − last_equity) / last_equity`, where `last_equity` is Alpaca's previous-close equity. At ≤ −2%, new entries are blocked for the day. Existing positions and their stops are untouched.
- **New trades today**: buy orders submitted on the current US/Eastern date, any status (conservative). Stops, targets and time exits do not count.
- **Open positions** for the cap: current positions + open buy orders.

## 5. Architecture

```
scheduler (once per weekday, ~10:00 ET; mechanism chosen in step 7)
  └─ bot/main.py
       1. kill switch on?          → log, exit
       2. paper endpoint?          → refuse to start if not
       3. broker.py: snapshot (clock, account, positions, today's orders, bars, latest prices)
       4. exits.py → validator.validate_exit → executor: close positions held ≥ 10 trading days
       5. signals.py: indicators → candidates (none → log, exit; Claude not called)
       6. runner.py: Agent SDK session → raw proposal text
       7. validator.validate_proposal → ApprovedOrder or rejection reasons
       8. executor.py: submit ApprovedOrder as a bracket (skipped in dry-run)
       9. tripwire: unexpected new orders? → create KILL, log alert
      10. journal.py: append one JSONL record
```

| File | Responsibility |
|---|---|
| `config/limits.yaml` | Hard limits |
| `config/strategy.yaml` | Strategy parameters (SMA 200, RSI 14 ≤ 40, ATR 14 × 2 / × 3, 10-day exit, 72h news window) |
| `bot/config.py` | Load and validate both YAML files into typed objects |
| `bot/broker.py` | Read-only wrapper over `alpaca-py`; hard-wired to paper; builds the `MarketSnapshot` |
| `bot/signals.py` | Pure functions: SMA, RSI, ATR, candidate selection, rules-only pick |
| `bot/schema.py` | `Proposal` / `NoTrade` models (pydantic, extra fields forbidden) |
| `bot/validator.py` | Pure functions, no network: `validate_proposal`, `validate_exit` |
| `bot/runner.py` | Agent SDK session; tool lockdown; returns raw output + tool-call trace + cost |
| `bot/exits.py` | Decide which positions hit the time exit |
| `bot/executor.py` | The only code that submits or cancels orders; accepts only `ApprovedOrder` / `ApprovedExit` |
| `bot/journal.py` | Append JSONL record |
| `bot/main.py` | Orchestrates one run; `--submit` flag enables paper orders (default is dry-run) |
| `scripts/benchmark.py` | Bot vs. rules-only vs. SPY |
| `scripts/backtest.py` | Rules-only historical backtest |

`ApprovedOrder` and `ApprovedExit` can only be constructed by the validator (private constructor token checked at runtime), so the executor cannot be handed an unvalidated order.

## 6. Proposal schema

Claude's final output must be exactly one JSON object, one of:

```json
{"decision": "trade", "ticker": "MSFT", "side": "buy", "quantity": 10,
 "order_type": "limit", "limit_price": 412.50,
 "reasoning": "...", "confidence": 0.7}
```
```json
{"decision": "no_trade", "reasoning": "..."}
```

- `quantity`: integer ≥ 1. `limit_price`: > 0, at most 2 decimal places. `confidence`: 0.0–1.0.
- Missing, extra, or wrongly typed fields → rejected as malformed.
- Stop and target are **not** in the schema; code computes them.
- `confidence` is logged but not used as a filter.

## 7. Validator rules

`validate_proposal(raw_text, snapshot, candidates, limits, env) → ValidationResult(approved, reasons, order)`

| # | Check | On failure |
|---|---|---|
| 1 | Kill switch off (`KILL` file absent, and `KILL_SWITCH` empty or `0` after stripping whitespace; any other value halts) | Reject immediately |
| 2 | Paper endpoint | Reject immediately |
| 3 | Valid JSON matching the schema | Reject immediately ("malformed") |
| 4 | `no_trade` | Not approved, nothing to do; logged |
| 5 | Market open (Alpaca clock) | Reject |
| 6 | `side == buy`, `order_type == limit` | Reject |
| 7 | Ticker whitelisted; not held; no open order for it | Reject |
| 8 | Ticker is in today's `signals.py` candidates | Reject |
| 9 | `0.90 × latest ≤ limit_price ≤ 1.01 × latest` | Reject |
| 10 | `quantity × limit_price ≤ 5% × equity` | Reject |
| 11 | positions + open buy orders < 6 | Reject |
| 12 | new trades today < 2 | Reject |
| 13 | daily loss > −2% | Reject |
| 14 | `quantity × limit_price ≤ buying_power` | Reject |

Checks 5–14 collect **all** failing reasons. All numbers come from the code-fetched `MarketSnapshot`, never from Claude. On approval, the validator computes stop and target from ATR (rounded to cents) and builds the `ApprovedOrder`.

`validate_exit(position, snapshot, env)` approves a full close of an existing long position only (checks 1, 2, 5; quantity equals held quantity; side is sell).

## 8. Runner and tool lockdown

Agent SDK options (exact names verified with the claude-api skill in step 5):
- `cwd` = project root; `setting_sources=["project"]` so `.claude/skills/` loads.
- `mcp_servers`: Alpaca MCP server via `uvx alpaca-mcp-server`, keys from `.env`, paper forced on.
- Model, max turns, and per-run spend cap chosen in step 5 to fit **$30/month** (≈ 22 runs → ≈ $1.35 per run ceiling). Claude is only called on days with candidates.
- Prompt contains: date, candidate table with indicators, current positions, remaining capacity (slots, trades left, max $ size), and the instruction to use the skills and end with only the JSON object.

Layered lockdown:
1. `allowed_tools`: `Skill` + read-only Alpaca tools approved by the user in step 5.
2. `disallowed_tools`: all Alpaca tools that place, replace, cancel or close orders/positions; built-in `Bash`, `Write`, `Edit`, `NotebookEdit`, `WebFetch`, `WebSearch`.
3. Permission callback: deny anything not in the allow list.
4. Tripwire: compare Alpaca's order list before and after the session; any order not placed by the executor → create `KILL`, log alert.
5. Test: the allow list contains no tool whose name matches trading verbs (place, submit, create_order, replace, cancel, close, exercise, liquidate).

If any layer cannot be verified in step 5, fall back to approach C: no MCP server in the bot; code fetches news and passes it in the prompt.

## 9. Skills (`.claude/skills/`, committed)

Each `SKILL.md` has frontmatter (`name`, specific `description` of when to use it) and plain-text instructions. Drafted with the skill-creator skill in step 3.

- **strategy**: the pullback rules (matching `strategy.yaml`), how to judge whether news breaks the thesis, how to pick among candidates, when to answer `no_trade`.
- **risk**: the hard limits (matching `limits.yaml`), how to size quantity, what to avoid.
- **journal**: structure of the `reasoning` field (setup, news read, thesis-break check, why this one, what would invalidate it).

Consistency test: parses `risk/SKILL.md` and `strategy/SKILL.md` for the limit and parameter values and asserts they match the YAML files. Changing a YAML value without updating the skill fails the test and names the stale line. `CLAUDE.md` states the same rule.

## 10. Alpaca MCP in Claude Code (step 4)

- Install `uv` (not currently installed).
- User runs `claude mcp add` themselves with the default local scope; keys passed as env vars, paper mode on. Keys never pass through chat and never land in a tracked file. (Local scope stores them in `~/.claude.json`, outside the repo, in plain text.)
- Verify with `/mcp`.
- No committed `.mcp.json`.

## 11. Logging

`logs/runs.jsonl`, one record per run:

```
run_id, timestamp_utc, mode (dry_run|paper), kill_switch,
account {equity, last_equity, buying_power}, positions,
candidates [{ticker, close, sma200, rsi14, atr14}], rules_only_pick,
agent {tool_calls [{name, input_summary}], skills_loaded, raw_output, cost_usd, duration_s},
proposal (parsed or null), validation {approved, reasons},
order {id, symbol, qty, limit, stop, target, status} | null,
time_exits [...], tripwire {unexpected_orders}, errors [{stage, message}]
```

## 12. Error handling

Fail closed. Every Alpaca and Claude call is wrapped. Any exception or implausible data (e.g. missing bars, equity ≤ 0) → log `{stage, message}`, place no new trade, exit non-zero. If the snapshot or the exit pass fails, the whole run stops.

Kill switch is checked in `main.py`, the validator, and the executor.

## 13. Testing

pytest, no network in tests.

- `test_validator.py`: pass and fail for every check, including: oversized, non-whitelisted, over daily cap, malformed JSON, kill switch file, kill switch env, live endpoint, not a candidate, chasing limit price, over position cap, daily loss breached.
- `test_signals.py`: SMA/RSI/ATR against hand-computed values on small fixed series.
- `test_schema.py`: valid/invalid proposals.
- `test_executor.py`: rejects non-approved objects, attaches bracket, refuses non-paper client.
- `test_runner.py`: fake SDK returning malformed output → no trade; tool allow-list test.
- `test_skills_consistency.py`: skills match YAML.
- `test_main.py`: end-to-end dry run with fake Alpaca and fake Claude.

## 14. Evaluation (step 7)

- `scripts/benchmark.py`: Alpaca paper portfolio history vs. buy-and-hold SPY vs. simulated rules-only shadow trades over the same dates. Reports total return, max drawdown, trade count, win rate.
- `scripts/backtest.py`: rules only, same limits and exits, over as much history as Alpaca's free data provides, vs. SPY. Claude is not backtested (training data may contain the outcomes).

## 15. Build order and gates

Each step: TDD, tests run and shown passing before claiming done, diffs shown, `/code-review` at the end, user approval before the next step.

1. Skeleton, `.gitignore`, `.env.example`, `requirements.txt`, venv, `CLAUDE.md` with the hard rules.
2. `config/limits.yaml`, `config/strategy.yaml`, `bot/config.py`, `bot/schema.py`, `bot/validator.py` with full tests.
3. Draft the three skills with the user (skill-creator) + consistency test.
4. `uv` + Alpaca MCP in Claude Code; test skills interactively against the paper account.
5. `signals.py`, `broker.py`, `runner.py`, `journal.py`, `main.py` in dry-run. Approve the read-only tool list.
6. `/security-review`, then `executor.py` and `exits.py`; connect `--submit` to paper.
7. Discuss local cron vs. cloud scheduling; set up schedule; `benchmark.py`, `backtest.py`.

## 16. Decisions deferred to later steps

| Item | Decided in |
|---|---|
| Exact read-only Alpaca tool list | Step 5 (user approval) |
| Model, max turns, per-run cost cap | Step 5 (current pricing via claude-api skill) |
| Agent SDK auth (Console API key) and option names | Step 5 |
| Alpaca MCP paper-mode setting name | Step 4 |
| Bracket leg time-in-force: whether `day` legs expire at close; if so use `gtc` and have the executor cancel stale unfilled entries next run | Step 6 |
| Time-exit order handling: a held position always has open bracket sells (stop/target), and a previous exit sell may still be open. The executor must cancel all open orders for the symbol, confirm they are cancelled, then sell the full quantity — never place a second sell on top | Step 6 |
| Scheduler: local cron vs. cloud | Step 7 |
| Python 3.13 compatibility of `claude-agent-sdk` and `alpaca-py` | Step 1 |
