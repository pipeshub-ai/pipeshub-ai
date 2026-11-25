"""Total accessors for untrusted JSON (engine payloads): wrong shapes read as empty, never raise."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from harness_core.engines.types import JsonValue


def as_object(value: JsonValue | object) -> Mapping[str, JsonValue]:
    if isinstance(value, Mapping):
        return cast("Mapping[str, JsonValue]", value)
    return {}


def as_list(value: JsonValue | object) -> Sequence[JsonValue]:
    if isinstance(value, list | tuple):
        return cast("Sequence[JsonValue]", value)
    return ()


def as_str(value: JsonValue | object) -> str:
    return value if isinstance(value, str) else ""


def as_int(value: JsonValue | object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def as_float(value: JsonValue | object) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


__all__ = ["as_float", "as_int", "as_list", "as_object", "as_str"]
