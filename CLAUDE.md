# Trading Bot — Project Rules

## Project

An AI-in-the-loop swing-trading bot on an Alpaca **paper** account. Code finds trend-pullback candidates; Claude (via the Agent SDK, using the skills in `.claude/skills/`) reads the news and proposes at most one trade per run; `bot/validator.py` checks every proposal against hard limits; only `bot/executor.py` submits orders. Full design: `docs/superpowers/specs/2026-10-07-trading-bot-design.md`.

## Hard rules (never break)

- **Paper only.** The only allowed trading endpoint is `https://paper-api.alpaca.markets`. Never add a setting, flag or code path that reaches the live endpoint.
- **`bot/executor.py` is the only code that submits or cancels orders**, and only for `ApprovedOrder` / `ApprovedExit` objects created by `bot/validator.py`.
- **Limits are enforced in code** (`bot/validator.py` reading `config/limits.yaml`), never only in prompts or skills.
- **Hard-coded, never configurable:** paper endpoint, long-only, limit orders only, kill switch.
- **Kill switch:** if a `KILL` file exists in the project root or `KILL_SWITCH` is set to anything other than empty or `0`, do nothing and log why.
- **Fail closed:** any error or implausible data from Alpaca or Claude means log it and make no trade.
- **Secrets:** never commit `.env` or any key; never print, log or paste keys. `.env.example` holds names only.
- **Bot tool lockdown:** never add an Alpaca tool that places, replaces, cancels or closes orders or positions to the bot's allowed tools.
- **Alpaca MCP in Claude Code** is added with the default local scope only. No `--scope user`, no committed `.mcp.json`.
- **Skills live only in `.claude/skills/`** in this repo and are committed. Do not rely on account-level skills.

## Consistency

- When `config/limits.yaml` changes, update `.claude/skills/risk/SKILL.md` to match and flag every stale line.
- When `config/strategy.yaml` changes, update `.claude/skills/strategy/SKILL.md` the same way.
- `tests/test_skills_consistency.py` enforces both; never weaken it to make it pass.

## Workflow

- Superpowers flow: brainstorm → spec → plan → TDD. No production code without a failing test first.
- Run `.venv/bin/pytest` and read the output before claiming any step is done.
- Work in small steps, show diffs, and wait for user approval before the next build step.
- Run `/code-review` at the end of each build step. Run `/security-review` before connecting `executor.py`.

## Commands

Python 3.13.

```bash
python3 -m venv .venv                                          # create the virtual environment
.venv/bin/pip install -r requirements.txt -c constraints.txt   # install exact locked versions
source .venv/bin/activate                                      # activate it
.venv/bin/pytest                                               # run all tests
```

After changing `requirements.txt`, regenerate the lock: `.venv/bin/pip freeze > constraints.txt` (keep the two header comment lines).

Dry-run command: added in build step 5.
