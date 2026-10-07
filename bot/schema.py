"""Strict schema for Claude's final output and the parser that enforces it.

Claude must answer with exactly one JSON object: a trade proposal or an explicit
no-trade. Anything else — prose around it, extra or missing fields, wrong types,
NaN, duplicate keys — raises MalformedProposal. The parser never raises anything else.
"""

import json
import re
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator, model_validator

MAX_RAW_CHARS = 20_000

_TICKER = re.compile(r"^[A-Z]{1,5}$")
_FENCE = re.compile(r"^```(?:json)?[ \t]*\n(.*)\n[ \t]*```$", re.DOTALL)
_STRICT = ConfigDict(frozen=True, extra="forbid")


class MalformedProposal(ValueError):
    pass


def _non_blank(text: str) -> str:
    if not text.strip():
        raise ValueError("must not be blank")
    return text


class TradeProposal(BaseModel):
    model_config = _STRICT

    decision: Literal["trade"]
    ticker: str
    side: Literal["buy", "sell"]
    quantity: StrictInt = Field(ge=1)
    order_type: Literal["market", "limit"]
    limit_price: Annotated[Decimal, Field(gt=0, decimal_places=2)] | None = None
    reasoning: str
    confidence: Decimal = Field(ge=0, le=1)

    @field_validator("quantity", "limit_price", "confidence", mode="before")
    @classmethod
    def _must_be_json_number(cls, value):
        if isinstance(value, (str, bool)):
            raise ValueError("must be a JSON number")
        return value

    @field_validator("ticker", mode="before")
    @classmethod
    def _normalise_ticker(cls, value):
        if not isinstance(value, str):
            raise ValueError("must be a string")
        ticker = value.strip().upper()
        if not _TICKER.match(ticker):
            raise ValueError(f"ticker {value!r} must be 1-5 letters")
        return ticker

    _reasoning_not_blank = field_validator("reasoning")(_non_blank)

    @model_validator(mode="after")
    def _limit_price_matches_order_type(self) -> "TradeProposal":
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit order requires limit_price")
        if self.order_type == "market" and self.limit_price is not None:
            raise ValueError("market order must not have limit_price")
        return self


class NoTrade(BaseModel):
    model_config = _STRICT

    decision: Literal["no_trade"]
    reasoning: str

    _reasoning_not_blank = field_validator("reasoning")(_non_blank)


def _reject_constant(name: str):
    raise ValueError(f"{name} is not allowed")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    keys = [k for k, _ in pairs]
    duplicates = {k for k in keys if keys.count(k) > 1}
    if duplicates:
        raise ValueError(f"duplicate keys: {sorted(duplicates)}")
    return dict(pairs)


def parse_proposal(raw: str) -> TradeProposal | NoTrade:
    if not isinstance(raw, str):
        raise MalformedProposal("output is not text")
    if len(raw) > MAX_RAW_CHARS:
        raise MalformedProposal(f"output longer than {MAX_RAW_CHARS} characters")

    text = raw.strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()

    try:
        data = json.loads(
            text,
            parse_float=Decimal,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except ValueError as e:  # JSONDecodeError is a ValueError
        raise MalformedProposal(f"invalid JSON: {e}") from None

    if not isinstance(data, dict):
        raise MalformedProposal("output is not a JSON object")

    model = {"trade": TradeProposal, "no_trade": NoTrade}.get(data.get("decision"))
    if model is None:
        raise MalformedProposal(f"unknown decision {data.get('decision')!r}")

    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise MalformedProposal(str(e)) from None
