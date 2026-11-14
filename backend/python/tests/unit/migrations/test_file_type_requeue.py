"""The file-type re-queue takes back exactly the refused records the indexer can read now, once."""
from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any

import pytest

from app.config import file_types
from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.migrations.file_type_requeue import gate_v1
from app.migrations.file_type_requeue.migration import (
    FLAG_KEY,
    OUTBOX_DIRECTORY,
    PAGE_SIZE,
    count_still_selected,
    run_file_type_requeue,
)
from app.models.entities import FileRecord, RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

FTNS = ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value
OCTET = "application/octet-stream"
SNAPSHOT = Path(__file__).resolve().parents[1] / "config" / "file_types" / "pinned_decisions.json"


def _file(key: str, name: str, *, ext: str | None, mime: str = OCTET, connector: str = "c-drive",
          record_type: RecordType = RecordType.FILE, status: str = FTNS, size: int | None = 100,
          **flags: bool | str) -> FileRecord:
    return FileRecord(
        id=key, org_id="org-1", record_name=name, record_type=record_type, external_record_id=f"x-{key}",
        version=0, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=connector,
        mime_type=mime, is_file=True, extension=ext, indexing_status=status, size_in_bytes=size, **flags,
    )


class FakeGraph:
    """Apps and records in memory, with the page, typed read and compare-and-set the migration uses."""

    def __init__(self, apps: list[dict[str, Any]], records: list[FileRecord]) -> None:
        self.apps = apps
        self.records = {r.id: r for r in records}
        self.pages = 0
        self.before_transition = None
        self.now = get_epoch_timestamp_in_ms()
        self.reads_fail = False

    async def get_documents_paginated(self, collection: str, limit: int, sort_field: str, after_key: str | None,
                                      raise_on_error: bool) -> list[dict[str, Any]]:
        rows = sorted(self.apps, key=lambda a: a["_key"])
        return [a for a in rows if after_key is None or a["_key"] > after_key][:limit]

    async def list_unsupported_page(self, org_id: str, connector_id: str, after_key: str | None,
                                    limit: int) -> list[dict[str, Any]]:
        self.pages += 1
        rows = sorted(
            (r for r in self.records.values()
             if r.org_id == org_id and r.connector_id == connector_id and r.indexing_status == FTNS and not r.is_deleted
             and r.id > (after_key or "")),
            key=lambda r: r.id,
        )[:limit]
        return [{"_key": r.id, "recordType": r.record_type.value, "mimeType": r.mime_type,
                 "sizeInBytes": r.size_in_bytes, "isInternal": r.is_internal, "isPlaceholder": r.is_placeholder}
                for r in rows]

    async def get_typed_records_batch(self, ids: list[str], raise_on_error: bool) -> dict[str, FileRecord]:
        return {i: self.records[i].model_copy() for i in ids if i in self.records}

    async def transition_unsupported(self, ids: list[str], to_status: str) -> list[str]:
        if self.before_transition:
            self.before_transition(ids)
        won = [i for i in ids if self.records[i].indexing_status == FTNS]
        for i in won:
            self.records[i].indexing_status = to_status
            self.records[i].reason = None
            if to_status == ProgressStatus.QUEUED.value:
                self.records[i].queued_at = self.now
        return won

    async def get_records_by_record_ids(self, ids: list[str], org_id: str, *, raise_on_error: bool = False) -> list[dict[str, Any]]:
        if self.reads_fail:
            if raise_on_error:
                raise ConnectionError("graph down")
            return []
        return [{"_key": r.id, "indexingStatus": r.indexing_status, "reason": r.reason, "isDeleted": r.is_deleted,
                 "queuedAtTimestamp": r.queued_at}
                for i in ids if (r := self.records.get(i)) is not None and r.org_id == org_id and not r.is_deleted]

    def status(self, key: str) -> str:
        return self.records[key].indexing_status


class FakeConfig:
    def __init__(self, values: dict[str, Any] | None = None, *, fail: set[str] | None = None) -> None:
        self.values = dict(values or {})
        self.fail = fail or set()
        self.saved: list[tuple[str, Any]] = []

    async def get_config(self, key: str, use_cache: bool = True, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if key in self.fail:
            # As ConfigurationService: a failed read answers the default unless asked to raise.
            if raise_on_error:
                raise ConnectionError("config store down")
            return None
        return self.values.get(key)

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        if key in self.fail:
            raise ConnectionError("config store down")
        self.values[key] = value
        self.saved.append((key, value))
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return sorted(k for k in self.values if k.startswith(directory))

    def outbox(self) -> dict[str, Any]:
        return {k: v for k, v in self.values.items() if k.startswith(OUTBOX_DIRECTORY)}


class FakeKafka:
    def __init__(self, *, ack: bool = True, raises: bool = False) -> None:
        self.events: list[dict[str, Any]] = []
        self.ack = ack
        self.raises = raises

    async def publish_event(self, topic: str, event: dict[str, Any]) -> bool:
        if self.raises:
            raise ConnectionError("broker down")
        self.events.append({"topic": topic, **event})
        return self.ack

    def ids(self) -> list[str]:
        return [i for e in self.events for i in e["payload"]["recordIds"]]


class FakeLock:
    def __init__(self, *, acquire: bool = True, refreshes: int | None = None) -> None:
        self.acquire = acquire
        self.refreshes = refreshes
        self.acquired = 0
        self.released = 0
        self.on_release = None

    async def try_acquire(self) -> bool:
        self.acquired += self.acquire
        return self.acquire

    async def refresh(self) -> bool:
        if self.refreshes is None:
            return True
        self.refreshes -= 1
        return self.refreshes >= 0

    async def release(self) -> None:
        self.released += 1
        if self.on_release:
            self.on_release()

    async def close(self) -> None:
        pass


DRIVE = {"_key": "c-drive", "orgId": "org-1", "type": "Drive", "isActive": True}


def _manual_config(connector_id: str, **values: bool) -> dict[str, Any]:
    return {f"/services/connectors/{connector_id}/config": {
        "filters": {"indexing": {"values": {k: {"operator": "is", "value": v, "type": "boolean"} for k, v in values.items()}}},
    }}


async def _run(graph: FakeGraph, *, config: FakeConfig | None = None, kafka: FakeKafka | None = None,
               lock: FakeLock | None = None, shares: tuple[str, ...] = ()) -> tuple[Any, FakeConfig, FakeKafka]:
    config = config or FakeConfig()
    kafka = kafka or FakeKafka()
    result = await run_file_type_requeue(
        config, graph, kafka, logging.getLogger("requeue-test"), lock=lock or FakeLock(),
        is_network_share=lambda t: t in shares, pause_seconds=0,
    )
    return result, config, kafka


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FILE_TYPE_REQUEUE", raising=False)
    monkeypatch.delenv("FILE_TYPES_DISABLED", raising=False)


async def test_a_type_read_now_is_queued_and_sent_once_and_the_flag_records_the_version() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm"), _file("r2", "bundle.zip", ext="zip")])

    result, config, kafka = await _run(graph)

    assert graph.status("r1") == ProgressStatus.QUEUED.value
    assert graph.status("r2") == FTNS
    assert kafka.ids() == ["r1"]
    assert kafka.events[0]["eventType"] == "drive.reindex" and kafka.events[0]["topic"] == "sync-events"
    assert kafka.events[0]["payload"]["connectorId"] == "c-drive" and kafka.events[0]["payload"]["orgId"] == "org-1"
    assert result.complete and result.requeued == 1
    flag = config.values[FLAG_KEY]
    assert flag["version"] == file_types.REGISTRY_VERSION and flag["skipped_disabled"] == [] and flag["requeued"] == 1


async def test_a_second_start_sends_nothing() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])
    _, config, kafka = await _run(graph)
    pages = graph.pages

    _, _, again = await _run(graph, config=config)

    assert again.events == [] and graph.pages == pages
    assert kafka.ids() == ["r1"]


async def test_a_run_whose_flag_was_not_saved_moves_and_sends_nothing_twice() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])
    lock = FakeLock()
    _, _, first = await _run(graph, lock=lock)

    _, _, second = await _run(graph, lock=lock)

    assert first.ids() == ["r1"] and second.events == []
    assert lock.acquired == 2 and lock.released == 2


async def test_a_flag_of_an_older_registry_runs_it_again() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])
    config = FakeConfig({FLAG_KEY: {"version": file_types.REGISTRY_VERSION - 1, "skipped_disabled": []}})

    _, config, kafka = await _run(graph, config=config)

    assert kafka.ids() == ["r1"] and config.values[FLAG_KEY]["version"] == file_types.REGISTRY_VERSION


async def test_the_switch_off_skips_it_without_a_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE_TYPE_REQUEUE", "false")
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])
    lock = FakeLock()

    result, config, kafka = await _run(graph, lock=lock)

    assert not result.ran and kafka.events == [] and config.saved == [] and lock.acquired == 0
    assert graph.status("r1") == FTNS


async def test_a_type_turned_off_waits_and_is_taken_once_it_is_on_again(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm"), _file("r2", "notes.odt", ext="odt")])
    config = FakeConfig({file_types.FILE_TYPES_CONFIG_KEY: {"disabled": ["docm"]}})

    _, config, kafka = await _run(graph, config=config)

    assert kafka.ids() == ["r2"] and graph.status("r1") == FTNS
    assert config.values[FLAG_KEY]["skipped_disabled"] == ["docm"]

    _, _, nothing = await _run(graph, config=config)
    assert nothing.events == []

    config.values[file_types.FILE_TYPES_CONFIG_KEY] = {"disabled": []}
    _, config, again = await _run(graph, config=config)
    assert again.ids() == ["r1"] and config.values[FLAG_KEY]["skipped_disabled"] == []


async def test_the_environment_turns_a_type_off_when_the_store_has_no_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FILE_TYPES_DISABLED", "docm")
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])

    _, config, kafka = await _run(graph)

    assert kafka.events == [] and config.values[FLAG_KEY]["skipped_disabled"] == ["docm"]


async def test_an_unreadable_off_switch_runs_nothing() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])
    config = FakeConfig(fail={file_types.FILE_TYPES_CONFIG_KEY})

    result, config, kafka = await _run(graph, config=config)

    assert not result.ran and kafka.events == [] and config.saved == [] and graph.status("r1") == FTNS


async def test_only_what_the_old_gate_refused_and_the_new_one_reads_is_selected() -> None:
    over = file_types.TEXT_MAX_DOWNLOAD_BYTES + 1
    records = [
        _file("a-zip", "bundle.zip", ext="zip"),
        _file("b-pdf", "Report.PDF", ext="PDF"),
        _file("c-gradle", "build.gradle", ext="gradle"),
        _file("c-gradle-text", "settings.gradle", ext="gradle", mime="text/plain"),
        _file("d-log-big", "huge.log", ext="log", size=over),
        _file("e-log", "app.log", ext="log"),
        _file("f-code-big", "big.py", ext="py", record_type=RecordType.CODE_FILE, mime="text/plain",
              reason="This file is too large to index"),
        _file("g-msg", "mail.msg", ext="msg"),
        _file("h-heic", "photo.heic", ext="heic"),
    ]
    graph = FakeGraph([DRIVE], records)

    _, _, kafka = await _run(graph)

    assert sorted(kafka.ids()) == ["b-pdf", "c-gradle", "e-log", "g-msg"]
    # settings.gradle as text/plain was read before; a status it holds now is not this gate's.
    for untouched in ("a-zip", "c-gradle-text", "d-log-big", "f-code-big", "h-heic"):
        assert graph.status(untouched) == FTNS, untouched
    assert graph.records["f-code-big"].reason == "This file is too large to index"


async def test_placeholders_internal_and_trashed_records_are_left_alone() -> None:
    records = [
        _file("p", "plan.docm", ext="docm", is_placeholder=True),
        _file("i", "plan2.docm", ext="docm", is_internal=True),
        _file("t", "plan3.docm", ext="docm", is_deleted=True),
    ]
    graph = FakeGraph([DRIVE], records)

    _, _, kafka = await _run(graph)

    assert kafka.events == [] and {graph.status(k) for k in ("p", "i", "t")} == {FTNS}


@pytest.mark.parametrize("values", [{"enable_manual_sync": True}, {"files": False}])
async def test_a_connector_that_does_not_index_files_by_itself_gets_them_for_manual_indexing(values: dict) -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])

    result, _, kafka = await _run(graph, config=FakeConfig(_manual_config("c-drive", **values)))

    assert graph.status("r1") == ProgressStatus.AUTO_INDEX_OFF.value and graph.records["r1"].reason is None
    assert kafka.events == [] and result.parked == 1 and result.requeued == 0


async def test_unreadable_connector_settings_leave_its_records_and_the_flag_unset() -> None:
    other = {"_key": "c-other", "orgId": "org-1", "type": "Drive", "isActive": True}
    graph = FakeGraph([DRIVE, other], [_file("r1", "plan.docm", ext="docm"),
                                       _file("r2", "plan.docm", ext="docm", connector="c-other")])
    config = FakeConfig(fail={"/services/connectors/c-drive/config"})

    result, config, kafka = await _run(graph, config=config)

    assert graph.status("r1") == FTNS and kafka.ids() == ["r2"]
    assert not result.complete and FLAG_KEY not in config.values


async def test_a_network_share_parks_the_records_and_hands_them_to_its_connector() -> None:
    smb = {"_key": "c-smb", "orgId": "org-1", "type": "SMB", "isActive": True}
    graph = FakeGraph([smb], [_file("r1", "plan.docm", ext="docm", connector="c-smb")])

    result, _, kafka = await _run(graph, shares=("smb",))

    assert graph.status("r1") == ProgressStatus.AUTO_INDEX_OFF.value and graph.records["r1"].reason is None
    assert kafka.ids() == ["r1"] and kafka.events[0]["eventType"] == "smb.reindex"
    assert result.parked == 1 and result.requeued == 0


async def test_a_connector_being_deleted_is_left_alone() -> None:
    deleting = {**DRIVE, "status": "DELETING"}
    graph = FakeGraph([deleting], [_file("r1", "plan.docm", ext="docm")])

    result, _, kafka = await _run(graph)

    assert kafka.events == [] and graph.status("r1") == FTNS and result.complete


async def test_a_record_the_indexer_took_meanwhile_is_not_taken_back_or_sent() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm"), _file("r2", "plan2.docm", ext="docm")])

    def indexer_moves_r1(_ids: list[str]) -> None:
        graph.records["r1"].indexing_status = ProgressStatus.IN_PROGRESS.value

    graph.before_transition = indexer_moves_r1

    _, _, kafka = await _run(graph)

    assert graph.status("r1") == ProgressStatus.IN_PROGRESS.value
    assert kafka.ids() == ["r2"]


async def test_an_event_not_sent_leaves_the_flag_unset() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])

    result, config, _ = await _run(graph, kafka=FakeKafka(ack=False))

    assert graph.status("r1") == ProgressStatus.QUEUED.value
    assert not result.complete and FLAG_KEY not in config.values
    assert [e["ids"] for e in config.outbox().values()] == [["r1"]]


async def test_a_lease_held_elsewhere_or_a_lock_backend_down_runs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])

    with caplog.at_level(logging.WARNING):
        result, config, kafka = await _run(graph, lock=FakeLock(acquire=False))

    assert not result.ran and kafka.events == [] and config.saved == [] and graph.pages == 0
    assert any("lock backend is unavailable" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)


async def test_a_lease_lost_mid_run_stops_it_without_a_flag() -> None:
    records = [_file(f"r{i:04d}", f"f{i}.docm", ext="docm") for i in range(PAGE_SIZE + 3)]
    graph = FakeGraph([DRIVE], records)

    result, config, kafka = await _run(graph, lock=FakeLock(refreshes=0))

    assert len(kafka.events) == 1 and len(kafka.ids()) == PAGE_SIZE
    assert not result.complete and FLAG_KEY not in config.values
    assert sum(graph.status(r.id) == FTNS for r in records) == 3


async def test_every_page_is_walked_and_nothing_selectable_is_left() -> None:
    records = [_file(f"r{i:04d}", f"f{i}.docm", ext="docm") for i in range(2 * PAGE_SIZE + 1)]
    graph = FakeGraph([DRIVE], records)

    _, _, kafka = await _run(graph)

    assert len(kafka.events) == 3 and sorted(kafka.ids()) == sorted(r.id for r in records)
    assert all(len(e["payload"]["recordIds"]) <= PAGE_SIZE for e in kafka.events)
    assert await count_still_selected(graph) == 0


def _snapshot_gate_rows() -> list[tuple[Any, Any, str, str, bool | None]]:
    data = json.loads(SNAPSHOT.read_text())
    cols = data["columns"]
    at = {c: cols.index(c) for c in ("extension", "mime", "name", "record_type", "gate")}
    rows = []
    for row in data["rows"]:
        gate = row[at["gate"]]
        if gate == "ok":
            expected: bool | None = True
        elif gate.startswith(f"{FTNS}|") or gate == "!AttributeError":
            # AttributeError: the gate refused it, then the refusal's reason could not be worded.
            expected = False
        elif gate.startswith("!"):
            expected = None
        else:
            continue  # a folder ends before the gate
        rows.append((row[at["extension"]], row[at["mime"]], row[at["name"]], row[at["record_type"]], expected))
    return rows


def test_the_frozen_gate_answers_as_the_gate_did_on_every_pinned_row() -> None:
    rows = _snapshot_gate_rows()
    assert len(rows) > 8000
    wrong = [
        (ext, mime, name, rt, expected)
        for ext, mime, name, rt, expected in rows
        if gate_v1.supported(extension=ext, mime=mime, record_name=name, is_code_file=rt == "CODE_FILE") is not expected
    ]
    assert wrong == []


def test_the_frozen_gate_answers_every_input() -> None:
    rng = random.Random(7)
    values: list[Any] = [None, "", "unknown", 7, [], ["pdf"], {"a": 1}, "pdf", "PDF", ".docm", "docm", "x" * 300,
                         OCTET, "text/plain", "application/pdf", "TEXT/PLAIN", "text/gmail_content", "a.b.c"]
    for _ in range(5000):
        answer = gate_v1.supported(
            extension=rng.choice(values), mime=rng.choice(values), record_name=rng.choice(values),
            is_code_file=rng.random() < 0.3,
        )
        assert answer in (True, False, None)


def test_the_frozen_gate_never_reads_the_registry() -> None:
    source = Path(gate_v1.__file__).read_text()
    assert "import" not in source.replace("from __future__ import annotations", "").replace("from typing import Final", "")


KB = {"_key": "c-kb", "orgId": "org-1", "type": "KB", "isActive": True}


def _upload(key: str) -> FileRecord:
    return _file(key, "upload.docm", ext="docm", connector="c-kb").model_copy(update={"origin": OriginTypes.UPLOAD})


@pytest.mark.parametrize("kafka", [FakeKafka(ack=False), FakeKafka(raises=True)], ids=["refused", "raised"])
async def test_an_upload_whose_event_was_not_sent_is_sent_by_the_next_start(kafka: FakeKafka) -> None:
    graph = FakeGraph([KB], [_upload("u1")])

    first, config, _ = await _run(graph, kafka=kafka)

    assert graph.status("u1") == ProgressStatus.QUEUED.value and not first.complete
    _, config, again = await _run(graph, config=config)

    assert again.ids() == ["u1"] and again.events[0]["eventType"] == "kb.reindex"
    assert FLAG_KEY in config.values


async def test_a_failure_after_the_move_is_sent_by_the_next_start() -> None:
    graph = FakeGraph([KB], [_upload("u1")])
    config = FakeConfig()

    def fail_after_move(ids: list[str]) -> None:
        graph.before_transition = None
        for i in ids:
            graph.records[i].indexing_status = ProgressStatus.QUEUED.value
        raise ConnectionError("lost between the move and the event")

    graph.before_transition = fail_after_move
    first, config, kafka = await _run(graph, config=config)
    assert graph.status("u1") == ProgressStatus.QUEUED.value and kafka.events == [] and not first.complete
    assert [e["ids"] for e in config.outbox().values()] == [["u1"]]

    _, config, again = await _run(graph, config=config)

    assert again.ids() == ["u1"]


HOUR_MS = 3600 * 1000


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """The migration's clock, in ms; tests move it by changing ``clock[0]``."""
    from app.migrations.file_type_requeue import migration

    now = [get_epoch_timestamp_in_ms()]
    monkeypatch.setattr(migration, "get_epoch_timestamp_in_ms", lambda: now[0])
    return now


async def test_a_waiting_record_is_sent_again_once_per_back_off_not_on_every_start(clock: list[int]) -> None:
    graph = FakeGraph([KB], [_upload("u1")])
    graph.now = clock[0]
    _, config, first = await _run(graph)
    assert first.ids() == ["u1"] and FLAG_KEY in config.values

    # Queued an hour and more ago, sent just now: three restarts within the hour send nothing.
    graph.records["u1"].queued_at = clock[0] - 10 * HOUR_MS
    for minutes in (1, 20, 59):
        clock[0] = graph.now + minutes * 60 * 1000
        _, config, early = await _run(graph, config=config)
        assert early.events == [], minutes

    clock[0] = graph.now + HOUR_MS
    _, config, due = await _run(graph, config=config)
    assert due.ids() == ["u1"]

    # The next wait is doubled: within it nothing, at its end exactly one more.
    sent_at = clock[0]
    for minutes in (1, 60, 119):
        clock[0] = sent_at + minutes * 60 * 1000
        _, config, again = await _run(graph, config=config)
        assert again.events == [], minutes
    clock[0] = sent_at + 2 * HOUR_MS
    _, config, second = await _run(graph, config=config)
    assert second.ids() == ["u1"]

    graph.records["u1"].indexing_status = ProgressStatus.COMPLETED.value
    _, config, done = await _run(graph, config=config)
    assert done.events == [] and config.outbox() == {}


async def test_a_record_that_never_leaves_queued_is_given_up_after_the_last_re_send(
    clock: list[int], caplog: pytest.LogCaptureFixture,
) -> None:
    from app.migrations.file_type_requeue.migration import MAX_RESENDS

    graph = FakeGraph([KB], [_upload("u1")])
    _, config, _ = await _run(graph)
    sends = 0
    for _ in range(MAX_RESENDS + 3):
        clock[0] += 30 * 24 * HOUR_MS
        _, config, kafka = await _run(graph, config=config)
        sends += len(kafka.events)

    assert sends == MAX_RESENDS and config.outbox() == {}
    assert graph.status("u1") == ProgressStatus.QUEUED.value
    assert sum("still queued after" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING) == 1


async def test_the_drain_holds_the_lease_while_it_works() -> None:
    graph = FakeGraph([KB], [_upload("u1"), _upload("u2")])
    _, config, _ = await _run(graph, kafka=FakeKafka(ack=False))

    result, config, kafka = await _run(graph, config=config, lock=FakeLock(refreshes=0))

    assert not result.complete and FLAG_KEY not in config.values and kafka.ids() == ["u1", "u2"]


async def test_a_record_that_moved_on_is_not_sent_again() -> None:
    graph = FakeGraph([KB], [_upload("u1"), _upload("u2")])
    _, config, _ = await _run(graph, kafka=FakeKafka(ack=False))
    graph.records["u1"].indexing_status = ProgressStatus.IN_PROGRESS.value

    _, config, again = await _run(graph, config=config)

    assert again.ids() == ["u2"]


async def test_an_outbox_that_cannot_be_written_moves_nothing() -> None:
    graph = FakeGraph([KB], [_upload("u1")])

    class NoOutbox(FakeConfig):
        async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
            if key.startswith(OUTBOX_DIRECTORY):
                return False
            return await super().set_config(key, value)

    result, config, kafka = await _run(graph, config=NoOutbox())

    assert graph.status("u1") == FTNS and kafka.events == [] and not result.complete and FLAG_KEY not in config.values


async def test_a_parked_share_entry_is_dropped_once_its_event_is_sent() -> None:
    smb = {"_key": "c-smb", "orgId": "org-1", "type": "SMB", "isActive": True}
    graph = FakeGraph([smb], [_file("r1", "plan.docm", ext="docm", connector="c-smb")])

    _, config, kafka = await _run(graph, shares=("smb",), kafka=FakeKafka(ack=False))
    assert [e["parked"] for e in config.outbox().values()] == [True]
    _, config, again = await _run(graph, config=config, shares=("smb",))

    assert again.ids() == ["r1"] and config.outbox() == {}


async def test_a_manual_connector_parks_records_that_are_not_files() -> None:
    code = _file("k1", "macro.docm", ext="docm", record_type=RecordType.CODE_FILE, mime="text/plain")
    graph = FakeGraph([DRIVE], [code])

    result, _, kafka = await _run(graph, config=FakeConfig(_manual_config("c-drive", enable_manual_sync=True)))

    assert graph.status("k1") == ProgressStatus.AUTO_INDEX_OFF.value and kafka.events == [] and result.parked == 1


async def test_the_flag_is_saved_before_the_lease_is_released() -> None:
    graph = FakeGraph([DRIVE], [_file("r1", "plan.docm", ext="docm")])
    config, lock = FakeConfig(), FakeLock()
    seen: list[bool] = []
    lock.on_release = lambda: seen.append(FLAG_KEY in config.values)

    await _run(graph, config=config, lock=lock)
    _, _, second = await _run(graph, config=config, lock=lock)

    assert seen[0] is True and second.events == []


async def test_a_failed_read_of_what_still_waits_keeps_the_entry_for_the_next_start() -> None:
    graph = FakeGraph([KB], [_upload("u1")])
    _, config, _ = await _run(graph, kafka=FakeKafka(ack=False))

    graph.reads_fail = True
    failed, config, nothing = await _run(graph, config=config)
    assert nothing.events == [] and not failed.complete and len(config.outbox()) == 1

    graph.reads_fail = False
    _, config, again = await _run(graph, config=config)
    assert again.ids() == ["u1"]


async def test_a_failed_read_of_an_outbox_entry_keeps_it() -> None:
    graph = FakeGraph([KB], [_upload("u1")])
    _, config, _ = await _run(graph, kafka=FakeKafka(ack=False))
    (key,) = config.outbox()
    config.fail.add(key)

    _, config, nothing = await _run(graph, config=config)
    assert nothing.events == [] and key in config.values

    config.fail.discard(key)
    _, config, again = await _run(graph, config=config)
    assert again.ids() == ["u1"]


def test_logs_and_configs_stored_under_their_own_mime_are_selected_as_their_type() -> None:
    from app.migrations.file_type_requeue.migration import is_selected

    over = file_types.TEXT_MAX_DOWNLOAD_BYTES + 1
    cases = {
        ("log", "text/x-log", "app.log", 100): True,
        ("cfg", "text/x-cfg", "app.cfg", 100): True,
        (None, "text/x-log", "app.log", 100): True,
        (None, "text/x-cfg", "app.cfg", 100): True,
        ("LOG", "TEXT/X-LOG", "APP.LOG", 100): True,
        ("log", "text/x-log", "huge.log", over): False,  # past the download ceiling: it would only be refused again
        ("log", "text/plain", "app.log", 100): False,  # read before: its status is not the old gate's
        ("cfg", "text/plain", "app.cfg", 100): False,
    }
    for (ext, mime, name, size), expected in cases.items():
        record = _file("r", name, ext=ext, mime=mime, size=size)
        row = {"mimeType": mime, "sizeInBytes": size}
        assert is_selected(record, row, frozenset())[0] is expected, (ext, mime)
        assert gate_v1.supported(extension=ext, mime=mime, record_name=name, is_code_file=False) is (not expected and mime == "text/plain"), (ext, mime)
    assert is_selected(_file("r", "app.log", ext="log", mime="text/x-log"), {"mimeType": "text/x-log"}, frozenset({"log"})) == (False, "log")
