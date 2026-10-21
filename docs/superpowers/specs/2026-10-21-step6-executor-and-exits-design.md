# Step 6: Executor and Exits — Design Spec

Date: 2026-10-21
Status: Draft for review
Parent spec: `docs/superpowers/specs/2026-10-07-trading-bot-design.md` (§5, §7, §12, §15, §16)

## 1. Goal

`python -m bot.main --submit` places real orders on the Alpaca **paper** account: a bracket entry for an approved proposal, cancellation of stale entries, and time exits. Without `--submit` the run stays a dry run and logs what it would have done.

## 2. Decisions (settled with the user on 2026-10-21)

| # | Question (parent spec §16) | Decision |
|---|---|---|
| 1 | Bracket time-in-force | **`gtc`** for the entry and both legs, so a filled position is protected from the moment it fills. Each run cancels the bot's buy orders still open from an earlier US/Eastern date. |
| 2 | Time-exit order handling | **Cancel, confirm, sell.** Cancel every open order for the symbol, poll until Alpaca confirms none remain (1 s interval, 30 s timeout), then submit a `day` limit sell for the full quantity. No confirmation → no sell. |
| 3 | Position with no stop | **Alert and block new entries.** Log `alerts.unprotected_positions`, outcome `unprotected`, exit code 1; Claude is not called. Time exits still run. No automatic re-protection. |
| 4 | Stop leg order type | **Stop order** (market when triggered), not stop-limit, so a gap down cannot leave the position without a working stop. The hard rule is reworded to: *limit orders only for entries and exits the bot initiates; the protective stop leg is a stop order.* |

## 3. Run flow

Steps marked *new* are added in step 6. With `--submit` the record has `mode: "paper"`; without it every order action is logged as `would_cancel`, `would_exit` or `would_submit` and nothing is sent.

1. Kill switch → broker → paper check (unchanged).
2. Snapshot (extended): open orders carry `id`, `client_order_id`, `order_type`, `submitted_at`, `stop_price`; each position carries `entry_fill_date`; the snapshot carries `sessions` (recent trading dates from Alpaca's calendar). A position whose fill date cannot be found is an error and the run stops.
3. Market closed → stop (unchanged).
4. *Housekeeping*, in order:
   1. Stale entries: each open buy order with the `tb-` client-id prefix submitted before today (US/Eastern) → `validate_cancel` → `executor.cancel`.
   2. Time exits: `exits.due_for_exit` → `validate_exit` → `executor.close`.
   3. If any order action was sent, take a fresh snapshot.
5. *Unprotected check*: any held position not exited in step 4 with no open stop order covering its full quantity → alert, stop before Claude, exit 1.
6. Bars → candidates → capacity → budget (unchanged). Housekeeping has already run, so exits never depend on there being candidates.
7. *Claude with tripwire*: record `session_start` before the session; afterwards `broker.orders_submitted_since(session_start)` must be empty. Any order → create `KILL`, log `tripwire.unexpected_orders`, outcome `halted`.
8. `validate_proposal` → `executor.submit_entry` (bracket). The record logs order id and status.
9. One JSONL record (unchanged).

## 4. Components

| File | Change |
|---|---|
| `bot/models.py` | `OpenOrder` + `id`, `client_order_id`, `order_type`, `submitted_at`, `stop_price`. `Position` + `entry_fill_date`. `MarketSnapshot` + `sessions`. |
| `bot/broker.py` | Fills the new fields. Fill date = `filled_at` (US/Eastern date) of the most recent filled buy order for the symbol. `orders_submitted_since(t)`. Still read-only. |
| `bot/exits.py` (new, pure) | `due_for_exit(positions, sessions, today, n)`: days held = trading sessions after the fill date up to and including today; due when ≥ `n` (`time_exit_trading_days`). `stale_entries(open_orders, today)`. `unprotected(positions, open_orders)`: no open sell stop order with qty ≥ the held qty. |
| `bot/validator.py` | `ApprovedCancel(order_id, symbol)` and `validate_cancel` (halt checks, market open, order is open, side buy, `tb-` prefix, submitted before today). `ApprovedExit` gains `limit_price` = latest price × 0.99 rounded down to the cent; missing or non-positive price → rejected. All approved types are issued only by the validator. |
| `bot/executor.py` (new) | Owns its own `TradingClient(paper=True)`; refuses a non-paper endpoint. `submit_entry(ApprovedOrder)`, `cancel(ApprovedCancel)`, `close(ApprovedExit)`; each accepts only its own type and re-checks the kill switch and paper endpoint before acting. Client order id `tb-<run_id[:12]>-<kind>-<symbol>`. No retries. Failures raise `ExecutorError`. Poll interval, timeout and sleep are injectable for tests. |
| `bot/main.py` | `run_once(..., executor: Executor | None)`; `None` is dry run. `--submit` builds the executor. New stages: `housekeeping`, `unprotected`, `tripwire`, `submit`. |
| `CLAUDE.md`, parent spec | Reword the limit-order rule (decision 4); mark §16 items as decided here. |

Bracket entry request: limit buy, `time_in_force=gtc`, `order_class=bracket`, `take_profit.limit_price = ApprovedOrder.take_profit_price`, `stop_loss.stop_price = ApprovedOrder.stop_price` (no `limit_price` on the stop leg).

## 5. Error handling

Fail closed, as in parent spec §12.

- Any housekeeping failure (rejected exit, `ExecutorError`, cancel not confirmed) stops the run: no Claude, no entries, exit 1. Actions already taken are in the record.
- An exit sell that was submitted but has not filled is not an error; the next run sees the position still due, cancels the leftover sell and tries again.
- Entry submission failure: logged with stage `submit`, exit 1, not retried.
- Tripwire: `KILL` stays until the user removes it.

## 6. Testing

No network; a fake trading client records calls.

- `test_exits.py`: 9 vs 10 sessions; weekends and holidays counted from `sessions`; stale entries only for `tb-` buys from earlier dates; unprotected when the stop is missing or covers fewer shares.
- `test_validator.py`: `validate_cancel` pass/fail; exit limit price rounding; missing price rejects.
- `test_executor.py`: rejects plain and forged objects and a non-paper client; kill switch blocks each method; bracket fields (gtc, prices, client id, stop has no limit); `close` cancels → confirms → sells in that order; timeout → no sell; never sells while any order for the symbol remains open.
- `test_main.py`: housekeeping before candidates and on no-candidate days; dry run sends nothing and logs `would_*`; unprotected stops before the agent; tripwire creates `KILL`; submit path logs order ids; housekeeping error stops the run.
- `test_broker.py`: new fields; fill-date lookup; missing fill → error.

## 7. Build order and gates

1. Models, broker, `exits.py`, validator additions, `executor.py` — TDD.
2. `/security-review` before anything is wired to `--submit`.
3. Wire `--submit` in `main.py`; reword the rule in `CLAUDE.md` and the parent spec.
4. `/code-review`, user approval.
5. First `--submit` run supervised by the user, to verify on the real paper account:
   - a waiting stop leg appears in the open-orders query (otherwise the unprotected check gives false alarms);
   - leg behaviour after a partially filled entry is cancelled;
   - `filled_at` gives the entry date.
   Any surprise is fixed before step 7 schedules the bot.

Step 6 implementation starts only after step 5 is approved.
