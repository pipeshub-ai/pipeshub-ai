"""The indexing gate for the new file types: every spelling, both record types, the stored-mime pairs
connectors write, the per-type off switch, and the download ceiling for capped text."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.config import file_types
from tests.unit.config.file_types import gen_pinned_decisions as gen

if TYPE_CHECKING:
    from app.services.messaging.kafka.handlers.record import RecordEventHandler

_NEW = ("msg", "odt", "rtf", "xlsm", "log", "cfg", "docm", "dotx", "potx")
_MIMES = {
    "msg": ("application/vnd.ms-outlook",),
    "odt": ("application/vnd.oasis.opendocument.text",),
    "rtf": ("application/rtf", "text/rtf", "application/x-rtf"),
    "xlsm": ("application/vnd.ms-excel.sheet.macroEnabled.12",),
    "docm": ("application/vnd.ms-word.document.macroEnabled.12",),
    "dotx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.template",),
    "potx": ("application/vnd.openxmlformats-officedocument.presentationml.template",),
}


def _forms(value: str) -> list[str]:
    return [value, value.upper(), f".{value}"]


@pytest.mark.parametrize("record_type", ["FILE", "CODE_FILE"])
@pytest.mark.parametrize("stored_mime", [None, "unknown", "application/octet-stream", "text/plain"])
@pytest.mark.parametrize("ext", _NEW)
async def test_every_extension_spelling_passes_the_gate(ext: str, stored_mime: str | None, record_type: str) -> None:
    for form in _forms(ext):
        assert await gen.gate(form, stored_mime, f"Report.{ext}", record_type) == "ok", form


@pytest.mark.parametrize("name", ["file", "Report.bin"])
@pytest.mark.parametrize("mime", [m for ms in _MIMES.values() for m in ms])
async def test_every_mime_spelling_passes_the_gate_without_an_extension(mime: str, name: str) -> None:
    for form in (mime, mime.lower(), mime.upper(), f"{mime}; charset=utf-8"):
        assert await gen.gate(None, form, name, "FILE") == "ok", form


@pytest.mark.parametrize("ext", ["zip", "pptm", "dotm", "xltx", "eml", "ods"])
async def test_types_still_not_indexed_are_refused(ext: str) -> None:
    decision = await gen.gate(ext, "application/octet-stream", f"f.{ext}", "FILE")
    assert decision.startswith("FILE_TYPE_NOT_SUPPORTED|"), decision


async def test_a_name_derived_extension_is_matched_as_before_except_for_the_new_types() -> None:
    # The dispatch only sees the record's own extension; the name decides only for the types
    # whose extension wins over the mime.
    assert (await gen.gate("unknown", None, "Report.DOCX", "FILE")).startswith("FILE_TYPE_NOT_SUPPORTED")
    assert await gen.gate("unknown", None, "Report.docx", "FILE") == "ok"
    assert await gen.gate("unknown", None, "build.LOG", "FILE") == "ok"


def _handler_with_config(config: object, record: dict[str, Any]) -> RecordEventHandler:
    handler = gen._gate_handler(record)
    handler.config_service.get_config = AsyncMock(return_value=config)
    return handler


async def _gate_with(handler: RecordEventHandler, record: dict[str, Any], ext: str, mime: str | None) -> str:
    from app.services.messaging.config import IndexingEvent

    payload = {
        "recordId": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "connectorId": "conn-1",
        "recordName": f"f.{ext}", "extension": ext, "mimeType": mime, "signedUrl": "https://example.invalid/f",
    }
    gen_ = handler.process_event("newRecord", payload)
    try:
        async for event in gen_:
            if event.event == IndexingEvent.START_DOWNLOAD:
                return "ok"
    finally:
        await gen_.aclose()
    return f"{record.get('indexingStatus')}|{record.get('reason')}"


def _record(ext: str, **extra: object) -> dict[str, Any]:
    return {
        "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "recordType": "FILE",
        "recordName": f"f.{ext}", "connectorId": "conn-1", "indexingStatus": "QUEUED", **extra,
    }


async def test_a_type_turned_off_is_refused_and_the_others_are_not() -> None:
    record = _record("msg")
    handler = _handler_with_config({"disabled": ["msg", "nonsense"]}, record)
    decision = await _gate_with(handler, record, "msg", "application/vnd.ms-outlook")
    assert decision.startswith("FILE_TYPE_NOT_SUPPORTED|")
    record = _record("docm")
    assert await _gate_with(_handler_with_config({"disabled": ["msg"]}, record), record, "docm", None) == "ok"


async def test_turning_off_a_type_the_extension_decides_wins_over_a_text_plain_mime() -> None:
    record = _record("docm")
    handler = _handler_with_config({"disabled": ["docm"]}, record)
    assert (await _gate_with(handler, record, "docm", "text/plain")).startswith("FILE_TYPE_NOT_SUPPORTED")


async def test_the_environment_seeds_the_off_switch_when_the_store_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE_TYPES_DISABLED", "rtf, log")
    record = _record("rtf")
    handler = _handler_with_config(None, record)
    assert (await _gate_with(handler, record, "rtf", None)).startswith("FILE_TYPE_NOT_SUPPORTED")
    record = _record("msg")
    assert await _gate_with(_handler_with_config(None, record), record, "msg", None) == "ok"


async def test_an_unreadable_off_switch_turns_nothing_off() -> None:
    record = _record("msg")
    handler = gen._gate_handler(record)
    handler.config_service.get_config = AsyncMock(side_effect=ConnectionError("kv down"))
    assert await _gate_with(handler, record, "msg", None) == "ok"


async def test_the_off_switch_is_read_once_a_minute() -> None:
    record = _record("msg")
    handler = _handler_with_config({"disabled": []}, record)
    await handler._disabled_file_types()
    await handler._disabled_file_types()
    assert handler.config_service.get_config.await_count == 1


@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_capped_text_past_the_ceiling_is_refused_before_the_download(ext: str) -> None:
    record = _record(ext, sizeInBytes=file_types.TEXT_MAX_DOWNLOAD_BYTES + 1)
    handler = gen._gate_handler(record)
    decision = await _gate_with(handler, record, ext, "application/octet-stream")
    assert decision == f"FILE_TYPE_NOT_SUPPORTED|This .{ext} file is larger than 256 MB, so PipesHub doesn't read it."


@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_a_text_plain_log_main_indexed_has_no_new_ceiling(ext: str) -> None:
    """D-16: main took a .log stored as text/plain whatever its size; it still does."""
    record = _record(ext, sizeInBytes=file_types.TEXT_MAX_DOWNLOAD_BYTES + 1)
    assert await _gate_with(gen._gate_handler(record), record, ext, "text/plain") == "ok"


async def test_the_ceiling_is_only_for_capped_text() -> None:
    record = _record("docm", sizeInBytes=file_types.TEXT_MAX_DOWNLOAD_BYTES + 1)
    assert await _gate_with(gen._gate_handler(record), record, "docm", None) == "ok"


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({"mime": "text/plain", "extension": "docm"}, ("docm", True)),
        ({"mime": "application/pdf", "extension": "pdf"}, ("pdf", True)),
        ({"mime": "application/vnd.ms-outlook", "extension": None}, ("msg", True)),
        ({"mime": "application/zip", "extension": "zip"}, (None, False)),
    ],
)
def test_the_decision_names_the_type_that_will_read_the_file(kw: dict[str, Any], expected: tuple[str | None, bool]) -> None:
    decision = file_types.gate_decision(
        **kw, record_name=None, is_code_file=False, code_file_extension=None
    )
    assert (decision.file_type.key if decision.file_type else None, decision.supported) == expected


def test_the_off_switch_reads_only_registry_keys() -> None:
    assert file_types.disabled_keys({"disabled": ["MSG", "rtf", "nope", 3]}) == {"msg", "rtf"}
    assert file_types.disabled_keys({"disabled": "msg"}) == frozenset()
    assert file_types.disabled_keys(None) == frozenset()
    assert file_types.disabled_from_env("") is None
    assert file_types.disabled_from_env(" msg ,, rtf") == {"disabled": ["msg", "rtf"]}
