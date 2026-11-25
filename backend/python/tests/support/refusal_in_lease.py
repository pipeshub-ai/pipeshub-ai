"""One assertion body for the page-limit refusal run inside the consumer's held record lease, on any lease manager.

The consumer holds ``record:<id>`` for the whole event, and both managers admit one owner per key, so a refusal that
asked for the same key again under another owner would wait for itself. It must request no lease at all.

The spy sits on the manager's class and on ``RecordLeases.hold``, so a lease taken by any code path under test, through
any instance, is recorded. ``refuse_inside_held_lease(..., acquire_in_unindex=True)`` is the negative control: its
un-index step takes the record lease through ``RecordLeases``, as the removed first step of the design did.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import ProgressStatus
from app.modules.indexing import page_limit
from app.modules.indexing.page_limit import LongDocument, Refuse
from app.modules.pipeline.leases import RecordLeases
from tests.support.unindex_rig import Rig


async def refuse_inside_held_lease(manager: Any, pool: str = "record:rec-1", *, acquire_in_unindex: bool = False) -> None:  # noqa: ANN401
    record_id = pool.split(":", 1)[1]
    assert await manager.try_acquire(pool, "consumer:1", 1, 60) is True
    assert await manager.try_acquire(pool, "stage:2", 1, 60) is False, "the manager must admit one owner per key"

    requested: list[tuple] = []
    real_acquire = type(manager).try_acquire
    real_hold = RecordLeases.hold

    async def spy_acquire(self: Any, *args: Any, **kwargs: Any) -> bool:  # noqa: ANN401
        requested.append(("try_acquire", args))
        return await real_acquire(self, *args, **kwargs)

    def spy_hold(self: Any, record: str, **kwargs: Any):  # noqa: ANN202, ANN401
        requested.append(("hold", record))
        return real_hold(self, record, **kwargs)

    rig = Rig()
    written: dict[str, Any] = {}

    async def mark(rec: str, status: ProgressStatus, reason: str | None = None, extra_fields: Any = None) -> None:  # noqa: ANN401
        written.update({"status": status, "reason": reason, **(extra_fields or {})})

    rig.processor._mark_record = AsyncMock(side_effect=mark)
    host = MagicMock()
    host.processor = rig.processor
    doc = LongDocument(
        record_id=record_id, snapshot={"_key": record_id, "orgId": "org-1", "virtualRecordId": "v-1"}, org_id="org-1",
        content=b"x", content_rev="r1", md5=None, host=host,
    )

    async def unindex_that_takes_the_lease(*_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        leases = RecordLeases()
        leases.bind(manager)

        async def current() -> bool:
            return True

        try:
            async with leases.hold(record_id, owner="refusal", wait_s=0.01, still_current=current):
                pass
        except Exception:
            pass

    with (
        patch.object(type(manager), "try_acquire", spy_acquire),
        patch.object(RecordLeases, "hold", spy_hold),
        patch("app.modules.indexing.unindex.GraphDBTransformer") as transformer,
        patch("app.modules.indexing.unindex.NamedEntityGraphWriter") as writer,
        patch("app.modules.indexing.unindex.GraphDataStore"),
        patch("app.modules.indexing.unindex.delete_unreached_taxonomy_points", rig.points),
    ):
        transformer.return_value.clear_classification = rig.clear_classification
        writer.return_value.clear_for_record = rig.clear_for_record
        if acquire_in_unindex:
            with patch("app.modules.indexing.unindex.unindex_record", unindex_that_takes_the_lease):
                await page_limit.apply_refuse(doc, Refuse(ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, "reason", 600))
        else:
            await page_limit.apply_refuse(doc, Refuse(ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value, "reason", 600))

    assert requested == [], f"the refusal asked for a lease; it would wait for the consumer's own: {requested}"
    assert written["status"] is ProgressStatus.FILE_TYPE_NOT_SUPPORTED
    assert "release" in rig.calls and "classification" in rig.calls
    assert await manager.try_acquire(pool, "consumer:1", 1, 60) is True, "the consumer still holds its lease"
