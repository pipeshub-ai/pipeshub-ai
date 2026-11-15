"""validate_body and the worker process around it: deaths, the lease and respawn."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import time
from typing import TYPE_CHECKING

import pytest

from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import worker as W
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.spool import Spool
from app.models.record_metadata import MetadataSchema, PropertyDefinition, PropertyType

from .bodies import TEXT, cells_body, envelope, raw

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

#: Tests marked with this read a process's counters, limits or memory from /proc (Linux only).
needs_proc = pytest.mark.skipif(not os.path.exists("/proc/self/io"), reason="not applicable on this platform: no /proc")

SCHEMA_ID = "org1:kb1:2"
SCHEMA = MetadataSchema(
    schema_id=SCHEMA_ID,
    properties=(
        PropertyDefinition(name="team", type=PropertyType.STRING, display_label="Team"),
        PropertyDefinition(name="tags", type=PropertyType.STRING_LIST, display_label="Tags"),
        PropertyDefinition(name="count", type=PropertyType.INT, display_label="Count"),
        PropertyDefinition(name="score", type=PropertyType.FLOAT, display_label="Score"),
        PropertyDefinition(name="tier", type=PropertyType.ENUM, display_label="Tier", enum_values=("P0", "P1")),
        PropertyDefinition(name="when", type=PropertyType.DATETIME, display_label="When"),
    ),
).model_dump(mode="json")


@pytest.fixture
def spool(tmp_path) -> Iterator[Spool]:
    s = Spool(str(tmp_path))
    yield s
    s.close()


def run(body: bytes, spool: Spool, *, schema=SCHEMA, schema_id=SCHEMA_ID) -> W.Validated:
    return W.validate_body(body, schema_id=schema_id, schema=schema, out_path=spool.new_path("out"))


def with_values(values: dict, schema_id: str = SCHEMA_ID) -> bytes:
    return raw(envelope(metadata={"schemaId": schema_id, "values": values}))


def refused(body: bytes, spool: Spool, **kw) -> PushRefusal:
    with pytest.raises(PushRefusal) as e:
        run(body, spool, **kw)
    return e.value


def test_valid_push_produces_stored_bytes_and_digests(spool) -> None:
    v = run(with_values({"team": "core", "tags": ["a", "b"], "count": 3, "score": 2, "tier": "P1", "when": "2026-01-02T03:04:05Z"}), spool)
    stored = open(v.stored_path, "rb").read()
    assert v.size == len(stored) and v.content_rev == C.content_rev(stored)
    assert b'"push_format":1' in stored and b'"id"' not in stored
    props = json.loads(stored)["properties"]
    assert props == {"schemaId": SCHEMA_ID, "values": {"count": 3, "score": 2.0, "tags": ["a", "b"], "team": "core", "tier": "P1", "when": 1767323045000}}
    assert v.external_record_id == "k1" and v.version == 1 and v.block_count == 1


def test_identical_requests_give_one_digest_and_one_content_rev(spool) -> None:
    a = run(with_values({"team": "x", "count": 1}), spool)
    b = run(raw(envelope(metadata={"values": {"count": 1, "team": "x"}, "schemaId": SCHEMA_ID})), spool)
    assert (a.digest, a.content_rev) == (b.digest, b.content_rev)


def test_empty_values_are_stored_as_an_empty_object(spool) -> None:
    """A caller that clears every property sends ``values: {}``; it is not an absent field."""
    v = run(with_values({}), spool)
    assert json.loads(open(v.stored_path, "rb").read())["properties"] == {"schemaId": SCHEMA_ID, "values": {}}


def test_no_metadata_no_properties(spool) -> None:
    v = run(raw(envelope()), spool, schema=None, schema_id=None)
    assert v.properties is None and b"properties" not in open(v.stored_path, "rb").read()


@pytest.mark.parametrize("name", ["orgId", "virtualRecordId", "recordId", "blockId", "isBlock", "contentRev", "schemaId", "connectorId", "metadata", "page_content"])
def test_reserved_names_are_422_naming_them(spool, name) -> None:
    e = refused(with_values({name: "x"}), spool)
    assert (e.status, e.reason, e.location) == (422, "PROPERTY_RESERVED", name)


@pytest.mark.parametrize("values,reason,prop", [
    ({"nope": 1}, "PROPERTY_UNKNOWN", "nope"),
    ({"count": "5"}, "PROPERTY_INVALID", "count"),
    ({"count": 2**63}, "PROPERTY_INVALID", "count"),
    ({"count": 1.5}, "PROPERTY_INVALID", "count"),
    ({"score": 2**1024}, "PROPERTY_INVALID", "score"),
    ({"score": "x"}, "PROPERTY_INVALID", "score"),
    ({"tier": "P9"}, "PROPERTY_INVALID", "tier"),
    ({"team": 5}, "PROPERTY_INVALID", "team"),
    ({"team": "é" * 513}, "PROPERTY_INVALID", "team"),
    ({"tags": ["a"] * 65}, "PROPERTY_INVALID", "tags"),
    ({"tags": "a"}, "PROPERTY_INVALID", "tags"),
    ({"when": "2026-01-02T03:04:05"}, "PROPERTY_INVALID", "when"),
])
def test_invalid_values_name_the_property(spool, values, reason, prop) -> None:
    e = refused(with_values(values), spool)
    assert (e.status, e.reason, e.location) == (422, reason, prop)
    assert e.extra["property"] == prop if "property" in e.extra else True


def test_dto_bounds_on_values_are_422_property_invalid(spool) -> None:
    for values in ({"tags": ["a"] * 65}, {"team": "a" * 1025}):
        e = refused(with_values(values), spool)
        assert (e.status, e.reason) == (422, "PROPERTY_INVALID")


def test_first_invalid_property_in_sorted_order(spool) -> None:
    e = refused(with_values({"tier": "P9", "count": "x"}), spool)
    assert e.location == "count"


def test_values_with_no_schema_is_a_schema_mismatch(spool) -> None:
    e = refused(with_values({"team": "x"}), spool, schema=None, schema_id=None)
    assert (e.status, e.reason) in {(422, "SCHEMA_MISMATCH"), (422, "PROPERTY_UNKNOWN")}


def test_schema_id_must_equal_the_one_loaded(spool) -> None:
    e = refused(with_values({"team": "x"}, schema_id="org1:kb1:1"), spool)
    assert (e.status, e.reason) == (422, "SCHEMA_MISMATCH")


def test_schema_id_hint_is_the_first_key_match_and_ignores_text_in_strings() -> None:
    body = raw(envelope({"blocks": [{**TEXT, "data": '"schemaId":"evil"'}]}, metadata={"schemaId": "o:k:1", "values": {}}))
    assert W.schema_id_hint(body) == "o:k:1"
    assert W.schema_id_hint(raw(envelope())) is None


def test_reserved_name_writes_nothing(spool, tmp_path) -> None:
    out = spool.new_path("out")
    with pytest.raises(PushRefusal):
        W.validate_body(with_values({"orgId": "x"}), schema_id=SCHEMA_ID, schema=SCHEMA, out_path=out)
    assert not os.path.exists(out)


PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 20).decode()


def image(uri: str) -> bytes:
    return raw(envelope({"blocks": [{"index": 0, "type": "image", "format": "base64", "data": {"uri": uri}}]}))


def test_data_image_is_accepted(spool) -> None:
    v = run(image(f"data:image/png;base64,{PNG}"), spool, schema=None, schema_id=None)
    assert f"data:image/png;base64,{PNG}".encode() in open(v.stored_path, "rb").read()


@pytest.mark.parametrize("uri,reason", [
    ("http://example.com/a.png", "IMAGE_REF_REFUSED"),
    ("HTTPS://example.com/a.png", "IMAGE_REF_REFUSED"),
    ("//example.com/a.png", "IMAGE_REF_REFUSED"),
    ("storage://" + "a" * 23, "IMAGE_REF_INVALID"),
    ("storage://" + "A" * 24, "IMAGE_REF_INVALID"),
    ("STORAGE://" + "a" * 24, "IMAGE_REF_INVALID"),
    ("storage://" + "a" * 24 + "/x", "IMAGE_REF_INVALID"),
    ("data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=", "IMAGE_REF_INVALID"),
    (f"data:image/jpeg;base64,{PNG}", "IMAGE_REF_INVALID"),
    ("data:image/png;base64,@@@@", "IMAGE_REF_INVALID"),
    ("data:text/html;base64,PGI+PC9iPg==", "IMAGE_REF_INVALID"),
    ("file:///etc/passwd", "IMAGE_REF_INVALID"),
])
def test_other_image_references_are_refused(spool, uri, reason) -> None:
    e = refused(image(uri), spool, schema=None, schema_id=None)
    assert (e.status, e.reason) == (422, reason) and e.location == "blocks.blocks[0].data.uri"


def test_a_processing_group_with_a_remote_image_is_refused_at_its_data(spool) -> None:
    body = raw(envelope({"block_groups": [{"index": 0, "type": "text_section", "requires_processing": True, "format": "markdown", "data": "![x](http://169.254.169.254/)"}]}))
    e = refused(body, spool, schema=None, schema_id=None)
    assert (e.status, e.reason, e.location) == (422, "IMAGE_REF_REFUSED", "blocks.block_groups[0].data")


def test_validator_errors_are_422_blocks_invalid_with_issues(spool) -> None:
    body = raw(envelope({
        "block_groups": [{"index": 0, "type": "table", "table_metadata": {"num_of_cells": 1}, "children": {"block_ranges": [{"start": 0, "end": 0}]}}],
        "blocks": [{"index": 0, "type": "table_row", "format": "txt", "parent_index": 0, "data": {"cells": ["a"]}}],
    }))
    e = refused(body, spool, schema=None, schema_id=None)
    assert (e.status, e.reason) == (422, "BLOCKS_INVALID") and e.issues and e.issues[0]["type"] == "TABLE_ROW_FORMAT_NOT_JSON"


def test_empty_container_is_accepted(spool) -> None:
    v = run(raw(envelope({})), spool, schema=None, schema_id=None)
    assert v.block_count == 0 and open(v.stored_path, "rb").read() == b'{"push_format":1}'


# ---- the worker process -------------------------------------------------------------------------------

def big_body(n: int = 60_000) -> bytes:
    return raw(envelope({"blocks": [{"index": i, "type": "text", "data": "x" * 20} for i in range(n)]}))


def _rchar(pid: int) -> int:
    with open(f"/proc/{pid}/io") as f:
        return next(int(line.split()[1]) for line in f if line.startswith("rchar:"))


async def delivered(worker: W.PushWorker, run: Callable[[], Awaitable[W.Validated]]) -> asyncio.Task:
    """Start ``run`` and return once the worker process has read the task, so a signal sent next hits it mid-task.

    The proof is on the child's side: ``rchar`` (bytes the kernel counted the child reading) is still while it waits
    for a task and moves when it reads one from its pipe. ``_tasks`` moves before the send, so a signal sent on it can
    land before the task is delivered, and the send then fails instead of the task dying.
    """
    await worker._ensure()  # an idle process, blocked reading its next task
    pid = worker._proc.pid
    before = _rchar(pid)
    task = asyncio.create_task(run())
    await read_since(pid, before)
    return task


async def read_since(pid: int, before: int) -> None:
    deadline = time.monotonic() + 30
    while _rchar(pid) == before:
        assert time.monotonic() < deadline, "the worker never read its task"
        await asyncio.sleep(0.002)


async def start(worker: W.PushWorker, spool: Spool, body: bytes) -> asyncio.Task:
    return await delivered(worker, lambda: worker.validate(spool.write(body), spool.new_path("out"), schema_id=None, schema=None))


async def test_worker_validates_in_another_process(spool) -> None:
    w = W.PushWorker()
    try:
        v = await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)
        assert v.digest and w._proc.pid != os.getpid()
    finally:
        await w.close()


async def test_worker_refusal_crosses_the_process_boundary(spool) -> None:
    w = W.PushWorker()
    try:
        with pytest.raises(PushRefusal) as e:
            await w.validate(spool.write(raw(envelope(extra=1))), spool.new_path("out"), schema_id=None, schema=None)
        assert (e.value.status, e.value.reason, e.value.location) == (422, "UNKNOWN_FIELD", "Envelope.extra")
    finally:
        await w.close()


@needs_proc
@pytest.mark.parametrize("sig", [signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS])
async def test_worker_death_by_abort_is_413_complexity_and_the_next_push_works(spool, sig) -> None:
    w = W.PushWorker()
    try:
        task = await start(w, spool, big_body())
        os.kill(w._proc.pid, sig)
        with pytest.raises(PushRefusal) as e:
            await task
        assert (e.value.status, e.value.extra["limit"]) == (413, "complexity")
        v = await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)
        assert v.digest
    finally:
        await w.close()


@needs_proc
async def test_external_sigkill_is_503_not_413(spool) -> None:
    w = W.PushWorker()
    try:
        task = await start(w, spool, big_body())
        os.kill(w._proc.pid, signal.SIGKILL)
        with pytest.raises(PushRefusal) as e:
            await task
        assert e.value.status == 503
    finally:
        await w.close()


async def test_a_worker_over_its_budget_is_killed_and_replaced(spool) -> None:
    w = W.PushWorker(kill_after_s=0.3)
    try:
        with pytest.raises(PushRefusal) as e:
            await w.validate(spool.write(big_body(100_000)), spool.new_path("out"), schema_id=None, schema=None)
        assert (e.value.status, e.value.reason) == (503, "PUSH_TIMEOUT")
        assert w._proc is None
        w.kill_after_s = 60
        v = await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)
        assert v.digest
    finally:
        await w.close()


@needs_proc
async def test_a_second_push_waits_for_the_lease_then_gets_busy(spool) -> None:
    w = W.PushWorker(lease_wait_s=0.2)
    try:
        first = await start(w, spool, big_body())
        with pytest.raises(PushRefusal) as e:
            await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)
        assert (e.value.status, e.value.reason) == (503, "PUSH_BUSY")
        assert (await first).block_count == 60_000
    finally:
        await w.close()


@needs_proc
async def test_a_queued_push_is_not_charged_for_the_first_ones_time(spool) -> None:
    w = W.PushWorker(lease_wait_s=30, kill_after_s=30)
    try:
        first = await start(w, spool, big_body())
        second = asyncio.create_task(w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None))
        assert (await first).block_count == 60_000
        assert (await second).block_count == 1
    finally:
        await w.close()


async def test_worker_is_replaced_after_its_task_limit_outside_any_budget(spool, monkeypatch) -> None:
    monkeypatch.setattr(W, "MAX_TASKS_PER_WORKER", 2)
    w = W.PushWorker()
    try:
        pids = []
        for _ in range(3):
            await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)
            pids.append(w._proc.pid)
        assert pids[0] == pids[1] != pids[2]
    finally:
        await w.close()


@needs_proc
async def test_worker_has_no_core_files_and_an_address_space_limit(spool) -> None:
    import resource

    w = W.PushWorker()
    try:
        await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)
        with open(f"/proc/{w._proc.pid}/limits") as f:
            limits = {line.split("  ")[0].strip(): line for line in f}
        assert limits["Max core file size"].split()[-3:-1] == ["0", "0"]
        assert "unlimited" not in limits["Max address space"].split()[3]
        assert resource.RLIM_INFINITY  # silence unused import on exotic platforms
    finally:
        await w.close()


# ---- worker_dos reduced: an unclosed-string body no longer holds the worker ----------------------

async def test_an_unclosed_string_body_does_not_hold_the_worker(spool) -> None:
    evil = b'{"recordName":"' + b'\\"' * 20_000      # 40 KB; the quadratic lexer held the worker for 50 s
    good = raw(envelope())
    w = W.PushWorker(lease_wait_s=5, kill_after_s=30)
    try:
        await w.validate(spool.write(good), spool.new_path("out"), schema_id=None, schema=None)  # warm up
        started = time.monotonic()
        with pytest.raises(PushRefusal) as e:
            await w.validate(spool.write(evil), spool.new_path("out"), schema_id=None, schema=None)
        refused_after = time.monotonic() - started
        assert (e.value.status, e.value.reason) == (400, "INVALID_JSON")
        started = time.monotonic()
        await w.validate(spool.write(good), spool.new_path("out"), schema_id=None, schema=None)
        assert refused_after < 2.0 and time.monotonic() - started < 2.0
        assert w.deaths == 0
    finally:
        await w.close()


def test_requires_processing_on_a_table_group_is_refused(spool) -> None:
    """a table group with requires_processing was stored and then always failed at indexing."""
    group = {"index": 0, "type": "table", "requires_processing": True, "data": {"table_summary": "s"}}
    e = refused(raw(envelope({"block_groups": [group]})), spool, schema=None, schema_id=None)
    assert (e.status, e.reason, e.location) == (422, "BLOCKS_INVALID", "blocks.block_groups[0].requires_processing")


@pytest.mark.parametrize("group_type", ["list", "text_section", "ordered_list"])
def test_a_processing_group_of_text_is_stored(spool, group_type) -> None:
    group = {"index": 0, "type": group_type, "requires_processing": True, "format": "markdown", "data": "# title\n\ntext"}
    v = run(raw(envelope({"block_groups": [group]})), spool, schema=None, schema_id=None)
    assert b"# title" in open(v.stored_path, "rb").read()


# ---- a valid list-heavy body at the cap is accepted inside the bound -----------------------------

def rss_of(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status") as f:
            return next(int(line.split()[1]) * 1024 for line in f if line.startswith("VmRSS:"))
    except OSError:
        return 0


@needs_proc
@pytest.mark.timeout(240)
async def test_a_sparse_cells_body_at_the_cap_is_accepted_inside_the_worker_bound(spool) -> None:
    """16 MiB of table rows of 512 mostly empty cells (5.1 M list elements). Before the lexer was made
    flat, such a body reached +939 MiB, and at the cap only the RLIMIT_AS backstop refused it."""
    import threading

    body = cells_body(10_000)
    assert 15 << 20 < len(body) <= 16 << 20, len(body)
    w = W.PushWorker(kill_after_s=120)
    try:
        await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)  # warm up
        pid = w._proc.pid
        base, peak, stop = rss_of(pid), [0], threading.Event()

        def watch() -> None:
            while not stop.is_set():
                peak[0] = max(peak[0], rss_of(pid))
                stop.wait(0.02)

        thread = threading.Thread(target=watch)
        thread.start()
        try:
            v = await w.validate(spool.write(body), spool.new_path("out"), schema_id=None, schema=None)
        finally:
            stop.set()
            thread.join()
        growth = (peak[0] - base) / 2**20
        assert v.block_count == 10_000 and w.deaths == 0
        assert growth <= 704, growth
    finally:
        await w.close()


def test_a_spool_whose_directory_disappeared_while_idle_still_takes_a_body(tmp_path) -> None:
    """Something outside the process (a tmp cleaner, an operator) removed the run directory: the next body recreates it."""
    import shutil

    idle = Spool(str(tmp_path))
    try:
        shutil.rmtree(idle.dir)

        path = idle.write(b"body")

        assert open(path, "rb").read() == b"body"
    finally:
        idle.close()


@needs_proc
async def test_a_cancelled_validation_does_not_leave_its_result_for_the_next_push(spool) -> None:
    w = W.PushWorker(lease_wait_s=30, kill_after_s=30)
    try:
        first = await start(w, spool, big_body())
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        v = await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None)

        assert v.block_count == 1
    finally:
        await w.close()


# ---- accounting: strikes, replays, overruns and shared-worker seconds -----------------------------

class Events:
    """Collects what a worker reports to the ledger."""

    def __init__(self) -> None:
        self.items: list[tuple[str, float]] = []

    def __call__(self, event: str, value: float = 0.0) -> None:
        self.items.append((event, value))

    @property
    def names(self) -> list[str]:
        return [e for e, _ in self.items]

    def lease_s(self) -> float:
        return sum(v for e, v in self.items if e == "lease")


async def start_accounted(worker: W.PushWorker, spool: Spool, body: bytes, events: Events) -> asyncio.Task:
    return await delivered(
        worker, lambda: worker.validate(spool.write(body), spool.new_path("out"), schema_id=None, schema=None, account=events),
    )


async def push_small(w: W.PushWorker, spool: Spool, events: Events | None = None) -> W.Validated:
    return await w.validate(spool.write(raw(envelope())), spool.new_path("out"), schema_id=None, schema=None, account=events)


@needs_proc
@pytest.mark.parametrize("sig", [signal.SIGABRT, signal.SIGSEGV, signal.SIGBUS])
async def test_a_real_signal_death_on_a_fresh_worker_is_a_strike(spool, sig) -> None:
    events, w = Events(), W.PushWorker()
    try:
        task = await start_accounted(w, spool, big_body(), events)
        os.kill(w._proc.pid, sig)
        with pytest.raises(PushRefusal) as e:
            await task
        assert e.value.status == 413
        assert events.names == ["strike", "lease"]  # no replay: nothing ran on this worker before
        assert w.deaths == 1
    finally:
        await w.close()


@needs_proc
async def test_an_external_sigkill_is_no_strike(spool) -> None:
    events, w = Events(), W.PushWorker()
    try:
        task = await start_accounted(w, spool, big_body(), events)
        os.kill(w._proc.pid, signal.SIGKILL)
        with pytest.raises(PushRefusal) as e:
            await task
        assert e.value.status == 503 and events.names == ["lease"]
    finally:
        await w.close()


@needs_proc
async def test_a_reused_worker_death_is_replayed_once_and_a_surviving_replay_is_only_suspect(spool) -> None:
    """The earlier push may have left the shared worker in a state that kills the next, harmless one."""
    events, w = Events(), W.PushWorker()
    try:
        await push_small(w, spool)  # the worker has now served a task
        task = await start_accounted(w, spool, big_body(), events)
        os.kill(w._proc.pid, signal.SIGSEGV)
        assert (await task).block_count == 60_000  # the replay on a fresh worker answered
        assert events.names == ["suspect", "lease"] and w.deaths == 1
    finally:
        await w.close()


@needs_proc
async def test_a_replay_that_dies_too_is_a_strike(spool, monkeypatch) -> None:
    ready: dict[int, int] = {}
    real_start = W.PushWorker._start

    def start_and_note_ready(self) -> None:
        real_start(self)
        ready[self._proc.pid] = _rchar(self._proc.pid)  # it has sent "ready" and waits for its first task

    monkeypatch.setattr(W.PushWorker, "_start", start_and_note_ready)
    events, w = Events(), W.PushWorker()
    try:
        await push_small(w, spool)
        task = await start_accounted(w, spool, big_body(), events)
        first = w._proc.pid
        os.kill(first, signal.SIGSEGV)
        for _ in range(3000):
            await asyncio.sleep(0.01)
            if w._proc is not None and w._proc.pid != first and w._proc.pid in ready:
                break
        await read_since(w._proc.pid, ready[w._proc.pid])  # the replay was delivered to the fresh process
        os.kill(w._proc.pid, signal.SIGSEGV)
        with pytest.raises(PushRefusal) as e:
            await task
        assert e.value.status == 413 and events.names == ["strike", "lease"]
    finally:
        await w.close()


@needs_proc
async def test_a_reused_worker_death_after_a_long_run_is_the_bodys_own_and_is_not_replayed(spool, monkeypatch) -> None:
    monkeypatch.setattr(W, "REPLAY_ONLY_BELOW_S", 0.0)
    events, w = Events(), W.PushWorker()
    try:
        await push_small(w, spool)
        task = await start_accounted(w, spool, big_body(), events)
        os.kill(w._proc.pid, signal.SIGSEGV)
        with pytest.raises(PushRefusal):
            await task
        assert events.names == ["strike", "lease"]
    finally:
        await w.close()


@needs_proc
async def test_a_replay_runs_inside_the_remaining_budget_not_a_fresh_one(spool) -> None:
    events, w = Events(), W.PushWorker(kill_after_s=0.6)
    try:
        await push_small(w, spool)
        task = await start_accounted(w, spool, big_body(150_000), events)
        os.kill(w._proc.pid, signal.SIGSEGV)
        started = time.monotonic()
        with pytest.raises(PushRefusal) as e:
            await task
        assert e.value.reason == "PUSH_TIMEOUT"  # the replay hit the same 0.6 s wall budget
        assert time.monotonic() - started < 2.0
    finally:
        await w.close()


@needs_proc
async def test_a_cpu_bound_overrun_is_a_strike(spool) -> None:
    events, w = Events(), W.PushWorker(kill_after_s=0.5)
    try:
        with pytest.raises(PushRefusal) as e:
            await w.validate(spool.write(big_body(150_000)), spool.new_path("out"), schema_id=None, schema=None, account=events)
        assert e.value.reason == "PUSH_TIMEOUT" and events.names == ["strike", "lease"]
    finally:
        await w.close()


@needs_proc
async def test_an_idle_overrun_is_a_refusal_not_a_strike(spool) -> None:
    """A worker that is stopped uses no CPU while it holds the lease: a slow host or dependency, not a hostile body."""
    events, w = Events(), W.PushWorker(kill_after_s=0.6)
    try:
        await push_small(w, spool)
        task = await start_accounted(w, spool, big_body(), events)
        os.kill(w._proc.pid, signal.SIGSTOP)
        with pytest.raises(PushRefusal) as e:
            await task
        assert e.value.reason == "PUSH_TIMEOUT" and events.names == ["overrun", "lease"]
    finally:
        await w.close()


async def test_unreadable_cpu_is_not_counted(spool, monkeypatch) -> None:
    monkeypatch.setattr(W.cpu, "read", lambda pid, proc_root="/proc": None)
    events, w = Events(), W.PushWorker(kill_after_s=0.5)
    try:
        with pytest.raises(PushRefusal):
            await w.validate(spool.write(big_body(150_000)), spool.new_path("out"), schema_id=None, schema=None, account=events)
        assert events.names == ["overrun", "lease"]
    finally:
        await w.close()


async def test_cpu_is_measured_per_task_not_as_the_workers_total(spool, monkeypatch) -> None:
    """A worker that spent 40 s of CPU on earlier pushes is not struck for a short overrun."""
    readings = iter([(4_000, 7), (4_010, 7)])  # ticks used so far, then 10 ticks later; same process
    monkeypatch.setattr(W.cpu, "_TICKS", 100)
    monkeypatch.setattr(W.cpu, "read", lambda pid, proc_root="/proc": next(readings))
    events, w = Events(), W.PushWorker(kill_after_s=0.5)
    try:
        with pytest.raises(PushRefusal):
            await w.validate(spool.write(big_body(150_000)), spool.new_path("out"), schema_id=None, schema=None, account=events)
        assert events.names == ["overrun", "lease"]
    finally:
        await w.close()


async def test_a_run_under_the_kill_is_still_charged_its_seconds(spool) -> None:
    """A body that finishes just under the kill holds the shared worker all the same."""
    events, w = Events(), W.PushWorker(kill_after_s=60)
    try:
        await push_small(w, spool)  # the worker is up: its start-up is not part of what is measured
        started = time.monotonic()
        v = await w.validate(spool.write(big_body()), spool.new_path("out"), schema_id=None, schema=None, account=events)
        took = time.monotonic() - started
        assert v.block_count == 60_000 and events.names == ["lease"]
        assert events.lease_s() >= took * 0.9 and events.lease_s() > 0.2
    finally:
        await w.close()


@needs_proc
async def test_a_cancelled_task_is_charged_no_strike_and_no_overrun(spool) -> None:
    events, w = Events(), W.PushWorker()
    try:
        task = await start_accounted(w, spool, big_body(), events)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events.names == ["lease"]
    finally:
        await w.close()


async def test_a_full_disk_at_the_out_write_is_spool_full_and_never_counted(spool) -> None:
    if not os.path.exists("/dev/full"):
        pytest.skip("not applicable on this platform: no /dev/full")
    events, w = Events(), W.PushWorker()
    try:
        with pytest.raises(PushRefusal) as e:
            await w.validate(spool.write(raw(envelope())), "/dev/full", schema_id=None, schema=None, account=events)
        assert (e.value.status, e.value.reason) == (503, "SPOOL_FULL") and events.names == ["lease"]
        assert (await push_small(w, spool)).block_count == 1  # the same worker went on
    finally:
        await w.close()


async def test_a_workers_start_up_is_not_charged_to_the_caller_whose_push_started_it(spool, monkeypatch) -> None:
    real_start = W.PushWorker._start

    def slow_start(self) -> None:
        time.sleep(0.7)  # a respawn after somebody else's crash
        real_start(self)

    monkeypatch.setattr(W.PushWorker, "_start", slow_start)
    events, w = Events(), W.PushWorker()
    try:
        started = time.monotonic()
        await push_small(w, spool, events)
        took = time.monotonic() - started
        assert took >= 0.7 and events.names == ["lease"]
        assert events.lease_s() < took - 0.6, "the 0.7 s the worker took to start is not the caller's"
    finally:
        await w.close()
