---
name: strategy
description: The trading bot's trend-pullback swing strategy — how to evaluate the day's pullback candidates, read their recent news for thesis-breaking events, and decide whether to propose one long trade or no trade. Use this whenever you are analysing trade candidates, checking a ticker's news before a possible entry, judging whether a dip is a buying opportunity, or producing the bot's trade proposal, even if the request is just "anything worth buying today?". Use together with the risk skill (sizing and limits) and the journal skill (output format).
---

# Trend-pullback strategy

The idea: buy quality, liquid names that are in a long-term uptrend but have just dipped, as long as the dip is noise (market or sector moves) rather than a sign that something is wrong with the company. Code finds the setups; your job is the part code can't do — reading the news and judging whether the dip has a thesis-breaking cause.

<!-- config: strategy.yaml -->
- `sma_period`: 200 — length of the uptrend moving average, in daily bars
- `rsi_period`: 14 — RSI lookback (Wilder smoothing)
- `rsi_max`: 40 — RSI at or below this counts as a pullback
- `atr_period`: 14 — ATR lookback used for stops and targets
- `stop_atr_multiple`: 2.0 — stop = entry minus this many ATRs (set by code)
- `target_atr_multiple`: 3.0 — target = entry plus this many ATRs (set by code)
- `time_exit_trading_days`: 10 — positions are closed after this many trading days
- `news_window_hours`: 72 — how far back to read news for each candidate
- `min_bars`: 220 — minimum daily price history for a ticker to be considered
<!-- /config -->

## What code has already done

The candidate list you receive was computed from daily bars: every candidate closed above its `sma_period`-day simple moving average (uptrend) and has RSI over `rsi_period` days at or below `rsi_max` (pullback). The list includes each candidate's close, SMA, RSI and ATR.

- Trust these numbers. Don't recompute indicators from raw bars — that is slower, costs tokens, and the validator recomputes nothing from your arithmetic anyway.
- Only candidates on today's list can be traded. The validator rejects any other ticker, even a whitelisted one with a great story.
- Stops and targets are attached by code from ATR. You don't choose them.

## Reading the news

For each candidate, read news from the last `news_window_hours` hours using the Alpaca news tool. Read headlines and summaries; open the full story only when a headline is ambiguous.

**Thesis-breaking — reject the candidate:**
- Earnings miss or guidance cut
- Investigation, lawsuit, regulatory action, or a fine large enough to matter
- Accounting problems, restatement, auditor issues
- A pending merger or acquisition (as target, the price is pinned to the deal; as acquirer of a large deal, the story has changed)
- Product recall or safety problem
- Several analyst downgrades citing a fundamental reason
- Executive departure surrounded by controversy
- **Earnings ahead:** the news says the company reports earnings within the next `time_exit_trading_days` trading days. An earnings gap can jump straight past the stop, so the loss isn't bounded the way the strategy assumes. There's no earnings calendar here, so this rule only applies when the news mentions the date — don't guess.

**Noise — the dip is acceptable:**
- Broad market selloff (rates, macro data, geopolitics affecting everything)
- Sector rotation or a sector-wide move
- Analyst price-target trims that keep the rating
- No company news at all

**ETFs** (SPY, sector funds) have no company news. Reject only for a sector-specific structural shock — for example, new regulation hitting the whole sector — not for ordinary market moves.

**If you can't get the news** for a candidate (tool error, empty result that looks like a failure rather than a quiet stock), skip that candidate. A trade you couldn't check is not a trade you can justify.

## Choosing

1. Drop every candidate with a thesis-breaking event.
2. Among the rest, prefer the one whose dip is best explained by a market or sector move and whose own news is clean or positive.
3. Break remaining ties by the lowest RSI (the deepest pullback).
4. Propose at most one trade. If nothing survives, answer no trade — that is a good outcome, not a failure. The point of this experiment is to learn whether your judgment beats the plain rules, and passing on a doubtful setup is exactly the judgment being tested.

Before proposing, use the Alpaca tools to confirm the ticker isn't already held or on order. Size from the capacity numbers in the prompt, not from live account values — the validator checks against that same snapshot, so a size based on a newer number can be rejected. Follow the risk skill for sizing and limits, and format your answer with the journal skill.
