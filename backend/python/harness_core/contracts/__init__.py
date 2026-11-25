"""Versioned harness contracts: models generated from ``contracts/harness`` plus RFC 8785 JSON.

Validate wire data with ``validate_contract`` (strict: no ``"1"`` -> ``1`` coercion) and emit it
with ``dump_contract`` so a parse/dump round trip is byte-stable under ``canonical_json``.
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel

from harness_core.contracts.canonical_json import (
    RESERVED_KEY,
    CanonicalJsonError,
    assert_no_reserved_keys,
    canonical_digest,
    canonical_json,
    canonical_sha256,
)

__all__ = [
    "RESERVED_KEY",
    "CanonicalJsonError",
    "assert_no_reserved_keys",
    "canonical_digest",
    "canonical_json",
    "canonical_sha256",
    "dump_contract",
    "validate_contract",
]

ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_contract(model: type[ModelT], data: object) -> ModelT:
    assert_no_reserved_keys(data)
    return model.model_validate(data, strict=True)


def dump_contract(instance: BaseModel) -> Any:  # noqa: ANN401 - JSON value
    # exclude_unset: absent optional fields stay absent instead of becoming null.
    return instance.model_dump(mode="json", by_alias=True, exclude_unset=True)
