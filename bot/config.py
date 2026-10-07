"""Load and validate config/limits.yaml and config/strategy.yaml."""

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROJECT_ROOT = Path(__file__).resolve().parent.parent

_TICKER = re.compile(r"^[A-Z]{1,5}$")
_STRICT = ConfigDict(frozen=True, extra="forbid")


class Whitelist(BaseModel):
    model_config = _STRICT

    etfs: tuple[str, ...]
    stocks: tuple[str, ...]

    @field_validator("etfs", "stocks")
    @classmethod
    def _tickers_well_formed(cls, tickers: tuple[str, ...]) -> tuple[str, ...]:
        for t in tickers:
            if not _TICKER.match(t):
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
    model_config = _STRICT

    max_position_pct: float = Field(gt=0, le=100)
    max_open_positions: int = Field(ge=1)
    max_new_trades_per_day: int = Field(ge=1)
    max_daily_loss_pct: float = Field(gt=0, le=100)
    whitelist: Whitelist

    @property
    def allowed_tickers(self) -> frozenset[str]:
        return frozenset(self.whitelist.etfs) | frozenset(self.whitelist.stocks)


class StrategyParams(BaseModel):
    model_config = _STRICT

    sma_period: int = Field(gt=0)
    rsi_period: int = Field(gt=0)
    rsi_max: float = Field(gt=0)
    atr_period: int = Field(gt=0)
    stop_atr_multiple: float = Field(gt=0)
    target_atr_multiple: float = Field(gt=0)
    time_exit_trading_days: int = Field(gt=0)
    news_window_hours: int = Field(gt=0)
    min_bars: int = Field(gt=0)


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def load_limits(path: Path = PROJECT_ROOT / "config/limits.yaml") -> Limits:
    return Limits.model_validate(_read_yaml(path))


def load_strategy(path: Path = PROJECT_ROOT / "config/strategy.yaml") -> StrategyParams:
    return StrategyParams.model_validate(_read_yaml(path))
