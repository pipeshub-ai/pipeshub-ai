"""R40-TEXT (ADR-TEXT-01): the display-safe rule (SEC-B-04) and the spreadsheet-cell rule (SEC-B-06) as the shared
vectors state them. Node and the web client replay the same files; a table here that drifts from the vector fails."""

from __future__ import annotations

import json
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from harness_core.security import display_safe as ds
from harness_core.security import spreadsheet_cells as sc

VECTORS = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors"


@cache
def _vector(name: str) -> dict[str, Any]:
    return json.loads((VECTORS / name).read_text(encoding="utf-8"))


def _ranges(pairs: list[list[str]]) -> tuple[tuple[int, int], ...]:
    return tuple((int(a, 16), int(b, 16)) for a, b in pairs)


def _chars(codes: list[str]) -> frozenset[str]:
    return frozenset(chr(int(c, 16)) for c in codes)


def test_the_display_tables_are_the_vectors() -> None:
    v = _vector("display-safe.json")
    assert (ds.MARKER_OPEN, ds.MARKER_CLOSE) == (v["marker"]["open"], v["marker"]["close"])
    assert ds.INVISIBLE_RANGES == _ranges(v["invisible"])
    keeps = v["proseKeeps"]
    assert ds.PROSE_CONTROLS == {int(c, 16) for c in keeps["controls"]}
    assert ds.PROSE_JOINERS == {int(c, 16) for c in keeps["joiners"]}
    assert ds.PROSE_VARIATION_SELECTORS == {int(c, 16) for c in keeps["variationSelectors"]}
    assert ds.SCRIPTS == tuple((s["name"], _ranges(s["ranges"])) for s in v["scripts"])
    assert ds.ALLOWED_MIXES == tuple(frozenset(m) for m in v["allowedMixes"])
    assert v["lookalikes"] == ds.LOOKALIKES
    assert v["latinPassesForAscii"] == ds.LATIN_PASSES_FOR_ASCII
    assert ds.HIDDEN_MARKS == {int(c, 16) for c in v["hiddenMarks"]}


@pytest.mark.parametrize("case", _vector("display-safe.json")["text"], ids=lambda c: c["name"])
def test_display_safe_text(case: dict[str, str]) -> None:
    shown = ds.display_safe(case["in"], case["mode"])  # type: ignore[arg-type]
    assert shown == case["out"]
    assert ds.display_safe(shown, case["mode"]) == shown  # type: ignore[arg-type]


@pytest.mark.parametrize("case", _vector("display-safe.json")["hosts"], ids=lambda c: c["name"])
def test_safe_host(case: dict[str, str]) -> None:
    shown = ds.safe_host(case["in"])
    assert shown == case["out"]
    assert ds.safe_host(shown) == shown


def test_every_bidi_and_zero_width_character_is_marked_in_both_modes() -> None:
    # The proof's set and the rest of the format characters a reader cannot see.
    hidden = [*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C, 0x200B, 0x2060, 0xFEFF]
    for cp in hidden:
        text = f"a{chr(cp)}b"
        assert ds.display_safe(text, "identifier") == f"a⟨U+{cp:04X}⟩b"
        assert ds.display_safe(text, "prose") == f"a⟨U+{cp:04X}⟩b"


def test_no_format_character_survives_identifier_mode() -> None:
    survivors = [
        cp for cp in range(0x110000)
        if 0xD800 > cp or cp > 0xDFFF
        if unicodedata.category(chr(cp)) == "Cf" and chr(cp) in ds.display_safe(f"x{chr(cp)}y")
    ]
    assert survivors == []


def test_a_marker_is_never_cut_in_half() -> None:
    text = "a" * 10 + "⟨U+202E⟩" + "b" * 10
    for limit in range(5, len(text) + 2):
        clipped = ds.clip_display(text, limit)
        assert len(clipped) <= limit
        assert clipped.count("⟨") == clipped.count("⟩")


def test_a_third_party_title_cannot_name_another_owner() -> None:
    assert ds.tool_display_name("Third party", "Slack · Send message") == "Third party · Slack - Send message"
    assert ds.tool_display_name("Third party", "\u202eatad lla eteled\u202c Read calendar").count("⟨U+") == 2
    assert ds.tool_display_name("Third party", "Read\ncalendar") == "Third party · Read calendar"


def test_the_spreadsheet_sets_are_the_vectors() -> None:
    v = _vector("spreadsheet-cells.json")
    assert sc.PREFIX == v["prefix"]
    assert _chars(v["triggers"]) == sc.TRIGGERS
    assert _chars(v["startsAlways"]) == sc.STARTS_ALWAYS
    assert _chars(v["signs"]) == sc.SIGNS
    assert _chars(v["leading"]) == sc.LEADING


@pytest.mark.parametrize("case", _vector("spreadsheet-cells.json")["cases"], ids=lambda c: c["name"])
def test_spreadsheet_cell(case: dict[str, str]) -> None:
    assert sc.spreadsheet_cell(case["in"]) == case["out"]
    assert sc.spreadsheet_cell(sc.spreadsheet_cell(case["in"])) == sc.spreadsheet_cell(case["in"])


def test_a_row_keeps_numbers_and_dates_as_values() -> None:
    import datetime as dt
    from decimal import Decimal

    when = dt.date(2026, 11, 13)
    assert sc.spreadsheet_row((-42, Decimal("-1.5"), None, when, "=1+1", "-7")) == [
        -42, Decimal("-1.5"), None, when, "'=1+1", "-7",
    ]
