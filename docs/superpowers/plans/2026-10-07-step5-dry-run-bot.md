# Step 5: Dry-Run Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m bot.main` runs one full dry run: kill switch → paper check → Alpaca snapshot → candidates → Claude (Agent SDK, read-only tools, skills) → validator → one JSONL log line. No order is ever sent (the executor arrives in step 6).

**Architecture:** `broker.py` is the only code-side Alpaca reader and builds the `MarketSnapshot` and daily bars. `signals.py` is pure indicator maths. `runner.py` builds a locked-down Agent SDK session and returns Claude's raw final text plus a tool-call trace and cost. `main.py` orchestrates and fails closed; `journal.py` writes the log. Every external dependency is injected so tests run with fakes and no keys.

**Tech Stack:** Python 3.13, `claude-agent-sdk` 0.2.164, `alpaca-py` 0.44.0, `alpaca-mcp-server==2.3.2` via `uvx`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-07-trading-bot-design.md`

## Global Constraints

- No real network, keys or Claude calls in tests. Every Alpaca client and the SDK `query` function are injected.
- Paper only: `TradingClient(paper=True)`; snapshot endpoint must pass `is_paper_endpoint`; MCP server env always has `ALPACA_PAPER_TRADE=true` (the server treats any other value as live).
- `--submit` is refused in this step with a clear message; there is no executor yet.
- Fail closed: any exception or implausible data → log `{stage, message}`, no trade, exit code 1.
- Money is `Decimal`. Dates for "today" use `America/New_York`.
- Verified Agent SDK option names (from the installed 0.2.164 source): `cwd`, `setting_sources`, `skills`, `tools`, `allowed_tools`, `disallowed_tools`, `permission_mode`, `strict_mcp_config`, `mcp_servers`, `model`, `effort`, `max_turns`, `max_budget_usd`, `hooks`, `env`. `ResultMessage` carries `result`, `total_cost_usd`, `num_turns`, `is_error`, `subtype`, `errors`, `permission_denials`.

## Review Focus

1. Alpaca returns a ticker with fewer than `min_bars` daily bars (new listing, data gap) → that ticker is skipped, not a crash and not a candidate. (Task 2)
2. Claude's session ends by hitting `max_turns` or `max_budget_usd`, or the API is down → `AgentRun.error` set, no proposal validated, exit code 1. (Task 4)
3. Claude tries a tool outside the allow list (Bash, a blocked Alpaca tool, an unknown MCP tool) → the hook denies it and the trace records the denial. (Task 4)
4. This month's logged Claude cost has reached the monthly budget → Claude is not called; record says why. (Task 6)
5. Weekend or holiday run (market closed) → snapshot still logged, Claude not called, no error. (Task 6)

---

## Decisions this plan needs from the user (marked ⚑)

- ⚑ **Read-only allow list** (Task 4): `get_news`, `get_corporate_action_announcements`, `get_account_info`, `get_all_positions`, `get_orders`, `get_stock_snapshot`, `get_clock`.
- ⚑ **Model and budget** (Task 1 `config/runner.yaml`): recommended `claude-opus-5-5`, effort `medium`, `max_turns: 12`, `max_budget_usd_per_run: 1.00`, `monthly_budget_usd: 27`.

## Rulings against the spec (recorded here, approved with the plan)

- Skills are enabled with the SDK's `skills=["strategy", "risk", "journal"]` option; passing `"Skill"` in `allowed_tools` is deprecated in 0.2.164. `setting_sources=["project"]` is still set explicitly.
- Built-in tools are removed entirely with `tools=[]` (stronger than listing them in `disallowed_tools`; they are listed there too as a second layer).
- Deny-by-default uses `permission_mode="dontAsk"` plus a `PreToolUse` hook, not `can_use_tool` (the SDK only calls `can_use_tool` for prompts, which `dontAsk` never produces; the hook sees every call and doubles as the "what the agent looked at" trace).
- `strict_mcp_config=True` so the bot ignores MCP servers configured elsewhere on the machine (including the Claude Code `alpaca` entry).
- The MCP server is started with `ALPACA_TOOLSETS=account,trading,stock-data,news,assets`, so crypto, options, watchlist, locate and bond tools are never loaded.
- No `output_format` JSON schema for now: the skills plus parser produced valid JSON in 6/6 test runs, and a trade/no-trade union schema is unverified. Revisit if malformed answers appear in logs.

---

## File Structure

| Path | Responsibility |
|---|---|
| `config/runner.yaml` | Model, effort, turn/cost caps, data feed |
| `bot/config.py` (modify) | `RunnerParams`, `load_runner` |
| `bot/models.py` (modify) | `Bar` |
| `bot/alpaca_mcp_tools.json` (moved from `tests/fixtures/`) | Pinned server tool list, used at runtime |
| `bot/alpaca_tools.py` (modify) | `READ_TOOLS`, `SERVER_TOOLS`, `mcp_name()` |
| `bot/signals.py` | `sma`, `rsi_wilder`, `atr_wilder`, `find_candidates`, `rules_only_pick` |
| `bot/broker.py` | `Broker`, `BrokerError` |
| `bot/runner.py` | `build_options`, `build_prompt`, `Capacity`, `capacity`, `run_agent`, `AgentRun`, `ToolCall` |
| `bot/journal.py` | `append_record`, `month_cost_usd` |
| `bot/main.py` | `run_once`, `main` |
| `tests/test_signals.py`, `tests/test_broker.py`, `tests/test_runner.py`, `tests/test_journal.py`, `tests/test_main.py` | Tests |

---

### Task 1: Runner config, `Bar`, tool lists

**Files:** Create `config/runner.yaml`; modify `bot/config.py`, `bot/models.py`, `bot/alpaca_tools.py`, `tests/test_config.py`, `tests/test_alpaca_tool_policy.py`; move `tests/fixtures/alpaca_mcp_tools.json` → `bot/alpaca_mcp_tools.json`.

**Interfaces — Produces:**
- `RunnerParams` (frozen, extra forbid): `model: str` (non-empty), `effort: Literal["low","medium","high","xhigh","max"]`, `max_turns: PositiveInt`, `max_budget_usd_per_run: PositiveFloat`, `monthly_budget_usd: PositiveFloat`, `data_feed: Literal["iex","sip","delayed_sip"]`; `load_runner(path=PROJECT_ROOT/"config/runner.yaml") -> RunnerParams`. Validator: `max_budget_usd_per_run <= monthly_budget_usd`.
- `Bar(day: date, high: Decimal, low: Decimal, close: Decimal)` frozen dataclass in `bot/models.py`.
- `bot/alpaca_tools.py`: `SERVER_TOOLS: frozenset[str]` loaded from `bot/alpaca_mcp_tools.json`; `READ_TOOLS = frozenset({...7 above...})`; `mcp_name(tool: str) -> str` returns `f"mcp__alpaca__{tool}"`.

`config/runner.yaml` (⚑ values):
```yaml
model: claude-opus-5-5
effort: medium
max_turns: 12
max_budget_usd_per_run: 1.00
monthly_budget_usd: 27
data_feed: iex
```

- [ ] **Step 1: Failing tests** — `test_load_runner_real_file` (values above); `test_runner_rejects_per_run_above_monthly`; `test_runner_rejects_unknown_effort`; `test_read_tools_are_in_server` (`READ_TOOLS <= SERVER_TOOLS`); `test_read_and_blocked_disjoint`; `test_no_read_tool_has_write_verb` (reuse `WRITE_VERB`); existing policy tests read the moved JSON.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_config.py tests/test_alpaca_tool_policy.py -q` → FAIL (missing names).
- [ ] **Step 3: Implement.** `SERVER_TOOLS` reads JSON via `Path(__file__).with_name("alpaca_mcp_tools.json")`.
- [ ] **Step 4: Run full suite** → all pass.
- [ ] **Step 5: Commit** `feat: runner config, Bar model, read-only tool list`

### Task 2: `signals.py`

**Files:** Create `bot/signals.py`, `tests/test_signals.py`.

**Interfaces — Consumes:** `Bar`, `Candidate`, `StrategyParams`. **Produces:**
- `sma(closes: Sequence[Decimal], period: int) -> Decimal` — mean of the last `period` closes; `ValueError` if fewer.
- `rsi_wilder(closes: Sequence[Decimal], period: int) -> Decimal` — first averages are the simple mean of the first `period` changes, then `avg = (prev*(period-1) + current)/period`; returns `100` when average loss is 0; `ValueError` if `len(closes) < period + 1`.
- `atr_wilder(bars: Sequence[Bar], period: int) -> Decimal` — true range from the second bar on: `max(h-l, |h-prev_close|, |l-prev_close|)`; first ATR = mean of first `period` TRs, then Wilder smoothing; `ValueError` if `len(bars) < period + 1`.
- `find_candidates(bars: Mapping[str, Sequence[Bar]], exclude: Set[str], params: StrategyParams) -> list[Candidate]` — skips tickers in `exclude` (held or with open orders) and tickers with fewer than `params.min_bars` bars; keeps those with `close > sma` and `rsi <= rsi_max`; `Candidate(ticker, close, sma, rsi, atr)`; sorted by ticker.
- `rules_only_pick(candidates: Sequence[Candidate]) -> str | None` — lowest RSI, ties by ticker.

- [ ] **Step 1: Failing tests** (hand-computed, period 3):
```python
def test_sma(): assert sma([D(10), D(11), D(12), D(10), D(13)], 3) == D(35) / 3
def test_rsi_wilder_hand_computed():   # closes 10,11,10,12,13,12 → RSI = 1800/31
    assert abs(rsi_wilder([D(x) for x in (10, 11, 10, 12, 13, 12)], 3) - D(1800) / 31) < D("1e-9")
def test_rsi_all_gains_is_100(): assert rsi_wilder([D(x) for x in range(10, 20)], 3) == 100
def test_atr_wilder_hand_computed():  # bars (h,l,c): (11,9,10) (12,10,11) (13,11,12) (12,9,10) (14,10,13) → 26/9
def test_too_few_values_raise()       # sma, rsi, atr each raise ValueError
def test_find_candidates()            # 220 bars: A rises 1/day for 215 days then falls 5/day for 5 → candidate;
                                      # B rises only → not (RSI 100); C falls steadily → not (below SMA);
                                      # D has 100 bars → skipped; A again but in exclude → not
def test_rules_only_pick_lowest_rsi_then_ticker()
def test_rules_only_pick_empty_is_none()
```
- [ ] **Step 2: Run** → FAIL (module missing).
- [ ] **Step 3: Implement** with `Decimal` arithmetic throughout.
- [ ] **Step 4: Run** `tests/test_signals.py` → pass.
- [ ] **Step 5: Commit** `feat: SMA, Wilder RSI/ATR and candidate selection`

### Task 3: `broker.py`

**Files:** Create `bot/broker.py`, `tests/test_broker.py`.

**Interfaces — Consumes:** `MarketSnapshot`, `Position`, `OpenOrder`, `Bar`, `is_paper_endpoint`. **Produces:**
- `class BrokerError(Exception)`.
- `Broker(trading, data, feed: str)` — `trading` / `data` are an `alpaca.trading.client.TradingClient` and `alpaca.data.historical.StockHistoricalDataClient` (fakes in tests). Raises `BrokerError` if `trading_endpoint` is not paper.
- `Broker.from_env(environ: Mapping[str, str], feed: str) -> Broker` — `BrokerError` if `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` missing; always `TradingClient(..., paper=True)`.
- `trading_endpoint: str` property — the trading client's base URL as a string.
- `snapshot(tickers: Sequence[str], now: datetime) -> MarketSnapshot` — `get_clock().is_open`; `get_account()` equity / last_equity / buying_power as `Decimal`; `get_all_positions()` → `Position`s (long only; a short position raises `BrokerError`); open orders via `get_orders(status=OPEN)`; `buys_submitted_today` = buy orders with `submitted_at` on `now`'s New York date via `get_orders(status=ALL, after=<NY midnight>)`; latest prices via `get_stock_latest_trade` for `tickers`.
- `daily_bars(tickers: Sequence[str], count: int, now: datetime) -> dict[str, list[Bar]]` — daily bars from `get_stock_bars(feed=self.feed)`, only sessions **before** `now`'s New York date, last `count` per ticker, oldest first.

- [ ] **Step 1: Failing tests** with `SimpleNamespace` fakes: `test_snapshot_maps_account_positions_orders_prices`; `test_buys_today_uses_new_york_date` (an order at 03:30 UTC counts for the previous NY day); `test_live_endpoint_refused`; `test_from_env_requires_keys`; `test_short_position_raises`; `test_daily_bars_drop_today_and_keep_last_count`; `test_client_error_becomes_broker_error`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Request objects from `alpaca.trading.requests` / `alpaca.data.requests`; wrap every client exception in `BrokerError(stage-specific message)`.
- [ ] **Step 4: Run** → pass. **Step 5: Commit** `feat: read-only Alpaca broker snapshot and daily bars`

### Task 4: `runner.py`

**Files:** Create `bot/runner.py`, `tests/test_runner.py`.

**Interfaces — Consumes:** `RunnerParams`, `Limits`, `MarketSnapshot`, `Candidate`, `READ_TOOLS`, `SERVER_TOOLS`, `BLOCKED_TOOLS`, `mcp_name`, `MCP_SERVER_PACKAGE`. **Produces:**
- `SKILLS = ["strategy", "risk", "journal"]`; `ALLOWED = {"Skill"} | {mcp_name(t) for t in READ_TOOLS}`.
- `@dataclass(frozen=True) ToolCall(name: str, input_summary: str, allowed: bool)`.
- `@dataclass AgentRun(raw_output: str | None, tool_calls: list[ToolCall], cost_usd: float | None, num_turns: int | None, duration_s: float, error: str | None)`.
- `@dataclass(frozen=True) Capacity(max_position_usd: Decimal, trades_left: int, open_slots: int, daily_pnl_pct: Decimal)`; `capacity(snapshot, limits) -> Capacity` (floors at 0).
- `build_prompt(today: date, candidates, snapshot, cap: Capacity) -> str` — deterministic text: candidate table (ticker, prev close, latest, SMA, RSI, ATR), capacity, positions, open orders, and the line "This is the bot's final-answer request: use the strategy, risk and journal skills and answer with only the JSON object."
- `make_gate(trace: list[ToolCall]) -> HookCallback` — async `PreToolUse` hook: appends a `ToolCall` (input summarised to ≤200 chars); returns `{}` for names in `ALLOWED`, otherwise `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "<name> is not on the bot's read-only allow list"}}`.
- `build_options(params: RunnerParams, environ: Mapping[str, str], trace: list[ToolCall]) -> ClaudeAgentOptions` — `cwd=PROJECT_ROOT`, `setting_sources=["project"]`, `skills=SKILLS`, `tools=[]`, `allowed_tools=sorted(ALLOWED - {"Skill"})`, `disallowed_tools=sorted({mcp_name(t) for t in SERVER_TOOLS - READ_TOOLS} | {"Bash","Write","Edit","NotebookEdit","WebFetch","WebSearch","Task"})`, `permission_mode="dontAsk"`, `strict_mcp_config=True`, `mcp_servers={"alpaca": {"type": "stdio", "command": <uvx path>, "args": [MCP_SERVER_PACKAGE], "env": {ALPACA_API_KEY, ALPACA_SECRET_KEY, "ALPACA_PAPER_TRADE": "true", "ALPACA_TOOLSETS": "account,trading,stock-data,news,assets"}}}`, `model`, `effort`, `max_turns`, `max_budget_usd=params.max_budget_usd_per_run`, `hooks={"PreToolUse": [HookMatcher(matcher=None, hooks=[make_gate(trace)])]}`, `env={"ANTHROPIC_API_KEY": ...}`. Raises `RunnerError` if `uvx` or a key is missing.
- `async run_agent(prompt: str, options: ClaudeAgentOptions, trace: list[ToolCall], query_fn=claude_agent_sdk.query) -> AgentRun` — consumes messages; takes `ResultMessage.result` as `raw_output`; sets `error` when an exception is raised, when `is_error`, or when `subtype` is not `"success"` (e.g. max turns, max budget); never raises.

- [ ] **Step 1: Failing tests:**
  - `test_options_lockdown` — asserts every field above; `ALPACA_PAPER_TRADE == "true"`; server pinned; no allowed name matches `WRITE_VERB`; every `BLOCKED_TOOLS` name is in `disallowed_tools`.
  - `test_gate_allows_read_tool_and_skill`, `test_gate_denies_blocked_tool`, `test_gate_denies_bash_and_unknown` — call the hook with `PreToolUseHookInput`-shaped dicts; trace records allowed flag.
  - `test_capacity` (equity 100000 → 5000; 1 buy today → 1 left; 2 positions + 1 open buy → 3 slots; pnl −0.4%), `test_capacity_floors_at_zero`.
  - `test_prompt_contains_candidates_capacity_and_final_answer_line`; `test_prompt_is_deterministic`.
  - `test_run_agent_success` (fake `query_fn` yields an `AssistantMessage` then `ResultMessage(subtype="success", result='{"decision":"no_trade","reasoning":"r"}', total_cost_usd=0.4, ...)`); `test_run_agent_budget_exceeded_is_error` (`subtype="error_max_budget_usd"`); `test_run_agent_exception_is_error`; `test_run_agent_no_result_is_error`.
  - `test_build_options_requires_keys`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → pass.
- [ ] **Step 5: Commit** `feat: locked-down Agent SDK runner with tool trace`

### Task 5: `journal.py`

**Files:** Create `bot/journal.py`, `tests/test_journal.py`.

**Interfaces — Produces:** `append_record(record: dict, log_dir: Path) -> Path` (creates dir; appends one JSON line to `log_dir/"runs.jsonl"`; serialises `Decimal` as string, `date`/`datetime` ISO, dataclasses via `asdict`); `month_cost_usd(log_dir: Path, now: datetime) -> Decimal` (sums `agent.cost_usd` for records whose `timestamp_utc` is in `now`'s calendar month; unreadable lines are skipped and counted, never crash).

- [ ] **Step 1: Failing tests:** `test_two_appends_two_lines`; `test_decimal_and_dates_serialised`; `test_month_cost_sums_only_this_month`; `test_month_cost_skips_corrupt_line`; `test_missing_log_dir_is_zero`.
- [ ] **Steps 2–4** RED → implement → GREEN. **Step 5: Commit** `feat: JSONL run journal and monthly cost`

### Task 6: `main.py` (dry run)

**Files:** Create `bot/main.py`, `tests/test_main.py`; modify `CLAUDE.md` (dry-run command).

**Interfaces — Consumes:** all of the above, `validate_proposal`. **Produces:**
- `run_once(*, broker: Broker, agent: Callable[[str], Awaitable[AgentRun]], limits, strategy, runner_params, now: datetime, project_root: Path, environ: Mapping[str, str], log_dir: Path) -> tuple[dict, int]` — returns `(record, exit_code)` and appends the record. Order: kill switch → `broker.trading_endpoint` paper check → snapshot → (market closed → stop, exit 0) → daily bars (`count = strategy.min_bars`) → candidates + rules-only pick → (none → stop, exit 0) → monthly budget (`month_cost_usd >= monthly_budget_usd` → stop, exit 0, reason logged) → prompt → agent → (agent error → exit 1) → `validate_proposal` → record `order = {"status": "would_submit", ...ApprovedOrder fields}` when approved. Record fields per spec §11 plus `mode: "dry_run"`.
- `main(argv: list[str] | None = None) -> int` — loads `.env` with python-dotenv (`dotenv_values`, not into `os.environ`), builds `Broker.from_env`, wires `run_agent(build_prompt..., build_options...)`; `--submit` prints "Order submission is not connected until build step 6." and returns 2. Entry: `python -m bot.main`.

- [ ] **Step 1: Failing tests** (fake broker + fake agent; `tmp_path` logs): `test_kill_switch_stops_before_broker`; `test_live_endpoint_stops`; `test_market_closed_no_agent_call`; `test_no_candidates_no_agent_call`; `test_monthly_budget_reached_no_agent_call`; `test_good_run_logs_would_submit` (record has candidates, rules_only_pick, tool_calls, validation approved, order status would_submit); `test_rejected_proposal_logged_with_reasons`; `test_agent_error_exit_1_no_validation`; `test_broker_error_logged_with_stage_exit_1`; `test_submit_flag_refused`; `test_every_path_writes_exactly_one_record`.
- [ ] **Steps 2–4** RED → implement → GREEN (full suite).
- [ ] **Step 5:** Add to `CLAUDE.md` Commands: `.venv/bin/python -m bot.main   # one dry run (no orders)`.
- [ ] **Step 6: Commit** `feat: dry-run orchestration with fail-closed logging`
- [ ] **Step 7: Build step 5 gate** — `/code-review`, show results, wait for approval. First live dry run happens once the user has Alpaca and Anthropic keys; it verifies the data feed, skill loading under `tools=[]`, and real cost per run.
