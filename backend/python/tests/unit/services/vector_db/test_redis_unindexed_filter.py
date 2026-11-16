"""The Redis provider's refusal of a filter on a field its index lacks, without a server.

The same behaviour against a real Redis Stack is in
``tests/integration/vector_db/test_redis_unindexed_filter.py``.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import ResponseError

from app.exceptions.non_retryable import NonRetryableError
from app.services.vector_db.models import FieldCondition, FilterExpression
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
        svc, execute = _service(_info("metadata_orgId"), b"OK", _info("metadata_orgId", "metadata_blockId"))
        await svc._indexed_fields("col_idx")
        await svc.create_index("col", "metadata.blockId", {"type": "keyword"})
        assert "col_idx" not in svc._schema_fields
        assert await svc._indexed_fields("col_idx") == {"metadata_orgId", "metadata_blockId"}

    async def test_a_failed_alter_drops_it_too(self) -> None:
        svc, _ = _service(_info("metadata_orgId"), ResponseError("boom"))
        await svc._indexed_fields("col_idx")
        with pytest.raises(ResponseError):
            await svc.create_index("col", "metadata.blockId", {"type": "keyword"})
        assert "col_idx" not in svc._schema_fields

    async def test_delete_collection_drops_it(self) -> None:
        svc, _ = _service(_info("metadata_orgId"), b"OK")
        await svc._indexed_fields("col_idx")
        await svc.delete_collection("col")
        assert "col_idx" not in svc._schema_fields


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

    @pytest.mark.parametrize("op", ["count", "scroll", "keys"])
    async def test_reads_and_scans_are_refused_too(self, op) -> None:
        svc, execute = _service(_info("metadata_orgId"), _info("metadata_orgId"))
        with pytest.raises(FilterFieldNotIndexed):
            if op == "count":
                await svc.count_points("col", self._filter())
            elif op == "scroll":
                await svc.scroll("col", self._filter(), limit=5)
            else:
                await svc._keys_matching_filter("col", self._filter())
        assert not _calls(execute, "FT.SEARCH") and not _calls(execute, "FT.AGGREGATE")


class TestDialectIsPinned:
    async def test_every_search_and_aggregate_carries_dialect_2(self) -> None:
        schema = _info("metadata_orgId", "metadata_virtualRecordId")
        svc, execute = _service(schema, [1], [1], schema, [1, b"k"], [0])
        flt = FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.virtualRecordId", value="v"),
        ])
        await svc.count_points("col", flt)
        await svc.scroll("col", flt, limit=3)
        await svc.delete_points("col", flt)
        queries = _calls(execute, "FT.SEARCH") + _calls(execute, "FT.AGGREGATE")
        assert len(queries) >= 3
        for args in queries:
            assert args[args.index("DIALECT") + 1] == "2", args
            assert args.index("DIALECT") == 3  # straight after the query, ahead of every option that has its own arguments


class TestBackfill:
    """``FT.ALTER`` returns at once and indexes the stored documents in the background: a delete in between would find only some of them."""

    @staticmethod
    def _filter() -> FilterExpression:
        return FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.blockId", values=["b1"]),
        ])

    @pytest.fixture(autouse=True)
    def _fast_wait(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "0.05")
        monkeypatch.setattr("app.services.vector_db.redis.redis_vector._BACKFILL_POLL_SECONDS", 0.01)

    @staticmethod
    def _old_field_filter() -> FilterExpression:
        return FilterExpression(must=[
            FieldCondition(key="metadata.orgId", value=ORG),
            FieldCondition(key="metadata.virtualRecordId", value="vr1"),
        ])

    @pytest.mark.parametrize(("indexing", "percent"), [(1, "0.4"), (1, "1"), (0, "0.9")])
    async def test_a_delete_by_the_field_being_filled_raises_after_the_bound(self, indexing, percent) -> None:
        names = ("metadata_orgId", "metadata_blockId")
        busy = _info(*names, indexing=indexing, percent=percent)
        svc, execute = _service(_info(*names), *[busy] * 500)
        with pytest.raises(IndexBackfilling) as raised:
            await svc.delete_points("col", self._filter())
        assert raised.value.index == "col_idx"
        assert not isinstance(raised.value, NonRetryableError)
        assert not _calls(execute, "FT.SEARCH") and not _calls(execute, "FT.AGGREGATE")
        svc.client.delete.assert_not_awaited()

    async def test_a_delete_by_the_field_being_filled_waits_for_the_scan_then_runs(self, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", "30")
        names = ("metadata_orgId", "metadata_blockId")
        busy = _info(*names, indexing=1, percent="0.3")
        svc, execute = _service(_info(*names), busy, busy, busy, _info(*names), [0])
        await svc.delete_points("col", self._filter())
        assert len(_calls(execute, "FT.INFO")) == 5  # one schema check, three polls that saw the scan, one that saw it done
        assert len(_calls(execute, "FT.SEARCH")) == 1

    async def test_a_delete_by_fields_already_complete_runs_during_the_scan(self) -> None:
        names = ("metadata_orgId", "metadata_virtualRecordId", "metadata_blockId")
        busy = _info(*names, indexing=1, percent="0.3")
        svc, execute = _service(_info(*names), busy, [0])
        await svc.delete_points("col", self._old_field_filter())
        assert len(_calls(execute, "FT.INFO")) == 2
        assert len(_calls(execute, "FT.SEARCH")) == 1

    async def test_a_field_this_process_added_is_waited_for_even_when_the_index_was_created_with_it(self) -> None:
        names = ("metadata_orgId", "metadata_virtualRecordId")
        busy = _info(*names, indexing=1, percent="0.3")
        svc, _ = _service(None, _info(*names), *[busy] * 500)
        await svc.create_index("col", "metadata.virtualRecordId", {"type": "keyword"})
        with pytest.raises(IndexBackfilling):
            await svc.delete_points("col", self._old_field_filter())

    async def test_a_payload_write_by_the_field_being_filled_waits_then_is_refused(self) -> None:
        names = ("metadata_orgId", "metadata_blockId")
        svc, execute = _service(_info(*names), *[_info(*names, indexing=1, percent="0.2")] * 500)
        with pytest.raises(IndexBackfilling):
            await svc.set_payload("col", {"metadata.x": "1"}, self._filter())
        assert not _calls(execute, "FT.SEARCH") and not _calls(execute, "FT.AGGREGATE")

    @pytest.mark.parametrize("raw", ["bad", "-5"])
    async def test_an_unusable_bound_falls_back_to_the_default_or_zero(self, raw, monkeypatch) -> None:
        monkeypatch.setenv("REDIS_VECTOR_BACKFILL_WAIT_SECONDS", raw)
        assert _backfill_wait_seconds() == (_DEFAULT_BACKFILL_WAIT_SECONDS if raw == "bad" else 0.0)

    async def test_a_delete_goes_ahead_once_the_index_has_caught_up(self) -> None:
        names = ("metadata_orgId", "metadata_blockId")
        svc, execute = _service(_info(*names), _info(*names), [0])
        await svc.delete_points("col", self._filter())
        assert len(_calls(execute, "FT.SEARCH")) == 1

    async def test_a_read_is_not_held_back(self) -> None:
        names = ("metadata_orgId", "metadata_blockId")
        svc, execute = _service(_info(*names, indexing=1, percent="0.1"), [2])
        assert await svc.count_points("col", self._filter()) == 2

    async def test_a_reply_that_does_not_say_is_an_error_not_a_green_light(self) -> None:
        reply = _info("metadata_orgId", "metadata_blockId")
        del reply[-4:]
        svc, _ = _service(reply, reply)
        with pytest.raises(RuntimeError, match="still indexing"):
            await svc.delete_points("col", self._filter())


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
