"""The knowledge-base endpoints the ingestor needs, behind one small class."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from benchmarks.harness.errors import IngestError
from benchmarks.harness.retry import http_retry, raise_for_transient
from benchmarks.harness.systems.pipeshub.session import ConnectorApi, UserSession
from kb_upload_sse import parse_kb_upload_response

logger = logging.getLogger(__name__)

UPLOAD_TIMEOUT_S = 900
_LIST_PAGE_SIZE = 100

UploadFile = tuple[str, bytes, str]  # (filename, content, mime type)


class RecordStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_id: str
    record_name: str
    status: str
    # Present since PipesHub exposes it on the listing; falls back to a
    # per-record lookup when the backend predates that.
    virtual_record_id: str | None = None


class UploadResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    record_ids: dict[str, str]  # filename -> record id
    failed: list[str]


def _kb_id_from(payload: dict[str, Any]) -> str:
    for key in ("id", "kbId", "_key"):
        if payload.get(key):
            return str(payload[key])
    raise IngestError(f"create KB response has no id: {payload}")


class KnowledgeBaseApi:
    def __init__(self, session: UserSession, connector: ConnectorApi) -> None:
        self._session = session
        self._connector = connector

    @http_retry()
    def create_kb(self, name: str) -> str:
        resp = raise_for_transient(self._session.request("POST", "/api/v1/knowledgeBase/", json={"kbName": name}))
        if resp.status_code >= 400:
            raise IngestError(f"create KB {name!r}: HTTP {resp.status_code} {resp.text[:300]}")
        return _kb_id_from(resp.json())

    @http_retry()
    def kb_exists(self, kb_id: str) -> bool:
        resp = raise_for_transient(self._session.request("GET", f"/api/v1/knowledgeBase/{kb_id}"))
        return resp.status_code < 400

    def upload(self, kb_id: str, files: Sequence[UploadFile]) -> UploadResult:
        resp = self._session.request(
            "POST", f"/api/v1/knowledgeBase/{kb_id}/upload",
            files=[("files", (name, content, mime)) for name, content, mime in files],
            timeout=UPLOAD_TIMEOUT_S,
        )
        parsed = parse_kb_upload_response(resp)
        return UploadResult(
            record_ids={str(r["fileName"]): str(r["recordId"]) for r in parsed["records"]},
            failed=[str(f.get("fileName")) for f in parsed["failed"]],
        )

    def list_records(self, kb_id: str) -> list[RecordStatus]:
        """Every record in the KB. Paged by name: names are unique within a KB,
        so page boundaries are deterministic (by timestamp, whole upload batches
        tie and SKIP/LIMIT repeats some records while never returning others)."""
        records: dict[str, RecordStatus] = {}
        page, reported = 1, 0
        while True:
            data = self._connector.get_json(
                f"/api/v1/kb/{kb_id}/records",
                params={"page": page, "limit": _LIST_PAGE_SIZE, "sort_by": "recordName", "sort_order": "asc"},
            )
            batch = data.get("records") or []
            for r in batch:
                records[str(r["id"])] = RecordStatus(
                    record_id=str(r["id"]), record_name=str(r["recordName"]), status=str(r.get("indexingStatus") or ""),
                    virtual_record_id=str(r["virtualRecordId"]) if r.get("virtualRecordId") else None,
                )
            pagination = data.get("pagination") or {}
            reported = int(pagination.get("totalCount") or 0)
            if not batch or page >= int(pagination.get("totalPages") or page):
                break
            page += 1
        if len(records) < reported:
            raise IngestError(f"KB {kb_id}: listing returned {len(records)} unique records of {reported} reported")
        return list(records.values())

    @http_retry()
    def reindex(self, record_id: str) -> None:
        resp = raise_for_transient(self._session.request(
            "POST", f"/api/v1/knowledgeBase/reindex/record/{record_id}", json={},
        ))
        if resp.status_code >= 400:
            logger.warning("reindex %s: HTTP %s %s", record_id, resp.status_code, resp.text[:200])
