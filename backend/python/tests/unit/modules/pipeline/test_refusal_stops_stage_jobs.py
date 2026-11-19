"""A refusal's status write carries a revision tombstone, so stage jobs still queued for the record write nothing.

Run through the real ``RecordStageIO._holding`` / ``_on_revision`` with a fake graph and leases.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.models.blocks import SemanticMetadata
from app.modules.indexing import page_limit
from app.modules.indexing.extension import LongDocument, Refuse
from app.modules.pipeline.leases import RevisionSuperseded
from tests.unit.modules.pipeline.test_classify_io_writes import (
    _BLOCKS,
    _Blob,
    _classify_io,
    _entities_io,
    _extraction,
    _job,
    _record_document,
    _sink,
)

REV = "rev-a"


class Host:
    def __init__(self, record: dict[str, Any]) -> None:
        self.record = record
        self.logger = MagicMock()
        self.processor = MagicMock()
        self.processor._mark_record = AsyncMock(side_effect=self._mark)

    async def _mark(self, record_id: str, status: ProgressStatus, reason: str | None = None, extra_fields: dict[str, Any] | None = None) -> None:
        self.record.update({"indexingStatus": status.value, "reason": reason, **(extra_fields or {})})


def _wire(io: object, record: dict[str, Any]) -> None:
    async def get_document(key: str, collection: str, *_a: object, **_k: object) -> dict[str, Any]:
        if collection == CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value:
            return {"storedRev": REV, "storedRun": "gen-1:run-1"}
        return record

    io._graph.get_document = AsyncMock(side_effect=get_document)


async def _refuse(host: Host, *, tombstone: bool) -> None:
    doc = LongDocument(
        record_id="rec-1", snapshot=dict(host.record), org_id="org-1", content=b"x", content_rev=REV, md5=None,
        extension="pdf", mime_type="application/pdf", host=host,
    )
    with patch("app.modules.indexing.unindex.unindex_record", new_callable=AsyncMock):
        if tombstone:
            await page_limit.apply_refuse(doc, Refuse(ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, "r", 600))
        else:
            host.record.update(indexingStatus=ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value)


async def _run_stale_jobs(host: Host) -> tuple[_Blob, MagicMock, list[type]]:
    blob = _Blob({"block_containers": {"blocks": [], "block_groups": []}})
    sink = _sink()
    classify = _classify_io(blob, sink=sink)
    entities = _entities_io(blob)
    for io in (classify, entities):
        _wire(io, host.record)
    outcomes: list[type] = []
    for call in (
        lambda: classify.save(_job(), ["rec-1"], SemanticMetadata(summary="s")),
        lambda: entities.save(_job("entities"), ["rec-1"], _extraction(), _BLOCKS),
    ):
        try:
            await call()
        except RevisionSuperseded:
            outcomes.append(RevisionSuperseded)
    return blob, sink, outcomes


@pytest.mark.asyncio
async def test_jobs_of_the_refused_revision_write_nothing_after_the_tombstone() -> None:
    """Same revision as the refused content: the case the first-use classify of unchanged content hits."""
    host = Host({**_record_document(REV), "indexingStatus": "QUEUED"})
    await _refuse(host, tombstone=True)
    assert host.record["contentRev"] == f"{page_limit.REFUSED_REV_PREFIX}{REV}"
    blob, sink, outcomes = await _run_stale_jobs(host)
    assert outcomes == [RevisionSuperseded, RevisionSuperseded]
    assert blob.saves == 0
    sink.write_enrichment.assert_not_awaited()
    sink.persist_named_entities.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_the_tombstone_the_same_jobs_do_write() -> None:
    """The negative control: a refusal that leaves the revision alone does not stop a same-revision job."""
    host = Host({**_record_document(REV), "indexingStatus": "QUEUED"})
    await _refuse(host, tombstone=False)
    blob, _sink_, outcomes = await _run_stale_jobs(host)
    assert outcomes == []
    assert blob.saves >= 1


def test_the_keep_rule_never_reads_the_tombstone_as_an_unchanged_revision() -> None:
    state = page_limit.content_state({"contentRev": f"{page_limit.REFUSED_REV_PREFIX}{REV}"}, REV, None)
    assert state == "uncertain"
