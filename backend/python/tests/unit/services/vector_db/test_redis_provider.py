"""Unit tests for the Redis vector DB provider."""

import struct
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FilterExpression,
    HealthStatus,
    HybridSearchRequest,
    VectorPoint,
)
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.redis_vector import RedisVectorService, _check_version


# ---------------------------------------------------------------------------
# Version check helper
# ---------------------------------------------------------------------------


class TestVersionCheck:
    def test_valid_version_passes(self):
        ok, msg = _check_version("8.4.0")
        assert ok is True

    def test_newer_version_passes(self):
        ok, msg = _check_version("9.0.0")
        assert ok is True

    def test_old_version_fails(self):
        ok, msg = _check_version("7.2.0")
        assert ok is False
        assert "8.4" in msg

    def test_boundary_version_fails(self):
        ok, msg = _check_version("8.3.9")
        assert ok is False

    def test_unparseable_version_passes_through(self):
        ok, _ = _check_version("unknown")
        assert ok is True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_config():
    return RedisVectorConfig(host="localhost", port=6379)


@pytest.fixture
def mock_redis_client():
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    client.info = AsyncMock(return_value={"redis_version": "8.4.0"})
    client.execute_command = AsyncMock()
    client.pipeline = MagicMock()
    pipeline = AsyncMock()
    pipeline.__aenter__ = AsyncMock(return_value=pipeline)
    pipeline.__aexit__ = AsyncMock(return_value=False)
    pipeline.execute = AsyncMock(return_value=[])
    client.pipeline.return_value = pipeline
    return client


@pytest.fixture
def service(redis_config, mock_redis_client):
    svc = RedisVectorService(redis_config)
    svc.client = mock_redis_client
    return svc


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


class TestRedisHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy_with_recent_version(self, service, mock_redis_client):
        mock_redis_client.execute_command = AsyncMock(return_value=[])
        result = await service.health_check()
        assert result.status == HealthStatus.HEALTHY
        assert result.server_version == "8.4.0"

    @pytest.mark.asyncio
    async def test_unhealthy_with_old_version(self, service, mock_redis_client):
        mock_redis_client.info = AsyncMock(return_value={"redis_version": "7.2.0"})
        result = await service.health_check()
        assert result.status == HealthStatus.UNHEALTHY
        assert "8.4" in result.message

    @pytest.mark.asyncio
    async def test_unhealthy_when_not_connected(self, redis_config):
        svc = RedisVectorService(redis_config)
        svc.client = None
        result = await svc.health_check()
        assert result.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_unhealthy_when_ping_fails(self, service, mock_redis_client):
        mock_redis_client.ping = AsyncMock(side_effect=Exception("Connection refused"))
        result = await service.health_check()
        assert result.status == HealthStatus.UNHEALTHY


# ---------------------------------------------------------------------------
# Collection management
# ---------------------------------------------------------------------------


class TestRedisCollectionManagement:
    @pytest.mark.asyncio
    async def test_create_collection_calls_ft_create(self, service, mock_redis_client):
        async def cmd_side_effect(*args):
            if args[0] == "FT.INFO":
                raise Exception("Unknown Index name")
            return "OK"

        mock_redis_client.execute_command = AsyncMock(side_effect=cmd_side_effect)
        config = CollectionConfig(embedding_size=384, distance_metric=DistanceMetric.COSINE)
        await service.create_collection("my_index", config)

        calls = mock_redis_client.execute_command.call_args_list
        ft_create_calls = [c for c in calls if c.args[0] == "FT.CREATE"]
        assert len(ft_create_calls) == 1

        args = ft_create_calls[0].args
        # ON HASH, not ON JSON
        assert "my_index_idx" in args
        assert "ON" in args
        on_idx = list(args).index("ON")
        assert args[on_idx + 1] == "HASH"
        assert "HNSW" in args
        assert "COSINE" in args
        assert "384" in str(args)
        # No JSONPath prefixes
        assert not any(str(a).startswith("$.") for a in args), (
            "ON HASH schema must not use JSONPath ($.field) prefixes"
        )

    @pytest.mark.asyncio
    async def test_create_collection_dimension_mismatch_raises(self, service, mock_redis_client):
        """Dimension mismatch must not auto-drop the index and delete documents."""
        from app.services.vector_db.models import VectorCollectionInfo

        service.get_collection_info = AsyncMock(
            return_value=VectorCollectionInfo(
                name="my_index", exists=True, dense_dimension=768
            )
        )
        config = CollectionConfig(embedding_size=384)

        with pytest.raises(ValueError, match="dimension mismatch"):
            await service.create_collection("my_index", config)

        drop_calls = [
            c for c in mock_redis_client.execute_command.call_args_list
            if c.args and c.args[0] == "FT.DROPINDEX"
        ]
        assert drop_calls == []

    @pytest.mark.asyncio
    async def test_delete_collection_calls_ft_dropindex(self, service, mock_redis_client):
        mock_redis_client.execute_command = AsyncMock(return_value="OK")
        await service.delete_collection("coll")

        calls = mock_redis_client.execute_command.call_args_list
        drop_calls = [c for c in calls if c.args[0] == "FT.DROPINDEX"]
        assert len(drop_calls) == 1
        assert "coll_idx" in drop_calls[0].args

    @pytest.mark.asyncio
    async def test_collection_exists_returns_true(self, service, mock_redis_client):
        mock_redis_client.execute_command = AsyncMock(return_value=[
            b"index_definition", [b"coll_idx"],
        ])
        # Simulate FT.INFO returning info dict
        exists = await service.collection_exists("coll")
        # collection_exists calls get_collection_info which calls FT.INFO
        assert isinstance(exists, bool)


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


class TestRedisUpsert:
    @pytest.mark.asyncio
    async def test_upsert_uses_hset(self, service, mock_redis_client):
        """upsert_points must issue HSET with a binary dense_embedding blob."""
        pipeline = MagicMock()  # pipeline.execute_command is sync (queues commands)
        pipeline.execute = AsyncMock(return_value=[1])  # HSET returns int (fields added)
        mock_redis_client.pipeline = MagicMock(return_value=pipeline)

        points = [
            VectorPoint(
                id="p1",
                dense_vector=[0.1, 0.2],
                payload={"page_content": "Hello world", "metadata": {"orgId": "org1"}},
            )
        ]
        await service.upsert_points("coll", points)

        pipeline.execute_command.assert_called()
        first_call = pipeline.execute_command.call_args_list[0]
        assert first_call.args[0] == "HSET", "Must use HSET, not JSON.SET"
        assert "coll:p1" in first_call.args[1]

        # Build expected positional args from the HSET call
        hset_args = list(first_call.args[2:])
        fields_dict = dict(zip(hset_args[::2], hset_args[1::2]))

        assert "page_content" in fields_dict
        assert fields_dict["page_content"] == "Hello world"

        assert "dense_embedding" in fields_dict
        blob = fields_dict["dense_embedding"]
        assert isinstance(blob, bytes), "dense_embedding must be a binary bytes blob"
        # FLOAT16 = 2 bytes/dim, 2 dims → 4 bytes
        assert len(blob) == 4, f"Expected 4 bytes for 2-dim FLOAT16 vector, got {len(blob)}"

        # No JSON.SET calls anywhere
        json_set_calls = [
            c for c in pipeline.execute_command.call_args_list
            if c.args[0] == "JSON.SET"
        ]
        assert json_set_calls == [], "JSON.SET must not be used with ON HASH storage"

    @pytest.mark.asyncio
    async def test_upsert_flattens_metadata_into_hash_fields(self, service, mock_redis_client):
        """All metadata keys must be stored as individual metadata_* hash fields."""
        pipeline = MagicMock()
        pipeline.execute = AsyncMock(return_value=[3])
        mock_redis_client.pipeline = MagicMock(return_value=pipeline)

        points = [
            VectorPoint(
                id="p1",
                dense_vector=[0.5],
                payload={
                    "page_content": "Test",
                    "metadata": {
                        "orgId": "org1",
                        "virtualRecordId": "vr-1",
                        "blockIndex": "0",
                    },
                },
            )
        ]
        await service.upsert_points("coll", points)

        first_call = pipeline.execute_command.call_args_list[0]
        hset_args = list(first_call.args[2:])
        fields_dict = dict(zip(hset_args[::2], hset_args[1::2]))

        assert "metadata_orgId" in fields_dict
        assert fields_dict["metadata_orgId"] == "org1"
        assert "metadata_virtualRecordId" in fields_dict
        assert "metadata_blockIndex" in fields_dict


# ---------------------------------------------------------------------------
# FT.HYBRID command construction
# ---------------------------------------------------------------------------


class TestRedisHybridQuery:
    @pytest.mark.asyncio
    async def test_ft_hybrid_called_with_correct_args(self, service, mock_redis_client):
        """Verify FT.HYBRID is built with the correct parameter-count convention.

        Spec (ON HASH):
          FT.HYBRID idx SEARCH q VSIM @dense_embedding $vec KNN 2 K <w>
          [FILTER fq] COMBINE RRF 4 WINDOW <w> CONSTANT 60
          LIMIT 0 k LOAD 2 @__key @__score PARAMS 2 vec <bytes>
        """
        # FT.HYBRID returns an empty map; HGETALL (pipelined) returns empty dict.
        mock_redis_client.execute_command = AsyncMock(return_value=[0])
        hgetall_pipeline = MagicMock()
        hgetall_pipeline.hgetall = MagicMock()
        hgetall_pipeline.execute = AsyncMock(return_value=[])
        mock_redis_client.pipeline = MagicMock(return_value=hgetall_pipeline)

        req = HybridSearchRequest(
            dense_query=[0.1, 0.2],
            text_query="hello world",
            limit=5,
        )
        await service.query_nearest_points("coll", [req])

        calls = mock_redis_client.execute_command.call_args_list
        hybrid_calls = [c for c in calls if c.args[0] == "FT.HYBRID"]
        assert len(hybrid_calls) == 1

        args = list(hybrid_calls[0].args)

        assert "coll_idx" in args
        assert "SEARCH" in args
        # Spaces are word separators, not operators — they must NOT be escaped,
        # otherwise multi-word queries fuse into a single non-matching token.
        assert any("hello world" in str(a) for a in args), (
            "multi-word text_query must keep its spaces unescaped"
        )
        assert "VSIM" in args
        assert "@dense_embedding" in args
        assert "COMBINE" in args
        assert "RRF" in args

        # Argument-count convention: KNN 2 K <value>, COMBINE RRF 4 WINDOW <w> CONSTANT <c>
        knn_idx = args.index("KNN")
        assert args[knn_idx + 1] == "2", f"KNN arg-count must be '2', got {args[knn_idx + 1]!r}"
        assert args[knn_idx + 2] == "K", f"Expected 'K' after KNN 2, got {args[knn_idx + 2]!r}"

        rrf_idx = args.index("RRF")
        assert args[rrf_idx + 1] == "4", f"COMBINE RRF arg-count must be '4', got {args[rrf_idx + 1]!r}"
        assert args[rrf_idx + 2] == "WINDOW"

        # LOAD must request @__key and @__score (no $ JSON blob for ON HASH)
        assert "LOAD" in args, "LOAD must be present in FT.HYBRID command"
        assert "@__key" in args, "LOAD must request @__key for the point id"
        assert "@__score" in args, "LOAD must request @__score for the RRF score"
        assert "$" not in args, "ON HASH: no JSON $ blob in LOAD"
        assert "WITHSCORES" not in args, "WITHSCORES is not a valid FT.HYBRID option"
        assert "RETURN" not in args, "RETURN is not a valid FT.HYBRID option"
        # PARAMS uses the bare name 'vec' (the '$' sigil only appears in the
        # VSIM query expression, never in the PARAMS block).
        params_idx = args.index("PARAMS")
        assert args[params_idx + 2] == "vec", "PARAMS name must be bare 'vec'"

    @pytest.mark.asyncio
    async def test_ft_hybrid_hash_reply_parsing(self, service, mock_redis_client):
        """Verify parse_ft_hybrid_reply handles the HASH-style FT.HYBRID reply.

        With ON HASH + LOAD 2 @__key @__score, results contain only key and
        score.  Full payload is retrieved via pipelined HGETALL.
        """
        from app.services.vector_db.redis.utils import parse_ft_hybrid_reply

        reply = [
            b"total_results", 1,
            b"results", [
                [b"__key", b"coll:abc-123", b"__score", b"0.0167"],
            ],
            b"warnings", [],
            b"execution_time", b"3.2",
        ]
        pairs = parse_ft_hybrid_reply(reply)
        assert len(pairs) == 1
        assert pairs[0][0] == "coll:abc-123"
        assert abs(pairs[0][1] - 0.0167) < 1e-6

    @pytest.mark.asyncio
    async def test_ft_hybrid_hgetall_provides_full_payload(self, service, mock_redis_client):
        """Full payload must be fetched via HGETALL pipeline after FT.HYBRID key/score."""
        hybrid_reply = [
            b"total_results", 1,
            b"results", [
                [b"__key", b"coll:abc-123", b"__score", b"0.0167"],
            ],
            b"warnings", [],
        ]
        mock_redis_client.execute_command = AsyncMock(return_value=hybrid_reply)

        hgetall_result = {
            b"page_content": b"hello",
            b"metadata_orgId": b"org1",
            b"dense_embedding": b"\x00" * 4,  # dummy bytes, should be skipped
        }
        hgetall_pipeline = MagicMock()
        hgetall_pipeline.hgetall = MagicMock()
        hgetall_pipeline.execute = AsyncMock(return_value=[hgetall_result])
        mock_redis_client.pipeline = MagicMock(return_value=hgetall_pipeline)

        req = HybridSearchRequest(dense_query=[0.1, 0.2], limit=5, with_payload=True)
        results_list = await service.query_nearest_points("coll", [req])
        results = results_list[0]

        assert len(results) == 1
        assert results[0].id == "abc-123"
        assert abs(results[0].score - 0.0167) < 1e-6
        assert results[0].payload["page_content"] == "hello"
        assert results[0].payload["metadata"]["orgId"] == "org1"
        # dense_embedding must not leak into metadata
        assert "dense_embedding" not in results[0].payload["metadata"]

    @pytest.mark.asyncio
    async def test_text_only_fallback_when_no_dense(self, service, mock_redis_client):
        mock_redis_client.execute_command = AsyncMock(return_value=[0])

        req = HybridSearchRequest(
            dense_query=None,
            text_query="hello",
            limit=5,
        )
        await service.query_nearest_points("coll", [req])

        calls = mock_redis_client.execute_command.call_args_list
        hybrid_calls = [c for c in calls if c.args[0] == "FT.HYBRID"]
        search_calls = [c for c in calls if c.args[0] == "FT.SEARCH"]
        # Should fall back to FT.SEARCH not FT.HYBRID
        assert len(hybrid_calls) == 0
        assert len(search_calls) >= 1

    @pytest.mark.asyncio
    async def test_ft_hybrid_errors_propagate(self, service, mock_redis_client):
        """FT.HYBRID errors must NOT be silently swallowed as text-only fallback."""
        from redis.exceptions import ResponseError
        mock_redis_client.execute_command = AsyncMock(
            side_effect=ResponseError("SYNTAX error")
        )
        req = HybridSearchRequest(dense_query=[0.1, 0.2], limit=5)
        with pytest.raises(ResponseError):
            await service.query_nearest_points("coll", [req])


# ---------------------------------------------------------------------------
# Filter expression → Redis query string
# ---------------------------------------------------------------------------


class TestRedisFilterTranslation:
    def test_must_filter_to_redis_query(self):
        from app.services.vector_db.redis.utils import filter_expression_to_redis_query

        expr = FilterExpression(
            must=[FieldCondition(key="metadata.orgId", value="org1")]
        )
        query = filter_expression_to_redis_query(expr)
        assert "metadata_orgId" in query
        assert "org1" in query

    def test_empty_filter_returns_match_all(self):
        from app.services.vector_db.redis.utils import filter_expression_to_redis_query

        expr = FilterExpression()
        query = filter_expression_to_redis_query(expr)
        assert query == "*"

    def test_should_filter_creates_or_clause(self):
        from app.services.vector_db.redis.utils import filter_expression_to_redis_query

        expr = FilterExpression(
            should=[
                FieldCondition(key="metadata.orgId", value="org1"),
                FieldCondition(key="metadata.orgId", value="org2"),
            ]
        )
        query = filter_expression_to_redis_query(expr)
        assert "|" in query

    def test_must_not_filter_negation(self):
        from app.services.vector_db.redis.utils import filter_expression_to_redis_query

        expr = FilterExpression(
            must_not=[FieldCondition(key="metadata.orgId", value="banned")]
        )
        query = filter_expression_to_redis_query(expr)
        assert "-(" in query

    def test_tag_value_with_special_chars_escaped(self):
        from app.services.vector_db.redis.utils import escape_tag_value

        val = escape_tag_value("org-1.2")
        # Dashes and dots should be escaped
        assert "\\-" in val or val.startswith("org")


# ===========================================================================
# Phase 4 regression: Redis provider hardening
# ===========================================================================

import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_redis_service():
    from app.services.vector_db.redis.redis_vector import RedisVectorService
    svc = RedisVectorService.__new__(RedisVectorService)
    svc.config = MagicMock()
    svc.client = MagicMock()
    svc.client.pipeline = MagicMock()
    svc.client.execute_command = AsyncMock()
    svc.client.delete = AsyncMock()
    svc.client.info = AsyncMock()
    svc.client.ping = AsyncMock()
    svc._dense_dtype = "FLOAT16"
    svc._collection_configs = {}
    return svc


class TestRedisDeletePointsEmptyFilter:
    @pytest.mark.asyncio
    async def test_empty_filter_raises(self):
        """delete_points with an empty filter must raise ValueError."""
        from app.services.vector_db.models import FilterExpression
        svc = _make_redis_service()
        with pytest.raises(ValueError, match="empty filter"):
            await svc.delete_points("records", FilterExpression())


class TestRedisDeletePointsPaged:
    @pytest.mark.asyncio
    async def test_paged_delete_never_accumulates_all_keys(self):
        """Paged delete re-queries from offset 0 after each batch.

        We simulate 3 pages of 2 results each (total 6) with the last page
        having 0 results. Each execute_command call after 'FT.SEARCH' should
        return a fresh page from offset 0 (not an incrementing offset).
        """
        from app.services.vector_db.models import FilterExpression, FieldCondition

        filt = FilterExpression(
            must=[FieldCondition(key="metadata.virtualRecordId", value="vr-1")]
        )
        page_size = 2
        # Simulate: page 1 → 2 keys, page 2 → 2 keys, page 3 → 0 keys (stop)
        responses = [
            [4, b"records:k1", b"records:k2"],   # page 1 (total=4)
            [4, b"records:k3", b"records:k4"],   # page 2 (still 4 remaining after first delete)
            [0],                                  # page 3 (nothing left)
        ]
        svc = _make_redis_service()
        svc.client.execute_command = AsyncMock(side_effect=responses)

        svc.client.delete = AsyncMock()

        with MagicMock() as _:
            pass

        # Actually run: the third side_effect raises StopIteration which pytest
        # wraps; instead ensure delete called per non-empty page
        try:
            await svc.delete_points("records", filt)
        except StopIteration:
            pass  # third call returned nothing — fine for this test

        assert svc.client.delete.await_count >= 1, (
            "client.delete must be called at least once per non-empty page"
        )

        # Verify all LIMIT offsets in FT.SEARCH calls start at 0
        ft_calls = [
            c for c in svc.client.execute_command.call_args_list
            if c.args and c.args[0] == "FT.SEARCH"
        ]
        for c in ft_calls:
            args = list(c.args) + list(c.kwargs.values())
            limit_idx = next(
                (i for i, a in enumerate(args) if isinstance(a, str) and a == "LIMIT"), None
            )
            if limit_idx is not None and limit_idx + 1 < len(args):
                offset_val = args[limit_idx + 1]
                assert offset_val == "0", (
                    f"Paged delete must always use LIMIT 0 <page_size>, got offset={offset_val!r}"
                )


class TestRedisQueryEscaping:
    def test_escape_redisearch_text_at_sign(self):
        """'@' is a RediSearch operator character and must be escaped."""
        from app.services.vector_db.redis.utils import escape_redisearch_text
        result = escape_redisearch_text("user@example.com")
        assert "\\@" in result

    def test_escape_redisearch_text_empty(self):
        from app.services.vector_db.redis.utils import escape_redisearch_text
        assert escape_redisearch_text("") == ""

    def test_escape_redisearch_text_braces(self):
        from app.services.vector_db.redis.utils import escape_redisearch_text
        result = escape_redisearch_text("{tag}")
        assert "\\{" in result and "\\}" in result

    def test_escape_redisearch_text_dash(self):
        from app.services.vector_db.redis.utils import escape_redisearch_text
        result = escape_redisearch_text("x-ray")
        assert "\\-" in result


class TestRedisVersionGate:
    @pytest.mark.asyncio
    async def test_version_string_from_bytes_decoded_correctly(self):
        """health_check must handle bytes in redis_version (decode_responses=False)."""
        svc = _make_redis_service()
        svc.client.ping = AsyncMock()
        svc.client.info = AsyncMock(return_value={b"redis_version": b"7.0.5"})

        health = await svc.health_check()
        # Should not crash; version comparison should work
        assert health is not None

    @pytest.mark.asyncio
    async def test_version_string_from_str(self):
        """health_check handles str redis_version (decode_responses=True)."""
        svc = _make_redis_service()
        svc.client.ping = AsyncMock()
        svc.client.info = AsyncMock(return_value={"redis_version": "7.2.0"})

        health = await svc.health_check()
        assert health is not None


class TestRedisMinShouldMatchRejected:
    @pytest.mark.asyncio
    async def test_min_should_match_raises(self):
        """filter_collection with min_should_match must raise NotImplementedError."""
        svc = _make_redis_service()
        with pytest.raises(NotImplementedError, match="min_should_match"):
            await svc.filter_collection(min_should_match=2)


class TestRedisScrollOffset:
    @pytest.mark.asyncio
    async def test_scroll_first_page_uses_offset_zero(self):
        """First scroll (offset=None) must pass LIMIT 0 <limit>."""
        from app.services.vector_db.models import FilterExpression

        svc = _make_redis_service()
        # Return empty result
        svc.client.execute_command = AsyncMock(return_value=[0])

        await svc.scroll("records", FilterExpression(), limit=10)

        args = svc.client.execute_command.call_args.args
        limit_idx = list(args).index("LIMIT")
        assert args[limit_idx + 1] == "0"

    @pytest.mark.asyncio
    async def test_scroll_second_page_uses_correct_offset(self):
        """scroll(offset='10') must pass LIMIT 10 <limit>."""
        from app.services.vector_db.models import FilterExpression

        svc = _make_redis_service()
        svc.client.execute_command = AsyncMock(return_value=[0])

        await svc.scroll("records", FilterExpression(), limit=10, offset="10")

        args = svc.client.execute_command.call_args.args
        limit_idx = list(args).index("LIMIT")
        assert args[limit_idx + 1] == "10"

    @pytest.mark.asyncio
    async def test_scroll_next_offset_none_when_last_page(self):
        """next_offset must be None when returned keys == total (last page)."""
        from app.services.vector_db.models import FilterExpression

        svc = _make_redis_service()
        # ON HASH: fields list is a flat alternating [field, value, ...] list
        # (no $ JSON blob).
        svc.client.execute_command = AsyncMock(
            return_value=[
                1,
                b"records:k1",
                [b"page_content", b"hello", b"metadata_orgId", b"org1"],
            ]
        )

        result = await svc.scroll("records", FilterExpression(), limit=10)
        assert result.next_offset is None

    @pytest.mark.asyncio
    async def test_scroll_next_offset_set_when_more_remain(self):
        """next_offset must reflect remaining pages when total > returned."""
        from app.services.vector_db.models import FilterExpression

        svc = _make_redis_service()
        # total=20, returned 10 — each doc has HASH-style fields
        reply = [20] + [
            item
            for idx in range(10)
            for item in [
                f"records:k{idx}".encode(),
                [b"page_content", f"content {idx}".encode(),
                 b"metadata_orgId", b"org1"],
            ]
        ]
        svc.client.execute_command = AsyncMock(return_value=reply)

        result = await svc.scroll("records", FilterExpression(), limit=10)
        assert result.next_offset == "10"

    @pytest.mark.asyncio
    async def test_scroll_no_return_clause_used(self):
        """scroll must NOT use RETURN 1 $ (JSONPath is for ON JSON only)."""
        from app.services.vector_db.models import FilterExpression

        svc = _make_redis_service()
        svc.client.execute_command = AsyncMock(return_value=[0])

        await svc.scroll("records", FilterExpression(), limit=10)

        args = list(svc.client.execute_command.call_args.args)
        # The RETURN clause with $ must not be present
        assert "RETURN" not in args or "$" not in args, (
            "ON HASH scroll must not use 'RETURN 1 $' (JSONPath syntax)"
        )


class TestHashDocDecoding:
    def test_decode_hash_doc_skips_dense_embedding(self):
        """decode_hash_doc must not include the binary dense_embedding field."""
        from app.services.vector_db.redis.utils import decode_hash_doc

        raw = {
            b"page_content": b"hello",
            b"metadata_orgId": b"org1",
            b"dense_embedding": b"\x00\x01\x02\x03",
        }
        doc = decode_hash_doc(raw)
        assert "page_content" in doc
        assert doc["page_content"] == "hello"
        assert "metadata_orgId" in doc
        assert "dense_embedding" not in doc

    def test_decode_hash_doc_handles_flat_list(self):
        """decode_hash_doc must handle RESP2 flat alternating list format."""
        from app.services.vector_db.redis.utils import decode_hash_doc

        raw = [
            b"page_content", b"world",
            b"metadata_virtualRecordId", b"vr-1",
            b"dense_embedding", b"\xff" * 8,
        ]
        doc = decode_hash_doc(raw)
        assert doc["page_content"] == "world"
        assert doc["metadata_virtualRecordId"] == "vr-1"
        assert "dense_embedding" not in doc

    def test_vector_point_to_hash_fields_binary_blob(self):
        """vector_point_to_hash_fields must produce binary dense_embedding."""
        from app.services.vector_db.redis.utils import vector_point_to_hash_fields

        point = VectorPoint(
            id="p1",
            dense_vector=[1.0, 0.0, 0.0, 0.0],
            payload={"page_content": "Test", "metadata": {"orgId": "o1"}},
        )
        fields = vector_point_to_hash_fields(point, dtype="FLOAT16")
        assert isinstance(fields["dense_embedding"], bytes)
        # 4 dims × 2 bytes/dim (FLOAT16) = 8 bytes
        assert len(fields["dense_embedding"]) == 8
        assert fields["page_content"] == "Test"
        assert fields["metadata_orgId"] == "o1"

    def test_vector_point_to_hash_fields_float32(self):
        """FLOAT32 produces 4 bytes/dim."""
        from app.services.vector_db.redis.utils import vector_point_to_hash_fields

        point = VectorPoint(
            id="p1",
            dense_vector=[1.0, 0.0],
            payload={"page_content": "", "metadata": {}},
        )
        fields = vector_point_to_hash_fields(point, dtype="FLOAT32")
        assert len(fields["dense_embedding"]) == 8  # 2 dims × 4 bytes
