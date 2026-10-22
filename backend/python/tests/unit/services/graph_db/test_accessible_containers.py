"""`get_accessible_containers` — what a search may be scoped by.

The container sets *widen*: the vector filter they build admits records the user
cannot read, and the batch access check (`check_access`) narrows it back. That
asymmetry decides every bound here. A container wrongly included costs
precision, which the verifier fixes. A container wrongly *omitted* is a record
that never comes back, with no error and nothing to notice — so every limit in
this module falls back to the record-id path rather than truncating.

The pure helpers are where those bounds live, so they are tested directly.

Neither backend builds containers from grants: each returns the Apps
the permission model's gate reaches and trusts none of them, and the verifier
decides every hit with the batch access check.
`TestContainersAreTheGate` covers that on both.
"""

import ast
import re
import textwrap


import pytest

from app.services.graph_db.common.utils import (
    CONTAINER_FILTER_MAX_TERMS,
    MAX_DIRECT_GRANT_RECORDS,
)
from app.services.graph_db.interface.graph_db_provider import (
    AccessibleContainers,
    IGraphDBProvider,
    _containers_from_row,
    _unsupported_container_filters,
    requested_scope_ids,
)

METHOD = "get_accessible_containers"

SOURCES = {
    "arango": ("app/services/graph_db/arango/arango_http_provider.py", METHOD),
    "neo4j": ("app/services/graph_db/neo4j/neo4j_provider.py", METHOD),
}
BACKENDS = sorted(SOURCES)


def _method_source(path: str, name: str) -> str:
    import pathlib

    # Explicit encoding: the providers carry emoji in log strings, and the
    # platform default is cp1252 on Windows.
    root = pathlib.Path(__file__).resolve().parents[4]
    text = (root / path).read_text(encoding="utf-8")
    try:
        start = text.index(f"async def {name}(")
    except ValueError:
        start = text.index(f"def {name}(")
    rest = text[start:]
    match = re.search(r"\n    (?:async )?def ", rest[10:])
    return rest[: match.start() + 10] if match else rest


def _method_body(path: str, name: str) -> str:
    """Method source with docstring and comments removed.

    Needed in both directions: absence assertions otherwise find the prose that
    names what the query deliberately does NOT do, and presence assertions can
    be satisfied by a comment rather than by the query.
    """
    src = textwrap.dedent(_method_source(path, name))
    fn = ast.parse(src).body[0]
    body = fn.body[1:] if ast.get_docstring(fn) is not None else fn.body
    return chr(10).join(ast.unparse(node) for node in body)


class _Logger:
    def __init__(self) -> None:
        self.warnings: list = []

    def warning(self, *args, **kwargs) -> None:
        self.warnings.append(args)


# ---------------------------------------------------------------------------
# Which requests can be expressed as containers at all
# ---------------------------------------------------------------------------


class TestUnsupportedFilters:
    def test_plain_request_is_supported(self):
        assert _unsupported_container_filters(None, None) is None
        assert _unsupported_container_filters({}, None) is None

    @pytest.mark.parametrize(
        "filters",
        [
            {"kb": ["kb-1"]},
            {"apps": ["app-1"]},
            {"kb": ["kb-1"], "apps": ["app-1"]},
            {"apps": ["app-1"], "kb": ["NO_KB_SELECTED"]},
            {"apps": "not-a-list"},
        ],
        ids=["kb", "apps", "both", "agent-fan-out", "malformed"],
    )
    def test_kb_and_apps_are_containers(self, filters):
        """Both queries and the verifier narrow to them, so they no longer
        force the record-id path. A malformed value is accepted here too: the
        scope helper turns it into a scope that matches nothing."""
        assert _unsupported_container_filters(filters, None) is None

    def test_a_scope_does_not_exempt_a_record_level_filter(self):
        reason = _unsupported_container_filters(
            {"apps": ["app-1"], "departments": ["d"]}, None
        )
        assert reason == "unsupported_filters:departments"

    def test_a_scope_does_not_exempt_a_time_range(self):
        reason = _unsupported_container_filters(
            {"kb": ["kb-1"]}, {"source_created_after_ms": 1}
        )
        assert reason == "unsupported_filter:time_range"

    def test_time_range_falls_back(self):
        """A container has no timestamp. Dropping the bound would widen the
        result set silently."""
        reason = _unsupported_container_filters(None, {"source_created_after_ms": 1})
        assert reason == "unsupported_filter:time_range"

    @pytest.mark.parametrize(
        "key", ["departments", "categories", "languages", "topics", "subcategories1"]
    )
    def test_record_level_filters_fall_back(self, key):
        reason = _unsupported_container_filters({key: ["x"]}, None)
        assert reason and key in reason

    def test_empty_filter_values_do_not_trigger_fallback(self):
        """Callers pass empty lists for filters the user did not set; treating
        those as unsupported would send every request down the old path."""
        assert _unsupported_container_filters({"departments": []}, None) is None

    def test_names_every_offending_key(self):
        """The reason string is what an operator reads to find out why a
        deployment never engages the container path."""
        reason = _unsupported_container_filters(
            {"topics": ["a"], "languages": ["b"], "kb": ["c"]}, None
        )
        for key in ("topics", "languages"):
            assert key in reason
        assert "kb" not in reason


class TestRequestedScopeIds:
    """One reading of `apps` ∪ `kb`, shared by both paths and both backends.
    The failure it guards against is widening: anything that is not clearly
    "no scope" must narrow, never fall open to the whole corpus."""

    @pytest.mark.parametrize(
        "filters",
        [None, {}, {"apps": [], "kb": []}, {"apps": None}, {"kb": None, "apps": ()},
         {"departments": ["d"]}],
        ids=["none", "empty-dict", "both-empty", "apps-none", "none-and-tuple", "other-keys"],
    )
    def test_unscoped(self, filters):
        assert requested_scope_ids(filters) is None

    def test_union_is_ordered_apps_first_and_deduplicated(self):
        """Order matters to the record-id path: a VRID shared by two scoped
        apps resolves to the one queried first."""
        got = requested_scope_ids({"kb": ["k1", "a1"], "apps": ["a2", "a1"]})
        assert got == ("a2", "a1", "k1")

    def test_no_kb_selected_is_kept_and_matches_like_any_id(self):
        """Stripping it would turn an agent's "no Collections" into "no scope",
        which searches the whole corpus."""
        assert requested_scope_ids({"apps": [], "kb": ["NO_KB_SELECTED"]}) == (
            "NO_KB_SELECTED",
        )

    def test_a_collection_id_is_honoured_under_apps(self):
        """Collection-page chat sends the Collection's id as an app."""
        assert requested_scope_ids({"apps": ["kb-1"], "kb": []}) == ("kb-1",)

    @pytest.mark.parametrize(
        "filters",
        [{"apps": [""]}, {"apps": [None]}, {"apps": [123]}, {"apps": [["a"]]},
         {"apps": [{"a": 1}]}, {"apps": "a"}, {"apps": 0}, {"apps": False},
         {"apps": {"a": 1}}, {"kb": "kb-1"}],
        ids=["empty-string", "none-item", "int-item", "nested-list", "dict-item",
             "bare-string", "zero", "false", "dict", "bare-string-kb"],
    )
    def test_values_that_cannot_be_ids_narrow_to_nothing(self, filters):
        got = requested_scope_ids(filters)
        assert got is not None, "a malformed scope must never read as unscoped"
        assert got == ()

    def test_malformed_items_do_not_discard_valid_ones(self):
        assert requested_scope_ids({"apps": ["", None, "a"], "kb": [7, "k"]}) == ("a", "k")

    def test_ids_are_matched_verbatim(self):
        """Graph ids are compared exactly; normalising here would make the
        scope match something the caller did not name."""
        assert requested_scope_ids({"apps": [" A "]}) == (" A ",)


# ---------------------------------------------------------------------------
# Turning a provider row into containers, and the bounds that refuse to
# ---------------------------------------------------------------------------


class TestContainersFromRow:
    def test_missing_row_is_not_an_empty_result(self):
        """No row means the user did not resolve. Returning empty sets with no
        reason would read as "this user can search nothing"."""
        result = _containers_from_row(None, logger=_Logger(), scope_connector_ids=None)
        assert result.fallback_reason == "user_not_found"
        assert not result.usable

    def test_scope_is_echoed(self):
        row = {"appIds": ["kb-1"], "unsafeApps": []}
        scope = frozenset({"kb-1"})
        result = _containers_from_row(row, logger=_Logger(), scope_connector_ids=scope)
        assert result.scope_connector_ids == scope

    def test_the_scope_cannot_be_forgotten(self):
        """Required, so a new call site cannot silently report "unscoped" for a
        scoped query — which the service would take at its word."""
        with pytest.raises(TypeError):
            _containers_from_row({}, logger=_Logger())

    def test_happy_path(self):
        row = {
            "appIds": ["kb-1", "s3-1"],
            "trusted": ["rg-trusted"],
            "verify": ["rg-verify"],
            "direct": [{"vid": "v1", "rid": "r1"}],
            "unsafeApps": [],
        }
        result = _containers_from_row(row, logger=_Logger(), scope_connector_ids=None)
        assert result.fallback_reason is None
        assert result.app_ids == frozenset({"kb-1", "s3-1"})
        assert result.record_group_ids_trusted == frozenset({"rg-trusted"})
        assert result.record_group_ids_verify == frozenset({"rg-verify"})
        assert result.direct_records == {"v1": "r1"}
        assert result.usable

    def test_unsafe_app_forces_fallback(self):
        """A connector whose membership arrays were never written has points
        with empty connectorIds/recordGroupIds — invisible to a container
        filter. All-or-nothing per request."""
        row = {"appIds": ["a"], "trusted": [], "verify": [], "direct": [],
               "unsafeApps": ["stale-connector"]}
        result = _containers_from_row(row, logger=_Logger(), scope_connector_ids=None)
        assert result.fallback_reason == "membership_not_backfilled:stale-connector"
        assert not result.usable

    def test_direct_overflow_falls_back_rather_than_truncating(self):
        row = {
            "appIds": [], "trusted": [], "verify": [], "unsafeApps": [],
            "direct": [
                {"vid": f"v{i}", "rid": f"r{i}"}
                for i in range(MAX_DIRECT_GRANT_RECORDS + 1)
            ],
        }
        logger = _Logger()
        result = _containers_from_row(row, logger=logger, scope_connector_ids=None)
        assert result.fallback_reason.startswith("direct_grant_overflow:")
        assert not result.direct_records, "truncation would be a silent hole"
        assert logger.warnings, "an operator has to learn a connector is at fault"

    def test_direct_at_the_limit_is_still_usable(self):
        """The provider probes with limit+1, so exactly the limit is fine."""
        row = {
            "appIds": [], "trusted": [], "verify": [], "unsafeApps": [],
            "direct": [
                {"vid": f"v{i}", "rid": f"r{i}"}
                for i in range(MAX_DIRECT_GRANT_RECORDS)
            ],
        }
        result = _containers_from_row(row, logger=_Logger(), scope_connector_ids=None)
        assert result.fallback_reason is None
        assert len(result.direct_records) == MAX_DIRECT_GRANT_RECORDS

    def test_term_budget_overflow_falls_back(self):
        row = {
            "appIds": [],
            "trusted": [],
            "verify": [f"rg{i}" for i in range(CONTAINER_FILTER_MAX_TERMS + 1)],
            "direct": [],
            "unsafeApps": [],
        }
        logger = _Logger()
        result = _containers_from_row(row, logger=logger, scope_connector_ids=None)
        assert result.fallback_reason.startswith("too_many_terms:")
        assert logger.warnings

    def test_tolerates_malformed_rows(self):
        """Nulls and half-built direct entries must not take a search down."""
        row = {
            "appIds": ["a", None, ""],
            "trusted": [None],
            "verify": ["g"],
            "direct": [{"vid": "v"}, {"rid": "r"}, None, {"vid": "v2", "rid": "r2"}],
            "unsafeApps": [],
        }
        result = _containers_from_row(row, logger=_Logger(), scope_connector_ids=None)
        assert result.app_ids == frozenset({"a"})
        assert result.record_group_ids_trusted == frozenset()
        assert result.direct_records == {"v2": "r2"}


class TestAccessibleContainersShape:
    def test_empty_is_not_usable(self):
        """Reaching nothing is a real answer, but no filter can be built from
        it — the caller must 404 rather than send an unbounded query."""
        c = AccessibleContainers()
        assert c.is_empty and not c.usable

    def test_fallback_reason_makes_it_unusable_even_when_populated(self):
        c = AccessibleContainers(
            app_ids=frozenset({"a"}), fallback_reason="membership_not_backfilled:x"
        )
        assert not c.is_empty
        assert not c.usable

    def test_group_sets_merge_for_the_filter(self):
        """The vector DB cannot tell trusted from verify; separating them there
        would only cost a clause."""
        c = AccessibleContainers(
            record_group_ids_trusted=frozenset({"a"}),
            record_group_ids_verify=frozenset({"b"}),
        )
        assert c.record_group_ids == frozenset({"a", "b"})

    def test_direct_records_alone_is_usable(self):
        c = AccessibleContainers(direct_records={"v": "r"})
        assert c.usable


class TestInterfaceDefault:
    def test_default_is_concrete_not_abstract(self):
        """A provider that has not implemented this must keep working. An
        abstract method would break both backends at construction; an all-empty
        result with no reason would read as a silent total outage."""
        assert not getattr(
            IGraphDBProvider.get_accessible_containers, "__isabstractmethod__", False
        )

    @pytest.mark.asyncio
    async def test_default_returns_a_reason(self):
        """Called unbound: the default touches no state, and a real subclass
        cannot be instantiated without implementing 200-odd other methods."""
        result = await IGraphDBProvider.get_accessible_containers(
            object(), "u", "o"
        )
        assert result.fallback_reason
        assert not result.usable

    def test_both_providers_override_it(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert METHOD in vars(cls), cls.__name__


# ---------------------------------------------------------------------------
# Both backends: the gate's Apps, nothing trusted
# ---------------------------------------------------------------------------


class TestTheTreeUiAffordanceIsNotAPermission:
    @pytest.mark.parametrize("backend", BACKENDS)
    def test_hide_children_is_not_copied(self, backend):
        """`hideChildren` hides children in the knowledge-base tree UI. It is
        not a permission, and honouring it here deletes search results the user
        is entitled to."""
        assert "hideChildren" not in _method_body(*SOURCES[backend])


class TestBackfillGate:
    @pytest.mark.parametrize("backend", BACKENDS)
    def test_checks_both_backfill_flags(self, backend):
        """`vectorMembershipBackfilled` is set to true on give-up as well as on
        success, so the exhausted flag is the half that matters."""
        body = _method_body(*SOURCES[backend])
        assert "vectorMembershipBackfilled" in body
        assert "vectorMembershipBackfillExhausted" in body

    def test_neo4j_treats_a_missing_flag_as_unsafe(self):
        """A property absent in Cypher is null, and `null AND ...` is not true,
        so coalesce is what makes an app that never ran the backfill unready."""
        src = _method_body(*SOURCES["neo4j"])
        assert "coalesce(a.vectorMembershipBackfilled, false)" in src
        assert "NOT coalesce(a.vectorMembershipBackfillExhausted, false)" in src

    def test_arango_treats_a_missing_flag_as_unsafe(self):
        """AQL `null == true` is false, so absence means unready."""
        src = _method_source(*SOURCES["arango"])
        assert "a.vectorMembershipBackfilled == true" in src
        assert "a.vectorMembershipBackfillExhausted != true" in src


class TestUnsupportedFiltersAreRoutedByBothBackends:
    @pytest.mark.parametrize("backend", BACKENDS)
    def test_both_check_before_querying(self, backend):
        assert "_unsupported_container_filters" in _method_body(*SOURCES[backend])


# ---------------------------------------------------------------------------
# Scope in the rendered queries
# ---------------------------------------------------------------------------


async def _render_containers(backend: str, filters=None, rows=(), user=True) -> tuple:
    """(query, params, result, called) for one get_accessible_containers call.
    ``rows`` is what the App query returns; the gate admits a, b, k and hidden-kb."""
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}
    access = {"gated_app_ids": ["a", "b", "k", "hidden-kb"], "grantee_ids": [], "by_connector": {}}

    if backend == "neo4j":
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        provider = Neo4jProvider.__new__(Neo4jProvider)

        async def _execute(query, parameters=None, txn_id=None):
            captured["query"], captured["params"] = query, parameters
            return list(rows)

        provider.client = MagicMock()
        provider.client.execute_query = _execute
        provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key"} if user else None)
    else:
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)

        async def _execute(query, bind_vars=None, txn_id=None, **_kwargs):
            captured["query"], captured["params"] = query, bind_vars
            return list(rows)

        provider.http_client = MagicMock()
        provider.http_client.execute_aql = _execute
        provider.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"} if user else None)
    # The gate alone: containers need no grants.
    provider.get_knowledge_hub_access_context_v2 = AsyncMock(return_value=access)
    provider.logger = MagicMock()
    result = await provider.get_accessible_containers("user-1", "org-1", filters)
    return captured.get("query"), captured.get("params"), result, bool(captured)


class TestScopeInTheContainerQuery:
    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.parametrize(
        "filters",
        [{"apps": [""]}, {"apps": "x", "kb": []}],
        ids=["empty-string", "bare-string"],
    )
    @pytest.mark.asyncio
    async def test_a_scope_that_names_nothing_skips_the_query(self, backend, filters):
        _, _, result, called = await _render_containers(backend, filters)
        assert not called
        assert result.fallback_reason is None
        assert result.is_empty
        assert result.scope_connector_ids == frozenset()

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_scope_is_always_bound_as_a_sorted_list(self, backend):
        """Arango rejects an undeclared or unsent bind variable, and Neo4j an
        unsent parameter; either one silently becomes query_failed and every
        scoped search quietly takes the old path."""
        _, unscoped, result_u, _ = await _render_containers(backend)
        _, scoped, result_s, _ = await _render_containers(
            backend, {"apps": ["b"], "kb": ["a"]}
        )
        assert "scope_ids" in unscoped and unscoped["scope_ids"] is None
        assert scoped["scope_ids"] == ["a", "b"]
        assert result_u.scope_connector_ids is None
        assert result_s.scope_connector_ids == frozenset({"a", "b"})

    @pytest.mark.parametrize("scoped", [False, True], ids=["unscoped", "scoped"])
    @pytest.mark.asyncio
    async def test_arango_bind_vars_match_the_query(self, scoped):
        query, bind_vars, _, _ = await _render_containers(
            "arango", {"kb": ["k"]} if scoped else None
        )
        referenced = set(re.findall(r"(?<!@)@([A-Za-z_][A-Za-z0-9_]*)", query))
        assert referenced == set(bind_vars)

    @pytest.mark.parametrize("scoped", [False, True], ids=["unscoped", "scoped"])
    @pytest.mark.asyncio
    async def test_neo4j_params_cover_the_query(self, scoped):
        query, params, _, _ = await _render_containers(
            "neo4j", {"kb": ["k"]} if scoped else None
        )
        referenced = set(re.findall(r"\$([A-Za-z_][A-Za-z0-9_]*)", query))
        assert referenced <= set(params)

    @pytest.mark.asyncio
    async def test_arango_scope_is_its_own_filter(self):
        """An appended AND would bind to the last disjunct only."""
        query, _, _, _ = await _render_containers("arango", {"kb": ["k"]})
        lines = [line for line in query.splitlines() if "@scope_ids == null OR" in line]
        assert lines
        for line in lines:
            assert line.strip().startswith("FILTER @scope_ids == null OR"), line

    @pytest.mark.asyncio
    async def test_neo4j_scope_is_parenthesised_where_it_joins_a_predicate(self):
        query, _, _, _ = await _render_containers("neo4j", {"kb": ["k"]})
        for line in query.splitlines():
            if "$scope_ids IS NULL OR" in line and "AND" in line:
                assert "AND ($scope_ids IS NULL OR" in line, line


class TestStrictScopeOnTheContainerPath:
    """`strictScope` (Projects) says how an EMPTY apps/kb selection must be
    read: a project chat with nothing selected searches nothing, rather than
    falling back to everything the user can reach. The record-id path already
    honours it; the container path has to agree or turning the flag on would
    widen those chats back out."""

    def test_it_does_not_force_the_record_id_path(self):
        assert _unsupported_container_filters({"strictScope": True}, None) is None
        assert _unsupported_container_filters(
            {"apps": ["a"], "strictScope": True}, None
        ) is None

    def test_it_is_not_part_of_the_scope(self):
        """It is a control flag, not a container id — on its own it leaves the
        request unscoped, and it never becomes a term to match on."""
        assert requested_scope_ids({"strictScope": True}) is None
        assert requested_scope_ids({"apps": ["a"], "strictScope": True}) == ("a",)

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_nothing_selected_reaches_nothing_without_querying(self, backend):
        _, _, result, called = await _render_containers(backend, {"strictScope": True})
        assert not called
        assert result.fallback_reason is None
        assert result.is_empty
        assert result.scope_connector_ids is None

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_a_selection_is_still_searched(self, backend):
        """It only short-circuits an empty selection — a project's own
        Collection, named explicitly, is still reached."""
        _, params, result, called = await _render_containers(
            backend, {"kb": ["hidden-kb"], "strictScope": True}
        )
        assert called
        assert params["scope_ids"] == ["hidden-kb"]
        assert result.scope_connector_ids == frozenset({"hidden-kb"})


class TestHiddenCollections:
    """A project's linked Collection is marked `isHidden` so it never surfaces
    in unscoped search, while staying reachable when named by id (the rule
    `_get_kb_virtual_ids` / `_get_accessible_kb_ids` apply on the record-id
    path). The container path builds the vector filter, so it has to apply the
    same rule or the flag would surface hidden content."""

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_hidden_collections_need_an_explicit_scope(self, backend):
        query, _, _, _ = await _render_containers(backend)
        guard = {
            "neo4j": "NOT coalesce(a.isHidden, false) OR $scope_ids IS NOT NULL",
            "arango": "FILTER a.isHidden != true OR @scope_ids != null",
        }[backend]
        assert query.count(guard) == 1

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_the_guard_yields_to_an_explicit_scope(self, backend):
        """Naming the Collection is what admits it; the scope clause beside
        the guard has already required that it was named."""
        query, _, _, _ = await _render_containers(backend, {"kb": ["hidden-kb"]})
        scope_clause = {
            "neo4j": "$scope_ids IS NULL OR a.id IN $scope_ids",
            "arango": "FILTER @scope_ids == null OR a._key IN @scope_ids",
        }[backend]
        assert scope_clause in query


class TestContainersAreTheGate:
    """Both backends search the Apps the gate reaches and verify every hit.
    Enumerating what the user may read cost 0.5-1.2 s at 50k-150k records;
    checking the top hits costs 10-130 ms."""

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_the_gated_apps_are_the_containers_and_none_is_trusted(self, backend) -> None:
        _, params, result, _ = await _render_containers(
            backend, rows=[{"id": "a", "ready": True}, {"id": "k", "ready": True}],
        )
        assert params["app_ids"] == ["a", "b", "k", "hidden-kb"]
        assert params["org_id"] == "org-1"
        assert result.app_ids == frozenset({"a", "k"}) and result.usable
        assert result.app_ids_trusted == frozenset()
        assert not result.record_group_ids and not result.direct_records

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_an_unready_app_falls_back(self, backend) -> None:
        _, _, result, _ = await _render_containers(
            backend, rows=[{"id": "a", "ready": True}, {"id": "b", "ready": False}],
        )
        assert result.fallback_reason == "membership_not_backfilled:b"

    @pytest.mark.parametrize("backend", BACKENDS)
    @pytest.mark.asyncio
    async def test_an_unknown_user_takes_the_record_id_path(self, backend) -> None:
        _, _, result, called = await _render_containers(backend, user=False)
        assert result.fallback_reason == "user_not_found" and not called
