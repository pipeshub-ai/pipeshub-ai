"""The documented rollback (docs/indexing-service.md 2.5) on a real ArangoDB running the previous schema.

The previous version's connector service re-applies its strict `records` schema, which lacks the newer
status values, and ArangoDB then rejects every update to a record that holds one. The statement in the
doc must take every such record, in every status field the product writes it to, back to a value that
schema accepts, in one pass: it is a single query, so one record it cannot update stops the rollback.

The previous schema is v0.10.0's status enums applied to this version's `records` schema (the other
properties are the same for the records written here).

Environment: ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import base64
import copy
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.schema.arango.documents import record_schema
from tests.integration.real_graph import (
    ARANGO_PASSWORD,
    ARANGO_URL,
    backend_unavailable,
    connect_arango,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

DB = "password_protected_rollback_it"
DOC = Path(__file__).resolve().parents[5] / "docs" / "indexing-service.md"
logger = logging.getLogger("password-protected-rollback-it")

# v0.10.0 backend/python/app/schema/arango/documents.py record_schema
V0100_ENUMS = {
    "parsingStatus": ["NOT_STARTED", "IN_PROGRESS", "FAILED", "COMPLETED", "FILE_TYPE_NOT_SUPPORTED",
                      "AUTO_INDEX_OFF", "EMPTY", "QUEUED"],
    "indexingStatus": ["NOT_STARTED", "IN_PROGRESS", "PAUSED", "FAILED", "COMPLETED", "FILE_TYPE_NOT_SUPPORTED",
                       "AUTO_INDEX_OFF", "EMPTY", "ENABLE_MULTIMODAL_MODELS", "QUEUED", "CONNECTOR_DISABLED"],
    "extractionStatus": ["NOT_STARTED", "IN_PROGRESS", "PAUSED", "FAILED", "COMPLETED", "FILE_TYPE_NOT_SUPPORTED",
                         "AUTO_INDEX_OFF", "EMPTY"],
}

# The statement as it stood before PASSWORD_PROTECTED reached parsingStatus (negative control).
AQL_WITHOUT_THE_PARSING_COPY = """
LET parsing = ["ENABLE_MULTIMODAL_MODELS", "PAUSED", "SKIPPED"]
LET extraction = ["ENABLE_MULTIMODAL_MODELS", "QUEUED", "SKIPPED"]
FOR r IN records
  FILTER HAS(r, "contentRev")
    OR r.parsingStatus IN parsing OR r.indexingStatus IN ["PASSWORD_PROTECTED", "SKIPPED"] OR r.extractionStatus IN extraction
  UPDATE r WITH {
    contentRev: null,
    parsingStatus: r.parsingStatus == "SKIPPED" ? "FAILED" : (r.parsingStatus IN parsing ? "NOT_STARTED" : r.parsingStatus),
    indexingStatus: r.indexingStatus IN ["PASSWORD_PROTECTED", "SKIPPED"] ? "FAILED" : r.indexingStatus,
    extractionStatus: r.extractionStatus == "SKIPPED" ? "FAILED" : (r.extractionStatus IN extraction ? "NOT_STARTED" : r.extractionStatus)
  } IN records OPTIONS { keepNull: false }
"""

REQUIRED = {"externalRecordId": "x", "recordType": "FILE", "origin": "UPLOAD", "createdAtTimestamp": 1, "connectorId": None}

# name -> (parsingStatus, indexingStatus, extractionStatus) as the product writes them
RECORDS = {
    "locked": ("PASSWORD_PROTECTED", "PASSWORD_PROTECTED", "SKIPPED"),
    "locked_indexing_only": (None, "PASSWORD_PROTECTED", "FAILED"),
    "locked_extraction_copy": ("PASSWORD_PROTECTED", "PASSWORD_PROTECTED", "PASSWORD_PROTECTED"),
    "stage_skipped": ("SKIPPED", "SKIPPED", "SKIPPED"),
    "needs_model": ("ENABLE_MULTIMODAL_MODELS", "ENABLE_MULTIMODAL_MODELS", "ENABLE_MULTIMODAL_MODELS"),
    "paused_queued": ("PAUSED", "QUEUED", "QUEUED"),
    "fine": ("COMPLETED", "COMPLETED", "COMPLETED"),
    "failed": ("FAILED", "FAILED", "FAILED"),
}
# What each field must read after the rollback.
EXPECTED = {
    "locked": ("FAILED", "FAILED", "FAILED"),
    "locked_indexing_only": (None, "FAILED", "FAILED"),
    "locked_extraction_copy": ("FAILED", "FAILED", "FAILED"),
    "stage_skipped": ("FAILED", "FAILED", "FAILED"),
    "needs_model": ("NOT_STARTED", "ENABLE_MULTIMODAL_MODELS", "NOT_STARTED"),
    "paused_queued": ("NOT_STARTED", "QUEUED", "NOT_STARTED"),
    "fine": ("COMPLETED", "COMPLETED", "COMPLETED"),
    "failed": ("FAILED", "FAILED", "FAILED"),
}


def _call(method: str, path: str, body: object = None, *, db: str | None = None) -> tuple[int, dict]:
    token = base64.b64encode(f"root:{ARANGO_PASSWORD}".encode()).decode()
    request = urllib.request.Request(
        ARANGO_URL + (f"/_db/{db}" if db else "") + path,
        method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def _previous_schema() -> dict:
    schema = copy.deepcopy(record_schema)
    for field, values in V0100_ENUMS.items():
        schema["rule"]["properties"][field]["enum"] = values
    return schema


def _documented_aql() -> str:
    section = DOC.read_text().split("**Rolling back to 0.10.0 on ArangoDB.**", 1)[1]
    match = re.search(r"```aql\n(.*?)```", section, re.S)
    assert match, "the rollback AQL is missing from docs/indexing-service.md"
    return match.group(1)


def _touch(key: str) -> int:
    """What any later status write is: a partial update, validated against the whole document."""
    return _call("PATCH", f"/_api/document/records/{key}", {"reason": "touched"}, db=DB)[0]


def _fields(key: str) -> tuple:
    _, doc = _call("GET", f"/_api/document/records/{key}", db=DB)
    return doc.get("parsingStatus"), doc.get("indexingStatus"), doc.get("extractionStatus")


@pytest.fixture
async def previous_version(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """This version's records written, then the previous version's schema applied over them."""
    try:
        provider = await connect_arango(logger, DB)
    except Exception as exc:
        backend_unavailable("arango", exc)
    try:
        for key, (parsing, indexing, extraction) in RECORDS.items():
            doc = {"_key": key, "recordName": f"{key}.pdf", "indexingStatus": indexing, "extractionStatus": extraction,
                   "contentRev": "abc", **REQUIRED}
            if parsing:
                doc["parsingStatus"] = parsing
            status, body = _call("POST", "/_api/document/records", doc, db=DB)
            assert status in (201, 202), body
        status, body = _call("PUT", "/_api/collection/records/properties", {"schema": _previous_schema()}, db=DB)
        assert status == 200, body
        yield
    finally:
        await provider.disconnect()
        _call("DELETE", f"/_api/database/{DB}")


async def test_the_documented_rollback_leaves_every_record_writable_under_the_previous_schema(
    previous_version: None,
) -> None:
    for key in ("locked", "locked_indexing_only", "stage_skipped", "needs_model", "paused_queued"):
        assert _touch(key) == 400, f"{key} should be unwritable under the previous schema before the rollback"
    assert _touch("fine") == 202

    status, body = _call("POST", "/_api/cursor", {"query": _documented_aql()}, db=DB)

    assert status in (200, 201), body
    for key, expected in EXPECTED.items():
        assert _fields(key) == expected, key
        assert _touch(key) == 202, key


async def test_the_statement_without_the_parsing_copy_aborts_on_a_locked_record(previous_version: None) -> None:
    status, body = _call("POST", "/_api/cursor", {"query": AQL_WITHOUT_THE_PARSING_COPY}, db=DB)

    assert status == 400, "the negative control stopped failing: the test no longer proves the fix"
    assert "does not match the record schema" in body.get("errorMessage", "")
