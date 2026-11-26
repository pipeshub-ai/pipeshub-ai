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
    ALLOWED_SIZE_DELTAS,
)

_PINNED = gen.load()
_CASES = gen.pinned_cases(_PINNED)
_SHARDS = 8


def _expected_row(row: list[Any]) -> list[Any]:
    delta = ALLOWED_DELTAS.get(gen.case_key(row), {})
    return [delta.get(col, value) for col, value in zip(gen.ROW_COLUMNS, row, strict=True)]


# The labels' readers come from the replay as the deltas describe it (its `legacy` column), never from the registry.
_ORACLE = delta_rules.Oracle(_PINNED, replayed=[_expected_row(r) for r in _PINNED["rows"]])
_TIER_KIND = ("tier", "kind")


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


async def test_docling_names_are_unchanged() -> None:
    actual = [[e, await gen.docling_name(e)] for e, _ in _PINNED["docling_names"]]
    assert actual == _PINNED["docling_names"]


def test_the_lists_offered_and_matched_are_unchanged() -> None:
    """The running lists (filter options, enums, probes, ...) are main's plus the reviewed changes."""
    expected = {**_PINNED["globals"], **ALLOWED_GLOBAL_DELTAS}
    actual = gen.global_lists()
    assert {k: v for k, v in actual.items() if v != expected.get(k)} == {}
    assert actual.keys() == expected.keys()


def test_every_allowed_delta_follows_its_reviewed_rule() -> None:
    """Each delta is explained by a reviewed reason, checked against main's own decisions where
    the reason says the new decision is main's decision for another spelling."""
    oracle = _ORACLE
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
    new = ["msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx", "pptm"]
    assert now["ExtensionTypes"] == [*pinned["ExtensionTypes"], *new]
    assert {k: v for k, v in now["MimeTypes"].items() if k not in pinned["MimeTypes"]}.keys() == {
        "MSG", "ODT", "RTF", "XLSM", "DOCM", "DOTX", "POTX", "LOG", "CFG", "PPTM",
    }
    assert {k: now["MimeTypes"][k] for k in pinned["MimeTypes"]} == pinned["MimeTypes"]
    assert now["EXCLUDABLE_FILE_EXTENSIONS"] == [e for e in pinned["EXCLUDABLE_FILE_EXTENSIONS"] if e != "log"]
    offered = [e for e in pinned["file_extension_filter"] if e not in pinned["EXCLUDABLE_FILE_EXTENSIONS"]]
    assert now["file_extension_filter"] == [*offered, *new, *now["EXCLUDABLE_FILE_EXTENSIONS"]]
    assert now["libreoffice_probe_inputs"] == sorted([*pinned["libreoffice_probe_inputs"], "odt", "rtf"])
    assert "OOXML_EXTENSIONS" not in now and "OOXML_MIME_TYPES" not in now


def test_no_rule_lets_a_cell_main_indexed_change_unless_it_is_listed() -> None:
    """A cell main read (gate ok, a parser reached) keeps every decision but its tier and kind, whatever the rules say,
    except the cells listed one by one in EXISTING_CELL_EXCEPTIONS. Its tier and kind may only become its reader's
    (``reader-labels``, test below)."""
    oracle = _ORACLE
    leaks = [
        f"{tuple(row[:4])} {column}"
        for row in _PINNED["rows"]
        if delta_rules.main_indexed(row)
        for column in gen.ROW_COLUMNS[4:]
        if column not in _TIER_KIND
        if (delta_rules.classify_cell(oracle, row, column, "<any other value>") is not None)
        != (gen.case_key(row) in delta_rules.EXISTING_CELL_EXCEPTIONS)
    ]
    assert not leaks, "\n".join(leaks[:40])
    assert len(delta_rules.EXISTING_CELL_EXCEPTIONS) == 8
    assert all(gen.case_key(json.loads(k)) == k for k in delta_rules.EXISTING_CELL_EXCEPTIONS)


def test_a_tier_or_kind_its_reader_does_not_have_is_refused_by_every_rule() -> None:
    """The reader's value is binding: no reason accepts another tier or kind for labels whose reader the replay shows,
    including the new-type and log-cfg reasons that accept any value elsewhere."""
    accepted = [
        f"{tuple(row[:4])} {column}"
        for row in _PINNED["rows"]
        for column in _TIER_KIND
        if _ORACLE.reader_value(row, column)[0]
        and delta_rules.classify_cell(_ORACLE, row, column, "<any other value>") is not None
    ]
    assert not accepted, "\n".join(accepted[:40])
    assert sum(_ORACLE.reader_value(r, "tier")[0] for r in _PINNED["rows"]) == 4923


def test_every_tier_and_kind_is_the_one_its_reader_has() -> None:
    """Binding on every row, not only on the deltas: a cell that moved back to main's value leaves no delta to check."""
    wrong = [
        f"{tuple(row[:4])} {column}"
        for row in _PINNED["rows"]
        for column in _TIER_KIND
        if (found := _ORACLE.reader_value(row, column))[0]
        and _expected_row(row)[gen.ROW_COLUMNS.index(column)] != found[1]
        and delta_rules._key(row) not in delta_rules.ORACLE_EXCEPTIONS
    ]
    assert not wrong, f"{len(wrong)} cells:\n" + "\n".join(wrong[:40])


def test_main_indexed_cells_differ_only_where_listed() -> None:
    """Outside the listed cells, a cell main read changes only its tier and kind, to its reader's."""
    rows = {gen.case_key(r): r for r in _PINNED["rows"]}
    changed = {k for k in ALLOWED_DELTAS if delta_rules.main_indexed(rows[k])}
    assert set(delta_rules.EXISTING_CELL_EXCEPTIONS) <= changed
    others = changed - set(delta_rules.EXISTING_CELL_EXCEPTIONS)
    assert {c for k in others for c in ALLOWED_DELTAS[k]} <= set(_TIER_KIND)
    assert {w for k in others for w in ALLOWED_DELTA_REASONS[k]} == {"reader-labels"}


@pytest.mark.parametrize(
    ("mime", "column", "new", "why"),
    [
        # Main read a .pptm stored as text/plain as text; the deck reader takes it (package-not-text).
        ("text/plain", "legacy", "process_pptx_document", "package-not-text"),
        # Main refused these spellings; the .pptm extension now reads them as the deck.
        ("application/x-pdf", "legacy", "process_pptx_document", "new-type"),
        ("text/plain; charset=utf-8", "parse_format", "pptx", "new-type"),
        # A cell main read under any other mime keeps main's reader: no reviewed reason covers a change.
        ("application/pdf", "legacy", "process_pptx_document", None),
        ("text/html", "kind", "docling_docx", None),
    ],
)
def test_a_pptm_cell_main_read_keeps_its_reader_unless_it_was_read_as_text(
    mime: str, column: str, new: str, why: str | None
) -> None:
    row = {gen.case_key(r): r for r in _PINNED["rows"]}[gen.case_key(["pptm", mime, "file.pptm", "FILE"])]
    assert delta_rules.main_indexed(row) is (why != "new-type")
    assert delta_rules.classify_cell(_ORACLE, row, column, new) == why


def test_only_the_listed_log_and_cfg_cells_are_bounded_by_size() -> None:
    """Main read every accepted cell at any size. The size rules bound exactly the listed cells,
    each a .log/.cfg; every other cell is still taken at any size."""
    bounded = {
        gen.case_key(r) for r in _PINNED["rows"]
        if delta_rules.main_indexed(r) and all(v is None or isinstance(v, str) for v in r[:2])
        and delta_rules.gate_over_limit(r) == "too_large"
    }
    assert bounded == set(ALLOWED_SIZE_DELTAS)
    rows = {gen.case_key(r): r for r in _PINNED["rows"]}
    assert all(delta_rules.bounded_text_row(rows[k]) for k in ALLOWED_SIZE_DELTAS)
    assert {v["why"] for v in ALLOWED_SIZE_DELTAS.values()} == {"bounded-token-cost"}
