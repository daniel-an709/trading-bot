from decimal import Decimal

import pytest

from bot.schema import MalformedProposal, NoTrade, TradeProposal, parse_proposal

GOOD = ('{"decision":"trade","ticker":"MSFT","side":"buy","quantity":12,"order_type":"limit",'
        '"limit_price":400.00,"reasoning":"r","confidence":0.7}')


def test_parses_trade():
    p = parse_proposal(GOOD)
    assert isinstance(p, TradeProposal)
    assert p.quantity == 12 and p.limit_price == Decimal("400.00") and p.confidence == Decimal("0.7")


def test_parses_no_trade():
    assert isinstance(parse_proposal('{"decision":"no_trade","reasoning":"nothing clean"}'), NoTrade)


def test_accepts_json_fence_and_whitespace():
    assert parse_proposal(f"\n```json\n{GOOD}\n```\n").ticker == "MSFT"


def test_accepts_bare_fence():
    assert parse_proposal(f"```\n{GOOD}\n```").ticker == "MSFT"


def test_normalises_ticker():
    assert parse_proposal(GOOD.replace('"MSFT"', '" msft "')).ticker == "MSFT"


def test_integer_limit_price_and_confidence_accepted():
    p = parse_proposal(GOOD.replace("400.00", "400").replace("0.7", "1"))
    assert p.limit_price == Decimal("400") and p.confidence == Decimal("1")


def test_market_order_without_price_parses():
    # Schema allows it so the validator can reject it with a clear "not_limit" reason.
    p = parse_proposal(GOOD.replace('"limit"', '"market"').replace('"limit_price":400.00,', ''))
    assert p.order_type == "market" and p.limit_price is None


def test_sell_parses():
    assert parse_proposal(GOOD.replace('"buy"', '"sell"')).side == "sell"


@pytest.mark.parametrize("raw", [
    "", "   ", "null", "[]", "{}", "not json", "{oops",
    "Here is my proposal: " + GOOD,
    GOOD + " hope that helps",
    f"```json\n{GOOD}\n```\nLet me know!",
    GOOD.replace("12", "true"), GOOD.replace("12", '"12"'), GOOD.replace("12", "12.5"),
    GOOD.replace("12", "0"), GOOD.replace("12", "-3"),
    GOOD.replace("0.7", "1.5"), GOOD.replace("0.7", "-0.1"), GOOD.replace("0.7", '"high"'),
    GOOD.replace("0.7", "true"),
    GOOD.replace("400.00", "NaN"), GOOD.replace("400.00", "Infinity"), GOOD.replace("400.00", "-1"),
    GOOD.replace("400.00", "0"), GOOD.replace("400.00", "400.001"), GOOD.replace("400.00", '"400.00"'),
    GOOD.replace('"r"', '"   "'), GOOD.replace('"r"', '""'),
    GOOD.replace('"limit_price":400.00,', ''),
    GOOD.replace('"limit"', '"market"'),
    GOOD.replace("}", ',"extra":1}'),
    GOOD.replace('"ticker":"MSFT"', '"ticker":"MSFT","ticker":"AAPL"'),
    GOOD.replace('"limit"', '"stop"'), GOOD.replace('"buy"', '"short"'),
    GOOD.replace('"MSFT"', '"MSFT1234"'), GOOD.replace('"MSFT"', '"BRK.B"'), GOOD.replace('"MSFT"', '7'),
    GOOD.replace('"quantity":12,', ''),
    '{"decision":"maybe","reasoning":"r"}',
    '{"decision":"no_trade"}',
    '{"decision":"no_trade","reasoning":"r","ticker":"MSFT"}',
    "x" * 50_000,
    "{" + '"a":1,' * 5000 + '"decision":"no_trade","reasoning":"r"}',
])
def test_malformed_rejected(raw):
    with pytest.raises(MalformedProposal):
        parse_proposal(raw)


def test_non_string_input_rejected():
    with pytest.raises(MalformedProposal):
        parse_proposal(None)  # type: ignore[arg-type]


@pytest.mark.parametrize("raw", [
    '{"decision":["trade"]}',
    '{"decision":{}}',
    "[" * 19_999,
    '{"a":' * 5_000 + "1" + "}" * 5_000,
], ids=["decision-list", "decision-object", "deep-list", "deep-object"])
def test_hostile_structures_rejected_as_malformed(raw):
    with pytest.raises(MalformedProposal):
        parse_proposal(raw)


def test_single_line_fence_accepted():
    assert parse_proposal('```json {"decision":"no_trade","reasoning":"r"} ```').decision == "no_trade"
