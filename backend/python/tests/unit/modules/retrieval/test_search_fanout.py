"""The search path across one and many collections.

Covers the two decisions that only exist because a search can span
collections: which ones to query (narrowing), and how to reduce their answers
to one top-K (merging). Both must leave a `single` deployment behaving exactly
as it did before either existed.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.retrieval.result_merging import (
    ReciprocalRankFusionMerger,
    ScoreOrderedMerger,
)
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.vector_db.models import ScoreSemantics
from app.services.vector_db.strategies.per_connector_type import (
    PerConnectorTypeStrategy,
)
from app.services.vector_db.strategies.single import SingleCollectionStrategy
from tests.support.vector_db import make_collection_registry

pytestmark = pytest.mark.asyncio


def _point(pid, score, vrid="vr-1", block_id=None):
    p = MagicMock()
    p.id = pid
    p.score = score
    p.payload = {
        "page_content": pid,
        "metadata": {"virtualRecordId": vrid, "blockId": block_id or pid},
    }
    return p


def _service(
    *,
    strategy=None,
    collections=("records",),
    semantics=ScoreSemantics.RANK_FUSED,
    connector_types=None,
):
    """A RetrievalService with only the search collaborators wired."""
    registry = make_collection_registry("records")
    registry.strategy = strategy or SingleCollectionStrategy()
    registry.resolve_for_query = AsyncMock(return_value=list(collections))

    svc = RetrievalService.__new__(RetrievalService)
    svc.logger = MagicMock()
    svc.collection_registry = registry
    svc.vector_db_service = AsyncMock()
    svc._capabilities = MagicMock(
        supports_sparse_vectors=False,
        supports_server_side_text_search=False,
        score_semantics=semantics,
    )

    svc.graph_provider = AsyncMock()
    svc.graph_provider.get_accessible_connector_types = AsyncMock(
        return_value=list(connector_types or [])
    )
    svc.get_embedding_model_instance = AsyncMock(
        return_value=AsyncMock(aembed_query=AsyncMock(return_value=[0.1]))
    )
    svc._ensure_sparse_embedder = AsyncMock(return_value=None)
    return svc


class TestMergerSelection:
    def test_rank_fused_provider_gets_the_fusion_merger(self):
        svc = _service(semantics=ScoreSemantics.RANK_FUSED)
        assert isinstance(svc._result_merger, ReciprocalRankFusionMerger)

    def test_similarity_provider_gets_the_score_merger(self):
        svc = _service(semantics=ScoreSemantics.SIMILARITY)
        assert isinstance(svc._result_merger, ScoreOrderedMerger)


class TestNarrowing:
    async def test_single_strategy_never_pays_for_the_graph_lookup(self):
        """One collection cannot be narrowed, so the query would be pure
        overhead on every search."""
        svc = _service(strategy=SingleCollectionStrategy())

        await svc._resolve_search_collections("org-1", "user-1")

        svc.graph_provider.get_accessible_connector_types.assert_not_awaited()

    async def test_per_connector_type_narrows_to_the_users_connectors(self):
        svc = _service(
            strategy=PerConnectorTypeStrategy(),
            collections=("drive_records",),
            connector_types=["DRIVE"],
        )

        await svc._resolve_search_collections("org-1", "user-1")

        svc.graph_provider.get_accessible_connector_types.assert_awaited_once_with(
            "user-1", "org-1"
        )
        ctx = svc.collection_registry.resolve_for_query.await_args.args[0]
        assert ctx.accessible_connector_names == ["DRIVE"]

    async def test_no_connector_types_widens_rather_than_returning_nothing(self):
        """An empty list would resolve to no collections and silently return
        no results; the honest reading is "could not narrow"."""
        svc = _service(strategy=PerConnectorTypeStrategy(), connector_types=[])

        await svc._resolve_search_collections("org-1", "user-1")

        ctx = svc.collection_registry.resolve_for_query.await_args.args[0]
        assert ctx.accessible_connector_names is None

    async def test_a_graph_failure_widens_rather_than_failing_the_search(self):
        svc = _service(strategy=PerConnectorTypeStrategy())
        svc.graph_provider.get_accessible_connector_types = AsyncMock(
            side_effect=ConnectionError("graph down")
        )

        collections = await svc._resolve_search_collections("org-1", "user-1")

        assert collections == ["records"]
        svc.logger.warning.assert_called()

    async def test_no_user_id_skips_narrowing(self):
        svc = _service(strategy=PerConnectorTypeStrategy())

        await svc._resolve_search_collections("org-1", None)

        svc.graph_provider.get_accessible_connector_types.assert_not_awaited()


class TestFanOut:
    async def test_single_collection_keeps_the_providers_order(self):
        """The regression that would hit every existing deployment: a merge
        must not reorder what one collection already ranked."""
        svc = _service()
        svc.vector_db_service.query_nearest_points = AsyncMock(
            return_value=[[_point("a", 0.1), _point("b", 0.9), _point("c", 0.5)]]
        )

        merged = await svc._fan_out_searches(["records"], [MagicMock()], limit=10)

        assert [p.id for p in merged[0]] == ["a", "b", "c"]

    async def test_two_collections_are_fused_by_rank_not_score(self):
        """Collection b's scores are larger, but they are RRF artifacts — rank
        1 from each collection must lead."""
        svc = _service(collections=("a_records", "b_records"))

        async def _query(collection_name, requests):
            if collection_name == "a_records":
                return [[_point("a1", 0.01, block_id="a1"), _point("a2", 0.009, block_id="a2")]]
            return [[_point("b1", 0.99, block_id="b1"), _point("b2", 0.98, block_id="b2")]]

        svc.vector_db_service.query_nearest_points = AsyncMock(side_effect=_query)

        merged = await svc._fan_out_searches(
            ["a_records", "b_records"], [MagicMock()], limit=10
        )

        assert {p.id for p in merged[0][:2]} == {"a1", "b1"}

    async def test_every_query_variant_gets_its_own_merge(self):
        svc = _service(collections=("a_records", "b_records"))
        svc.vector_db_service.query_nearest_points = AsyncMock(
            return_value=[[_point("q1", 0.9, block_id="q1")], [_point("q2", 0.8, block_id="q2")]]
        )

        merged = await svc._fan_out_searches(
            ["a_records", "b_records"], [MagicMock(), MagicMock()], limit=10
        )

        assert len(merged) == 2
        assert [p.id for p in merged[0]] == ["q1"]
        assert [p.id for p in merged[1]] == ["q2"]

    async def test_one_failing_collection_degrades(self):
        svc = _service(collections=("a_records", "b_records"))

        async def _query(collection_name, requests):
            if collection_name == "a_records":
                raise ConnectionError("that shard is down")
            return [[_point("survivor", 0.5, block_id="s")]]

        svc.vector_db_service.query_nearest_points = AsyncMock(side_effect=_query)

        merged = await svc._fan_out_searches(
            ["a_records", "b_records"], [MagicMock()], limit=10
        )

        assert [p.id for p in merged[0]] == ["survivor"]
        svc.logger.warning.assert_called()

    async def test_every_collection_failing_raises(self):
        """An outage reported as "no documents found" sends the user off to
        reword a query that was never run."""
        svc = _service(collections=("a_records", "b_records"))
        svc.vector_db_service.query_nearest_points = AsyncMock(
            side_effect=ConnectionError("vector db down")
        )

        with pytest.raises(ConnectionError):
            await svc._fan_out_searches(
                ["a_records", "b_records"], [MagicMock()], limit=10
            )

    async def test_no_collections_returns_empty_batches(self):
        svc = _service()
        svc.vector_db_service.query_nearest_points = AsyncMock()

        merged = await svc._fan_out_searches([], [MagicMock(), MagicMock()], limit=10)

        assert merged == [[], []]
        svc.vector_db_service.query_nearest_points.assert_not_awaited()

    async def test_each_collection_is_asked_for_the_full_limit(self):
        """Splitting the budget would cost recall: nothing knows in advance
        which collection holds the best matches."""
        svc = _service(collections=("a_records", "b_records"))
        svc.vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
        request = MagicMock()
        request.limit = 20

        await svc._fan_out_searches(["a_records", "b_records"], [request], limit=20)

        for call in svc.vector_db_service.query_nearest_points.await_args_list:
            assert call.kwargs["requests"][0].limit == 20
