---
name: journal
description: Output format for the trading bot's decisions — the exact JSON proposal (trade or no trade) and how to write its reasoning and confidence so every run can be reviewed later. Use this whenever you produce the bot's final trade proposal or no-trade answer, write up why a trade was or wasn't taken, or are asked to explain a decision in the bot's logs.
---

# Decision journal

Every run is logged and later compared against SPY and against the plain rules without you. That comparison is only useful if each decision explains itself the same way, so a reader months later can see what you saw and why you chose. Write for that reader.

## The answer

When the bot asks for your final answer, reply with **only one JSON object** — no text before or after it. A ```json code fence around it is fine. Anything else is rejected as malformed and the run is wasted.

**Trade:**
```json
{"decision": "trade", "ticker": "MSFT", "side": "buy", "quantity": 12,
 "order_type": "limit", "limit_price": 398.50,
 "reasoning": "Setup: ...\nNews: ...\nThesis check: ...\nChoice: ...\nInvalidation: ...",
 "confidence": 0.6}
```

**No trade:**
```json
{"decision": "no_trade", "reasoning": "Skipped: ...\nSummary: ..."}
```

Field rules: `ticker` uppercase; `side` always `"buy"`; `quantity` a whole number; `order_type` always `"limit"`; `limit_price` a number with at most two decimals (not a string); `confidence` a number from 0 to 1. No other fields.

In an interactive Claude Code session, show the same JSON and then explain it in plain words below — the strict JSON-only rule is for the bot.

## Reasoning for a trade

Five labelled lines, in this order, separated by `\n`:

- **Setup:** the candidate's close, SMA, RSI and ATR from the candidate list.
- **News:** the headlines that matter, each with its date and source. Say so if there was no company news.
- **Thesis check:** why nothing breaks the thesis — name the categories you checked (earnings/guidance, legal/regulatory, deal, upcoming earnings).
- **Choice:** why this candidate over the others on the list.
- **Invalidation:** what would show the trade was wrong (beyond hitting the stop).

Keep it under about 1,200 characters. Facts first, judgment second; don't pad.

## Reasoning for no trade

- **Skipped:** one line per candidate — ticker, then the specific reason (headline and date, or "news unavailable", or "no capacity").
- **Summary:** one sentence on the overall picture.

## Confidence

Start at 0.5 and move it only for reasons you can name in the reasoning: a clearly market-wide dip and clean company news push it up; thin or mixed news pushes it down. Most trades should land between 0.4 and 0.75. Confidence is logged, not used to filter trades — later analysis will check whether higher-confidence trades actually did better, so an honest number is worth more than a high one.

## Example

```json
{"decision": "trade", "ticker": "MSFT", "side": "buy", "quantity": 12, "order_type": "limit", "limit_price": 398.50,
 "reasoning": "Setup: close 401.20, SMA 380.10, RSI 35.2, ATR 8.10.\nNews: 2026-10-06 Reuters 'Tech shares slide as Treasury yields jump'; no MSFT-specific headlines in window.\nThesis check: no earnings/guidance, legal, deal or upcoming-earnings news; dip is rate-driven and market-wide.\nChoice: cleaner news than XLE (OPEC supply headline) and deeper RSI than AAPL.\nInvalidation: company-specific negative news, or the tech selloff turning into a break below the SMA.",
 "confidence": 0.6}
```
