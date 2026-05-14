"""Enterprise search/conversation IT fixtures.

Provisions a KB at session start by downloading a known PDF from the public
pipeshub-ai/integration-test GitHub repo and uploading it via the existing
``/api/v1/knowledgeBase/{kbId}/upload`` endpoint. The KB is deleted on teardown.

To swap PDFs, update ASANA_PDF_BLOB_URL below — accepts either a github.com
/blob/ URL (gets converted to the raw URL) or a raw URL directly.
"""

from __future__ import annotations

import io
import logging
import mimetypes
from urllib.parse import unquote, urlparse

import pytest
import requests

from messaging.test_e2e_record_pipeline import (
    KBClient,
    TERMINAL_STATUSES,
    _extract_kb_id,
    _extract_record_id,
    _get_record_status,
    poll_until,
)
from pipeshub_client import PipeshubClient

logger = logging.getLogger("enterprise-search-conftest")

ASANA_PDF_BLOB_URL = (
    "https://github.com/pipeshub-ai/integration-test/blob/main/"
    "sample-data/entities/enterprise-search/"
    "Asana%20Disaster%20Recovery%20Summary%20Report%20(2023-08).pdf"
)

INDEX_TIMEOUT_SEC = 180
INDEX_POLL_INTERVAL_SEC = 3


def _github_blob_to_raw(blob_url: str) -> str:
    parsed = urlparse(blob_url)
    if parsed.netloc == "raw.githubusercontent.com":
        return blob_url
    if parsed.netloc != "github.com" or "/blob/" not in parsed.path:
        raise ValueError(f"Not a GitHub blob URL: {blob_url}")
    new_path = parsed.path.replace("/blob/", "/", 1)
    return f"https://raw.githubusercontent.com{new_path}"


def _fetch_url_bytes(
    raw_url: str, preferred_name: str | None = None,
) -> tuple[bytes, str, str]:
    u = urlparse(raw_url.strip())
    if u.scheme not in ("http", "https"):
        raise ValueError(f"Only http(s) URLs supported, got {u.scheme!r}")

    resp = requests.get(raw_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    buffer = resp.content

    fallback = unquote(u.path.rsplit("/", 1)[-1]) or "file"
    originalname = (
        (preferred_name or fallback).replace("/", "").replace("\\", "")[:255]
        or "file"
    )

    mimetype, _ = mimetypes.guess_type(originalname)
    if not mimetype:
        ct = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        mimetype = ct or "application/octet-stream"

    return buffer, originalname, mimetype


@pytest.fixture(scope="session")
def session_kb(pipeshub_client: PipeshubClient, ai_models_configured):
    """Session-scoped KB with the Asana DR PDF uploaded and indexed.

    Yields ``{"kb_id": str, "record_id": str}``. Deletes the KB on teardown.
    """
    kb_client = KBClient(pipeshub_client)

    kb_resp = kb_client.create_kb(name="enterprise-search-it-kb")
    kb_id = _extract_kb_id(kb_resp)
    assert kb_id, f"KB create returned no id: {kb_resp}"
    logger.info("Created KB %s for enterprise search IT", kb_id)

    try:
        raw_url = _github_blob_to_raw(ASANA_PDF_BLOB_URL)
        buffer, originalname, mimetype = _fetch_url_bytes(raw_url)

        # Upload directly so the multipart tuple carries the real mimetype
        # (KBClient.upload_file hardcodes text/plain).
        files = [("files", (originalname, io.BytesIO(buffer), mimetype))]
        resp = requests.post(
            kb_client._url(f"/{kb_id}/upload"),
            headers=kb_client._headers(content_type=None),
            files=files,
            timeout=pipeshub_client.timeout_seconds,
        )
        upload_resp = pipeshub_client._handle_response(resp)
        record_id = _extract_record_id(upload_resp)
        assert record_id, f"Upload returned no record id: {upload_resp}"
        logger.info(
            "Uploaded %s (%s, %d bytes) to KB %s, record %s",
            originalname, mimetype, len(buffer), kb_id, record_id,
        )

        def _is_indexed() -> bool:
            return _get_record_status(kb_client.get_record(record_id)) in TERMINAL_STATUSES

        poll_until(
            _is_indexed,
            timeout=INDEX_TIMEOUT_SEC,
            interval=INDEX_POLL_INTERVAL_SEC,
            description=f"record {record_id} to finish indexing",
        )

        final_status = _get_record_status(kb_client.get_record(record_id))
        assert final_status == "COMPLETED", (
            f"PDF reached terminal status {final_status!r}, expected COMPLETED. "
            f"Search/conversation tests will not have any data to query."
        )

        yield {"kb_id": kb_id, "record_id": record_id}
    finally:
        try:
            kb_client.delete_kb(kb_id)
            logger.info("Deleted KB %s", kb_id)
        except Exception as e:
            logger.warning("Failed to delete KB %s: %s", kb_id, e)
