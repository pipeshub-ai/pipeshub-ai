"""`virtualRecordId` for every ingested record — the key Qdrant points carry.

KB listings don't return it, so each record is read once and the mapping is
cached next to the ingest cache (it only changes if the KB is rebuilt).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from benchmarks.harness.errors import IngestError
from benchmarks.harness.models import IngestManifest
from benchmarks.harness.store import atomic_write_text
from benchmarks.harness.systems.pipeshub.session import UserSession

logger = logging.getLogger(__name__)

_WORKERS = 8
_MAX_MISSING_RATIO = 0.005


def _virtual_record_id(payload: Mapping[str, Any]) -> str | None:
    for container in (payload, payload.get("record") or {}, payload.get("data") or {}):
        if isinstance(container, Mapping) and container.get("virtualRecordId"):
            return str(container["virtualRecordId"])
    return None


def resolve_virtual_record_ids(
    session: UserSession,
    ingest: IngestManifest,
    cache_dir: Path,
    listed: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """`virtualRecordId -> recordId` for the ingested KB.

    `listed` (recordId -> virtualRecordId straight from the KB listing) covers
    every record in one paged call; the per-record endpoint is only for
    backends whose listing omits the field — 12k of those calls trip
    PipesHub's per-minute rate limit."""
    path = cache_dir / "pipeshub" / f"vrids-{ingest.kb_id}.json"
    cached: dict[str, str] = json.loads(path.read_text()) if path.exists() else {}
    for record_id, vrid in (listed or {}).items():
        cached[vrid] = record_id
    known_records = set(cached.values())
    todo = [r.record_id for r in ingest.records if r.record_id not in known_records]

    def fetch(record_id: str) -> tuple[str, str | None]:
        resp = session.request("GET", f"/api/v1/knowledgeBase/record/{record_id}")
        if resp.status_code >= 400:
            logger.warning("record %s: HTTP %s", record_id, resp.status_code)
            return record_id, None
        return record_id, _virtual_record_id(resp.json())

    if todo:
        logger.info("resolving virtualRecordId for %d records", len(todo))
        with ThreadPoolExecutor(_WORKERS) as pool:
            for record_id, vrid in pool.map(fetch, todo):
                if vrid:
                    cached[vrid] = record_id
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(cached))
    missing = len(ingest.records) - len(set(cached.values()) & {r.record_id for r in ingest.records})
    if ingest.records and missing / len(ingest.records) > _MAX_MISSING_RATIO:
        raise IngestError(f"no virtualRecordId for {missing}/{len(ingest.records)} records")
    return cached
