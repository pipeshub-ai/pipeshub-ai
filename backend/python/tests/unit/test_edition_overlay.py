"""``tests/edition_overlay.py``: an edition adds entries to a test's own lists, and never redefines one."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.edition_overlay import OVERLAY_DIR, extended, overlay

if TYPE_CHECKING:
    from pathlib import Path


def test_no_overlay_file_adds_nothing(tmp_path: Path) -> None:
    assert overlay("anything", tmp_path) == {}
    assert extended({"a": "why"}, "anything", "allowed", tmp_path) == {"a": "why"}


def test_an_overlay_adds_entries_after_the_lists_own(tmp_path: Path) -> None:
    (tmp_path / "funnel.json").write_text(json.dumps({"allowed": {"b": "its reason"}}), encoding="utf-8")

    assert extended({"a": "why"}, "funnel", "allowed", tmp_path) == {"a": "why", "b": "its reason"}


def test_an_overlay_cannot_redefine_an_entry(tmp_path: Path) -> None:
    (tmp_path / "funnel.json").write_text(json.dumps({"allowed": {"a": "looser"}}), encoding="utf-8")

    with pytest.raises(ValueError, match=r"already has \['a'\]"):
        extended({"a": "why"}, "funnel", "allowed", tmp_path)


def test_an_overlay_is_an_object(tmp_path: Path) -> None:
    (tmp_path / "funnel.json").write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="JSON object"):
        overlay("funnel", tmp_path)


def test_the_overlay_directory_sits_beside_the_tests() -> None:
    assert OVERLAY_DIR.parts[-3:] == ("tests", "ee", "ratchets")
