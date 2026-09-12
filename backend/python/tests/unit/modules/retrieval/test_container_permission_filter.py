"""Container-scoped permission filtering in `search_with_filters`.

The filter this builds *widens*: it admits records the user cannot read, and
`filter_accessible_virtual_record_ids` narrows the result back. So the tests
that matter most are the ones proving the narrowing actually happens, and the
ones proving the widening cannot become unbounded.

Two failure modes here disclose the entire corpus rather than raising, which is
why they get dedicated tests: an empty `must` makes OpenSearch treat the should
clauses as score-only, and an empty `should` list is silently dropped, leaving
`orgId` alone. Both would pass a naive "did it return results" check.

The flag is forced by patching the module constant, not the environment — it is
read once at import.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import math

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.exceptions.fastapi_responses import Status
from app.services.graph_db.interface.graph_db_provider import AccessibleContainers
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
)


@pytest.fixture(autouse=True)
def _clear_user_cache():
    mod._user_cache.clear()
    yield
    mod._user_cache.clear()


@pytest.fixture
def _patch_sparse():
    """Patch SparseEmbedder so no model is loaded."""
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder") as mock_cls:
        instance = AsyncMock()
        instance.embed_query = AsyncMock(
            return_value=MagicMock(indices=[0], values=[1.0])
        )
        mock_cls.return_value = instance
        yield instance


@pytest.fixture(autouse=True)
def _container_filter_on():
    """These tests describe the container path, which is off by default."""
    with patch(
        "app.modules.retrieval.retrieval_service.read_platform_feature_flag",
        new=AsyncMock(return_value=True),
    ) as flag:
        yield flag


@pytest.fixture
def retrieval_service(
    logger, mock_config_service, mock_vector_db_service, mock_graph_provider,
    mock_blob_store, _patch_sparse,
):
    from app.modules.retrieval.retrieval_service import RetrievalService

    registry = MagicMock()
    registry.strategy_name = "single"
    registry.resolve_for_query = AsyncMock(return_value=["test_collection"])

    return RetrievalService(
        logger=logger,
        config_service=mock_config_service,
        collection_registry=registry,
        vector_db_service=mock_vector_db_service,
        graph_provider=mock_graph_provider,
        blob_store=mock_blob_store,
    )


def _hit(vrid: str, score: float = 0.9) -> dict:
    return {
        "score": score,
        "content": "chunk text",
        "citationType": "vectordb|document",
        "metadata": {"virtualRecordId": vrid, "orgId": "o1"},
    }


def _containers(**kwargs) -> AccessibleContainers:
    return AccessibleContainers(**kwargs)


# ---------------------------------------------------------------------------
# Filter construction
# ---------------------------------------------------------------------------


class TestFilterConstruction:
    def test_all_buckets_become_one_should_clause_each(self, retrieval_service):
        must, should = retrieval_service._build_container_clauses(
            "o1",
            _containers(
                app_ids=frozenset({"kb-1"}),
                record_group_ids_trusted=frozenset({"rg-t"}),
                record_group_ids_verify=frozenset({"rg-v"}),
                direct_records={"v1": "r1"},
            ),
            None,
        )
        assert must == {"orgId": "o1"}
        assert should[CONNECTOR_IDS_FIELD] == ["kb-1"]
        assert set(should[RECORD_GROUP_IDS_FIELD]) == {"rg-t", "rg-v"}
        assert should["virtualRecordId"] == ["v1"]

    def test_trusted_and_verify_share_one_clause(self, retrieval_service):
        """The vector DB cannot tell them apart; a separate clause would only
        cost a term."""
        _, should = retrieval_service._build_container_clauses(
            "o1",
            _containers(
                record_group_ids_trusted=frozenset({"a"}),
                record_group_ids_verify=frozenset({"b"}),
            ),
            None,
        )
        assert len([k for k in should if k == RECORD_GROUP_IDS_FIELD]) == 1
        assert set(should[RECORD_GROUP_IDS_FIELD]) == {"a", "b"}

    def test_empty_buckets_yield_no_clause_not_an_empty_list(self, retrieval_service):
        """`build_conditions` drops empty lists, which would leave `orgId` alone
        and match the whole org."""
        _, should = retrieval_service._build_container_clauses(
            "o1", _containers(app_ids=frozenset({"a"})), None
        )
        assert RECORD_GROUP_IDS_FIELD not in should
        assert "virtualRecordId" not in should

    def test_nothing_reachable_returns_none(self, retrieval_service):
        assert retrieval_service._build_container_clauses("o1", _containers(), None) is None

    def test_must_always_carries_org_id(self, retrieval_service):
        """An empty `must` makes OpenSearch treat every should clause as
        score-only, matching the entire index."""
        must, _ = retrieval_service._build_container_clauses(
            "o1", _containers(app_ids=frozenset({"a"})), None
        )
        assert must["orgId"] == "o1"

    def test_tool_ids_narrow_via_must_containers_still_authorise(self, retrieval_service):
        must, should = retrieval_service._build_container_clauses(
            "o1", _containers(app_ids=frozenset({"a"})), ["v-tool"]
        )
        assert must["virtualRecordId"] == ["v-tool"]
        assert should[CONNECTOR_IDS_FIELD] == ["a"]


class TestRootScopedGroups:
    """Slack matches on a record's root group instead of its own, so a channel
    grant covers every thread under it without listing them."""

    def test_root_groups_get_their_own_clause(self, retrieval_service):
        must, should = retrieval_service._build_container_clauses(
            "o1", _containers(root_group_ids=frozenset({"channel-1"})), None
        )
        assert should[mod.ROOT_RECORD_GROUP_IDS_FIELD] == ["channel-1"]

    def test_no_clause_when_no_connector_is_root_scoped(self, retrieval_service):
        """Every other connector must keep matching on its own group ids."""
        must, should = retrieval_service._build_container_clauses(
            "o1", _containers(record_group_ids_verify=frozenset({"rg"})), None
        )
        assert mod.ROOT_RECORD_GROUP_IDS_FIELD not in should
        assert should[mod.RECORD_GROUP_IDS_FIELD] == ["rg"]

    def test_root_groups_alone_are_enough_to_search(self, retrieval_service):
        """A user reaching only Slack still gets a filter; returning None here
        would 404 them instead."""
        clauses = retrieval_service._build_container_clauses(
            "o1", _containers(root_group_ids=frozenset({"channel-1"})), None
        )
        assert clauses is not None

    def test_root_clause_does_not_displace_the_others(self, retrieval_service):
        must, should = retrieval_service._build_container_clauses(
            "o1",
            _containers(
                app_ids=frozenset({"kb"}),
                record_group_ids_verify=frozenset({"rg"}),
                root_group_ids=frozenset({"channel-1"}),
            ),
            None,
        )
        assert set(should) == {
            mod.CONNECTOR_IDS_FIELD,
            mod.RECORD_GROUP_IDS_FIELD,
            mod.ROOT_RECORD_GROUP_IDS_FIELD,
        }
        assert must == {"orgId": "o1"}


class TestFilterIsPassedToTheVectorDb:
    @pytest.mark.asyncio
    async def test_min_should_match_is_never_passed(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service
    ):
        """Redis raises NotImplementedError on it even with no should clauses,
        and all three providers already mean "at least one" when must is set."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        kwargs = mock_vector_db_service.filter_collection.await_args.kwargs
        assert "min_should_match" not in kwargs
        assert set(kwargs) == {"must", "should"}

    @pytest.mark.asyncio
    async def test_no_filter_is_built_when_nothing_is_reachable(
        self, retrieval_service, mock_graph_provider, mock_vector_db_service
    ):
        """The leak guard: an unbounded query must never be issued."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers()
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
        mock_vector_db_service.filter_collection.assert_not_awaited()
        retrieval_service._execute_parallel_searches.assert_not_awaited()


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


class TestAdjudication:
    @pytest.mark.asyncio
    async def test_denied_records_never_reach_the_response(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(record_group_ids_verify=frozenset({"rg"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v-allowed"), _hit("v-denied")]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v-allowed": "r-allowed"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(
            return_value=[{
                "_key": "r-allowed", "virtualRecordId": "v-allowed",
                "origin": "CONNECTOR", "recordName": "n", "mimeType": "text/plain",
                "connectorName": "X", "webUrl": "http://x",
            }]
        )

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        returned = {
            r["metadata"].get("virtualRecordId") for r in result.get("searchResults", [])
        }
        assert "v-denied" not in returned

    @pytest.mark.asyncio
    async def test_a_denied_hit_with_complete_metadata_is_still_dropped(
        self, retrieval_service, mock_graph_provider
    ):
        """The enrichment-loop guard, isolated from the completeness filter.

        Runs on the legacy path deliberately: the container path filters denied
        vids in _search_and_adjudicate before the loop is reached, so only here
        can a chunk arrive whose vid is absent from the accessible map — the
        stale-vector-index case. The chunk carries every required_fields entry
        pre-populated, so the metadata check downstream cannot mask the result.
        Drop the guard and this chunk is served to a user denied access to it.
        """
        denied = _hit("v-denied")
        denied["metadata"].update({
            "origin": "CONNECTOR",
            "recordName": "secret.txt",
            "recordId": "r-denied",
            "mimeType": "text/plain",
        })

        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v-allowed": "r-allowed"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v-allowed"), denied]
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(
            return_value=[{
                "_key": "r-allowed", "virtualRecordId": "v-allowed",
                "origin": "CONNECTOR", "recordName": "n", "mimeType": "text/plain",
                "connectorName": "X", "webUrl": "http://x",
            }]
        )

        # Location resolution walks a MagicMock adjacency graph and dominates
        # the runtime; stub it so a regression here fails on the assertion
        # rather than timing out.
        with patch(
            "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations",
            AsyncMock(return_value={}),
        ):
            result = await retrieval_service.search_with_filters(
                queries=["q"], user_id="u1", org_id="o1"
            )

        returned = {
            r["metadata"].get("virtualRecordId") for r in result.get("searchResults", [])
        }
        assert "v-denied" not in returned

    @pytest.mark.asyncio
    async def test_the_adjudicated_map_is_what_gates_records(
        self, retrieval_service, mock_graph_provider
    ):
        """The verifier decides which record id a vid resolves to — the
        cross-connector disambiguation the precomputed map used to do."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v1")]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "the-permitted-copy"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        fetched = mock_graph_provider.get_records_by_record_ids.await_args.args[0]
        assert list(fetched) == ["the-permitted-copy"]

    @pytest.mark.asyncio
    async def test_verifier_is_asked_only_about_returned_vids(
        self, retrieval_service, mock_graph_provider
    ):
        """Bounded by the result set, not the corpus — the whole point."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v1"), _hit("v1"), _hit("v2")]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={}
        )

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        asked = mock_graph_provider.filter_accessible_virtual_record_ids.await_args.args[0]
        assert sorted(asked) == ["v1", "v2"]


# ---------------------------------------------------------------------------
# Over-fetch sizing and the retry
# ---------------------------------------------------------------------------


class TestOverfetchSizing:
    def test_no_verify_groups_costs_nothing(self, retrieval_service):
        """The common case — an all-Collections or all-app-level tenant — must
        not pay for a change it cannot benefit from."""
        c = _containers(
            app_ids=frozenset({"a"}), record_group_ids_trusted=frozenset({"t"})
        )
        assert retrieval_service._overfetch_limit(20, c) == 20

    def test_mixed_buckets_overfetch_within_the_cap(self, retrieval_service):
        c = _containers(
            app_ids=frozenset({"a"}), record_group_ids_verify=frozenset({"v"})
        )
        got = retrieval_service._overfetch_limit(20, c)
        assert 20 < got <= 20 * mod._OVERFETCH_MAX_MULTIPLIER

    def test_all_verify_hits_the_multiplier_ceiling(self, retrieval_service):
        """Pinned to the derived value, not bounded by it: an upper bound is
        also satisfied by a multiplier that silently collapsed to 1.0, which is
        the failure that makes every all-verify tenant under-fetch and retry.
        """
        c = _containers(record_group_ids_verify=frozenset({"v"}))
        # p_verify == 1.0 with no trusted containers
        expected = math.ceil(20 / (1.0 - mod._ASSUMED_DENY_RATE))
        assert retrieval_service._overfetch_limit(20, c) == expected == 40

    def test_absolute_cap_bounds_the_overfetch(self, retrieval_service):
        c = _containers(record_group_ids_verify=frozenset({"v"}))
        got = retrieval_service._overfetch_limit(200, c)
        assert got == mod._OVERFETCH_ABSOLUTE_CAP

    def test_never_returns_less_than_the_caller_asked_for(self, retrieval_service):
        """The cap bounds the *over*-fetch. Returning below `limit` would
        under-fetch a large search while claiming to have widened it — and
        `_should_requery`'s cap guard would then block any recovery."""
        c = _containers(record_group_ids_verify=frozenset({"v"}))
        assert retrieval_service._overfetch_limit(10_000, c) >= 10_000

    def test_never_returns_less_than_limit_with_no_verify_groups(self, retrieval_service):
        c = _containers(app_ids=frozenset({"a"}))
        assert retrieval_service._overfetch_limit(10_000, c) == 10_000


class TestRequeryGuards:
    def _args(self, **over):
        base = dict(
            attempt=0, surviving=1, limit=20, raw=60, fetch_limit=60,
            max_batch=60, denied=5, granted=3, allow_requery=True,
        )
        base.update(over)
        return base

    def test_requeries_on_a_permission_shortfall(self, retrieval_service):
        assert retrieval_service._should_requery(**self._args())

    def test_no_requery_when_the_corpus_is_exhausted(self, retrieval_service):
        """The decisive guard. Without it every small tenant re-queries on
        every single search, forever."""
        assert not retrieval_service._should_requery(**self._args(max_batch=12))

    def test_no_requery_when_the_limit_is_already_met(self, retrieval_service):
        assert not retrieval_service._should_requery(**self._args(surviving=20))

    def test_no_requery_when_nothing_was_denied(self, retrieval_service):
        """A shortfall of unattributed vids is a stale index, not a sizing
        problem; more results only amplify it."""
        assert not retrieval_service._should_requery(**self._args(denied=0))

    def test_no_requery_when_verification_granted_nothing(self, retrieval_service):
        """Indistinguishable from the verifier failing, which also returns {}.
        Retrying would hammer a graph that may already be down."""
        assert not retrieval_service._should_requery(**self._args(granted=0))

    def test_no_requery_past_the_attempt_cap(self, retrieval_service):
        assert not retrieval_service._should_requery(
            **self._args(attempt=mod.MAX_SEARCH_ATTEMPTS - 1)
        )

    def test_the_absolute_cap_is_judged_per_query_not_across_the_fan_out(
        self, retrieval_service
    ):
        """The cap bounds `_overfetch_limit`'s per-query ask. Comparing a
        fan-out total against it would stop retrying on any multi-query search
        long before a single query reached the ceiling."""
        assert retrieval_service._should_requery(
            **self._args(fetch_limit=101, max_batch=101, raw=303)
        )

    def test_no_requery_when_disallowed(self, retrieval_service):
        assert not retrieval_service._should_requery(**self._args(allow_requery=False))


class TestRequeryBehaviour:
    @pytest.mark.asyncio
    async def test_second_attempt_uses_a_larger_limit(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(record_group_ids_verify=frozenset({"rg"}))
        )
        # Full page both times, one survivor -> a genuine permission shortfall.
        hits = [_hit(f"v{i}") for i in range(60)]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v0": "r0"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", limit=20
        )

        limits = [
            call.args[2]
            for call in retrieval_service._execute_parallel_searches.await_args_list
        ]
        assert len(limits) == 2, "expected exactly one retry"
        assert limits[1] > limits[0]

    @pytest.mark.asyncio
    async def test_embeddings_are_reused_across_attempts(
        self, retrieval_service, mock_graph_provider
    ):
        """A retry re-runs the search, not the embedding — the only expensive
        uncached step."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(record_group_ids_verify=frozenset({"rg"}))
        )
        hits = [_hit(f"v{i}") for i in range(60)]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v0": "r0"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", limit=20
        )

        plans = {
            id(call.kwargs["plan"])
            for call in retrieval_service._execute_parallel_searches.await_args_list
        }
        assert len(plans) == 1, "both attempts must share one _QueryPlan"

    @pytest.mark.asyncio
    async def test_tool_scoped_search_never_requeries(
        self, retrieval_service, mock_graph_provider
    ):
        """That path is scoped to one document; more results do not exist."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(record_group_ids_verify=frozenset({"rg"}))
        )
        hits = [_hit(f"v{i}") for i in range(60)]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v0": "r0"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", limit=20,
            virtual_record_ids_from_tool=["v0"],
        )

        assert retrieval_service._execute_parallel_searches.await_count == 1


# ---------------------------------------------------------------------------
# Routing between the two paths
# ---------------------------------------------------------------------------


class TestDegradedVerification:
    @pytest.mark.asyncio
    async def test_verification_failure_is_not_reported_as_an_empty_corpus(
        self, retrieval_service, mock_graph_provider
    ):
        """Both providers fail closed to {}, which is indistinguishable from
        total denial. Serving 404 "upload some documents" to a user whose graph
        is merely down is wrong and unactionable."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v1")]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={}
        )

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        assert result["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
        assert result["status_code"] == 503

    @pytest.mark.asyncio
    async def test_genuine_denial_of_some_records_is_not_degraded(
        self, retrieval_service, mock_graph_provider
    ):
        """Partial denial is a normal answer, not an outage."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v1"), _hit("v2")]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        result = await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        assert result["status"] != Status.PERMISSION_CHECK_UNAVAILABLE.value


class TestResultsAreCappedAtLimit:
    @pytest.mark.asyncio
    async def test_overfetched_results_are_trimmed_back(
        self, retrieval_service, mock_graph_provider
    ):
        """Over-fetching is a means, not a promise. Returning the whole
        over-fetch doubles the context handed to the LLM."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(record_group_ids_verify=frozenset({"rg"}))
        )
        hits = [_hit(f"v{i}", score=1.0 - i / 100) for i in range(60)]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={f"v{i}": f"r{i}" for i in range(60)}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        results, _, _ = await retrieval_service._search_and_adjudicate(
            ["q"], object(), 10, "o1", "u1",
            _containers(record_group_ids_verify=frozenset({"rg"})),
            allow_requery=False,
        )

        assert len(results) == 10

    @pytest.mark.asyncio
    async def test_the_kept_results_are_the_highest_scoring(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={f"v{i}": f"r{i}" for i in range(5)}
        )
        hits = [_hit(f"v{i}", score=i / 10) for i in range(5)]
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)

        results, _, _ = await retrieval_service._search_and_adjudicate(
            ["q"], object(), 2, "o1", "u1", _containers(app_ids=frozenset({"a"})),
            allow_requery=False,
        )

        assert [r["metadata"]["virtualRecordId"] for r in results] == ["v4", "v3"]


class TestRetryNeverRegresses:
    @pytest.mark.asyncio
    async def test_a_worse_second_attempt_is_discarded(
        self, retrieval_service, mock_graph_provider
    ):
        """A retry exists to improve recall. If the second round comes back
        worse — the graph blinked, say — the servable first answer must stand."""
        good = [_hit(f"v{i}") for i in range(60)]
        retrieval_service._execute_parallel_searches = AsyncMock(
            side_effect=[good, []]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            side_effect=[{"v0": "r0"}, {}]
        )

        results, accessible, _ = await retrieval_service._search_and_adjudicate(
            ["q"], object(), 20, "o1", "u1",
            _containers(record_group_ids_verify=frozenset({"rg"})),
            allow_requery=True,
        )

        assert accessible == {"v0": "r0"}
        assert len(results) == 1


class TestRouting:
    @pytest.mark.asyncio
    async def test_a_scoped_request_falls_back_without_asking_the_graph(
        self, retrieval_service, mock_graph_provider
    ):
        """kb/apps are containers in principle but the queries do not apply
        them yet, and the check is local — so a scoped request should not pay
        for a container query it will discard."""
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"kb": ["kb-1"]}
        )

        mock_graph_provider.get_accessible_virtual_record_ids.assert_awaited()
        mock_graph_provider.get_accessible_containers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_time_ranged_request_falls_back(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1",
            time_range={"source_created_after_ms": 1},
        )

        mock_graph_provider.get_accessible_containers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_declined_containers_fall_back(
        self, retrieval_service, mock_graph_provider
    ):
        """An unbackfilled connector, an oversized filter, a record-level
        predicate — all route to the path that can still answer."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(fallback_reason="membership_not_backfilled:x")
        )
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        mock_graph_provider.get_accessible_virtual_record_ids.assert_awaited()

class TestTheLegacyEnumerationIsSkipped:
    """The entire reason for this change: ON must never enumerate the user's
    accessible records. Every other assertion here is about correctness; this
    one is the performance claim, and nothing else covers it."""

    @pytest.mark.asyncio
    async def test_on_mode_never_enumerates_accessible_record_ids(
        self, retrieval_service, mock_graph_provider
    ):
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"should-never-be-built": "r"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v1")]
        )
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        mock_graph_provider.get_accessible_virtual_record_ids.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_vector_filter_carries_no_virtual_record_id_term(
        self, retrieval_service, mock_graph_provider
    ):
        """The 200k-term filter this replaces. A residue `virtualRecordId IN
        [...]` would reintroduce the exact cost the containers exist to avoid.
        """
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        kwargs = retrieval_service.vector_db_service.filter_collection.await_args.kwargs
        assert "virtualRecordId" not in kwargs["must"]


class TestTheQueryPlanIsReused:
    """A retry differs from the first attempt in nothing but `limit`, and
    embedding is the only expensive uncached step in the path. Re-embedding on
    every retry would make the over-fetch fallback cost more than the legacy
    filter it replaces."""

    @pytest.fixture
    def planned(self, retrieval_service):
        dense = MagicMock()
        dense.aembed_query = AsyncMock(return_value=[0.1, 0.2])
        retrieval_service.get_embedding_model_instance = AsyncMock(return_value=dense)
        retrieval_service._ensure_sparse_embedder = AsyncMock(return_value=None)
        retrieval_service._capabilities = MagicMock(supports_sparse_vectors=False)
        retrieval_service._run_searches = AsyncMock(return_value=[])
        return retrieval_service, dense

    @pytest.mark.asyncio
    async def test_a_second_call_sharing_a_plan_does_not_re_embed(self, planned):
        service, dense = planned
        plan = mod._QueryPlan()

        await service._execute_parallel_searches(
            ["q"], None, 10, "o1", "u1", plan=plan
        )
        await service._execute_parallel_searches(
            ["q"], None, 40, "o1", "u1", plan=plan
        )

        service.get_embedding_model_instance.assert_awaited_once()
        dense.aembed_query.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_reused_call_still_applies_the_new_limit(self, planned):
        """The reuse branch returns early, so a limit dropped there would make
        the retry an exact repeat of the attempt that already fell short."""
        service, _ = planned
        plan = mod._QueryPlan()

        await service._execute_parallel_searches(
            ["q"], None, 10, "o1", "u1", plan=plan
        )
        await service._execute_parallel_searches(
            ["q"], None, 40, "o1", "u1", plan=plan
        )

        assert [c.args[2] for c in service._run_searches.await_args_list] == [10, 40]

    @pytest.mark.asyncio
    async def test_without_a_plan_every_call_embeds(self, planned):
        """The mocked-`_execute_parallel_searches` tests elsewhere depend on the
        plan being caller-owned and optional."""
        service, dense = planned

        await service._execute_parallel_searches(["q"], None, 10, "o1", "u1")
        await service._execute_parallel_searches(["q"], None, 10, "o1", "u1")

        assert service.get_embedding_model_instance.await_count == 2

class TestTheFeatureFlagGatesTheWholeChange:
    """The kill switch. OFF must reproduce pre-change behaviour exactly:
    enumerate record ids, never ask the graph for containers."""

    @pytest.mark.asyncio
    async def test_off_enumerates_record_ids_and_never_asks_for_containers(
        self, retrieval_service, mock_graph_provider, _container_filter_on
    ):
        _container_filter_on.return_value = False
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(
            return_value=[_hit("v1")]
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        mock_graph_provider.get_accessible_virtual_record_ids.assert_awaited()
        mock_graph_provider.get_accessible_containers.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_off_sends_the_virtual_record_id_term_the_containers_replace(
        self, retrieval_service, mock_graph_provider, _container_filter_on
    ):
        _container_filter_on.return_value = False
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        await retrieval_service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1"
        )

        kwargs = retrieval_service.vector_db_service.filter_collection.await_args.kwargs
        assert "virtualRecordId" in kwargs["must"], (
            "OFF must still scope by record id; without this term the legacy "
            "path would return the whole corpus"
        )

    @pytest.mark.asyncio
    async def test_the_flag_is_read_per_search_not_cached(
        self, retrieval_service, mock_graph_provider, _container_filter_on
    ):
        """An admin flipping it in Labs must take effect on the next search."""
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        for _ in range(2):
            await retrieval_service.search_with_filters(
                queries=["q"], user_id="u1", org_id="o1"
            )

        assert _container_filter_on.await_count >= 2, (
            "flag was read once and reused; a Labs toggle would need a restart"
        )

    @pytest.mark.asyncio
    async def test_an_unreadable_setting_keeps_the_container_path(
        self, retrieval_service, mock_graph_provider
    ):
        """Defaults on, including when the setting cannot be read. Failing to
        the container path is the safe direction: it adjudicates every vid it
        returns through the verifier, so it is the stricter of the two. Runs the
        real flag reader over a config service that raises, so the fallback
        under test is the shipped one and not the fixture's stand-in."""
        from app.services.featureflag import platform_settings

        retrieval_service.config_service.get_config = AsyncMock(
            side_effect=RuntimeError("kv down")
        )
        mock_graph_provider.get_accessible_containers = AsyncMock(
            return_value=_containers(app_ids=frozenset({"a"}))
        )
        mock_graph_provider.get_accessible_virtual_record_ids = AsyncMock(
            return_value={"v1": "r1"}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=[])
        mock_graph_provider.get_records_by_record_ids = AsyncMock(return_value=[])

        with patch(
            "app.modules.retrieval.retrieval_service.read_platform_feature_flag",
            platform_settings.read_platform_feature_flag,
        ):
            await retrieval_service.search_with_filters(
                queries=["q"], user_id="u1", org_id="o1"
            )

        mock_graph_provider.get_accessible_containers.assert_awaited()
        mock_graph_provider.get_accessible_virtual_record_ids.assert_not_awaited()

class TestTheContainerPathReturnsAsMuchAsTheRecordIdPath:
    """`limit` is per-query: `_run_searches` issues one request per expanded
    query and concatenates them uncapped. Trimming the adjudicated set to
    `limit` made the same search return a fraction of the context it returns
    with the flag off — invisible to every permission assertion, because
    verification denies nothing."""

    @pytest.mark.asyncio
    async def test_all_admitted_results_survive_when_nothing_is_denied(
        self, retrieval_service, mock_graph_provider
    ):
        queries = ["q1", "q2", "q3", "q4"]
        hits = [_hit(f"v{i}") for i in range(40)]
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={f"v{i}": f"r{i}" for i in range(40)}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)

        admitted, _, _ = await retrieval_service._search_and_adjudicate(
            queries, None, 25, "o1", "u1",
            _containers(app_ids=frozenset({"a"})), allow_requery=False,
        )

        assert len(admitted) == 40, (
            f"nothing was denied, so all 40 accessible blocks must survive; "
            f"got {len(admitted)} - a global trim to `limit` would give 25"
        )

    @pytest.mark.asyncio
    async def test_the_budget_scales_with_the_number_of_expanded_queries(
        self, retrieval_service, mock_graph_provider
    ):
        hits = [_hit(f"v{i}") for i in range(300)]
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={f"v{i}": f"r{i}" for i in range(300)}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)

        one, _, _ = await retrieval_service._search_and_adjudicate(
            ["q"], None, 10, "o1", "u1",
            _containers(app_ids=frozenset({"a"})), allow_requery=False,
        )
        four, _, _ = await retrieval_service._search_and_adjudicate(
            ["q1", "q2", "q3", "q4"], None, 10, "o1", "u1",
            _containers(app_ids=frozenset({"a"})), allow_requery=False,
        )

        assert len(one) == 10
        assert len(four) == 40, (
            "four expanded queries at limit=10 is a 40-result budget, matching "
            "what the record-id path concatenates"
        )

class TestTheRetryTargetsTheWholeFanOut:
    """`surviving` is counted across every expanded query, so comparing it to
    `limit` made the retry give up at one query's worth of results. With
    denials in play that is the same shortfall the trim fix addressed, in the
    other half of the loop."""

    @pytest.mark.asyncio
    async def test_a_multi_query_search_retries_until_the_fan_out_budget(
        self, retrieval_service, mock_graph_provider
    ):
        queries = ["q1", "q2", "q3"]
        # 40 survivors: past one query's `limit` of 25, well short of the
        # 3 x 25 = 75 the fan-out may return. Comparing against `limit` stops
        # here; comparing against the budget keeps going. Raw is kept high so
        # the corpus-exhausted guard does not decide it instead.
        hits = [_hit(f"v{i}") for i in range(200)]
        granted = {f"v{i}": f"r{i}" for i in range(40)}
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value=granted
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)

        await retrieval_service._search_and_adjudicate(
            queries, None, 25, "o1", "u1",
            _containers(app_ids=frozenset({"a"}),
                        record_group_ids_verify=frozenset({"g1"})),
            allow_requery=True,
        )

        assert retrieval_service._execute_parallel_searches.await_count == 2, (
            "15 survivors is short of the 75-result fan-out budget and half the "
            "vids were denied, so the shortfall is permission-related and worth "
            "a second, larger search"
        )

    @pytest.mark.asyncio
    async def test_the_cap_is_not_tripped_by_multiplying_the_per_query_ask(
        self, retrieval_service, mock_graph_provider
    ):
        """`fetch_limit` and the fan-out total are different numbers and the
        guard uses each for a different thing. Feeding the total into the
        per-query cap stops a 101-per-query search retrying at 3 queries,
        because 303 clears a ceiling that 101 does not."""
        queries = ["q1", "q2", "q3"]
        hits = [_hit(f"v{i}") for i in range(400)]
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={f"v{i}": f"r{i}" for i in range(50)}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)

        await retrieval_service._search_and_adjudicate(
            queries, None, 101, "o1", "u1",
            _containers(app_ids=frozenset({"a"})), allow_requery=True,
        )

        assert retrieval_service._execute_parallel_searches.await_count == 2, (
            "101 per query is under the 300 cap; only the fan-out total clears it"
        )

    @pytest.mark.asyncio
    async def test_a_single_query_search_is_unchanged(
        self, retrieval_service, mock_graph_provider
    ):
        """One query means budget == limit, so nothing about the retry moves."""
        hits = [_hit(f"v{i}") for i in range(30)]
        mock_graph_provider.filter_accessible_virtual_record_ids = AsyncMock(
            return_value={f"v{i}": f"r{i}" for i in range(30)}
        )
        retrieval_service._execute_parallel_searches = AsyncMock(return_value=hits)

        admitted, _, _ = await retrieval_service._search_and_adjudicate(
            ["q"], None, 25, "o1", "u1",
            _containers(app_ids=frozenset({"a"})), allow_requery=True,
        )

        assert retrieval_service._execute_parallel_searches.await_count == 1
        assert len(admitted) == 25

