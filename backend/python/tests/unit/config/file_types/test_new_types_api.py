"""Smaller consumers of the new types: the network-share stored mime, case-insensitive mime enums,
citations for the types read like another, and the live-check fixture script."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import file_types
from app.config.constants.arangodb import ExtensionTypes, MimeTypes
from app.connectors.core.registry.connector_builder import CommonFields
from app.connectors.core.registry.filters import (
    Filter,
    FilterCollection,
    FilterType,
    MultiselectOperator,
    extension_passes_filter,
)
from app.connectors.sources.network_share.record_mapper import mime_for
from app.utils.chat_helpers import create_block_from_metadata, get_enhanced_metadata


@pytest.mark.parametrize(
    ("path", "mime"),
    [
        ("Shared/app.cfg", "text/x-cfg"),
        ("Shared/server.LOG", "text/x-log"),
        ("Shared/mail.msg", "application/vnd.ms-outlook"),
        ("Shared/notes.odt", "application/vnd.oasis.opendocument.text"),
        ("Shared/letter.rtf", "application/rtf"),
        ("Shared/budget.xlsm", "application/vnd.ms-excel.sheet.macroEnabled.12"),
        ("Shared/report.docm", "application/vnd.ms-word.document.macroEnabled.12"),
        ("Shared/form.dotx", "application/vnd.openxmlformats-officedocument.wordprocessingml.template"),
        ("Shared/deck.potx", "application/vnd.openxmlformats-officedocument.presentationml.template"),
        ("Shared/deck.pptm", "application/vnd.ms-powerpoint.presentation.macroEnabled.12"),
        # Unchanged for every other type.
        ("Shared/a.zip", "application/zip"),
        ("Shared/a.xyz", "application/octet-stream"),
        ("Shared/noextension", "application/octet-stream"),
        ("Shared/a.pdf", "application/pdf"),
    ],
)
def test_a_network_share_stores_each_new_type_under_its_own_mime(path: str, mime: str) -> None:
    assert mime_for(path, is_directory=False) == mime


def test_a_mime_enum_is_found_in_any_case() -> None:
    assert MimeTypes("Application/PDF") is MimeTypes.PDF
    assert MimeTypes("application/vnd.ms-excel.sheet.macroenabled.12") is MimeTypes.XLSM
    with pytest.raises(ValueError):
        MimeTypes("application/x-nothing")
    with pytest.raises(ValueError):
        MimeTypes(7)


@pytest.mark.parametrize(
    ("extension", "cited_as"),
    [("xlsm", "xlsx"), ("docm", "docx"), ("dotx", "docx"), ("odt", "docx"), ("rtf", "docx"),
     ("potx", "pptx"), ("pptm", "pptx"), ("msg", "html"), ("log", "txt"), ("cfg", "txt"),
     ("xlsx", "xlsx"), ("pdf", "pdf"), ("XLSX", "XLSX"), (None, None), ("weird", "weird")],
)
def test_a_new_type_is_cited_like_the_type_it_is_read_as(extension: str | None, cited_as: str | None) -> None:
    assert file_types.blocks_like(extension) == cited_as


def test_the_extension_filter_offers_pptm_with_the_indexable_types() -> None:
    ids = [option.id for option in CommonFields.file_extension_filter().options]
    assert ids.index("pptm") == ids.index("potx") + 1
    assert ids.index("pptm") < ids.index(file_types.EXCLUDE_ONLY_EXTENSIONS[0])
    assert ExtensionTypes("pptm") is ExtensionTypes.PPTM


@pytest.mark.parametrize(
    ("operator", "value", "kept"),
    [
        (MultiselectOperator.IN, ["pptm"], True),
        (MultiselectOperator.NOT_IN, ["pptm"], False),
        # Filters saved before pptm was offered decide a .pptm as they did.
        (MultiselectOperator.IN, ["pptx", "pdf"], False),
        (MultiselectOperator.NOT_IN, ["zip"], True),
        (MultiselectOperator.IN, [], True),
    ],
)
def test_a_sync_filter_decides_a_pptm_by_its_extension(
    operator: MultiselectOperator, value: list[str], kept: bool
) -> None:
    filters = FilterCollection(filters=[
        Filter(key="file_extensions", value=value, type=FilterType.MULTISELECT, operator=operator),
    ])
    assert extension_passes_filter(filters, "pptm") is kept
    assert extension_passes_filter(filters, "PPTM") is kept


def test_a_pptm_upload_is_stored_under_its_mime_and_is_indexable() -> None:
    assert file_types.node_upload_table()["pptm"] == MimeTypes.PPTM.value
    assert "pptm" in file_types.node_indexable_extensions()


def test_an_xlsm_row_is_cited_with_its_sheet_and_row() -> None:
    block = {"type": "table_row", "data": {"row_natural_language_text": "x", "row_number": 7,
                                           "sheet_name": "Budget", "sheet_number": 2}}
    meta = get_enhanced_metadata({"id": "r1"}, block, {"extension": "xlsm"})
    assert (meta["blockNum"], meta["sheetName"], meta["sheetNum"]) == ([7], "Budget", 2)


def test_a_docm_block_is_read_from_the_page_content_like_a_docx_one() -> None:
    block = create_block_from_metadata({"extension": "docm", "blockText": "stored text"}, "page content")
    assert block["data"] == "page content"


def test_the_live_check_script_writes_one_file_of_each_type(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[6] / "scripts" / "file_types" / "make_fixtures.py"
    subprocess.run([sys.executable, str(script), str(tmp_path), "--sentinel", "Check 99"], check=True,
                   capture_output=True, timeout=120)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert sorted(Path(n).suffix.lstrip(".") for n in manifest) == sorted(
        ["msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx"]
    )
    for name, entry in manifest.items():
        assert (tmp_path / name).stat().st_size == entry["bytes"] > 0
        assert file_types.gate_decision(
            mime="application/octet-stream", extension=Path(name).suffix.lstrip("."), record_name=name,
            is_code_file=False, code_file_extension=None,
        ).supported


def _repository_text() -> list[str]:
    return [e for ft in file_types.FILE_TYPES if ft.repo_code and not ft.ext_indexable for e in ft.extensions]


def test_the_repository_filter_offers_the_repository_text_types_before_the_exclude_only_ones() -> None:
    offered = [e.value for e in ExtensionTypes]
    assert file_types.filter_option_ids(repository=True) == (
        *offered, *_repository_text(), *file_types.EXCLUDE_ONLY_EXTENSIONS,
    )
    assert len(_repository_text()) == 24
    assert not set(_repository_text()) & set(file_types.filter_option_ids())


def test_an_explicit_extension_list_wins_over_the_repository_list() -> None:
    field = CommonFields.file_extension_filter(repository=True, extensions=[ExtensionTypes.PDF])
    assert [o.id for o in field.options] == ["pdf"]
