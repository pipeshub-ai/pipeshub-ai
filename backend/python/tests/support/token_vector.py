"""The shared token vector as test data (QA-11): every entry and its examples, read from the contract file.

Tests that run each example through a redactor read the file, not the generated module, so a redactor that stops
reading the vector, or a pattern added without an example, fails them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import JsonValue

VECTOR_PATH = Path(__file__).resolve().parents[4] / "contracts" / "harness" / "vectors" / "token-patterns.json"


@dataclass(frozen=True, slots=True)
class VectorEntry:
    kind: str
    source: str
    forbidden: bool
    redact_only: bool
    # Joined from parts at load time: the file holds no literal credential.
    examples: tuple[str, ...]


def load_vector() -> tuple[str, tuple[VectorEntry, ...]]:
    raw = json.loads(VECTOR_PATH.read_text(encoding="utf-8"))
    entries = [
        VectorEntry(
            kind=e["kind"],
            source=e["source"],
            forbidden=bool(e.get("forbidden", False)),
            redact_only=redact_only,
            examples=tuple("".join(parts) for parts in e.get("examples", ())),
        )
        for key, redact_only in (("patterns", False), ("redactOnly", True))
        for e in raw[key]
    ]
    return raw["mask"], tuple(entries)


MASK, ENTRIES = load_vector()
# One pytest param per example, ids like ``github_token-1``.
EXAMPLES: tuple[tuple[str, str, VectorEntry], ...] = tuple(
    (f"{e.kind}-{i}", example, e) for e in ENTRIES for i, example in enumerate(e.examples)
)


def strings_in(value: JsonValue) -> list[str]:
    """Every string in a JSON value, decoded: a ``repr`` or ``json.dumps`` escapes a key's newlines and hides it."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [t for v in value.values() for t in strings_in(v)]
    if isinstance(value, list):
        return [t for v in value for t in strings_in(v)]
    return []


def holding(secret: str, value: JsonValue) -> list[str]:
    return [t for t in strings_in(value) if secret in t]
