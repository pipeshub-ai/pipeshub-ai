"""An edition's overlay of a contract document (``contracts/harness/ee/flags.json``, ``config-surface.json``): an
extension point C-16 and C4.10 read through ``with_overlay``. It adds entries; it never redefines one."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from tests.cohesion.support import with_overlay

if TYPE_CHECKING:
    from pathlib import Path

BASE: dict[str, Any] = {
    "description": "base",
    "flags": [{"name": "A_FLAG", "default": False}],
    "settings": [{"name": "A_SETTING"}],
    "envFlagParse": {"true": ["1"]},
}


def _overlay(tmp_path: Path, name: str, document: dict[str, Any]) -> Path:
    (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")
    return tmp_path


def test_no_overlay_file_leaves_the_document_as_it_is(tmp_path: Path) -> None:
    assert with_overlay(BASE, "flags.json", tmp_path) is BASE


def test_an_overlay_adds_entries_after_the_documents_own(tmp_path: Path) -> None:
    overlay = _overlay(
        tmp_path,
        "flags.json",
        {"description": "the overlay's own", "flags": [{"name": "B_FLAG", "default": True}], "envFlagParse": {"on": []}},
    )
    merged = with_overlay(BASE, "flags.json", overlay)
    assert [f["name"] for f in merged["flags"]] == ["A_FLAG", "B_FLAG"]
    assert merged["settings"] == BASE["settings"]
    assert merged["envFlagParse"] == {"true": ["1"], "on": []}
    assert merged["description"] == "base"
    assert [f["name"] for f in BASE["flags"]] == ["A_FLAG"], "the base document is not changed"


def test_rows_are_named_by_their_setting(tmp_path: Path) -> None:
    base = {"rows": [{"setting": "One", "status": "flag"}]}
    overlay = _overlay(tmp_path, "config-surface.json", {"rows": [{"setting": "Two", "status": "flag"}]})
    assert [r["setting"] for r in with_overlay(base, "config-surface.json", overlay)["rows"]] == ["One", "Two"]
    clash = _overlay(tmp_path, "config-surface.json", {"rows": [{"setting": "One", "status": "missing"}]})
    with pytest.raises(ValueError, match="already has One"):
        with_overlay(base, "config-surface.json", clash)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({"flags": [{"name": "A_FLAG", "default": True}]}, "already has A_FLAG"),
        ({"envFlagParse": {"true": ["yes"]}}, r"already has \['true'\]"),
        ({"unknown": []}, "not a section"),
        ({"flags": {"name": "B_FLAG"}}, "a list or a map"),
    ],
)
def test_an_overlay_cannot_redefine_or_invent(tmp_path: Path, document: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        with_overlay(BASE, "flags.json", _overlay(tmp_path, "flags.json", document))

