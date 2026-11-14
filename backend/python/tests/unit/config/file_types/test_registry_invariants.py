"""The registry's own rules, and its agreement with the enums, parsers and dispatch it describes."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.config import file_types
from app.config.constants.arangodb import ExtensionTypes, MimeTypes
from app.config.file_types import FileType, FilterRole, Route
from app.utils import libreoffice_convert
from tests.unit.config.file_types import gen_pinned_decisions as gen

_APP = Path(__file__).resolve().parents[4] / "app"
_TYPES = file_types.FILE_TYPES

# The gate accepts it, but the legacy dispatch has no branch for it (F23).
_INDEXABLE_WITHOUT_ROUTE = {"htm"}

# The legacy handler each route reaches, as the black-box snapshot records it.
_ROUTE_HANDLER = {
    Route.GOOGLE_SLIDES: "process_pptx_document",
    Route.GOOGLE_DOCS: "process_docx_document",
    Route.GOOGLE_SHEETS: "process_excel_document",
    Route.GMAIL: "process_gmail_message",
    Route.BLOCKS: "process_blocks",
    Route.PDF: "_dispatch_pdf_binary",
    Route.EPUB: "process_epub_document",
    Route.DOCX: "process_docx_document",
    Route.DOC: "process_doc_document",
    Route.XLSX: "process_excel_document",
    Route.XLS: "process_xls_document",
    Route.CSV: "process_delimited_document",
    Route.TSV: "process_delimited_document",
    Route.HTML: "process_html_document",
    Route.PPTX: "process_pptx_document",
    Route.PPT: "process_ppt_document",
    Route.MD: "process_md_document",
    Route.MDX: "process_mdx_document",
    Route.TXT: "process_txt_document",
    Route.SQL_TABLE: "process_sql_structured_data",
    Route.SQL_VIEW: "process_sql_structured_data",
    Route.JSON: "process_structured_document",
    Route.YAML: "process_structured_document",
    Route.IMAGE: "process_image",
    Route.CODE: "process_code_document",
}


def _registered_formats() -> set[str]:
    """Format keys ``parsing_main._build_registry`` registers a parser for."""
    tree = ast.parse((_APP / "parsing_main.py").read_text())
    formats: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "register" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Constant):
                formats.add(arg.value)
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            formats.update(e.value for e in node.iter.elts if isinstance(e, ast.Constant))
    return formats


def _legacy_parser_keys() -> set[str]:
    """Keys of the in-process parser set (``containers/utils/utils.py``)."""
    tree = ast.parse((_APP / "containers/utils/utils.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == "parsers":
            return {getattr(ExtensionTypes, k.value.attr).value for k in node.value.keys}  # type: ignore[union-attr]
    raise AssertionError("parsers dict not found")


def test_every_extension_and_mime_belongs_to_one_type() -> None:
    exts = [e for ft in _TYPES for e in ft.extensions]
    mimes = [file_types.normalize_mime(m) for ft in _TYPES for m in ft.mimes]
    assert len(exts) == len(set(exts))
    assert len(mimes) == len(set(mimes))
    assert len({ft.key for ft in _TYPES}) == len(_TYPES)


def test_the_import_time_checks_pass() -> None:
    assert list(file_types._problems()) == []


def test_an_indexable_type_has_a_registered_parser_and_a_route() -> None:
    registered = _registered_formats()
    assert {"pdf", "docx", "code", "png", "heif"} <= registered
    for ft in _TYPES:
        if ft.indexable:
            assert ft.format in registered, ft.key
            assert (ft.route is not None) != (ft.key in _INDEXABLE_WITHOUT_ROUTE), ft.key


def test_the_route_is_the_branch_the_legacy_dispatch_takes() -> None:
    legacy = {
        (r[0], r[1]): r[5]
        for r in gen.load()["rows"]
        if r[3] == "FILE" and not isinstance(r[0], (list, dict)) and not isinstance(r[1], (list, dict))
    }
    for ft in _TYPES:
        if ft.route is None:
            continue
        case = (ft.extensions[0], None) if ft.ext_indexable else (None, ft.canonical_mime)
        assert legacy[case] == _ROUTE_HANDLER[ft.route], ft.key


def test_the_filter_roles_follow_the_gate() -> None:
    legacy = {ft.key for ft in _TYPES if ft.filter is FilterRole.OFFER_LEGACY}
    assert legacy == {"heic", "heif", "pyi", "mts", "cts", "scala", "groovy", "gradle", "lua", "code", "blocks"}
    for ft in _TYPES:
        if ft.filter is FilterRole.OFFER:
            assert ft.ext_indexable, ft.key
        if ft.filter is FilterRole.OFFER_LEGACY:
            assert not ft.ext_indexable, ft.key
        if ft.filter is FilterRole.EXCLUDE_ONLY:
            assert not ft.indexable, ft.key


def test_acceptance_is_split_by_side_as_the_gate_splits_it() -> None:
    def get(key: str) -> FileType:
        return next(ft for ft in _TYPES if ft.key == key)

    blocks, sql_table, sql_view, code = get("blocks"), get("sql_table"), get("sql_view"), get("code")
    assert blocks.mime_indexable and not blocks.ext_indexable
    assert sql_table.ext_indexable and sql_table.mime_indexable and sql_table.filter is FilterRole.OFFER
    assert sql_view.ext_indexable and sql_view.mime_indexable and sql_view.filter is FilterRole.OFFER
    assert not code.indexable and code.internal


def test_the_extension_enum_is_exactly_what_the_filter_offers() -> None:
    assert {e.value for e in ExtensionTypes} == file_types.offered_extensions()


def test_every_mime_enum_value_is_a_registered_spelling() -> None:
    assert [m.value for m in MimeTypes if file_types.by_mime(m.value) is None] == []


def test_an_indexable_file_type_is_stored_under_a_mime_enum_value() -> None:
    values = {m.value for m in MimeTypes}
    for ft in _TYPES:
        if ft.indexable and not ft.internal:
            assert ft.canonical_mime in values, ft.key


def test_every_libreoffice_input_has_a_probe_sample() -> None:
    inputs = {ft.libreoffice_from for ft in _TYPES if ft.libreoffice_from}
    assert inputs == set(libreoffice_convert._PROBE_SAMPLES)


def test_the_legacy_parser_set_is_the_registry_s() -> None:
    expected = {e for ft in _TYPES if ft.legacy_parser for e in ft.extensions}
    assert _legacy_parser_keys() == expected


def test_the_upload_table_stores_every_indexable_extension() -> None:
    table = file_types.node_upload_table()
    assert set(file_types.node_indexable_extensions()) <= table.keys()


@pytest.mark.parametrize(
    "broken",
    [
        FileType(key="Bad", extensions=("bad",)),
        FileType(key="x", extensions=("Has Space",)),
        FileType(key="y", extensions=("y",), filter=FilterRole.OFFER),
        FileType(key="z", extensions=("z",), ext_indexable=True, filter=FilterRole.EXCLUDE_ONLY),
        FileType(key="w", extensions=("w",), mimes=("a/w",), alias_mime="text/plain"),
    ],
)
def test_the_import_time_checks_catch_a_broken_entry(broken: FileType, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(file_types, "FILE_TYPES", (*_TYPES, broken))
    assert list(file_types._problems())
