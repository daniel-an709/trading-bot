# Step 6: Executor and Exits Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `python -m bot.main --submit` sends bracket entries, stale-entry cancels and time exits to the Alpaca paper account; without `--submit` the same run logs what it would have done.

**Architecture:** `exits.py` is pure decision logic over the snapshot. `validator.py` issues `ApprovedCancel` / `ApprovedExit` / `ApprovedOrder`. `executor.py` owns its own paper `TradingClient` and is the only code that submits or cancels. `main.py` adds housekeeping, unprotected check, tripwire and submit stages; `executor_factory=None` means dry run.

**Tech Stack:** Python 3.13, `alpaca-py` 0.44.0 (`submit_order`, `cancel_order_by_id`, `get_orders`, `get_calendar`; `LimitOrderRequest`, `TakeProfitRequest`, `StopLossRequest`, `GetCalendarRequest`, `OrderClass.BRACKET`, `TimeInForce.GTC/DAY`), pytest.

**Spec:** `docs/superpowers/specs/2026-10-21-step6-executor-and-exits-design.md` (parent: `docs/superpowers/specs/2026-10-07-trading-bot-design.md`)

**Start condition:** step 5 approved and `build/step5` merged to `main`; work on a new branch `build/step6`.

## Global Constraints

- No network in tests. Alpaca clients, sleep and clocks are injected.
- Paper only: executor builds `TradingClient(key, secret, paper=True)` and refuses any endpoint failing `is_paper_endpoint`.
- Executor re-checks `kill_switch_reason(project_root, environ)` and the paper endpoint at the start of every method.
- Entries: limit buy, `time_in_force=gtc`, `order_class=bracket`, take-profit limit, stop leg with `stop_price` only (no `limit_price`).
- Exits: `day` limit sell, full quantity, `limit_price` = latest × 0.99 rounded **down** to the cent.
- Close sequence: cancel every open order for the symbol → poll open orders for the symbol every 1 s, up to 30 s → sell only when none remain.
- Client order id: `tb-<run_id[:12]>-<kind>-<symbol>`, kind ∈ `entry`, `exit`. Stale-entry rule matches the `tb-` prefix.
- "Today" and "submitted before today" use `America/New_York` dates.
- No retries anywhere. Failures fail closed: log `{stage, message}`, exit 1, no new entry.

## Review Focus

1. Alpaca lists a waiting stop leg with status `held` → it must still count as protection, or every position looks unprotected and entries stop forever. (Task 2 test `test_held_stop_leg_is_an_open_order`; confirmed live in Task 7.)
2. Cancelling one bracket leg makes Alpaca cancel its sibling, so cancelling the sibling then errors → `close` must not abort on that; the confirmation poll decides. (Task 4 `test_close_tolerates_cancel_error_for_order_already_gone`)
3. A position with no findable filled buy (bought by hand, or older than the 90-day lookup) → clear broker error naming the symbol, run stops; never a guessed age. (Task 2 `test_position_without_filled_buy_raises`)
4. Local clock behind Alpaca's → housekeeping orders look "submitted after session start" → false tripwire. Orders whose id the executor created this run are never unexpected. (Task 6 `test_tripwire_ignores_orders_the_executor_placed`)
5. A symbol exited this run reappears as a candidate the same run → its pending sell is an open order, so it is excluded. (Task 6 `test_exited_symbol_not_a_candidate_same_run`)

---

## File Structure

| Path | Responsibility |
|---|---|
| `bot/models.py` (modify) | New fields on `OpenOrder`, `Position`, `MarketSnapshot` |
| `bot/exits.py` (create) | `due_for_exit`, `stale_entries`, `unprotected` — pure |
| `bot/broker.py` (modify) | Fill dates, sessions, richer open orders, `orders_submitted_since` |
| `bot/validator.py` (modify) | `ApprovedCancel`, `CancelResult`, `validate_cancel`; `ApprovedExit.limit_price` |
| `bot/executor.py` (create) | `Executor`, `ExecutorError`, `SubmittedOrder` |
| `bot/main.py` (modify) | New stages, `--submit`, `executor_factory` |
| `CLAUDE.md`, parent spec (modify) | Reworded limit-order rule; §16 marked decided |

---

### Task 1: Models and `exits.py`

**Files:** Modify `bot/models.py`. Create `bot/exits.py`, `tests/test_exits.py`.

**Interfaces — Produces:**
- `OpenOrder(symbol, side, qty, id: str = "", client_order_id: str = "", order_type: str = "limit", submitted_at: datetime | None = None, stop_price: Decimal | None = None)` — new fields defaulted so existing constructions keep working.
- `Position(symbol, qty, avg_entry_price, entry_fill_date: date | None = None)`.
- `MarketSnapshot(..., sessions: tuple[date, ...] = ())` — trading dates, ascending.
- `due_for_exit(positions: Sequence[Position], sessions: Sequence[date], today: date, n: int) -> list[Position]` — held days = sessions `s` with `fill < s <= today`; due when `>= n`. Raises `ValueError` naming the symbol if `entry_fill_date` is `None` or earlier than `sessions[0]`.
- `stale_entries(open_orders: Sequence[OpenOrder], today: date) -> list[OpenOrder]` — side `buy`, `client_order_id` starts with `tb-`, `submitted_at` New York date `< today`.
- `unprotected(positions: Sequence[Position], open_orders: Sequence[OpenOrder]) -> list[str]` — symbols with no open order where side `sell`, `order_type == "stop"`, `qty >= position.qty`.

- [ ] **Step 1: Failing tests** in `tests/test_exits.py`: `test_ten_sessions_is_due_nine_is_not`; `test_weekend_and_holiday_not_counted` (sessions skip a Monday holiday; due day moves by one); `test_fill_day_itself_not_counted`; `test_missing_fill_date_raises`; `test_fill_before_first_session_raises`; `test_stale_entry_only_tb_buys_from_earlier_ny_date` (a `tb-` buy at 23:30 New York yesterday is stale; one from 00:30 New York today is not; a non-`tb-` buy and a `tb-` sell are ignored); `test_unprotected_when_no_stop`; `test_unprotected_when_stop_covers_fewer_shares`; `test_take_profit_limit_alone_is_not_protection`; `test_protected_with_full_stop`.
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_exits.py -v` — FAIL (module missing).
- [ ] **Step 3:** Add the model fields and implement `bot/exits.py`.
- [ ] **Step 4:** Run `.venv/bin/pytest` — all pass (existing tests untouched).
- [ ] **Step 5: Commit** `feat: time-exit, stale-entry and protection checks`

### Task 2: Broker reads for step 6

**Files:** Modify `bot/broker.py`, `tests/test_broker.py`.

**Interfaces — Consumes:** Task 1 models. **Produces:**
- `snapshot()` fills `OpenOrder.id` (`str(o.id)`), `client_order_id`, `order_type` (`_value(o.order_type)`), `submitted_at`, `stop_price` (Decimal or None); `Position.entry_fill_date`; `MarketSnapshot.sessions`.
- Fill dates: only when positions exist, one `get_orders(GetOrdersRequest(status=CLOSED, side=BUY, symbols=[held], after=now - 90 days, limit=ORDER_LIMIT))`; per symbol the latest `filled_at` (New York date) among orders with `filled_at` set. A held symbol with none → `BrokerError("fill date: no filled buy order found for <SYM> in the last 90 days")`. Result full (`>= ORDER_LIMIT`) → `BrokerError`.
- Sessions: `get_calendar(GetCalendarRequest(start=min(fill dates, today - 30 days), end=today))` → `tuple(c.date ...)`.
- `orders_submitted_since(t: datetime) -> tuple[OpenOrder, ...]` — `GetOrdersRequest(status=ALL, after=t, nested=True, limit=ORDER_LIMIT)`; full result → `BrokerError`.

- [ ] **Step 1: Failing tests** (extend `FakeTrading` with `closed_orders`, `calendar`, and order objects carrying `id`, `client_order_id`, `order_type`, `stop_price`, `filled_at`): `test_open_orders_carry_id_type_and_stop`; `test_held_stop_leg_is_an_open_order` (an order with `status="held"`, `order_type="stop"` in the open list maps through); `test_fill_date_is_latest_filled_buy_in_new_york`; `test_position_without_filled_buy_raises`; `test_no_positions_skips_fill_and_calendar_lookups`; `test_sessions_from_calendar_cover_oldest_fill`; `test_orders_submitted_since_uses_after_and_nested`; `test_orders_submitted_since_full_page_raises`. Update `test_snapshot_maps_account_positions_orders_prices` for the new fields.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `feat: broker reads fill dates, sessions and order details`

### Task 3: Validator — cancels and exit price

**Files:** Modify `bot/validator.py`, `tests/test_validate_exit.py`. Create `tests/test_validate_cancel.py`.

**Interfaces — Produces:**
- `ApprovedExit(symbol: str, quantity: int, limit_price: Decimal)` — `limit_price = (latest * Decimal("0.99")).quantize(Decimal("0.01"), rounding=ROUND_DOWN)`. Missing or `<= 0` latest price → reason `no_price: no latest price for <SYM>`.
- `ApprovedCancel(order_id: str, symbol: str)`, issued only via `_issue`.
- `CancelResult(status: Literal["approved", "rejected", "halted"], reasons: tuple[str, ...], cancel: ApprovedCancel | None = None)` with `.approved`.
- `validate_cancel(order_id: str, snapshot: MarketSnapshot, today: date, *, project_root: Path, environ: Mapping[str, str]) -> CancelResult` — never raises. Halt checks first; then collects: `market_closed`, `not_open` (id not in `snapshot.open_orders`), `not_buy`, `not_bot_order` (no `tb-` prefix), `not_stale` (submitted New York date `>= today`).

- [ ] **Step 1: Failing tests:** `test_exit_limit_is_one_percent_below_latest_rounded_down` (latest `400.019` → `396.01`); `test_exit_without_price_rejected`; `test_cancel_approved_for_stale_bot_buy`; one test per rejection code above; `test_cancel_kill_switch_halts`; `test_cancel_live_endpoint_halts`; `test_approved_cancel_cannot_be_forged`; `test_cancel_unexpected_snapshot_fails_closed`. Update existing exit tests for the new field.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `feat: validate stale-entry cancels and price time exits`

### Task 4: `executor.py`

**Files:** Create `bot/executor.py`, `tests/test_executor.py`.

**Interfaces — Consumes:** `ApprovedOrder`, `ApprovedExit`, `ApprovedCancel` (Task 3), `kill_switch_reason`, `is_paper_endpoint`. **Produces:**
- `class ExecutorError(Exception)`.
- `@dataclass(frozen=True) SubmittedOrder(id: str, client_order_id: str, symbol: str, side: str, qty: int, status: str)`.
- `Executor(trading, *, run_id: str, project_root: Path, environ: Mapping[str, str], sleep=time.sleep, poll_interval_s: float = 1.0, timeout_s: float = 30.0)` — raises `ExecutorError` if the endpoint is not paper. `Executor.from_env(environ, *, run_id, project_root) -> Executor` (missing key → `ExecutorError`).
- `submitted_ids: set[str]` — every order id this executor created.
- `submit_entry(order: ApprovedOrder) -> SubmittedOrder` — one `submit_order(LimitOrderRequest(...))` per Global Constraints.
- `cancel(c: ApprovedCancel) -> None` — `cancel_order_by_id(c.order_id)`.
- `close(x: ApprovedExit) -> SubmittedOrder` — list open orders for the symbol, `cancel_order_by_id` each (individual errors recorded, not raised), poll until the list is empty (elapsed time = number of polls × `poll_interval_s`), then `submit_order(LimitOrderRequest(side=SELL, qty=x.quantity, limit_price=x.limit_price, time_in_force=DAY))`. Still not empty at `timeout_s` → `ExecutorError("<SYM>: open orders not cancelled after 30s: <ids>")`, no sell.
- Every method: `type(arg) is <Approved type>` else `TypeError`; kill switch on → `ExecutorError("kill_switch: ...")`; Alpaca exceptions → `ExecutorError(f"{kind} <SYM>: {type(e).__name__}: {e}")`.

- [ ] **Step 1: Failing tests** with a `FakeTrading` that records calls in order: `test_rejects_plain_objects_and_lookalikes` (a `SimpleNamespace` with the same fields → `TypeError`, nothing sent); `test_refuses_live_endpoint`; `test_kill_switch_blocks_each_method` (file and env, parametrised over the three methods, no Alpaca call made); `test_entry_is_gtc_bracket_with_stop_order_leg` (asserts `order_class`, `time_in_force`, `take_profit.limit_price`, `stop_loss.stop_price`, `stop_loss.limit_price is None`, `client_order_id == "tb-<run12>-entry-MSFT"`); `test_entry_id_recorded_in_submitted_ids`; `test_close_cancels_confirms_then_sells` (call order: list → cancel × n → list (empty) → submit); `test_close_tolerates_cancel_error_for_order_already_gone`; `test_close_timeout_places_no_sell` (orders never clear; 30 polls; no `submit_order`); `test_close_sell_is_day_limit_full_qty`; `test_alpaca_error_becomes_executor_error`.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5: Commit** `feat: paper executor for bracket entries, cancels and time exits`

### Task 5: Security review gate

- [ ] **Step 1:** Run `/security-review` on the branch. Focus: only `executor.py` reaches `submit_order` / `cancel_order_by_id` (grep `bot/` to confirm); paper endpoint cannot be bypassed; approved objects cannot be forged or altered; no keys in logs or errors.
- [ ] **Step 2:** Fix findings test-first; show results; wait for user approval before Task 6.

### Task 6: Wire `main.py` and `--submit`

**Files:** Modify `bot/main.py`, `tests/test_main.py`, `CLAUDE.md`, `docs/superpowers/specs/2026-10-07-trading-bot-design.md`.

**Interfaces — Consumes:** everything above. **Produces:**
- `run_once(..., executor_factory: Callable[[str], Executor] | None = None)` — called with `run_id` after the paper check (stage `executor`); `None` → dry run.
- Record: `mode` is `"paper"` when an executor exists; new keys `stale_cancels: []`, `alerts: {}`, `tripwire: None`; `time_exits` entries `{symbol, quantity, limit_price, status, order_id}` with status `would_exit` / `submitted`; `stale_cancels` entries `{order_id, symbol, status}` with `would_cancel` / `cancelled`; `order.status` becomes `submitted` with `id` and `client_order_id` in paper mode.
- Stage order after `market_closed`: `housekeeping` (stale cancels → exits → re-snapshot if anything was sent; any rejection or `ExecutorError` → `done("error", "housekeeping", ...)`), `unprotected` (excluding symbols exited this run → `done("unprotected")`, exit 1, `alerts.unprotected_positions`), bars … budget unchanged, `agent` with `session_start = datetime.now(UTC)` taken just before the call, `tripwire` (`orders_submitted_since(session_start)` minus `executor.submitted_ids`; any left → write `KILL` in `project_root` with the reason, `tripwire.unexpected_orders`, `done("halted")`), `validation`, `submit`.
- `main()`: `--submit` passes `executor_factory=lambda run_id: Executor.from_env(environ, run_id=run_id, project_root=project_root)`; parser description drops "(dry run)".

- [ ] **Step 1: Failing tests** (extend `FakeBroker` with `sessions`, fill dates, `orders_submitted_since`, a re-snapshot hook; add `FakeExecutor` recording calls and ids): `test_dry_run_housekeeping_sends_nothing_and_logs_would`; `test_paper_run_submits_entry_and_logs_order_id`; `test_time_exit_runs_on_no_candidate_day`; `test_stale_entry_cancelled_before_candidates`; `test_resnapshot_after_housekeeping`; `test_exited_symbol_not_a_candidate_same_run`; `test_housekeeping_error_stops_before_agent`; `test_unprotected_position_stops_before_agent_exit_1`; `test_tripwire_creates_kill_and_halts`; `test_tripwire_ignores_orders_the_executor_placed`; `test_submit_error_logged_exit_1`; `test_submit_flag_builds_executor` (replaces `test_submit_flag_refused`). Give existing fixtures fill dates, sessions and a stop order per position so they stay on their old paths; add the new scenarios to `test_every_path_writes_exactly_one_record`.
- [ ] **Steps 2–4:** RED → implement → GREEN (full suite).
- [ ] **Step 5:** `CLAUDE.md` hard rules: replace "limit orders only" with "limit orders only for entries and exits the bot initiates; the protective stop leg is a stop order"; Commands: add `.venv/bin/python -m bot.main --submit   # one run that sends orders to the paper account`. Parent spec §4 and §7 check 6 get the same wording; §16 rows for bracket TIF and time exits say "Decided in step 6 spec". Run `.venv/bin/pytest`.
- [ ] **Step 6: Commit** `feat: connect --submit to the paper executor with housekeeping and tripwire`

### Task 7: Build step 6 gate

- [ ] **Step 1:** `/code-review`; fix findings test-first; show results.
- [ ] **Step 2:** User approval.
- [ ] **Step 3:** First `--submit` run during market hours, supervised by the user. Read the record and the Alpaca dashboard and confirm: the bracket's stop leg appears in the next run's open orders (held status); after a filled entry the position's `entry_fill_date` is right; a deliberately unfilled entry is cancelled by the next run, and what happens to its legs after a partial fill. Any mismatch → systematic-debugging, failing test, fix, before step 7.
