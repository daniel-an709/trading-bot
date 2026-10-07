---
name: risk
description: Position sizing and hard risk limits for the trading bot — how many shares to propose, at what limit price, and which proposals the validator will reject. Use this whenever you size a trade, pick a limit price, check whether the account has room for another position, or are about to produce the bot's trade proposal; also use it when someone asks what the bot's limits are or why a proposal was rejected.
---

# Risk and sizing

These limits are enforced in code by `bot/validator.py`, which reads `config/limits.yaml`. Following them here doesn't make the bot safe — the validator does — but a proposal that breaks a limit is simply rejected, which wastes the run. Size correctly and the trade can actually happen.

<!-- config: limits.yaml -->
- `max_position_pct`: 5 — largest new position, as a percentage of account equity
- `max_open_positions`: 6 — positions held plus pending buy orders
- `max_new_trades_per_day`: 2 — new entries per US/Eastern trading day
- `max_daily_loss_pct`: 2 — at this loss versus previous-close equity, no new entries today
- `whitelist.etfs`: SPY, QQQ, IWM, XLK, XLF, XLV, XLE, XLI, XLY, XLP, XLU
- `whitelist.stocks`: AAPL, MSFT, NVDA, AMZN, GOOGL, META, AVGO, JPM, V, LLY, XOM, COST, WMT, HD, UNH
<!-- /config -->

## Fixed rules (hard-coded, not configurable)

- Paper trading only.
- Long only: every proposal is a buy. No selling, no shorting.
- Limit orders only. No market orders.
- If the kill switch is on, nothing trades.

## Sizing

The prompt gives you today's remaining capacity: the maximum position size in dollars, trades left today, open slots, and buying power. Use those numbers — they come from the account, computed by code.

1. **Limit price:** the lower of the previous close and the latest price, rounded down to whole cents. This buys the dip rather than chasing a bounce. The validator rejects a limit more than one percent above the latest price or more than ten percent below it. If the previous close is that far below the latest price, the stock has already rallied away from the setup — answer no trade rather than chasing it. A limit below the latest price may not fill; an unfilled order still uses a slot and a trade for the day, which is an accepted cost of not chasing.
2. **Quantity:** the largest whole number of shares whose cost (quantity times limit price) fits within both the maximum position size in dollars and the buying power. Round down, never up.
3. If that quantity is less than one share, answer no trade.

## Answer no trade instead when

- Trades left today is zero, or there are no open slots.
- The account is at or past the daily loss limit.
- The candidate is already held or already has an open order.

## Avoid

- Proposing anything not on today's candidate list or not whitelisted.
- Adding to a position you already hold — one position per ticker.
- Rounding a quantity up to "use" the full allowance.
- Treating these limits as targets. They are ceilings; a smaller size is always allowed.
