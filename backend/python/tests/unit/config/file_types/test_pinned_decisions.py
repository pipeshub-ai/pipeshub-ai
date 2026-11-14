"""Every file-type decision equals the one pinned on main, except the reviewed deltas."""
from __future__ import annotations

from typing import Any

import pytest

from tests.unit.config.file_types import gen_pinned_decisions as gen
from tests.unit.config.file_types.allowed_deltas import (
    ALLOWED_DELTAS,
    ALLOWED_GLOBAL_DELTAS,
)

_PINNED = gen.load()
_CASES = gen.pinned_cases(_PINNED)
_SHARDS = 8


def _expected_row(row: list[Any]) -> list[Any]:
    delta = ALLOWED_DELTAS.get(gen.case_key(row), {})
    return [delta.get(col, value) for col, value in zip(gen.ROW_COLUMNS, row, strict=True)]


def _mismatches(expected: list[list[Any]], actual: list[list[Any]]) -> list[str]:
    out = []
    for exp, act in zip(expected, actual, strict=True):
        for col, e, a in zip(gen.ROW_COLUMNS, exp, act, strict=True):
            if e != a:
                out.append(f"{tuple(exp[:4])} {col}: pinned {e!r}, now {a!r}")
    return out


def test_the_pinned_universe_is_the_one_main_produced() -> None:
    assert len(_CASES) == len({gen.case_key(c) for c in _CASES}) > 6000
    assert ("blocks", None, "file.blocks", "FILE") in _CASES
    assert ("pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "file.pdf", "FILE") in _CASES
    # Malformed producer payloads: a list or dict where the mime or extension string belongs.
    assert (None, ["text/plain"], "file", "FILE") in _CASES
    assert (["pdf"], None, "file", "FILE") in _CASES


# A shard replays ~750 records through the real handler; a loaded runner needs more than 30 s.
@pytest.mark.timeout(180)
@pytest.mark.parametrize("shard", range(_SHARDS))
async def test_gate_and_dispatch_decisions_are_unchanged(shard: int) -> None:
    pinned = list(gen.iter_shard(_PINNED["rows"], shard, _SHARDS))
    with gen._quiet_env():
        actual = [[*row[:4], *await gen.decide(row[:4])] for row in pinned]
    mismatches = _mismatches([_expected_row(r) for r in pinned], actual)
    assert not mismatches, f"{len(mismatches)} decisions changed:\n" + "\n".join(mismatches[:40])


def test_per_extension_and_per_mime_decisions_are_unchanged() -> None:
    assert [[e, *gen.per_extension(e)] for e, *_ in _PINNED["extensions"]] == _PINNED["extensions"]
    assert [[m, *gen.per_mime(m)] for m, *_ in _PINNED["mimes"]] == _PINNED["mimes"]


async def test_docling_names_are_unchanged() -> None:
    actual = [[e, await gen.docling_name(e)] for e, _ in _PINNED["docling_names"]]
    assert actual == _PINNED["docling_names"]


def test_the_lists_offered_and_matched_are_unchanged() -> None:
    expected = {**_PINNED["globals"], **ALLOWED_GLOBAL_DELTAS}
    actual = gen.global_lists()
    assert {k: v for k, v in actual.items() if v != expected.get(k)} == {}
    assert actual.keys() == expected.keys()
