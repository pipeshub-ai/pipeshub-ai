"""A new .log/.cfg is stored under a mime the old text path never read, so it takes the capped
plain-text route; a row already stored as text/plain keeps the text path it had (D-16)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import file_types
from app.config.file_types import Prep, Route
from app.connectors.sources.network_share.record_mapper import mime_for

_STORED_BEFORE = "text/plain"


def _route(ext: str, mime: str) -> file_types.Dispatch | None:
    return file_types.legacy_route(mime, ext, code_extension=ext, reads_as_code_file=False)


@pytest.mark.parametrize("ext", ["log", "cfg"])
def test_a_kb_upload_is_stored_under_its_own_mime_and_takes_the_capped_route(ext: str) -> None:
    mime = file_types.node_upload_table()[ext]
    assert mime != _STORED_BEFORE
    dispatch = _route(ext, mime)
    assert dispatch is not None and dispatch.route is Route.PLAIN_TEXT
    assert dispatch.file_type is not None and dispatch.file_type.prep is Prep.CAPPED_TEXT
    decision = file_types.gate_decision(
        mime=mime, extension=ext, record_name=f"a.{ext}", is_code_file=False, code_file_extension=None,
        size_bytes=file_types.TEXT_MAX_DOWNLOAD_BYTES + 1,
    )
    assert decision.refusal == "too_large"


@pytest.mark.parametrize("ext", ["log", "cfg"])
def test_a_new_share_file_takes_the_capped_route(ext: str) -> None:
    mime = mime_for(f"Shared/server.{ext}", is_directory=False)
    assert mime != _STORED_BEFORE
    dispatch = _route(ext, mime)
    assert dispatch is not None and dispatch.route is Route.PLAIN_TEXT


@pytest.mark.parametrize("ext", ["log", "cfg"])
def test_a_row_stored_as_text_plain_keeps_the_text_path(ext: str) -> None:
    dispatch = _route(ext, _STORED_BEFORE)
    assert dispatch is not None and dispatch.route is Route.TXT
    assert file_types.parse_format(_STORED_BEFORE, ext) == "txt"


@pytest.mark.parametrize("ext", ["log", "cfg"])
def test_a_resync_keeps_the_mime_a_row_was_stored_under(ext: str) -> None:
    """The next sync of a share file stored as text/plain must not read as a file-type change
    (which would re-index it onto the new route) nor rewrite its stored mime."""
    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )

    new = SimpleNamespace(mime_type=mime_for(f"Shared/a.{ext}", is_directory=False), record_name=f"a.{ext}",
                          external_revision_id="r1", is_file=True)
    old = SimpleNamespace(mime_type=_STORED_BEFORE, record_name=f"a.{ext}", external_revision_id="r1", is_file=True)
    DataSourceEntitiesProcessor._keep_the_stored_text_mime(new, old)
    assert new.mime_type == _STORED_BEFORE
    assert not DataSourceEntitiesProcessor._content_changed(new, old)


def test_only_the_text_types_keep_a_text_plain_row() -> None:
    assert file_types.keeps_stored_mime(_STORED_BEFORE, file_types.node_upload_table()["log"], "a.log")
    assert not file_types.keeps_stored_mime("application/octet-stream", file_types.node_upload_table()["log"], "a.log")
    assert not file_types.keeps_stored_mime(_STORED_BEFORE, "application/pdf", "a.pdf")
    assert not file_types.keeps_stored_mime(_STORED_BEFORE, file_types.node_upload_table()["docm"], "a.docm")
