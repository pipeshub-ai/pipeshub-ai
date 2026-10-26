"""Typed values built in code. The model never fills these."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class _Value(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateRangeValue(_Value):
    value_type: Literal["date_range"] = "date_range"
    start_ms: int | None = None
    end_ms: int | None = None
    timex: str = ""
    granularity: Literal["year", "quarter", "month", "week", "day", "hour", "minute"] = "day"
    anchored: bool = False
    anchor_source: str = ""
    ambiguous: bool = False


class DurationValue(_Value):
    value_type: Literal["duration"] = "duration"
    iso: str
    seconds: float | None = None
    approx: bool = False


class MoneyValue(_Value):
    value_type: Literal["money"] = "money"
    amount: Decimal
    amount_float: float
    currency: str
    currency_ambiguous: bool = False


class QuantityValue(_Value):
    value_type: Literal["quantity"] = "quantity"
    value: Decimal
    unit: str
    dimension: str
    si_value: float
    si_unit: str


class PercentValue(_Value):
    value_type: Literal["percent"] = "percent"
    value: Decimal


class ContactValue(_Value):
    value_type: Literal["contact"] = "contact"
    scheme: Literal["email", "url", "phone", "ip"]
    canonical: str


class NameValue(_Value):
    value_type: Literal["name"] = "name"
    canonical: str


TypedValue = Annotated[
    Union[
        DateRangeValue,
        DurationValue,
        MoneyValue,
        QuantityValue,
        PercentValue,
        ContactValue,
        NameValue,
    ],
    Field(discriminator="value_type"),
]
