import pydantic
import pytest
import yaml

from bot.config import PROJECT_ROOT, load_limits, load_strategy


def write_limits(tmp_path, **changes):
    data = yaml.safe_load((PROJECT_ROOT / "config/limits.yaml").read_text())
    for key, value in changes.items():
        if value is _DROP:
            data.pop(key)
        else:
            data[key] = value
    path = tmp_path / "limits.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


_DROP = object()


def test_load_limits_real_file():
    limits = load_limits()
    assert (limits.max_position_pct, limits.max_open_positions,
            limits.max_new_trades_per_day, limits.max_daily_loss_pct) == (5, 6, 2, 2)
    assert len(limits.allowed_tickers) == 26 and "MSFT" in limits.allowed_tickers


def test_load_strategy_real_file():
    s = load_strategy()
    assert (s.sma_period, s.rsi_max, s.stop_atr_multiple, s.target_atr_multiple,
            s.time_exit_trading_days) == (200, 40, 2.0, 3.0, 10)


@pytest.mark.parametrize("mutation", [
    {"max_position_pct": -1}, {"max_position_pct": 0}, {"max_position_pct": 101},
    {"max_open_positions": 0}, {"max_new_trades_per_day": 0},
    {"max_daily_loss_pct": 0}, {"max_daily_loss_pct": None},
    {"surprise_key": 1},
])
def test_bad_limits_rejected(tmp_path, mutation):
    with pytest.raises(pydantic.ValidationError):
        load_limits(write_limits(tmp_path, **mutation))


def test_missing_key_rejected(tmp_path):
    with pytest.raises(pydantic.ValidationError):
        load_limits(write_limits(tmp_path, max_new_trades_per_day=_DROP))


@pytest.mark.parametrize("whitelist", [
    {"etfs": ["spy"], "stocks": ["MSFT"]},
    {"etfs": ["SPY"], "stocks": ["MSFT", "MSFT"]},
    {"etfs": ["MSFT"], "stocks": ["MSFT"]},
    {"etfs": ["SPY"], "stocks": ["BRK.B"]},
])
def test_bad_whitelist_rejected(tmp_path, whitelist):
    with pytest.raises(pydantic.ValidationError):
        load_limits(write_limits(tmp_path, whitelist=whitelist))


def test_limits_are_frozen():
    with pytest.raises(pydantic.ValidationError):
        load_limits().max_position_pct = 50


def test_bad_strategy_rejected(tmp_path):
    path = tmp_path / "strategy.yaml"
    data = yaml.safe_load((PROJECT_ROOT / "config/strategy.yaml").read_text())
    data["stop_atr_multiple"] = 0
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(pydantic.ValidationError):
        load_strategy(path)
