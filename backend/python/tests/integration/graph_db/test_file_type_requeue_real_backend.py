"""The file-type re-queue on a real graph store: exactly the refused records the indexer reads now move, once.

Runs in backend-matrix on both graph jobs (NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD) and, where PG_CORE_URL names a migrated database (PG_POOLER=none), on the
PostgreSQL graph store.
"""
from __future__ import annotations

import contextlib
import logging
import os
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.sources.network_share.entry import DirectoryEntry
from app.connectors.sources.network_share.operations import requeue_parked_records
from app.core.org_context import for_org
from app.migrations.file_type_requeue.migration import (
    FLAG_KEY,
    count_still_selected,
    run_file_type_requeue,
)
from app.models.entities import FileRecord, RecordType
from app.modules.indexing.connector_off_events import CONNECTOR_OFF
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

ARANGO_DB = "file_type_requeue_it"
RECORDS = CollectionNames.RECORDS.value
FILES = CollectionNames.FILES.value
APPS = CollectionNames.APPS.value
FTNS = ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
OCTET = "application/octet-stream"
logger = logging.getLogger("file-type-requeue-it")


class _Seed:
    def __init__(self, graph: IGraphDBProvider, run: str) -> None:
        self.graph = graph
        self.run = run
        self.org = run.rjust(24, "0")  # an org id is 24 hex digits
        self.drive, self.manual, self.share, self.kb = (f"{k}-{run}" for k in ("drive", "manual", "smb", "kb"))
        self.keys: list[str] = []
        self.app_keys = [self.drive, self.manual, self.share, self.kb]

    def key(self, name: str) -> str:
        return f"{name}-{self.run}"

    async def app(self, key: str, app_type: str, group: str) -> None:
        await self.graph.batch_upsert_nodes([{
            "_key": key, "orgId": self.org, "name": key, "type": app_type, "appGroup": group, "scope": "team",
            "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }], APPS)

    async def record(self, name: str, file_name: str, connector: str, *, connector_name: Connectors = Connectors.GOOGLE_DRIVE,
                     origin: OriginTypes = OriginTypes.CONNECTOR, record_type: RecordType = RecordType.FILE,
                     mime_type: str = OCTET, **fields: Any) -> str:  # noqa: ANN401
        key = self.key(name)
        record = FileRecord(
            id=key, org_id=self.org, record_name=file_name, record_type=record_type, external_record_id=f"ext-{key}",
            external_record_group_id="docs", version=0, origin=origin, connector_name=connector_name, connector_id=connector,
            mime_type=mime_type, is_file=True, extension=file_name.rsplit(".", 1)[-1], path=file_name, size_in_bytes=100,
            indexing_status=FTNS, reason="PipesHub can't read this type of file yet.", **fields,
        )
        assert await self.graph.batch_upsert_nodes([record.to_arango_base_record()], RECORDS)
        assert await self.graph.batch_upsert_nodes([record.to_arango_record()], FILES)
        assert await self.graph.batch_create_edges(
            [{"from_id": key, "from_collection": RECORDS, "to_id": key, "to_collection": FILES, "createdAtTimestamp": 1}],
            collection=CollectionNames.IS_OF_TYPE.value,
        )
        self.keys.append(key)
        return key

    async def doc(self, key: str) -> dict[str, Any]:
        with for_org(self.org, job="test"):
            doc = await self.graph.get_document(key, RECORDS)
        assert doc, key
        return doc


async def _postgres() -> tuple[IGraphDBProvider, Any]:
    from app.core.db import DbConfig, KernelDB
    from app.services.graph_db.postgres.provider import PostgresGraphProvider

    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    return PostgresGraphProvider(logger, db), db


@pytest.fixture(params=["neo4j", "arango", "postgres"])
async def seed(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Seed]:
    monkeypatch.delenv("FILE_TYPE_REQUEUE", raising=False)
    monkeypatch.delenv("FILE_TYPES_DISABLED", raising=False)
    async with contextlib.AsyncExitStack() as cleanup:
        if request.param == "postgres":
            if not os.environ.get("PG_CORE_URL"):
                pytest.skip("PG_CORE_URL is not set")
            monkeypatch.setenv("GRAPH_SCHEMA_ENFORCEMENT", "off")
            graph, db = await _postgres()
            cleanup.push_async_callback(db.close)
        else:
            try:
                graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
            except Exception as exc:
                backend_unavailable(request.param, exc)
            if (disconnect := getattr(graph, "disconnect", None)) is not None:
                cleanup.push_async_callback(disconnect)

        s = _Seed(graph, uuid.uuid4().hex[:10])

        async def remove() -> None:
            with contextlib.suppress(Exception), for_org(s.org, job="test"):
                if isinstance(graph, Neo4jProvider):
                    await graph.client.execute_query("MATCH (n) WHERE n.id ENDS WITH $run DETACH DELETE n", parameters={"run": s.run})
                    return
                await graph.delete_nodes_and_edges(s.keys, RECORDS)
                await graph.delete_nodes_and_edges(s.keys, FILES)
                await graph.delete_nodes(s.app_keys, APPS)

        cleanup.push_async_callback(remove)
        with for_org(s.org, job="test"):
            await s.app(s.drive, "Drive", "Google Workspace")
            await s.app(s.manual, "Drive", "Google Workspace")
            await s.app(s.share, "SMB", "Network Share")
            await s.app(s.kb, "KB", "Local Storage")
            await s.record("docm", "plan.docm", s.drive)
            await s.record("zip", "bundle.zip", s.drive)
            await s.record("trashed", "old.docm", s.drive, is_deleted=True)
            await s.record("placeholder", "stub.docm", s.drive, is_placeholder=True)
            await s.record("kb-upload", "upload.docm", s.kb, connector_name=Connectors.KNOWLEDGE_BASE, origin=OriginTypes.UPLOAD)
            await s.record("manual", "plan.docm", s.manual)
            # Not a FILE: only the manual-mode rule parks it.
            await s.record("manual-code", "macro.docm", s.manual, record_type=RecordType.CODE_FILE)
            await s.record("share", "plan.docm", s.share, connector_name=Connectors.SMB)
        yield s


class _Config:
    def __init__(self, values: dict[str, Any]) -> None:
        self.values = values

    async def get_config(self, key: str, use_cache: bool = True, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.values[key] = value
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return sorted(k for k in self.values if k.startswith(directory))


class _Kafka:
    def __init__(self, *, raises: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.raises = raises

    async def publish_event(self, topic: str, event: dict[str, Any]) -> bool:
        if self.raises:
            raise ConnectionError("broker down")
        self.events.append(event)
        return True

    def sent(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for e in self.events:
            out.setdefault(e["eventType"], []).extend(e["payload"]["recordIds"])
        return out


class _Lock:
    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        return True

    async def release(self) -> None:
        pass

    async def close(self) -> None:
        pass


def _manual(connector_id: str) -> dict[str, Any]:
    return {f"/services/connectors/{connector_id}/config": {"filters": {"indexing": {"values": {
        "enable_manual_sync": {"operator": "is", "value": True, "type": "boolean"},
    }}}}}


async def _run(s: _Seed, config: _Config, kafka: _Kafka | None = None) -> _Kafka:
    kafka = kafka or _Kafka()
    await run_file_type_requeue(config, s.graph, kafka, logger, lock=_Lock(), pause_seconds=0)
    return kafka


def _mine(kafka: _Kafka, s: _Seed) -> dict[str, list[str]]:
    return {t: sorted(i for i in ids if i.endswith(s.run)) for t, ids in kafka.sent().items() if any(i.endswith(s.run) for i in ids)}


async def test_two_runs_send_each_readable_record_once_and_move_nothing_else(seed: _Seed) -> None:
    s = seed
    config = _Config(_manual(s.manual))
    before = {k: await s.doc(k) for k in s.keys}

    first = await _run(s, config)

    assert _mine(first, s) == {
        "drive.reindex": [s.key("docm")], "kb.reindex": [s.key("kb-upload")], "smb.reindex": [s.key("share")],
    }
    for name in ("docm", "kb-upload"):
        doc = await s.doc(s.key(name))
        assert doc["indexingStatus"] == ProgressStatus.QUEUED.value and doc.get("reason") is None, name
        assert isinstance(doc.get("queuedAtTimestamp"), int), name
    for name in ("manual", "manual-code", "share"):
        doc = await s.doc(s.key(name))
        assert doc["indexingStatus"] == ProgressStatus.AUTO_INDEX_OFF.value and doc.get("reason") is None, name
    moved = {s.key(n) for n in ("docm", "kb-upload", "manual", "manual-code", "share")}
    for key in set(s.keys) - moved:
        doc = await s.doc(key)
        assert doc["indexingStatus"] == FTNS and doc.get("reason") == before[key].get("reason"), key
    assert await count_still_selected(s.graph) == 0

    second = await _run(s, config)
    config.values.pop(FLAG_KEY)
    third = await _run(s, config)

    assert _mine(second, s) == {} and _mine(third, s) == {}
    for key in s.keys:
        assert (await s.doc(key))["indexingStatus"] == (
            ProgressStatus.QUEUED.value if key in {s.key("docm"), s.key("kb-upload")}
            else ProgressStatus.AUTO_INDEX_OFF.value if key in moved else FTNS
        ), key


async def _sweep_pages(s: _Seed) -> set[str]:
    """What the stranded-record sweep reads for the org: QUEUED, NOT_STARTED, and what a turn-off parked."""
    seen: set[str] = set()
    with for_org(s.org, job="test"):
        for status in (ProgressStatus.QUEUED.value, ProgressStatus.NOT_STARTED.value):
            page = await s.graph.get_documents_paginated(
                RECORDS, limit=1000, filters={"indexingStatus": status, "orgId": s.org}, sort_field="_key", raise_on_error=True,
            )
            seen |= {d.get("_key") or d.get("id") for d in page}
        page = await s.graph.get_documents_paginated(
            RECORDS, limit=1000, sort_field="_key", raise_on_error=True, after_key="",
            filters={"connectorId": s.share, "indexingStatus": ProgressStatus.AUTO_INDEX_OFF.value, "reason": CONNECTOR_OFF, "orgId": s.org},
        )
        seen |= {d.get("_key") or d.get("id") for d in page}
    return seen


async def test_a_lost_share_event_leaves_the_file_out_of_the_sweep_until_it_is_read_on_disk(seed: _Seed) -> None:
    s = seed
    await _run(s, _Config({}))
    share_key = s.key("share")

    # The event never reached the connector: nothing re-reads the file by itself.
    assert share_key not in await _sweep_pages(s)

    with for_org(s.org, job="test"):
        typed = await s.graph.get_typed_records_batch([share_key], raise_on_error=True)
        source = MagicMock()
        source.stat = AsyncMock(return_value=DirectoryEntry(
            name="plan.docm", is_directory=False, is_symlink=False, size=100, created_time=None,
            last_write_time=None, file_id=1,
        ))
        processor = MagicMock()
        processor.data_store_provider = s.graph
        processor.reindex_existing_records = AsyncMock(return_value=[])  # the record event is lost
        from app.connectors.core.registry.filters import FilterCollection

        sent = await requeue_parked_records(
            data_source=source, processor=processor, records=[typed[share_key]],
            indexing_filters=FilterCollection(), logger=logger,
        )

    assert sent == []
    assert (await s.doc(share_key))["indexingStatus"] == ProgressStatus.NOT_STARTED.value
    # On disk when it was read, so the sweep may send it again.
    assert share_key in await _sweep_pages(s)


async def test_the_page_reads_only_refused_live_records_in_key_order(seed: _Seed) -> None:
    s = seed
    with for_org(s.org, job="test"):
        first = await s.graph.list_unsupported_page(s.org, s.drive, None, 1)
        rest = await s.graph.list_unsupported_page(s.org, s.drive, first[0]["_key"], 10)
    rows = first + rest
    # The trashed record is left out; the placeholder is read and the selection leaves it.
    assert [r["_key"] for r in rows] == sorted([s.key("docm"), s.key("placeholder"), s.key("zip")])
    assert all(r["connectorId"] == s.drive and r["orgId"] == s.org for r in rows)
    assert {r["_key"]: bool(r.get("isPlaceholder")) for r in rows}[s.key("placeholder")] is True


async def test_the_move_takes_only_records_still_refused(seed: _Seed) -> None:
    s = seed
    taken, refused = s.key("docm"), s.key("zip")
    with for_org(s.org, job="test"):
        assert await s.graph.update_node(taken, RECORDS, {"indexingStatus": ProgressStatus.IN_PROGRESS.value})
        won = await s.graph.transition_unsupported([taken, refused], ProgressStatus.QUEUED.value)

    assert won == [refused]
    assert (await s.doc(taken))["indexingStatus"] == ProgressStatus.IN_PROGRESS.value
    moved = await s.doc(refused)
    assert moved["indexingStatus"] == ProgressStatus.QUEUED.value and moved.get("reason") is None


async def test_every_moved_record_is_sent_again_after_a_publish_that_failed(seed: _Seed) -> None:
    from app.modules.indexing.record_republish import republish_eligible

    s = seed
    config = _Config(_manual(s.manual))

    await _run(s, config, _Kafka(raises=True))

    queued = [s.key("docm"), s.key("kb-upload")]
    for key in queued:
        assert (await s.doc(key))["indexingStatus"] == ProgressStatus.QUEUED.value, key
    assert FLAG_KEY not in config.values
    # The stranded-record sweep would re-send the connector's record but not the upload.
    eligible = {k: republish_eligible(await s.doc(k), ProgressStatus.QUEUED.value)[0] for k in queued}
    assert eligible == {s.key("docm"): True, s.key("kb-upload"): False}

    again = await _run(s, config)

    assert _mine(again, s) == {
        "drive.reindex": [s.key("docm")], "kb.reindex": [s.key("kb-upload")], "smb.reindex": [s.key("share")],
    }
    assert FLAG_KEY in config.values


async def test_a_waiting_page_is_sent_again_once_its_back_off_is_over_not_on_every_start(
    seed: _Seed, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.migrations.file_type_requeue import migration
    from app.utils.time_conversion import get_epoch_timestamp_in_ms

    s = seed
    config = _Config(_manual(s.manual))
    start = get_epoch_timestamp_in_ms()
    now = [start]
    monkeypatch.setattr(migration, "get_epoch_timestamp_in_ms", lambda: now[0])
    first = await _run(s, config)
    assert _mine(first, s)["kb.reindex"] == [s.key("kb-upload")]

    hour = 3600 * 1000
    for minutes in (5, 30, 59):  # three restarts while the records still wait, inside the back-off
        now[0] = start + minutes * 60 * 1000
        assert _mine(await _run(s, config), s) == {}, minutes

    # The records were queued a moment after ``start`` by the store's own clock.
    now[0] = start + hour + 60 * 1000
    due = _mine(await _run(s, config), s)
    now[0] = start + hour + 2 * 60 * 1000
    after = _mine(await _run(s, config), s)

    assert due == {"drive.reindex": [s.key("docm")], "kb.reindex": [s.key("kb-upload")]}
    assert after == {}


async def test_the_record_read_raises_when_asked_and_answers_empty_otherwise(seed: _Seed) -> None:
    s = seed
    bad = [s.key("docm"), object()]  # a value the store's client cannot send: a real failed read
    with for_org(s.org, job="test"):
        assert await s.graph.get_records_by_record_ids(bad, s.org) == []
        with pytest.raises(Exception):  # noqa: B017
            await s.graph.get_records_by_record_ids(bad, s.org, raise_on_error=True)
        found = await s.graph.get_records_by_record_ids([s.key("docm")], s.org, raise_on_error=True)
    assert [d.get("_key") or d.get("id") for d in found] == [s.key("docm")]


async def test_a_failed_read_during_the_drain_keeps_the_outbox_and_the_next_start_recovers(
    seed: _Seed, monkeypatch: pytest.MonkeyPatch,
) -> None:
    s = seed
    config = _Config(_manual(s.manual))
    await _run(s, config, _Kafka(raises=True))
    entries = [k for k in config.values if k.startswith("/migrations/file_type_requeue_outbox/")]
    assert entries

    real_read = s.graph.get_records_by_record_ids

    async def failing_read(ids: list[str], org_id: str, *args: object, **kwargs: object) -> list[dict[str, Any]]:
        return await real_read([*ids, object()], org_id, *args, **kwargs)

    monkeypatch.setattr(s.graph, "get_records_by_record_ids", failing_read)
    during = await _run(s, config)
    assert _mine(during, s) == {} and FLAG_KEY not in config.values
    assert all(k in config.values for k in entries)

    monkeypatch.setattr(s.graph, "get_records_by_record_ids", real_read)
    after = await _run(s, config)
    assert _mine(after, s) == {
        "drive.reindex": [s.key("docm")], "kb.reindex": [s.key("kb-upload")], "smb.reindex": [s.key("share")],
    }


async def test_logs_and_configs_under_their_own_mime_are_queued_and_text_plain_ones_left(seed: _Seed) -> None:
    s = seed
    with for_org(s.org, job="test"):
        keys = {
            "x-log": await s.record("x-log", "app.log", s.drive, mime_type="text/x-log"),
            "x-cfg": await s.record("x-cfg", "app.cfg", s.drive, mime_type="text/x-cfg"),
            "plain-log": await s.record("plain-log", "old.log", s.drive, mime_type="text/plain"),
        }

    sent = _mine(await _run(s, _Config(_manual(s.manual))), s)

    assert sorted(sent["drive.reindex"]) == sorted([s.key("docm"), keys["x-log"], keys["x-cfg"]])
    assert (await s.doc(keys["x-log"]))["indexingStatus"] == ProgressStatus.QUEUED.value
    assert (await s.doc(keys["plain-log"]))["indexingStatus"] == FTNS
