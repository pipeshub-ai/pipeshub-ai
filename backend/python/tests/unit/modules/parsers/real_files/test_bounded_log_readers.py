"""The 256 KiB cap is for a .log/.cfg read as text (plain text, Markdown, code). One whose content is
a binary or structured format reaches its own parser with its original bytes, as before."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from app.config.file_types import TEXT_LOG_MAX_BYTES
from app.events.events import DedupDecision
from tests.unit.events.test_events import (
    _drain,
    _make_event_payload,
    _make_event_processor,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

KIB = 1024


def _pdf() -> bytes:
    return b"%PDF-1.4\n" + b"%" + b"x" * (600 * KIB) + b"\n%%EOF\n"


def _png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * (2 * KIB)


def _json() -> bytes:
    return json.dumps({"rows": [{"id": i, "note": "n" * 40} for i in range(10_000)]}).encode()


def _yaml() -> bytes:
    return "".join(f"key{i}: value {i}\n" for i in range(40_000)).encode()


def _csv() -> bytes:
    return "".join(f"{i},name {i},value\n" for i in range(40_000)).encode()


def _text() -> bytes:
    return "".join(f"2026-11-14 line {i} " + "x" * 50 + "\n" for i in range(20_000)).encode()


async def _seen_bytes(ext: str, mime: str, content: bytes) -> tuple[str, bytes]:
    ep, _, processor, gp = _make_event_processor()
    gp.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
    seen: dict[str, object] = {}

    def spy(name: str, key: str) -> Callable[..., AsyncIterator[None]]:
        async def run(*_a: object, **kw: object) -> AsyncIterator[None]:
            seen["handler"], seen["bytes"] = name, kw.get(key, _a[1] if len(_a) > 1 else None)
            return
            yield
        return run

    for name, key in [
        ("process_structured_document", "file_content"), ("process_delimited_document", "file_binary"),
        ("process_image", "content"), ("process_txt_document", "txt_binary"), ("process_md_document", "md_binary"),
        ("process_code_document", "code_binary"), ("process_plain_text_document", "text_binary"),
    ]:
        setattr(processor, name, spy(name, key))
    with patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock,
                      return_value=DedupDecision(virtual_record_id=None, skip_indexing=False)), \
         patch.object(ep, "_dispatch_pdf_binary", side_effect=spy("_dispatch_pdf_binary", "pdf_binary")):
        await _drain(ep.on_event(_make_event_payload(
            extension=ext, mime_type=mime, buffer=content, record_name=f"big.{ext}",
        )))
    data = seen["bytes"]
    return str(seen["handler"]), data.encode() if isinstance(data, str) else bytes(data)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mime", "make", "handler"),
    [
        ("application/pdf", _pdf, "_dispatch_pdf_binary"),
        ("image/png", _png, "process_image"),
        ("application/json", _json, "process_structured_document"),
        ("application/yaml", _yaml, "process_structured_document"),
        ("text/csv", _csv, "process_delimited_document"),
    ],
)
@pytest.mark.parametrize("ext", ["log", "cfg"])
async def test_a_binary_or_structured_log_reaches_its_parser_with_its_own_bytes(
    ext: str, mime: str, make: object, handler: str,
) -> None:
    content = make()  # type: ignore[operator]
    assert len(content) > TEXT_LOG_MAX_BYTES
    seen_handler, seen = await _seen_bytes(ext, mime, content)
    assert seen_handler == handler
    assert seen == content


@pytest.mark.parametrize(
    ("mime", "handler"),
    [("text/plain", "process_txt_document"), ("text/markdown", "process_md_document"),
     ("application/javascript", "process_code_document"), ("application/octet-stream", "process_plain_text_document")],
)
async def test_a_log_read_as_text_is_capped(mime: str, handler: str) -> None:
    seen_handler, seen = await _seen_bytes("log", mime, _text())
    assert seen_handler == handler
    assert len(seen) <= TEXT_LOG_MAX_BYTES
