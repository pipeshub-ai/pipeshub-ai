"""Every file-type decision equals the one pinned on main, except the reviewed deltas."""
from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.config.file_types import delta_rules
from tests.unit.config.file_types import gen_pinned_decisions as gen
from tests.unit.config.file_types.allowed_deltas import (
    ALLOWED_DELTA_REASONS,
    ALLOWED_DELTAS,
    ALLOWED_GLOBAL_DELTAS,
    ALLOWED_MIME_DELTAS,
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
    expected = [[m, ALLOWED_MIME_DELTAS.get(m, {}).get("extension", e)] for m, e in _PINNED["mimes"]]
    assert [[m, *gen.per_mime(m)] for m, *_ in _PINNED["mimes"]] == expected


def test_every_allowed_delta_follows_its_reviewed_rule() -> None:
    """Each delta is explained by a reviewed reason, checked against main's own decisions where
    the reason says the new decision is main's decision for another spelling."""
    oracle = delta_rules.Oracle(_PINNED)
    rows = {gen.case_key(r): r for r in _PINNED["rows"]}
    wrong = []
    for key, changes in ALLOWED_DELTAS.items():
        assert key in rows, f"delta for a row the snapshot does not pin: {key}"
        assert set(ALLOWED_DELTA_REASONS[key]) <= delta_rules.REASONS.keys()
        for column, value in changes.items():
            if delta_rules.classify_cell(oracle, rows[key], column, value) not in ALLOWED_DELTA_REASONS[key]:
                wrong.append(f"{key} {column} -> {value!r}")
    for mime, entry in ALLOWED_MIME_DELTAS.items():
        if delta_rules.classify_mime(oracle, mime, entry["extension"]) != entry["why"]:
            wrong.append(f"mime {mime} -> {entry}")
    assert not wrong, "\n".join(wrong[:40])
    assert set(ALLOWED_GLOBAL_DELTAS) <= delta_rules.GLOBAL_REASONS.keys()


def test_the_changed_lists_change_only_by_the_new_types() -> None:
    pinned, now = _PINNED["globals"], ALLOWED_GLOBAL_DELTAS
    new = ["msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx"]
    assert now["ExtensionTypes"] == [*pinned["ExtensionTypes"], *new]
    assert {k: v for k, v in now["MimeTypes"].items() if k not in pinned["MimeTypes"]}.keys() == {
        "MSG", "ODT", "RTF", "XLSM", "DOCM", "DOTX", "POTX",
    }
    assert {k: now["MimeTypes"][k] for k in pinned["MimeTypes"]} == pinned["MimeTypes"]
    assert now["EXCLUDABLE_FILE_EXTENSIONS"] == [e for e in pinned["EXCLUDABLE_FILE_EXTENSIONS"] if e != "log"]
    offered = [e for e in pinned["file_extension_filter"] if e not in pinned["EXCLUDABLE_FILE_EXTENSIONS"]]
    assert now["file_extension_filter"] == [*offered, *new, *now["EXCLUDABLE_FILE_EXTENSIONS"]]
    assert now["libreoffice_probe_inputs"] == sorted([*pinned["libreoffice_probe_inputs"], "odt", "rtf"])
    assert "OOXML_EXTENSIONS" not in now and "OOXML_MIME_TYPES" not in now


def test_no_rule_lets_a_cell_main_indexed_change_unless_it_is_listed() -> None:
    """A cell main read (gate ok, a parser reached) keeps every decision, whatever the rules say,
    except the cells listed one by one in EXISTING_CELL_EXCEPTIONS."""
    oracle = delta_rules.Oracle(_PINNED)
    leaks = [
        f"{tuple(row[:4])} {column}"
        for row in _PINNED["rows"]
        if delta_rules.main_indexed(row)
        for column in gen.ROW_COLUMNS[4:]
        if (delta_rules.classify_cell(oracle, row, column, "<any other value>") is not None)
        != (gen.case_key(row) in delta_rules.EXISTING_CELL_EXCEPTIONS)
    ]
    assert not leaks, "\n".join(leaks[:40])
    assert len(delta_rules.EXISTING_CELL_EXCEPTIONS) == 7
    assert all(gen.case_key(json.loads(k)) == k for k in delta_rules.EXISTING_CELL_EXCEPTIONS)


def test_main_indexed_cells_differ_only_where_listed() -> None:
    rows = {gen.case_key(r): r for r in _PINNED["rows"]}
    changed = {k for k in ALLOWED_DELTAS if delta_rules.main_indexed(rows[k])}
    assert changed == set(delta_rules.EXISTING_CELL_EXCEPTIONS)
