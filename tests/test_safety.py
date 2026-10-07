import pytest

from bot.safety import PAPER_ENDPOINT, is_paper_endpoint, kill_switch_reason


def test_no_kill(tmp_path):
    assert kill_switch_reason(tmp_path, {}) is None


def test_kill_file(tmp_path):
    (tmp_path / "KILL").touch()
    assert "KILL file" in kill_switch_reason(tmp_path, {})


def test_kill_directory_also_counts(tmp_path):
    (tmp_path / "KILL").mkdir()
    assert kill_switch_reason(tmp_path, {}) is not None


@pytest.mark.parametrize("value,on", [
    ("1", True), ("true", True), ("yes", True), (" 1 ", True), ("on", True),
    ("0", False), ("", False), (" 0 ", False), ("  ", False),
])
def test_kill_env(tmp_path, value, on):
    assert (kill_switch_reason(tmp_path, {"KILL_SWITCH": value}) is not None) == on


def test_kill_env_reason_names_value(tmp_path):
    assert "KILL_SWITCH=true" in kill_switch_reason(tmp_path, {"KILL_SWITCH": "true"})


def test_paper_endpoint_constant():
    assert PAPER_ENDPOINT == "https://paper-api.alpaca.markets"


@pytest.mark.parametrize("url,ok", [
    ("https://paper-api.alpaca.markets", True),
    ("https://paper-api.alpaca.markets/", True),
    ("https://api.alpaca.markets", False),
    ("http://paper-api.alpaca.markets", False),
    ("https://paper-api.alpaca.markets.evil.com", False),
    ("https://paper-api.alpaca.markets//", False),
    ("https://PAPER-API.alpaca.markets", False),
    ("", False),
])
def test_paper_endpoint(url, ok):
    assert is_paper_endpoint(url) == ok


def test_paper_endpoint_non_string():
    assert is_paper_endpoint(None) is False  # type: ignore[arg-type]
