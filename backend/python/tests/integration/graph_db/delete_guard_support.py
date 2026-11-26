"""Scenarios of the late-purge guard and of pushed values at index time, over a store-neutral ``Lives``.

A test module supplies the store: ``Lives`` is a record that can be committed at a revision, purged (the row
gone), and pushed again under the same id. The graph reads, the record handler and the processor are the real ones.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any, Protocol
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import CollectionNames, EventTypes
from app.connectors.sources.localKB.push import contract as C
from app.events import processor as processor_module
from app.events.processor import Processor
from app.modules.record_metadata.projection import PROVIDED_PROPERTIES
from app.services.messaging.kafka.handlers import record as record_module
from tests.support.record_handler import make_record_handler

if TYPE_CHECKING:
    from collections.abc import Callable

OLD_VRID = "vr-old"
ID_KEYED = ["delete_entity", "taxonomy", "paid_delete", "paid_repurge", "code_edge"]
VRID_KEYED = ["bulk_delete_embeddings", "hand_over"]


class Lives(Protocol):
    graph: Any
    org_id: str
    record_id: str

    async def commit(self, revision: int) -> bytes:
        """Push revision ``n`` (values ``{"n": n}``): the row now names it. Returns the stored bytes a run downloads."""

    async def purge(self) -> None: ...

    async def repush(self) -> None:
        """The same key pushed again after a purge."""


def pushed_bytes(revision: int) -> bytes:
    doc = {
        "blocks": [
            {"index": 0, "type": "text", "format": "txt", "data": f"rev {revision}"}
        ],
        "block_groups": [],
        C.PUSH_FORMAT_KEY: C.PUSH_FORMAT,
        "properties": C.canonical_properties("s1", {"n": revision}),
    }
    return json.dumps(doc, sort_keys=True).encode()


# --- the handler -------------------------------------------------------------------------------------------------


class DeleteWorld:
    """The record handler over the store's graph, with fakes for every vector and storage step, logged in order."""

    def __init__(self, graph: Any, org_id: str, record_id: str) -> None:  # noqa: ANN401
        self.org_id, self.record_id = org_id, record_id
        self.calls: list[str] = []
        self.points: set[str] = set()
        handler = make_record_handler()
        handler.event_processor.graph_provider = graph
        handler._request_code_edge_build_for_deleted = AsyncMock(
            side_effect=lambda *_: self.calls.append("code_edge")
        )
        handler._hand_over_shared_copy = AsyncMock(
            side_effect=lambda *_: self.calls.append("hand_over")
        )
        handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = (
            AsyncMock(
                side_effect=lambda *a, **k: self.calls.append("bulk_delete_embeddings")
            )
        )
        store = AsyncMock()

        async def delete_entity(_org: str, _kind: str, record_id: str) -> None:
            self.calls.append("delete_entity")
            self.points.discard(record_id)

        store.delete_entity.side_effect = delete_entity
        handler.event_processor.sink_orchestrator = MagicMock(entity_vector_store=store)
        self.handler = handler
        self.paid = MagicMock(
            delete_record=AsyncMock(
                side_effect=lambda *_: self.calls.append("paid_delete")
            )
        )

    async def delete(self) -> None:
        async def taxonomy(*_a: object) -> None:
            self.calls.append("taxonomy")

        async def repurge(*_a: object, **_k: object) -> None:
            self.calls.append("paid_repurge")

        payload = {
            "recordId": self.record_id,
            "orgId": self.org_id,
            "virtualRecordId": OLD_VRID,
            "connectorId": "kb",
            "taxonomyRefs": [],
        }
        with (
            patch.object(record_module, "delete_unreached_taxonomy_points", taxonomy),
            patch.object(record_module, "schedule_paid_repurge", repurge),
            patch.object(
                record_module.paid_cache_access,
                "paid_cache_for",
                lambda *a, **k: self.paid,
            ),
            patch.object(record_module, "EMPTY_CONFIRM_DELAY_SECONDS", 0.0),
        ):
            async for _ in self.handler.process_event(
                EventTypes.DELETE_RECORD.value, payload
            ):
                pass


async def check_stale_delete_after_repush(lives: Lives) -> None:
    await lives.commit(1)
    await lives.purge()
    await lives.repush()
    world = DeleteWorld(lives.graph, lives.org_id, lives.record_id)

    await world.delete()

    assert world.calls == VRID_KEYED, world.calls


async def check_plain_delete(lives: Lives) -> None:
    await lives.commit(1)
    await lives.purge()
    world = DeleteWorld(lives.graph, lives.org_id, lives.record_id)

    await world.delete()

    assert sorted(world.calls) == sorted(VRID_KEYED + ID_KEYED), world.calls


async def check_repush_between_read_and_delete(lives: Lives) -> None:
    """The re-push commits after the guard read 'gone' and before ``delete_entity``. Its index run needs the record's
    lease, which the delete holds, so the run writes the entity point only after the delete: the point is there at the end."""
    await lives.commit(1)
    await lives.purge()
    world = DeleteWorld(lives.graph, lives.org_id, lives.record_id)
    lease = asyncio.Lock()
    runs: list[asyncio.Task] = []

    async def index_run() -> None:
        async with lease:
            world.calls.append("index_run_upsert")
            world.points.add(lives.record_id)

    real_get = lives.graph.get_document
    seen = {"n": 0}

    async def get_document(*a: object, **k: object) -> Any:  # noqa: ANN401
        found = await real_get(*a, **k)
        seen["n"] += 1
        if seen["n"] == 1:
            await lives.repush()
            runs.append(asyncio.create_task(index_run()))
        return found

    with patch.object(
        world.handler.event_processor.graph_provider,
        "get_document",
        get_document,
        create=True,
    ):
        async with lease:
            await world.delete()
    await asyncio.gather(*runs)

    assert world.calls.index("delete_entity") < world.calls.index("index_run_upsert"), (
        world.calls
    )
    assert lives.record_id in world.points, (
        "the re-created record's entity point survived"
    )


# --- pushed values at index time -----------------------------------------------------------------------------------


class _Capture:
    def __init__(self) -> None:
        self.applied: list[Any] = []

    async def apply(self, ctx: Any) -> None:  # noqa: ANN401
        self.applied.append(ctx)


async def run_index(lives: Lives, downloaded: bytes) -> Any:  # noqa: ANN401
    """One run's ``process_blocks`` over the real graph: the settings the pipeline was given."""
    processor = Processor.__new__(Processor)
    processor.logger = MagicMock()
    processor.graph_provider = lives.graph
    processor.config_service = MagicMock()
    pipeline = _Capture()
    processor._pipeline = lambda: pipeline  # type: ignore[method-assign]
    processor._process_blockgroups = AsyncMock(
        side_effect=lambda containers, _n, **_k: containers
    )  # type: ignore[method-assign]
    with patch.object(processor_module, "enhance_tables_with_llm", AsyncMock()):
        async for _ in processor.process_blocks(
            "doc", lives.record_id, 1, "KB", lives.org_id, downloaded, "vr-1"
        ):
            pass
    [ctx] = pipeline.applied
    return ctx


def values_of(ctx: Any) -> dict[str, Any] | None:  # noqa: ANN401
    provided = ctx.settings.get(PROVIDED_PROPERTIES, "unset")
    return (
        None
        if provided == "unset"
        else {k: v.value for k, v in provided.values.items()}
    )


async def check_stale_values_never_win(lives: Lives) -> None:
    first = await lives.commit(1)
    second = await lives.commit(
        2
    )  # run 1 downloaded revision 1; revision 2 is committed before its read of the row

    stale = await run_index(lives, first)
    assert values_of(stale) is None, (
        "revision-1 values must not be stamped as the newest"
    )
    assert PROVIDED_PROPERTIES not in stale.settings

    current = await run_index(lives, second)
    assert values_of(current) == {"n": 2}

    late = await run_index(lives, first)  # run 1 finishes after run 2
    assert PROVIDED_PROPERTIES not in late.settings
    assert values_of(await run_index(lives, second)) == {"n": 2}


CHECKS: dict[str, Callable[[Lives], Any]] = {
    "stale_delete_after_repush": check_stale_delete_after_repush,
    "plain_delete": check_plain_delete,
    "repush_between_read_and_delete": check_repush_between_read_and_delete,
    "stale_values_never_win": check_stale_values_never_win,
}
RECORDS = CollectionNames.RECORDS.value
