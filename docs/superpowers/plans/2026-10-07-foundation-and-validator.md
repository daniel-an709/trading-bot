# Foundation and Validator Implementation Plan (Build Steps 1–2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working project skeleton and a fully tested, network-free `validator.py` that approves or rejects trade proposals against the hard limits.

**Architecture:** Config is loaded from YAML into frozen pydantic models. Claude's raw text is parsed into a strict schema. The validator is a set of pure functions over that proposal, a code-fetched `MarketSnapshot`, today's candidates and the limits; it returns a result object and, only on approval, an `ApprovedOrder` that nothing else can construct.

**Tech Stack:** Python 3.13 (installed), venv, pydantic v2, PyYAML, python-dotenv, pytest. `alpaca-py` and `claude-agent-sdk` are installed now only to confirm Python 3.13 compatibility; they are not imported until step 5.

**Spec:** `docs/superpowers/specs/2026-10-07-trading-bot-design.md`

## Global Constraints

- Paper endpoint only: `https://paper-api.alpaca.markets`. Hard-coded, not configurable.
- Long-only, limit orders only. Hard-coded.
- Kill switch: `KILL` file in project root, or `KILL_SWITCH` env var set.
- Limits: `max_position_pct: 5`, `max_open_positions: 6`, `max_new_trades_per_day: 2`, `max_daily_loss_pct: 2`.
- Whitelist ETFs: `SPY, QQQ, IWM, XLK, XLF, XLV, XLE, XLI, XLY, XLP, XLU`. Stocks: `AAPL, MSFT, NVDA, AMZN, GOOGL, META, AVGO, JPM, V, LLY, XOM, COST, WMT, HD, UNH`.
- Money values are `decimal.Decimal`, never float. Prices rounded to cents with `ROUND_HALF_UP`.
- Tests never touch the network.
- `.env` is never committed. `.env.example` holds key names only, no values.
- Every task: failing test first, run it, implement, run tests, commit. Show the diff to the user.
- End of each build step: full test suite passing, `/code-review`, then wait for user approval.

## Review Focus

1. Claude wraps its JSON in a ```` ```json ```` fence or adds whitespace → accepted; any prose before or after the JSON → rejected as malformed. (Task 3)
2. Ticker sent lowercase or with spaces (`" msft "`) → normalised to `MSFT`, not rejected. (Task 3)
3. Hostile JSON: `NaN`/`Infinity`, duplicate keys, `true` as quantity, `"10"` as quantity, a top-level list, empty string, 50 KB of text → rejected as malformed, never an exception. (Task 3)
4. `KILL_SWITCH=true` or `KILL_SWITCH=yes` → treated as ON (anything except empty or `0` halts). (Task 4)
5. Snapshot missing a latest price for the ticker, or equity / last equity ≤ 0 → rejected with a reason, never a crash or a divide-by-zero. (Task 5)

---

## File Structure

| Path | Responsibility |
|---|---|
| `requirements.txt` | Pinned dependencies |
| `pytest.ini` | `pythonpath = .`, `testpaths = tests` |
| `.env.example` | Key names, empty values |
| `CLAUDE.md` | Hard rules for every Claude Code session |
| `config/limits.yaml` | Hard limits |
| `config/strategy.yaml` | Strategy parameters |
| `bot/__init__.py` | Empty |
| `bot/config.py` | `Limits`, `StrategyParams`, loaders |
| `bot/schema.py` | `TradeProposal`, `NoTrade`, `parse_proposal`, `MalformedProposal` |
| `bot/models.py` | `Position`, `OpenOrder`, `MarketSnapshot`, `Candidate` |
| `bot/safety.py` | `PAPER_ENDPOINT`, `kill_switch_reason`, `is_paper_endpoint` |
| `bot/validator.py` | `validate_proposal`, `validate_exit`, `ValidationResult`, `ExitResult`, `ApprovedOrder`, `ApprovedExit` |
| `tests/test_repo_hygiene.py`, `tests/test_config.py`, `tests/test_schema.py`, `tests/test_safety.py`, `tests/test_validator.py`, `tests/test_validate_exit.py` | Tests |

---

## BUILD STEP 1

### Task 1: Project skeleton, dependencies, CLAUDE.md

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `.env.example`, `CLAUDE.md`, `bot/__init__.py`
- Test: `tests/test_repo_hygiene.py`

**Interfaces:**
- Produces: `.venv/` with dependencies; `pytest` runnable from the project root.

- [ ] **Step 1: Create the venv and install unpinned dependencies**

Run: `python3 -m venv .venv && .venv/bin/pip install --upgrade pip && .venv/bin/pip install pydantic PyYAML python-dotenv pytest alpaca-py claude-agent-sdk`
Expected: installs without errors on Python 3.13. If `alpaca-py` or `claude-agent-sdk` fails, STOP and report to the user (spec §16 deferred item).

- [ ] **Step 2: Write `requirements.txt` pinned to the installed versions**

Six top-level packages only, each `name==<version from .venv/bin/pip show name>`.

- [ ] **Step 3: Write `pytest.ini`, `.env.example`, empty `bot/__init__.py`**

`.env.example` content exactly:
```
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ANTHROPIC_API_KEY=
KILL_SWITCH=0
```

- [ ] **Step 4: Write the failing hygiene test**

```python
# tests/test_repo_hygiene.py
def test_gitignore_covers_secrets_and_runtime():
    lines = set((ROOT / ".gitignore").read_text().split())
    assert {".env", "logs/", "KILL", ".venv/"} <= lines

def test_env_file_is_not_tracked():
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True).stdout.split()
    assert ".env" not in tracked

def test_env_example_has_no_values():
    for line in (ROOT / ".env.example").read_text().splitlines():
        key, _, value = line.partition("=")
        assert value in ("", "0"), f"{key} has a value in .env.example"

def test_claude_md_states_hard_rules():
    text = (ROOT / "CLAUDE.md").read_text()
    for phrase in ["paper", "KILL", "executor.py", "validator.py", "limits.yaml", "risk/SKILL.md", ".env"]:
        assert phrase in text
```

- [ ] **Step 5: Run it to verify it fails**

Run: `.venv/bin/pytest tests/test_repo_hygiene.py -v`
Expected: `test_claude_md_states_hard_rules` FAILS (no CLAUDE.md); others pass.

- [ ] **Step 6: Write `CLAUDE.md`**

Sections, in this order, each as short bullet rules:
- **Project:** one-paragraph summary pointing to the spec path.
- **Hard rules (never break):** paper endpoint only; `executor.py` is the only code that submits or cancels orders and only for validator-approved objects; limits are enforced in `validator.py`, not prompts; kill switch (`KILL` file or `KILL_SWITCH`) means do nothing and log why; never commit `.env` or keys, never print keys; never add order-placing Alpaca tools to the bot's allow list; Alpaca MCP is added with local scope only, no committed `.mcp.json`.
- **Consistency:** when `config/limits.yaml` or `config/strategy.yaml` changes, update `.claude/skills/risk/SKILL.md` / `strategy/SKILL.md` and flag stale lines; the consistency test enforces it.
- **Workflow:** superpowers flow (brainstorm → plan → TDD); run `.venv/bin/pytest` before claiming done; small steps, show diffs, wait for approval; `/code-review` at the end of each build step; `/security-review` before connecting the executor.
- **Commands:** activate venv, run tests, dry-run command (filled in at step 5).

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/pytest -v`
Expected: 4 passed.

- [ ] **Step 8: Commit**

```bash
git add requirements.txt pytest.ini .env.example CLAUDE.md bot/__init__.py tests/test_repo_hygiene.py
git commit -m "chore: project skeleton, pinned deps, CLAUDE.md hard rules"
```

- [ ] **Step 9: Build step 1 gate** — run `/code-review`, show results, wait for user approval.

---

## BUILD STEP 2

### Task 2: Config files and loader

**Files:**
- Create: `config/limits.yaml`, `config/strategy.yaml`, `bot/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `class Whitelist(BaseModel)`: `etfs: tuple[str, ...]`, `stocks: tuple[str, ...]`
  - `class Limits(BaseModel)` (frozen, `extra="forbid"`): `max_position_pct: float` (0 < x ≤ 100), `max_open_positions: int` (≥ 1), `max_new_trades_per_day: int` (≥ 1), `max_daily_loss_pct: float` (0 < x ≤ 100), `whitelist: Whitelist`; property `allowed_tickers -> frozenset[str]`
  - `class StrategyParams(BaseModel)` (frozen, `extra="forbid"`): `sma_period: int`, `rsi_period: int`, `rsi_max: float`, `atr_period: int`, `stop_atr_multiple: float`, `target_atr_multiple: float`, `time_exit_trading_days: int`, `news_window_hours: int`, `min_bars: int` — all > 0
  - `PROJECT_ROOT: Path`, `load_limits(path: Path = PROJECT_ROOT / "config/limits.yaml") -> Limits`, `load_strategy(path: Path = PROJECT_ROOT / "config/strategy.yaml") -> StrategyParams`

- [ ] **Step 1: Write the YAML files**

`config/limits.yaml`: exactly the block in spec §4 (values in Global Constraints).
`config/strategy.yaml`:
```yaml
sma_period: 200
rsi_period: 14
rsi_max: 40
atr_period: 14
stop_atr_multiple: 2.0
target_atr_multiple: 3.0
time_exit_trading_days: 10
news_window_hours: 72
min_bars: 220
```

- [ ] **Step 2: Write the failing tests**

```python
def test_load_limits_real_file():
    limits = load_limits()
    assert (limits.max_position_pct, limits.max_open_positions,
            limits.max_new_trades_per_day, limits.max_daily_loss_pct) == (5, 6, 2, 2)
    assert len(limits.allowed_tickers) == 26 and "MSFT" in limits.allowed_tickers

def test_load_strategy_real_file():
    s = load_strategy()
    assert (s.sma_period, s.rsi_max, s.stop_atr_multiple, s.target_atr_multiple, s.time_exit_trading_days) == (200, 40, 2.0, 3.0, 10)

@pytest.mark.parametrize("mutation", [
    {"max_position_pct": -1}, {"max_position_pct": 0}, {"max_open_positions": 0},
    {"surprise_key": 1}, {"max_daily_loss_pct": None},
])
def test_bad_limits_rejected(tmp_path, mutation): ...  # write mutated copy, expect pydantic.ValidationError

def test_missing_key_rejected(tmp_path): ...            # drop max_new_trades_per_day → ValidationError
def test_lowercase_or_duplicate_ticker_rejected(tmp_path): ...  # "msft" → error; MSFT in etfs and stocks → error
def test_limits_are_frozen():
    with pytest.raises(pydantic.ValidationError):
        load_limits().max_position_pct = 50
```

- [ ] **Step 3: Run to verify failure**

Run: `.venv/bin/pytest tests/test_config.py -v` — Expected: FAIL, `ModuleNotFoundError: bot.config`.

- [ ] **Step 4: Implement `bot/config.py`**

Use `yaml.safe_load`; whitelist validator requires each ticker to match `^[A-Z]{1,5}$` and no ticker in both lists.

- [ ] **Step 5: Run to verify pass**

Run: `.venv/bin/pytest tests/test_config.py -v` — Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add config/ bot/config.py tests/test_config.py
git commit -m "feat: limits and strategy config with strict loader"
```

### Task 3: Proposal schema and parser

**Files:**
- Create: `bot/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `class TradeProposal(BaseModel)` (frozen, `extra="forbid"`): `decision: Literal["trade"]`, `ticker: str` (stripped, uppercased, then must match `^[A-Z]{1,5}$`), `side: Literal["buy", "sell"]`, `quantity: StrictInt` (≥ 1), `order_type: Literal["market", "limit"]`, `limit_price: Decimal | None` (> 0, ≤ 2 decimal places; required when `order_type == "limit"`, must be null for market), `reasoning: str` (non-blank), `confidence: Decimal` (0–1)
  - `quantity`, `limit_price` and `confidence` must arrive as JSON numbers: a field-level `mode="before"` validator rejects `str` and `bool` values (JSON floats arrive as `Decimal` because of `parse_float=Decimal`, ints as `int`).
  - `class NoTrade(BaseModel)` (frozen, `extra="forbid"`): `decision: Literal["no_trade"]`, `reasoning: str` (non-blank)
  - `class MalformedProposal(ValueError)`
  - `parse_proposal(raw: str) -> TradeProposal | NoTrade` — raises only `MalformedProposal`
  - `MAX_RAW_CHARS = 20_000`

`side` and `order_type` allow `sell` / `market` in the schema so the validator can reject them with a clear reason (spec §7 check 6) rather than "malformed".

- [ ] **Step 1: Write the failing tests**

```python
GOOD = '{"decision":"trade","ticker":"MSFT","side":"buy","quantity":12,"order_type":"limit","limit_price":400.00,"reasoning":"r","confidence":0.7}'

def test_parses_trade():
    p = parse_proposal(GOOD)
    assert isinstance(p, TradeProposal) and p.quantity == 12 and p.limit_price == Decimal("400.00")

def test_parses_no_trade():
    assert isinstance(parse_proposal('{"decision":"no_trade","reasoning":"nothing clean"}'), NoTrade)

def test_accepts_json_fence_and_whitespace():
    assert parse_proposal(f"\n```json\n{GOOD}\n```\n").ticker == "MSFT"

def test_normalises_ticker():
    assert parse_proposal(GOOD.replace('"MSFT"', '" msft "')).ticker == "MSFT"

@pytest.mark.parametrize("raw", [
    "", "null", "[]", "{}", "not json",
    "Here is my proposal: " + GOOD,                          # prose before
    GOOD + " hope that helps",                               # prose after
    GOOD.replace("12", "true"), GOOD.replace("12", '"12"'), GOOD.replace("12", "12.5"),
    GOOD.replace("12", "0"), GOOD.replace("0.7", "1.5"), GOOD.replace("0.7", '"high"'),
    GOOD.replace("400.00", "NaN"), GOOD.replace("400.00", "Infinity"), GOOD.replace("400.00", "-1"),
    GOOD.replace("400.00", "400.001"), GOOD.replace('"r"', '"   "'),
    GOOD.replace('"limit_price":400.00,', ''),               # limit without price
    GOOD.replace("}", ',"extra":1}'),                        # extra field
    GOOD.replace('"ticker":"MSFT"', '"ticker":"MSFT","ticker":"AAPL"'),  # duplicate key
    GOOD.replace('"limit"', '"stop"'), GOOD.replace('"MSFT"', '"MSFT1234"'),
    '{"decision":"maybe","reasoning":"r"}',
    "x" * 50_000,
])
def test_malformed_rejected(raw):
    with pytest.raises(MalformedProposal):
        parse_proposal(raw)
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_schema.py -v` → FAIL, module missing.

- [ ] **Step 3: Implement `bot/schema.py`**

Parse with `json.loads(text, parse_float=Decimal, parse_constant=<raise>, object_pairs_hook=<raise on duplicate keys>)`. Strip whitespace, then strip one surrounding ```` ```json … ``` ```` (or bare ```` ``` ````) fence; anything else around the object fails. Dispatch on `decision`. Wrap every `json`/pydantic error in `MalformedProposal` with the original message.

- [ ] **Step 4: Run to verify pass** — all tests pass.

- [ ] **Step 5: Commit**

```bash
git add bot/schema.py tests/test_schema.py
git commit -m "feat: strict proposal schema and parser"
```

### Task 4: Shared models and safety checks

**Files:**
- Create: `bot/models.py`, `bot/safety.py`
- Test: `tests/test_safety.py`

**Interfaces:**
- Produces, in `bot/models.py` (all `@dataclass(frozen=True)`):
  - `Position(symbol: str, qty: int, avg_entry_price: Decimal)`
  - `OpenOrder(symbol: str, side: Literal["buy", "sell"], qty: int)`
  - `Candidate(ticker: str, close: Decimal, sma: Decimal, rsi: Decimal, atr: Decimal)`
  - `MarketSnapshot(market_open: bool, equity: Decimal, last_equity: Decimal, buying_power: Decimal, positions: tuple[Position, ...], open_orders: tuple[OpenOrder, ...], buys_submitted_today: int, latest_prices: Mapping[str, Decimal], trading_endpoint: str)`
- Produces, in `bot/safety.py`:
  - `PAPER_ENDPOINT = "https://paper-api.alpaca.markets"`
  - `kill_switch_reason(project_root: Path, environ: Mapping[str, str]) -> str | None` — `"KILL file present"` if `project_root / "KILL"` exists; `"KILL_SWITCH=<value>"` if the env value stripped is anything other than `""` or `"0"`; else `None`
  - `is_paper_endpoint(url: str) -> bool` — exact match after stripping one trailing `/`

`models.py` is plain data, no tests of its own; it is exercised through Tasks 5–6.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_kill(tmp_path): assert kill_switch_reason(tmp_path, {}) is None
def test_kill_file(tmp_path):
    (tmp_path / "KILL").touch(); assert "KILL file" in kill_switch_reason(tmp_path, {})

@pytest.mark.parametrize("value,on", [("1", True), ("true", True), ("yes", True), (" 1 ", True), ("0", False), ("", False)])
def test_kill_env(tmp_path, value, on):
    assert (kill_switch_reason(tmp_path, {"KILL_SWITCH": value}) is not None) == on

@pytest.mark.parametrize("url,ok", [
    ("https://paper-api.alpaca.markets", True), ("https://paper-api.alpaca.markets/", True),
    ("https://api.alpaca.markets", False), ("http://paper-api.alpaca.markets", False),
    ("https://paper-api.alpaca.markets.evil.com", False), ("", False),
])
def test_paper_endpoint(url, ok): assert is_paper_endpoint(url) == ok
```

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_safety.py -v` → FAIL.
- [ ] **Step 3: Implement `bot/models.py` and `bot/safety.py`.**
- [ ] **Step 4: Run to verify pass** — all pass.
- [ ] **Step 5: Commit**

```bash
git add bot/models.py bot/safety.py tests/test_safety.py
git commit -m "feat: snapshot models, kill switch and paper endpoint checks"
```

### Task 5: `validate_proposal`

**Files:**
- Create: `bot/validator.py`
- Test: `tests/test_validator.py`

**Interfaces:**
- Consumes: `Limits`, `StrategyParams` (Task 2); `parse_proposal`, `TradeProposal`, `NoTrade`, `MalformedProposal` (Task 3); `MarketSnapshot`, `Candidate` (Task 4); `kill_switch_reason`, `is_paper_endpoint` (Task 4).
- Produces:
  - `@dataclass(frozen=True) class ApprovedOrder`: `ticker: str`, `quantity: int`, `limit_price: Decimal`, `stop_price: Decimal`, `take_profit_price: Decimal`. Constructing it outside `validator.py` raises `PermissionError` (module-private sentinel passed as a hidden init field and checked in `__post_init__`).
  - `@dataclass(frozen=True) class ValidationResult`: `status: Literal["approved", "rejected", "no_trade", "halted"]`, `reasons: tuple[str, ...]`, `proposal: TradeProposal | NoTrade | None`, `order: ApprovedOrder | None`; property `approved -> bool`.
  - `validate_proposal(raw: str, snapshot: MarketSnapshot, candidates: Sequence[Candidate], limits: Limits, strategy: StrategyParams, *, project_root: Path, environ: Mapping[str, str]) -> ValidationResult` — never raises.

Each reason string is `"<code>: <detail>"`. Codes, in check order (spec §7):

| Check | Code | Status |
|---|---|---|
| 1 | `kill_switch` | halted |
| 2 | `not_paper` | halted |
| 3 | `malformed` | rejected (stops here) |
| 4 | — | `no_trade`, reasons `()` |
| — | `bad_account_data` (equity, last_equity ≤ 0) | rejected (stops here) |
| 5 | `market_closed` | rejected |
| 6 | `not_buy`, `not_limit` | rejected |
| 7 | `not_whitelisted`, `already_held`, `open_order_exists` | rejected |
| 8 | `not_candidate` | rejected |
| — | `no_price` (no latest price for ticker) | rejected |
| 9 | `limit_out_of_band` (inclusive `0.90 × latest ≤ limit ≤ 1.01 × latest`) | rejected |
| 10 | `position_too_large` (inclusive `qty × limit ≤ max_position_pct% × equity`) | rejected |
| 11 | `too_many_positions` (`len(positions) + open buy orders ≥ max_open_positions`) | rejected |
| 12 | `daily_trade_cap` (`buys_submitted_today ≥ max_new_trades_per_day`) | rejected |
| 13 | `daily_loss_halt` (`(equity − last_equity) / last_equity ≤ −max_daily_loss_pct%`) | rejected |
| 14 | `insufficient_buying_power` (`qty × limit > buying_power`) | rejected |
| — | `bad_stop` (computed stop ≤ 0) | rejected |

Checks 5–14 and `bad_stop` all run and every failing code is collected. Checks that need a price or a candidate are skipped when that input is missing (the `no_price` / `not_candidate` reason already covers it). On approval: `stop = limit − stop_atr_multiple × candidate.atr`, `target = limit + target_atr_multiple × candidate.atr`, both quantized to `0.01` with `ROUND_HALF_UP`.

- [ ] **Step 1: Write the failing tests**

Fixtures: `limits = load_limits()`, `strategy = load_strategy()`; `snapshot()` factory with defaults `market_open=True, equity=last_equity=Decimal("100000"), buying_power=Decimal("200000"), positions=(), open_orders=(), buys_submitted_today=0, latest_prices={"MSFT": Decimal("400"), "AAPL": Decimal("200")}, trading_endpoint=PAPER_ENDPOINT` accepting overrides; `candidates = [Candidate("MSFT", Decimal("400"), Decimal("380"), Decimal("35"), Decimal("8"))]`; `proposal(**overrides)` returning JSON for MSFT buy 12 limit 400.00; helper `codes(result) -> set[str]` (text before `:`); `validate(raw, snap=None, cands=None, environ=None)` wrapping `validate_proposal` with `project_root=tmp_path`.

```python
def test_good_proposal_approved():
    r = validate(proposal())
    assert r.approved and r.order.stop_price == Decimal("384.00") and r.order.take_profit_price == Decimal("424.00")

def test_no_trade(): assert validate('{"decision":"no_trade","reasoning":"r"}').status == "no_trade"

# Spec §13 deliberately bad proposals
def test_oversized():          assert "position_too_large" in codes(validate(proposal(quantity=13)))     # 5200 > 5000
def test_not_whitelisted():    assert "not_whitelisted" in codes(validate(proposal(ticker="GME")))
def test_daily_trade_cap():    assert "daily_trade_cap" in codes(validate(proposal(), snapshot(buys_submitted_today=2)))
def test_malformed_json():     r = validate("{oops"); assert r.status == "rejected" and codes(r) == {"malformed"}
def test_kill_file(tmp_path):  (tmp_path / "KILL").touch(); assert validate(proposal()).status == "halted"
def test_kill_env():           assert validate(proposal(), environ={"KILL_SWITCH": "1"}).status == "halted"
def test_live_endpoint():      assert codes(validate(proposal(), snapshot(trading_endpoint="https://api.alpaca.markets"))) == {"not_paper"}
def test_not_candidate():      assert "not_candidate" in codes(validate(proposal(ticker="AAPL", limit_price=200, quantity=10)))
def test_chasing_limit():      assert "limit_out_of_band" in codes(validate(proposal(limit_price=404.01)))
def test_position_cap():       # 5 positions + 1 open buy = 6
def test_daily_loss_halt():    assert "daily_loss_halt" in codes(validate(proposal(), snapshot(equity=Decimal("98000"))))

# Other checks
def test_market_closed(), test_sell_rejected(), test_market_order_rejected(), test_already_held(),
    test_open_order_exists(), test_insufficient_buying_power(), test_no_price(), test_bad_stop()  # atr=300 → stop < 0
def test_zero_last_equity_rejected_not_crash(): assert codes(validate(proposal(), snapshot(last_equity=Decimal("0")))) == {"bad_account_data"}

# Boundaries (inclusive limits pass, one cent over fails)
def test_size_exactly_5pct_ok():          # quantity=10, limit=400, equity=last_equity=80000 → 4000 == 4000 → approved
def test_limit_exactly_101pct_ok():       # limit 404.00 → approved
def test_daily_loss_exactly_2pct_halts(): # equity 98000 vs 100000 → daily_loss_halt
def test_daily_loss_just_under_ok():      # equity 98000.01 → approved

def test_collects_all_reasons():
    r = validate(proposal(ticker="GME", quantity=100), snapshot(buys_submitted_today=2, latest_prices={"GME": Decimal("400")}))
    assert {"not_whitelisted", "not_candidate", "position_too_large", "daily_trade_cap"} <= codes(r)

def test_approved_order_cannot_be_forged():
    with pytest.raises(PermissionError):
        ApprovedOrder("MSFT", 1, Decimal("1"), Decimal("1"), Decimal("1"))
```

Test bodies marked with comments are written out by the implementer using the fixture factories; each asserts the named code is in `codes(result)` (or `approved` for the boundary-pass tests).

- [ ] **Step 2: Run to verify failure** — `.venv/bin/pytest tests/test_validator.py -v` → FAIL, module missing.
- [ ] **Step 3: Implement `validate_proposal` and its types in `bot/validator.py`.** Convert limit percentages with `Decimal(str(x)) / 100`.
- [ ] **Step 4: Run to verify pass** — all pass.
- [ ] **Step 5: Commit**

```bash
git add bot/validator.py tests/test_validator.py
git commit -m "feat: validate_proposal enforcing hard limits"
```

### Task 6: `validate_exit`

**Files:**
- Modify: `bot/validator.py`
- Test: `tests/test_validate_exit.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class ApprovedExit`: `symbol: str`, `quantity: int`; same forge protection as `ApprovedOrder`.
  - `@dataclass(frozen=True) class ExitResult`: `status: Literal["approved", "rejected", "halted"]`, `reasons: tuple[str, ...]`, `exit: ApprovedExit | None`; property `approved -> bool`.
  - `validate_exit(symbol: str, snapshot: MarketSnapshot, *, project_root: Path, environ: Mapping[str, str]) -> ExitResult` — checks `kill_switch` (halted), `not_paper` (halted), `market_closed`, `not_held` (no position or qty ≤ 0); approves a sell of the full held quantity.

- [ ] **Step 1: Write the failing tests**

```python
def test_exit_approved_full_quantity():  # position MSFT qty 12 → exit.quantity == 12
def test_exit_not_held():                # codes == {"not_held"}
def test_exit_kill_switch_halts(tmp_path)
def test_exit_live_endpoint_halts()
def test_exit_market_closed()
def test_approved_exit_cannot_be_forged()
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full suite** — `.venv/bin/pytest -v` → all pass.
- [ ] **Step 5: Commit**

```bash
git add bot/validator.py tests/test_validate_exit.py
git commit -m "feat: validate_exit for time-based position closes"
```

- [ ] **Step 6: Build step 2 gate** — run `/code-review`, show full test output and review results, wait for user approval.

---

## Roadmap: build steps 3–7 (each gets its own plan when reached)

| Step | Delivers | Decided at the start of that step |
|---|---|---|
| 3 | `.claude/skills/{strategy,risk,journal}/SKILL.md` drafted with you using skill-creator; `tests/test_skills_consistency.py` | Skill wording, with you |
| 4 | `uv` installed; you run `claude mcp add` (local scope); `/mcp` verified; skills tried interactively on the paper account | Alpaca MCP paper-mode setting name |
| 5 | `broker.py`, `signals.py`, `runner.py`, `journal.py`, `main.py` (dry-run only) | Read-only tool list (your approval); SDK option names, auth and pricing via claude-api skill; model and per-run cap |
| 6 | `/security-review`; `executor.py`, `exits.py`, tripwire; `--submit` to paper | Bracket leg time-in-force behaviour |
| 7 | Scheduling; `scripts/benchmark.py`, `scripts/backtest.py` | Local cron vs. cloud |
