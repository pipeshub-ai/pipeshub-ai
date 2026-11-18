"""The Redis provider's refusal of a filter on a field its index lacks, without a server.

The same behaviour against a real Redis Stack is in
``tests/integration/vector_db/test_redis_unindexed_filter.py``.
"""

import asyncio
import contextlib
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ResponseError

from app.exceptions.non_retryable import NonRetryableError
from app.services.vector_db.models import (
    CollectionConfig,
    FieldCondition,
    FilterExpression,
)
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.errors import FilterFieldNotIndexed, IndexBackfilling
from app.services.vector_db.redis.redis_vector import (
    _DEFAULT_BACKFILL_WAIT_SECONDS,
    RedisVectorService,
    _backfill_wait_seconds,
)
from app.services.vector_db.redis.utils import (
    escape_tag_value,
    field_conditions_to_redis_query,
    filter_expression_to_redis_query,
    query_fields,
)

ORG = "o1"


def _info(*names: str, indexing: int = 0, percent: str = "1") -> list:
    """An FT.INFO reply shaped like Redis 8's (RESP2): the schema is the nested ``attributes`` lists."""
    attributes = [[b"identifier", n.encode(), b"attribute", n.encode(), b"type", b"TAG"] for n in names]
    return [b"index_name", b"col_idx", b"attributes", attributes, b"num_docs", b"3",
            b"indexing", str(indexing).encode(), b"percent_indexed", percent.encode()]


def _service(*replies) -> tuple[RedisVectorService, AsyncMock]:
    svc = RedisVectorService(RedisVectorConfig(host="localhost", port=6379))
    client = MagicMock()
    client.execute_command = AsyncMock(side_effect=list(replies) if replies else None)
    client.delete = AsyncMock()
    svc.client = client
    return svc, client.execute_command


def _calls(execute: AsyncMock, command: str) -> list[tuple]:
    return [c.args for c in execute.await_args_list if c.args[0] == command]


class TestQueryFields:
    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("*", set()),
            ("@a:{x}", {"a"}),
            ("@a:{x} -(@b:{y}) (@c:[1 1] | @d_e:{z})", {"a", "b", "c", "d_e"}),
            ("(hello | world) @metadata_orgId:{o}", {"metadata_orgId"}),
        ],
    )
    def test_names_every_field_modifier(self, query, expected) -> None:
        assert query_fields(query) == expected

    @pytest.mark.parametrize(
        "value",
        ["@evil:{x}", "a\\", "\\@evil", "x}  @evil:{y", "@", "a@b", "\\\\@evil", "-@evil", "(@evil:{a})"],
    )
    def test_a_value_cannot_smuggle_a_field_name_into_the_query(self, value) -> None:
        query = field_conditions_to_redis_query([
            FieldCondition(key="metadata.orgId", value=value),
            FieldCondition(key="connectorIds", values=[value, "ok"]),
        ])
        assert query_fields(query) == {"metadata_orgId", "connectorIds"}
        assert escape_tag_value(value) in query

    def test_covers_must_should_and_must_not(self) -> None:
        expr = FilterExpression(
            must=[FieldCondition(key="metadata.orgId", value=ORG)],
            should=[FieldCondition(key="connectorIds", values=["c"])],
            must_not=[FieldCondition(key="metadata.blockId", value="b")],
        )
        assert query_fields(filter_expression_to_redis_query(expr)) == {"metadata_orgId", "connectorIds", "metadata_blockId"}


def test_a_condition_with_nothing_to_match_is_refused_not_dropped() -> None:
    with pytest.raises(ValueError, match="no value to match"):
        field_conditions_to_redis_query([FieldCondition(key="metadata.blockId")])


class TestSchemaLookup:
    async def test_reads_the_attributes_of_ft_info_once_and_keeps_them(self) -> None:
        svc, execute = _service(_info("metadata_orgId", "metadata_blockId"))
        first = await svc._indexed_fields("col_idx")
        second = await svc._indexed_fields("col_idx")
        assert first == second == {"metadata_orgId", "metadata_blockId"}
        assert len(_calls(execute, "FT.INFO")) == 1

    async def test_a_reply_without_attributes_is_an_error_not_an_empty_schema(self) -> None:
        svc, _ = _service([b"index_name", b"col_idx"])
        with pytest.raises(RuntimeError, match="no schema attributes"):
            await svc._indexed_fields("col_idx")

    async def test_a_missing_index_is_none_and_is_not_kept(self) -> None:
        svc, execute = _service(ResponseError("Unknown index name"), ResponseError("Unknown index name"))
        assert await svc._indexed_fields("col_idx") is None
        assert await svc._indexed_fields("col_idx") is None
        assert len(_calls(execute, "FT.INFO")) == 2

    async def test_other_errors_propagate(self) -> None:
        svc, _ = _service(ConnectionError("down"))
        with pytest.raises(ConnectionError):
            await svc._indexed_fields("col_idx")

    async def test_create_index_drops_the_kept_schema(self) -> None:
        svc, execute = _service(_info("metadata_orgId"), 1, b"OK", _info("metadata_orgId", "metadata_blockId"))
        await svc._indexed_fields("col_idx")
        await svc.create_index("col", "metadata.blockId", {"type": "keyword"})
        assert "col_idx" not in svc._schema_fields
        assert await svc._indexed_fields("col_idx") == {"metadata_orgId", "metadata_blockId"}

    async def test_a_failed_alter_drops_it_too(self) -> None:
        svc, _ = _service(_info("metadata_orgId"), 1, ResponseError("boom"))
        await svc._indexed_fields("col_idx")
        with pytest.raises(ResponseError):
            await svc.create_index("col", "metadata.blockId", {"type": "keyword"})
        assert "col_idx" not in svc._schema_fields

    async def test_create_collection_forgets_what_an_earlier_index_of_that_name_had_complete(self) -> None:
        svc, execute = _service(ResponseError("Unknown index name"), 1, b"OK")
        await svc.create_collection("col", CollectionConfig(embedding_size=8))
        assert [c.args[0] for c in execute.await_args_list] == ["FT.INFO", "DEL", "FT.CREATE"]
        assert _calls(execute, "DEL") == [("DEL", "col_idx.complete_fields")]
        # Document keys are "<collection>:<id>" and collection names are [a-z0-9_]: a key with no ":" is never one of them.
        assert ":" not in svc._complete_key(svc._index_name("col"))

    async def test_delete_collection_drops_it(self) -> None:
        svc, execute = _service(_info("metadata_orgId"), b"OK", 1)
        await svc._indexed_fields("col_idx")
        await svc.delete_collection("col")
        assert "col_idx" not in svc._schema_fields
        assert ("DEL", "col_idx.complete_fields") in _calls(execute, "DEL")


class TestRequireIndexed:
    async def test_a_known_field_costs_no_second_read(self) -> None:
        svc, execute = _service(_info("metadata_orgId"))
        await svc._require_indexed("col_idx", "@metadata_orgId:{o}")
        await svc._require_indexed("col_idx", "@metadata_orgId:{p}")
        assert len(_calls(execute, "FT.INFO")) == 1

    async def test_an_unknown_field_is_read_again_then_refused(self) -> None:
        svc, execute = _service(_info("metadata_orgId"), _info("metadata_orgId"))
        with pytest.raises(FilterFieldNotIndexed) as raised:
            await svc._require_indexed("col_idx", "@metadata_orgId:{o} @metadata_blockId:{b}")
        assert raised.value.fields == {"metadata_blockId"}
        assert raised.value.index == "col_idx"
        assert len(_calls(execute, "FT.INFO")) == 2

    async def test_a_field_another_process_added_is_found_by_the_second_read(self) -> None:
        svc, _ = _service(_info("metadata_orgId"), _info("metadata_orgId", "metadata_blockId"))
        await svc._indexed_fields("col_idx")
        await svc._require_indexed("col_idx", "@metadata_blockId:{b}")
        assert svc._schema_fields["col_idx"] == {"metadata_orgId", "metadata_blockId"}

    async def test_a_query_with_no_field_reads_nothing(self) -> None:
        svc, execute = _service()
        await svc._require_indexed("col_idx", "*", "(hello | world)")
        execute.assert_not_awaited()

    async def test_a_missing_index_is_left_to_the_command(self) -> None:
        svc, _ = _service(ResponseError("Unknown index name"))
        await svc._require_indexed("col_idx", "@metadata_orgId:{o}")


class TestNothingRunsOnARefusedFilter:
    @staticmethod
    def _filter() -> FilterExpression:
        return FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.blockId", values=["b1"]),
        ])

    async def test_delete_sends_no_search_and_deletes_no_key(self) -> None:
        svc, execute = _service(_info("metadata_orgId"), _info("metadata_orgId"))
        with pytest.raises(FilterFieldNotIndexed):
            await svc.delete_points("col", self._filter())
        assert [c.args[0] for c in execute.await_args_list] == ["FT.INFO", "FT.INFO"]
        svc.client.delete.assert_not_awaited()

    async def test_delete_with_a_length_bound_sends_no_aggregate(self) -> None:
        svc, execute = _service(_info("metadata_orgId", "connectorIds"), _info("metadata_orgId", "connectorIds"))
        flt = FilterExpression(must=[
            FieldCondition(key="connectorIds", values=["c1"], values_count_lte=1),
            FieldCondition(key="metadata.blockId", values=["b1"]),
        ])
        with pytest.raises(FilterFieldNotIndexed):
            await svc.delete_points("col", FilterExpression(must=[FieldCondition(key="metadata.orgId", value=ORG), *flt.must]))
        assert not _calls(execute, "FT.AGGREGATE") and not _calls(execute, "FT.SEARCH")

    @pytest.mark.parametrize("op", ["count", "scroll", "scroll past the search window", "scroll with vectors", "keys"])
    async def test_reads_and_scans_are_refused_too(self, op) -> None:
        svc, execute = _service(_info("metadata_orgId"), _info("metadata_orgId"))
        with pytest.raises(FilterFieldNotIndexed):
            if op == "count":
                await svc.count_points("col", self._filter())
            elif op == "scroll":
                await svc.scroll("col", self._filter(), limit=5)
            elif op == "scroll past the search window":
                await svc.scroll("col", self._filter(), limit=5, offset="10000")
            elif op == "scroll with vectors":
                await svc.scroll_with_vectors("col", self._filter(), limit=5)
            else:
                await svc._keys_matching_filter("col", self._filter())
        assert not _calls(execute, "FT.SEARCH") and not _calls(execute, "FT.AGGREGATE")


class TestDialectIsPinned:
    async def test_every_search_and_aggregate_carries_dialect_2(self) -> None:
        schema = _info("metadata_orgId", "metadata_virtualRecordId")
        svc, execute = _service(schema, [1], [1], schema, 1, [1, b"k"], [0], schema, 1, schema, 1, [1], [[1, [b"__key", b"col:k"]], 0])
        flt = FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.virtualRecordId", value="v"),
        ])
        await svc.count_points("col", flt)
        await svc.scroll("col", flt, limit=3)
        await svc.delete_points("col", flt)
        assert await svc._keys_matching_filter("col", flt) == [b"col:k"]  # the key scan of a filtered payload write
        assert _calls(execute, "FT.AGGREGATE")
        queries = _calls(execute, "FT.SEARCH") + _calls(execute, "FT.AGGREGATE")
        assert len(queries) >= 5
        for args in queries:
            assert args[args.index("DIALECT") + 1] == "2", args
            assert args.index("DIALECT") == 3  # straight after the query, ahead of every option that has its own arguments


class _Tasks(list):
    def spawn(self, coro: object) -> asyncio.Task:
        self.append(asyncio.ensure_future(coro))
        return self[-1]


@contextlib.asynccontextmanager
async def _workers() -> object:
    """Tasks spawned inside are cancelled and joined on every exit, so a failing test does not leave one waiting on an event."""
    tasks = _Tasks()
    try:
        yield tasks
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _reached(event: asyncio.Event, *workers: asyncio.Task) -> None:
    """Wait for ``event``. A worker that ends first fails the test at once with its own error; so does a wait past 5 s."""
    waiter = asyncio.ensure_future(event.wait())
    try:
        done, _ = await asyncio.wait({waiter, *workers}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
    for worker in workers:
        if worker in done and waiter not in done:
            worker.result()
            raise AssertionError("the worker ended before it reached the step the test waits for")
    assert waiter in done, "timed out waiting for the worker"


class _Server:
    """One fake Redis several store instances share: an index, its scan (``scan_polls`` FT.INFO replies say "indexing") and plain sets."""

    def __init__(self, *fields: str) -> None:
        self.fields = list(fields)
        self.scan_polls = 0
        self.sets: dict[str, set[str]] = {}
        self.run_id = 1
        self.calls: list[tuple] = []

    async def execute(self, *args: object) -> object:
        self.calls.append(args)
        cmd = args[0]
        if cmd == "FT.INFO":
            busy = self.scan_polls > 0
            self.scan_polls = max(0, self.scan_polls - 1)
            return _info(*self.fields, indexing=int(busy), percent="0.3" if busy else "1")
        if cmd == "FT.ALTER":
            if str(args[4]) in self.fields:
                raise ResponseError("Duplicate field in schema")
            self.fields.append(str(args[4]))
            self.scan_polls = 10**6
            return b"OK"
        if cmd == "EVAL" and "SISMEMBER" in str(args[1]):  # the script that reads the fields recorded in this server run
            members = self.sets.get(str(args[3]), set())
            return [m.encode() for m in members] if f"run:{self.run_id}" in members else []
        if cmd == "EVAL":  # the script that records the schema of an idle index as complete
            if self.scan_polls > 0:
                return 0
            self.sets[str(args[3])] = {*self.fields, f"run:{self.run_id}"}
            return 1
        if cmd == "DEL":
            self.sets.pop(str(args[1]), None)
            return 1
        if cmd == "FT.SEARCH":
            return [0]
        raise AssertionError(args)

    def finish_scan(self) -> None:
        self.scan_polls = 0

    def restart(self) -> None:
        """A restart with AOF persistence: the data and every set come back, FT.CREATE / FT.ALTER replayed from the log read the hashes
        again in the background while the server already answers, and the server has a new run id."""
        self.run_id += 1
        self.scan_polls = 10**6

    def stall(
        self, svc: RedisVectorService, *, alter_waits_for: asyncio.Event | None = None, applied: asyncio.Event | None = None,
        reply_waits_for: asyncio.Event | None = None, reply_lost: bool = False,
    ) -> asyncio.Event:
        """Interleave ``svc``'s create_index with other processes. Its FT.ALTER waits for ``alter_waits_for``, adds the field and sets
        ``applied``; its reply then waits for ``reply_waits_for``, or never arrives (a connection error) when ``reply_lost``. Returns an
        event set when its FT.ALTER reaches the server."""
        reached = asyncio.Event()

        async def execute(*args: object) -> object:
            if args[0] != "FT.ALTER":
                return await self.execute(*args)
            reached.set()
            if alter_waits_for is not None:
                await alter_waits_for.wait()
            reply = await self.execute(*args)
            if applied is not None:
                applied.set()
            if reply_lost:
                raise ConnectionError("connection lost after the server applied the alter")
            if reply_waits_for is not None:
                await reply_waits_for.wait()
            return reply

        svc.client.execute_command.side_effect = execute
        return reached

    def store(self) -> RedisVectorService:
        svc = RedisVectorService(RedisVectorConfig(host="localhost", port=6379))
        svc.client = MagicMock()
        svc.client.execute_command = AsyncMock(side_effect=self.execute)
        svc.client.delete = AsyncMock()
        return svc

    def searched(self) -> bool:
        return any(c[0] in ("FT.SEARCH", "FT.AGGREGATE") for c in self.calls)


class TestBackfill:
    """``FT.ALTER`` returns at once and indexes the stored documents in the background: a delete in between would find only some of them."""

    FIELDS = ("metadata_orgId", "metadata_virtualRecordId", "metadata_blockId")

    @staticmethod
    def _block_filter() -> FilterExpression:
        return FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.blockId", values=["b1"]),
        ])

    @staticmethod
    def _record_filter() -> FilterExpression:
        return FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.virtualRecordId", value="vr1"),
        ])

    @pytest.fixture(autouse=True)
    def _fast_wait(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0.05")
        monkeypatch.setattr("app.services.vector_db.redis.redis_vector._BACKFILL_POLL_SECONDS", 0.01)

    async def test_a_delete_by_the_field_being_filled_raises_after_the_bound(self) -> None:
        server = _Server(*self.FIELDS)
        server.scan_polls = 10**6
        svc = server.store()
        with pytest.raises(IndexBackfilling) as raised:
            await svc.delete_points("col", self._block_filter())
        assert raised.value.index == "col_idx"
        assert not isinstance(raised.value, NonRetryableError)
        assert not server.searched()
        svc.client.delete.assert_not_awaited()

    async def test_a_delete_by_the_field_being_filled_waits_for_the_scan_then_runs(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "30")
        server = _Server(*self.FIELDS)
        server.scan_polls = 4
        await server.store().delete_points("col", self._block_filter())
        assert server.searched()

    async def test_a_delete_by_fields_already_complete_runs_during_the_scan(self) -> None:
        server = _Server(*self.FIELDS[:2])
        svc = server.store()
        await svc.create_index("col", "metadata.blockId", {"type": "keyword"})  # from an idle index: the scan starts
        await svc.delete_points("col", self._record_filter())
        assert server.searched()

    async def test_a_field_another_process_added_is_waited_for_even_when_this_one_counts_it_as_created_with_the_index(self, monkeypatch) -> None:
        server = _Server("metadata_orgId")
        a, b = server.store(), server.store()
        await b.create_index("col", "metadata.virtualRecordId", {"type": "keyword"})  # the scan starts
        with pytest.raises(IndexBackfilling):
            await a.delete_points("col", self._record_filter())
        with pytest.raises(IndexBackfilling):
            await a.set_payload("col", {"metadata.x": "1"}, self._record_filter())
        assert not server.searched()

        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "30")
        server.scan_polls = 3  # the scan ends while a waits
        await a.delete_points("col", self._record_filter())
        assert server.searched()

    async def test_a_field_seen_complete_once_is_not_held_by_a_later_scan_in_any_process(self) -> None:
        server = _Server("metadata_orgId")
        a, b = server.store(), server.store()
        await b.create_index("col", "metadata.virtualRecordId", {"type": "keyword"})
        server.finish_scan()
        await a.delete_points("col", self._record_filter())  # sees the index idle
        assert server.sets["col_idx.complete_fields"] == {"metadata_orgId", "metadata_virtualRecordId", "run:1"}
        await a.create_index("col", "metadata.blockId", {"type": "keyword"})  # another scan starts
        await b.delete_points("col", self._record_filter())
        assert sum(c[0] == "FT.SEARCH" for c in server.calls) == 2

    async def test_a_failed_alter_marks_nothing_complete(self) -> None:
        server = _Server("metadata_orgId")
        svc = server.store()
        real = svc.client.execute_command.side_effect

        async def boom(*args: object) -> object:
            if args[0] == "FT.ALTER":
                raise ResponseError("boom")
            return await real(*args)

        svc.client.execute_command.side_effect = boom
        with pytest.raises(ResponseError):
            await svc.create_index("col", "metadata.blockId", {"type": "keyword"})
        assert "metadata_blockId" not in server.sets.get("col_idx.complete_fields", set())
        assert server.fields == ["metadata_orgId"]

    async def test_an_upgrade_ensure_that_starts_one_scan_does_not_hold_the_fields_it_repeats(self) -> None:
        """The start-up ensure of an older index adds metadata.blockId (the scan starts), then repeats connectorIds, already complete."""
        server = _Server("metadata_orgId", "metadata_virtualRecordId", "connectorIds")
        svc = server.store()
        for field in ("metadata.virtualRecordId", "metadata.orgId", "metadata.blockId", "connectorIds"):
            await svc.create_index("col", field, {"type": "keyword"})
        assert server.scan_polls > 0
        await svc.delete_points("col", FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG), FieldCondition(key="connectorIds", values=["c1"]),
        ]))
        await svc.delete_points("col", self._record_filter())
        assert sum(c[0] == "FT.SEARCH" for c in server.calls) == 2

    async def test_an_alter_that_adds_nothing_does_not_erase_the_record_of_a_scan_in_progress(self) -> None:
        server = _Server("metadata_orgId")
        a, b = server.store(), server.store()
        await b.create_index("col", "metadata.virtualRecordId", {"type": "keyword"})  # the scan starts
        await a.create_index("col", "metadata.virtualRecordId", {"type": "keyword"})  # already there
        with pytest.raises(IndexBackfilling):
            await a.delete_points("col", self._record_filter())

    async def test_a_duplicate_alter_answered_before_the_winning_alter_reports_back_keeps_the_field_waiting(self) -> None:
        """Two processes ensure a field an older index lacks: a's ALTER is answered "duplicate" after b's started the scan, while b still
        waits for its own reply."""
        server = _Server("metadata_orgId")
        a, b, c = server.store(), server.store(), server.store()
        b_applied, b_reply = asyncio.Event(), asyncio.Event()
        a_at_alter = server.stall(a, alter_waits_for=b_applied)
        server.stall(b, applied=b_applied, reply_waits_for=b_reply)
        async with _workers() as tasks:
            task_a = tasks.spawn(a.create_index("col", "metadata.virtualRecordId", {"type": "keyword"}))
            await _reached(a_at_alter, task_a)
            task_b = tasks.spawn(b.create_index("col", "metadata.virtualRecordId", {"type": "keyword"}))
            await asyncio.wait_for(task_a, 5)
            assert not task_b.done(), "b's reply is still on its way"
            with pytest.raises(IndexBackfilling):
                await c.delete_points("col", self._record_filter())
            assert not server.searched()
            b_reply.set()
            await asyncio.wait_for(task_b, 5)

    async def test_a_lost_reply_to_a_successful_alter_does_not_take_the_wait_away(self) -> None:
        server = _Server("metadata_orgId")
        a, b = server.store(), server.store()
        server.stall(b, reply_lost=True)
        with pytest.raises(ConnectionError):
            await b.create_index("col", "metadata.virtualRecordId", {"type": "keyword"})
        assert "metadata_virtualRecordId" in server.fields, "the server applied the alter"
        with pytest.raises(IndexBackfilling):
            await a.delete_points("col", self._record_filter())
        assert not server.searched()

    async def test_an_alter_held_up_for_an_hour_is_still_waited_for(self, monkeypatch) -> None:
        """No age makes a field complete: an idle check while a's ALTER is in flight must not let a later delete skip a's scan."""
        server = _Server("metadata_orgId")
        a, c = server.store(), server.store()
        release, a_applied, a_reply = asyncio.Event(), asyncio.Event(), asyncio.Event()
        a_at_alter = server.stall(a, alter_waits_for=release, applied=a_applied, reply_waits_for=a_reply)
        async with _workers() as tasks:
            task = tasks.spawn(a.create_index("col", "metadata.virtualRecordId", {"type": "keyword"}))
            await _reached(a_at_alter, task)
            later = time.time() + 3600
            monkeypatch.setattr(time, "time", lambda: later)
            await c.delete_points("col", FilterExpression(must=[FieldCondition(key="metadata.orgId", value=ORG)]))  # idle
            release.set()
            await _reached(a_applied, task)
            with pytest.raises(IndexBackfilling):
                await c.delete_points("col", self._record_filter())
            a_reply.set()
            await asyncio.wait_for(task, 5)

    async def test_a_restart_that_rebuilds_the_index_does_not_inherit_the_fields_seen_complete_before_it(self) -> None:
        server = _Server(*self.FIELDS)
        svc = server.store()
        await svc.delete_points("col", self._record_filter())  # idle: the fields are recorded
        server.restart()
        with pytest.raises(IndexBackfilling):
            await svc.delete_points("col", self._record_filter())
        assert sum(c[0] == "FT.SEARCH" for c in server.calls) == 1
        server.finish_scan()
        await svc.delete_points("col", self._record_filter())
        server.scan_polls = 10**6  # another scan in the same run: the fields seen since the restart hold
        await svc.delete_points("col", self._record_filter())
        assert sum(c[0] == "FT.SEARCH" for c in server.calls) == 3

    async def test_an_index_that_turns_busy_between_the_check_and_the_record_holds_the_delete(self) -> None:
        """FT.INFO says idle, then the index is re-created over the stored hashes before the recording script runs: the script's answer decides."""
        server = _Server(*self.FIELDS)
        svc = server.store()
        real = server.execute
        infos = 0

        async def execute(*args: object) -> object:
            nonlocal infos
            reply = await real(*args)
            infos += args[0] == "FT.INFO"
            if infos == 2:  # the first is the schema lookup, the second the readiness check
                server.scan_polls = 10**6
            return reply

        svc.client.execute_command.side_effect = execute
        with pytest.raises(IndexBackfilling):
            await svc.delete_points("col", self._record_filter())
        assert not server.searched()

    @staticmethod
    def _recreated_mid_walk(server: _Server, svc: RedisVectorService, pages: list[list[bytes]]) -> None:
        """The collection is re-created while the walk runs, over hashes an asynchronous FT.DROPINDEX DD had not removed: from the
        second query on, the index scans (and its record of complete fields is gone) until a few checks later. Each FT.SEARCH /
        FT.AGGREGATE answers the next page of keys from ``pages``."""
        real = server.execute
        queries = 0

        async def execute(*args: object) -> object:
            nonlocal queries
            if args[0] not in ("FT.SEARCH", "FT.AGGREGATE"):
                return await real(*args)
            server.calls.append(args)
            queries += 1
            if queries == 2:
                server.scan_polls, server.sets = 3, {}
            keys = pages.pop(0) if pages else []
            if args[0] == "FT.AGGREGATE":
                return [[len(keys), *[[b"__key", k] for k in keys]], 0]
            return [len(keys), *keys]

        svc.client.execute_command.side_effect = execute

    async def test_a_scan_that_starts_during_a_delete_is_waited_out_and_its_matches_deleted(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "30")
        server = _Server(*self.FIELDS)
        svc = server.store()
        # page 1, then the re-created index has not reached the rest (empty page), then after its scan the rest shows up
        self._recreated_mid_walk(server, svc, [[b"col:a"], [], [b"col:b"], []])
        await svc.delete_points("col", self._record_filter())
        deleted = [k for c in svc.client.delete.await_args_list for k in c.args]
        assert deleted == [b"col:a", b"col:b"]

    async def test_a_scan_that_starts_during_a_payload_write_is_waited_out_and_its_matches_written(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "30")
        server = _Server(*self.FIELDS)
        svc = server.store()
        pipeline = MagicMock()
        pipeline.execute = AsyncMock(side_effect=lambda: [1] * len(pipeline.execute_command.call_args_list))
        svc.client.pipeline = MagicMock(return_value=pipeline)
        # count, keys (the scan starts here: the index reached only "a"), then after the scan: count, keys
        self._recreated_mid_walk(server, svc, [[b"col:a"], [b"col:a"], [b"col:a", b"col:b"], [b"col:a", b"col:b"]])
        await svc.set_payload("col", {"metadata.x": "1"}, self._record_filter())
        written = {c.args[1] for c in pipeline.execute_command.call_args_list}
        assert written == {b"col:a", b"col:b"}

    async def test_fields_created_with_the_index_wait_while_it_reads_the_documents_already_stored(self) -> None:
        """FT.CREATE over hashes already under the prefix scans them too: no field of the index is complete until it ends."""
        server = _Server(*self.FIELDS)
        server.scan_polls = 10**6
        with pytest.raises(IndexBackfilling):
            await server.store().delete_points("col", self._record_filter())
        assert not server.searched()

    async def test_a_payload_write_by_the_field_being_filled_waits_then_is_refused(self) -> None:
        server = _Server(*self.FIELDS)
        server.scan_polls = 10**6
        with pytest.raises(IndexBackfilling):
            await server.store().set_payload("col", {"metadata.x": "1"}, self._block_filter())
        assert not server.searched()

    @pytest.mark.parametrize("raw", ["bad", "-5", "inf", "nan"])
    async def test_an_unusable_bound_falls_back_to_the_default_or_zero(self, raw, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", raw)
        assert _backfill_wait_seconds() == (0.0 if raw == "-5" else _DEFAULT_BACKFILL_WAIT_SECONDS)

    async def test_a_read_is_not_held_back(self) -> None:
        names = ("metadata_orgId", "metadata_blockId")
        svc, execute = _service(_info(*names, indexing=1, percent="0.1"), [2])
        assert await svc.count_points("col", self._block_filter()) == 2

    async def test_a_reply_that_does_not_say_is_an_error_not_a_green_light(self) -> None:
        reply = _info("metadata_orgId", "metadata_blockId")
        del reply[-4:]
        svc, _ = _service(reply, reply)
        with pytest.raises(RuntimeError, match="still indexing"):
            await svc.delete_points("col", self._block_filter())


class TestStaleSchemaCache:
    """The index was dropped and rebuilt without a field this process had cached: the server says "Unknown field"."""

    @staticmethod
    def _filter() -> FilterExpression:
        return FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.blockId", values=["b1"]),
        ])

    async def test_the_server_refusal_becomes_the_typed_error_and_the_cache_heals(self) -> None:
        svc, execute = _service(
            _info("metadata_orgId", "metadata_blockId"),
            ResponseError("Unknown field at offset 7 near metadata_blockId"),
            _info("metadata_orgId"),
        )
        with pytest.raises(FilterFieldNotIndexed) as raised:
            await svc.count_points("col", self._filter())
        assert raised.value.fields == {"metadata_blockId"}
        assert isinstance(raised.value.__cause__, ResponseError)
        assert svc._schema_fields["col_idx"] == {"metadata_orgId"}

    async def test_a_field_that_is_back_in_the_schema_is_not_blamed(self) -> None:
        svc, _ = _service(
            _info("metadata_orgId", "metadata_blockId"),
            ResponseError("Unknown field at offset 7 near metadata_blockId"),
            _info("metadata_orgId", "metadata_blockId"),
        )
        with pytest.raises(ResponseError):
            await svc.count_points("col", self._filter())

    async def test_another_server_error_is_left_alone(self) -> None:
        svc, execute = _service(_info("metadata_orgId", "metadata_blockId"), ResponseError("Syntax error"))
        with pytest.raises(ResponseError, match="Syntax"):
            await svc.count_points("col", self._filter())
        assert len(_calls(execute, "FT.INFO")) == 1
