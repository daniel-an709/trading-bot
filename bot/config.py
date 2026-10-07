"""Load and validate config/limits.yaml and config/strategy.yaml."""

import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TICKER_PATTERN = re.compile(r"[A-Z]{1,5}")  # use with fullmatch
STRICT_CONFIG = ConfigDict(frozen=True, extra="forbid")

PositiveInt = Annotated[StrictInt, Field(ge=1)]
Percent = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0, le=100)]
PositiveFloat = Annotated[float, Field(strict=True, allow_inf_nan=False, gt=0)]


class Whitelist(BaseModel):
    model_config = STRICT_CONFIG

    etfs: tuple[str, ...]
    stocks: tuple[str, ...]

    @field_validator("etfs", "stocks")
    @classmethod
    def _tickers_well_formed(cls, tickers: tuple[str, ...]) -> tuple[str, ...]:
        for t in tickers:
            if not TICKER_PATTERN.fullmatch(t):
                raise ValueError(f"ticker {t!r} must be 1-5 uppercase letters")
        if len(set(tickers)) != len(tickers):
            raise ValueError("duplicate ticker in list")
        return tickers

    @model_validator(mode="after")
    def _no_overlap(self) -> "Whitelist":
        overlap = set(self.etfs) & set(self.stocks)
        if overlap:
            raise ValueError(f"tickers in both etfs and stocks: {sorted(overlap)}")
        return self


class Limits(BaseModel):
    model_config = STRICT_CONFIG

    max_position_pct: Percent
    max_open_positions: PositiveInt
    max_new_trades_per_day: PositiveInt
    max_daily_loss_pct: Percent
    whitelist: Whitelist

    @property
    def allowed_tickers(self) -> frozenset[str]:
        return frozenset(self.whitelist.etfs) | frozenset(self.whitelist.stocks)


class StrategyParams(BaseModel):
    model_config = STRICT_CONFIG

    sma_period: PositiveInt
    rsi_period: PositiveInt
    rsi_max: Percent
    atr_period: PositiveInt
    stop_atr_multiple: PositiveFloat
    target_atr_multiple: PositiveFloat
    time_exit_trading_days: PositiveInt
    news_window_hours: PositiveInt
    min_bars: PositiveInt

    @model_validator(mode="after")
    def _enough_bars(self) -> "StrategyParams":
        longest = max(self.sma_period, self.rsi_period, self.atr_period)
        if self.min_bars <= longest:
            raise ValueError(f"min_bars ({self.min_bars}) must exceed the longest period ({longest})")
        return self


class RunnerParams(BaseModel):
    model_config = STRICT_CONFIG

    model: Annotated[str, Field(min_length=1)]
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_turns: PositiveInt
    max_budget_usd_per_run: PositiveFloat
    monthly_budget_usd: PositiveFloat
    data_feed: Literal["iex", "sip", "delayed_sip"]

    @model_validator(mode="after")
    def _per_run_within_month(self) -> "RunnerParams":
        if self.max_budget_usd_per_run > self.monthly_budget_usd:
            raise ValueError("max_budget_usd_per_run must not exceed monthly_budget_usd")
        return self


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses duplicate keys instead of silently keeping the last one."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        duplicates = sorted({k for k in keys if keys.count(k) > 1}, key=str)
        if duplicates:
            raise ValueError(f"duplicate keys in YAML: {duplicates}")
        return super().construct_mapping(node, deep)


def _read_yaml(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=_UniqueKeyLoader)


def load_limits(path: Path = PROJECT_ROOT / "config/limits.yaml") -> Limits:
    return Limits.model_validate(_read_yaml(path))


def load_strategy(path: Path = PROJECT_ROOT / "config/strategy.yaml") -> StrategyParams:
    return StrategyParams.model_validate(_read_yaml(path))


def load_runner(path: Path = PROJECT_ROOT / "config/runner.yaml") -> RunnerParams:
    return RunnerParams.model_validate(_read_yaml(path))
