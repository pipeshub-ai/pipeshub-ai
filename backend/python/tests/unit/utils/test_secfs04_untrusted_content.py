"""SEC-FS-04 (C6.37): the connector service serves an artifact or upload of an
active type (HTML, SVG, XML, script) as a sandboxed attachment, and every type
with nosniff, so no proxy of it shows one as a page."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.utils.untrusted_content import (
    UNTRUSTED_CONTENT_CSP,
    is_active_content_type,
    untrusted_content_headers,
)

_ROUTER = "app.connectors.api.router"


@pytest.mark.parametrize(
    "content_type",
    ["text/html", "text/html; charset=utf-8", "image/svg+xml", "application/xhtml+xml", "text/xml",
     "application/xml", "application/atom+xml", "text/javascript", "application/javascript", "TEXT/HTML"],
)
def test_sec_fs_04_active_types_are_sandboxed_attachments(content_type: str) -> None:
    assert is_active_content_type(content_type)
    assert untrusted_content_headers(content_type) == {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": UNTRUSTED_CONTENT_CSP,
        "Content-Disposition": "attachment",
    }


@pytest.mark.parametrize("content_type", ["image/png", "application/pdf", "text/plain", "text/csv", None, ""])
def test_sec_fs_04_passive_types_keep_their_preview_with_nosniff(content_type: str | None) -> None:
    assert not is_active_content_type(content_type)
    assert untrusted_content_headers(content_type) == {"X-Content-Type-Options": "nosniff"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mime", "attachment"),
    [("text/html", True), ("image/svg+xml", True), ("image/png", False)],
)
async def test_sec_fs_04_artifact_stream_carries_the_headers(mime: str, attachment: bool) -> None:
    from app.connectors.api.router import _stream_artifact_from_storage

    record = MagicMock()
    record.external_record_id = "ext-1"
    record.mime_type = mime
    config = AsyncMock()
    config.get_config = AsyncMock(return_value={"storage": {"endpoint": "http://store"}})
    with patch(f"{_ROUTER}.generate_jwt", new_callable=AsyncMock, return_value="tok"), \
         patch(f"{_ROUTER}.make_api_call", new_callable=AsyncMock, return_value={"data": b"<svg onload=x()>"}):
        response = await _stream_artifact_from_storage(record, "o1", config)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert (response.headers.get("content-disposition") == "attachment") is attachment
    assert (response.headers.get("content-security-policy") == UNTRUSTED_CONTENT_CSP) is attachment
