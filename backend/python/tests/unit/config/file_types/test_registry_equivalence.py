"""Every list derived from the registry equals the hand-kept list it replaced on main.

``pinned_lists.json`` holds those lists as main had them (``pin_main_lists``). The registry's own
lookups are also replayed over the whole pinned universe against the black-box snapshot.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.config import file_types
from app.config.constants import arangodb
from app.modules.parsers import encryption
from app.services.parsing import registry
from app.services.parsing.providers import local_docling_parser
from tests.unit.config.file_types import gen_pinned_decisions as gen
from tests.unit.config.file_types.pin_main_lists import PINNED_LISTS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_MAIN: dict[str, Any] = json.loads(PINNED_LISTS.read_text())


def _sorted(values: Iterable[str]) -> list[str]:
    return sorted(values)


@pytest.mark.parametrize(
    ("pinned", "derived"),
    [
        ("gate_mimes", lambda: _sorted(file_types.GATE_MIMES)),
        ("gate_extensions", lambda: _sorted(file_types.GATE_EXTENSIONS)),
        ("SUPPORTED_CODE_FILE_EXTENSIONS", lambda: _sorted(arangodb.SUPPORTED_CODE_FILE_EXTENSIONS)),
        ("CODE_FILE_MIME_TYPE_VALUES", lambda: _sorted(arangodb.CODE_FILE_MIME_TYPE_VALUES)),
        ("CODE_FILE_EXTENSION_VALUES", lambda: _sorted(arangodb.CODE_FILE_EXTENSION_VALUES)),
        ("EXCLUDABLE_FILE_EXTENSIONS", lambda: list(arangodb.EXCLUDABLE_FILE_EXTENSIONS)),
        ("FILE_MIME_TYPES", lambda: {k: v.value for k, v in arangodb.FILE_MIME_TYPES.items()}),
        ("RECONCILIATION_ENABLED_MIME_TYPES", lambda: _sorted(arangodb.RECONCILIATION_ENABLED_MIME_TYPES)),
        ("RECONCILIATION_ENABLED_EXTENSIONS", lambda: _sorted(arangodb.RECONCILIATION_ENABLED_EXTENSIONS)),
        ("_MIME_TO_FORMAT", lambda: dict(registry._MIME_TO_FORMAT)),
        ("_EXT_TO_FORMAT", lambda: dict(registry._EXT_TO_FORMAT)),
        ("mime_to_extension", lambda: dict(file_types.MIME_EXTENSION_HINTS)),
        ("OOXML_EXTENSIONS", lambda: _sorted(encryption.OOXML_EXTENSIONS)),
        ("OOXML_MIME_TYPES", lambda: _sorted(encryption.OOXML_MIME_TYPES)),
        ("docling_formats", lambda: list(local_docling_parser._SUPPORTED_FORMATS)),
        ("docling_name_ext", lambda: dict(local_docling_parser._FORMAT_EXTS)),
        ("libreoffice_probe_inputs",
         lambda: _sorted(ft.libreoffice_from for ft in file_types.FILE_TYPES if ft.libreoffice_from)),
        ("legacy_parser_extensions",
         lambda: _sorted(e for ft in file_types.FILE_TYPES if ft.legacy_parser for e in ft.extensions)),
    ],
)
def test_a_derived_list_equals_the_list_it_replaced(pinned: str, derived: Callable[[], object]) -> None:
    assert derived() == _MAIN[pinned]


def test_the_tier_lists_are_reproduced_by_the_fall_through() -> None:
    by_ext = {e: file_types.by_extension(e).tier for e in file_types._BY_EXTENSION}  # type: ignore[union-attr]
    assert _sorted(e for e, t in by_ext.items() if t == "heavy") == _MAIN["HEAVY_EXTENSIONS"]
    assert _sorted(e for e, t in by_ext.items() if t == "light") == _MAIN["LIGHT_EXTENSIONS"]
    by_mime = file_types._TIER_BY_MIME
    assert _sorted(m for m, ft in by_mime.items() if ft.mime_tier == "heavy") == _MAIN["HEAVY_MIME_TYPES"]
    assert _sorted(m for m, ft in by_mime.items() if ft.mime_tier == "light") == _MAIN["LIGHT_MIME_TYPES"]


def test_the_kind_lists_are_reproduced_by_the_fall_through() -> None:
    by_ext = {e: ft.kind for e, ft in file_types._BY_EXTENSION.items() if ft.kind}
    assert dict(sorted(by_ext.items())) == _MAIN["kind_by_extension"]
    by_mime = {m: ft.mime_kind for m, ft in file_types._KIND_BY_MIME.items()}
    assert dict(sorted(by_mime.items())) == _MAIN["kind_by_mime"]


def test_the_node_table_is_main_s_without_the_upper_case_duplicates() -> None:
    main = {k: v for k, v in _MAIN["node_extension_to_mime"].items() if k == k.lower()}
    assert file_types.node_upload_table() == main
    dropped = {k for k in _MAIN["node_extension_to_mime"] if k != k.lower()}
    assert dropped == {"PNG", "JPEG", "JPG", "GIF"}


_PINNED = gen.load()
_COLUMN = {name: i for i, name in enumerate(gen.ROW_COLUMNS)}


def test_the_registry_lookups_match_the_snapshot_over_the_pinned_universe() -> None:
    mismatches = []
    for row in _PINNED["rows"]:
        ext, mime = row[0], row[1]
        if not all(v is None or isinstance(v, str) for v in (ext, mime)):
            continue
        tier, kind = file_types.tier(ext, mime), file_types.kind(ext, mime) or "unknown"
        is_code = mime in file_types.CODE_MIMES or (
            file_types.normalize_extension(ext) in file_types.GRAMMAR_EXTENSIONS
        )
        for col, value in (("tier", tier), ("kind", kind), ("is_code_file", is_code)):
            if row[_COLUMN[col]] != value:
                mismatches.append((ext, mime, col, row[_COLUMN[col]], value))
    for mime, ext in _PINNED["mimes"]:
        if file_types.extension_for_mime(mime) != ext:
            mismatches.append((None, mime, "extension_for_mime", ext, file_types.extension_for_mime(mime)))
    assert mismatches == []


def test_the_pinned_lists_are_main_s() -> None:
    assert Path(PINNED_LISTS).name == "pinned_lists.json"
    assert len(_MAIN["node_extension_to_mime"]) == 165
    assert len(_MAIN["gate_mimes"]) == 49 and len(_MAIN["gate_extensions"]) == 51
