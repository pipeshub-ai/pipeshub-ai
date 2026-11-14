"""Every list derived from the registry equals the hand-kept list it replaced on main, changed only
by the reviewed additions in ``_ADDED``/``_REMOVED`` (reasons in ``delta_rules.REASONS``).

``pinned_lists.json`` holds those lists as main had them (``pin_main_lists``). The registry's own
lookups are also replayed over the whole pinned universe against the black-box snapshot, with the
reviewed deltas applied.
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
from tests.unit.config.file_types.allowed_deltas import (
    ALLOWED_DELTAS,
    ALLOWED_MIME_DELTAS,
)
from tests.unit.config.file_types.pin_main_lists import PINNED_LISTS

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_MAIN: dict[str, Any] = json.loads(PINNED_LISTS.read_text())

_NEW = ("msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx")
_GRAMMARS = ("pyi", "mts", "cts", "scala", "groovy", "gradle", "lua")
_RTF = ("application/rtf", "text/rtf", "application/x-rtf")
_NEW_MIMES = (
    "application/vnd.ms-outlook", "application/vnd.oasis.opendocument.text", *_RTF,
    "application/vnd.ms-excel.sheet.macroEnabled.12", "application/vnd.ms-word.document.macroEnabled.12",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    "application/vnd.openxmlformats-officedocument.presentationml.template",
)
_TEMPLATE_MIMES = _NEW_MIMES[-2:]
# What each list gains: the new types (D1, D7), the code grammars (D5), the xlsm hint (D8).
_ADDED: dict[str, object] = {
    "gate_mimes": _NEW_MIMES,
    "gate_extensions": (*_NEW, *_GRAMMARS),
    "_MIME_TO_FORMAT": {
        "application/vnd.ms-outlook": "msg", "application/vnd.oasis.opendocument.text": "odt",
        **dict.fromkeys(_RTF, "rtf"), "application/vnd.ms-excel.sheet.macroEnabled.12": "xlsx",
        "application/vnd.ms-word.document.macroEnabled.12": "docm",
        _TEMPLATE_MIMES[0]: "dotx", _TEMPLATE_MIMES[1]: "potx",
    },
    "_EXT_TO_FORMAT": {**{k: k for k in _NEW}, "xlsm": "xlsx"},
    "mime_to_extension": {
        "application/vnd.ms-outlook": "msg", "application/vnd.oasis.opendocument.text": "odt",
        **dict.fromkeys(_RTF, "rtf"), "application/vnd.ms-excel.sheet.macroEnabled.12": "xlsm",
        "application/vnd.ms-word.document.macroEnabled.12": "docm",
        _TEMPLATE_MIMES[0]: "dotx", _TEMPLATE_MIMES[1]: "potx",
    },
    "libreoffice_probe_inputs": ("odt", "rtf"),
}
_REMOVED: dict[str, tuple[str, ...]] = {"EXCLUDABLE_FILE_EXTENSIONS": ("log",)}


def _expected(name: str) -> object:
    main, added, removed = _MAIN[name], _ADDED.get(name), _REMOVED.get(name, ())
    if isinstance(main, dict):
        return {**main, **(added or {})}  # type: ignore[dict-item]
    values = [v for v in main if v not in removed]
    if added is None:
        return values
    return sorted({*values, *added}) if values == sorted(values) else [*values, *added]  # type: ignore[misc]


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
    assert derived() == _expected(pinned)


def _main_value_for_spelling(table: dict[str, str], mime: str) -> str | None:
    """Main's value for *mime*, or for the canonical spelling of the type it spells (D2, D3)."""
    ft = file_types._ROUTED_MIMES.get(mime)
    return table.get(mime) or (table.get(ft.mimes[0].lower()) if ft is not None else None)


def test_the_tier_lists_are_reproduced_by_the_fall_through() -> None:
    by_ext = {e: file_types.by_extension(e).tier for e in file_types._BY_EXTENSION}  # type: ignore[union-attr]
    heavy_new = {"odt", "rtf", "xlsm", "docm", "dotx", "potx"}
    light_new = {"msg", "log", "cfg"}
    assert _sorted(e for e, t in by_ext.items() if t == "heavy") == sorted({*_MAIN["HEAVY_EXTENSIONS"], *heavy_new})
    assert _sorted(e for e, t in by_ext.items() if t == "light") == sorted({*_MAIN["LIGHT_EXTENSIONS"], *light_new})
    main_by_mime = {
        **dict.fromkeys(_MAIN["HEAVY_MIME_TYPES"], "heavy"), **dict.fromkeys(_MAIN["LIGHT_MIME_TYPES"], "light"),
    }
    new_mimes = {m.lower() for m in _NEW_MIMES}
    for mime, ft in file_types._TIER_BY_MIME.items():
        expected = ft.mime_tier if mime in new_mimes else _main_value_for_spelling(main_by_mime, mime)
        assert ft.mime_tier == expected, mime
    assert set(main_by_mime) <= file_types._TIER_BY_MIME.keys()


def test_the_kind_lists_are_reproduced_by_the_fall_through() -> None:
    by_ext = {e: ft.kind for e, ft in file_types._BY_EXTENSION.items() if ft.kind}
    new = {e: by_ext[e] for e in _NEW if e != "xlsm"}
    assert dict(sorted(by_ext.items())) == dict(sorted({**_MAIN["kind_by_extension"], **new}.items()))
    new_mimes = {m.lower() for m in _NEW_MIMES}
    for mime, ft in file_types._KIND_BY_MIME.items():
        expected = ft.mime_kind if mime in new_mimes else _main_value_for_spelling(_MAIN["kind_by_mime"], mime)
        assert ft.mime_kind == expected, mime
    assert set(_MAIN["kind_by_mime"]) <= file_types._KIND_BY_MIME.keys()


def test_the_node_table_is_main_s_without_the_upper_case_duplicates() -> None:
    main = {k: v for k, v in _MAIN["node_extension_to_mime"].items() if k == k.lower()}
    added = {
        "cfg": "text/plain", "docm": "application/vnd.ms-word.document.macroEnabled.12",
        "dotx": _TEMPLATE_MIMES[0], "potx": _TEMPLATE_MIMES[1],
        "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    }
    assert file_types.node_upload_table() == dict(sorted({**main, **added}.items()))
    dropped = {k for k in _MAIN["node_extension_to_mime"] if k != k.lower()}
    assert dropped == {"PNG", "JPEG", "JPG", "GIF"}


_PINNED = gen.load()
_COLUMN = {name: i for i, name in enumerate(gen.ROW_COLUMNS)}


def test_the_registry_lookups_match_the_snapshot_over_the_pinned_universe() -> None:
    mismatches = []
    for pinned in _PINNED["rows"]:
        delta = ALLOWED_DELTAS.get(gen.case_key(pinned), {})
        row = [delta.get(col, value) for col, value in zip(gen.ROW_COLUMNS, pinned, strict=True)]
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
    for mime, pinned_ext in _PINNED["mimes"]:
        ext = ALLOWED_MIME_DELTAS.get(mime, {}).get("extension", pinned_ext)
        if file_types.extension_for_mime(mime) != ext:
            mismatches.append((None, mime, "extension_for_mime", ext, file_types.extension_for_mime(mime)))
    assert mismatches == []


def test_the_pinned_lists_are_main_s() -> None:
    assert Path(PINNED_LISTS).name == "pinned_lists.json"
    assert len(_MAIN["node_extension_to_mime"]) == 165
    assert len(_MAIN["gate_mimes"]) == 49 and len(_MAIN["gate_extensions"]) == 51
