"""`get_accessible_containers` — what a search may be scoped by.

The container sets *widen*: the vector filter they build admits records the user
cannot read, and `filter_accessible_virtual_record_ids` narrows it back. That
asymmetry decides every bound here. A container wrongly included costs
precision, which the verifier fixes. A container wrongly *omitted* is a record
that never comes back, with no error and nothing to notice — so every limit in
this module falls back to the record-id path rather than truncating.

The pure helpers are where those bounds live, so they are tested directly. The
two query builders need a live graph, so they are held to the same predicates by
reading their source — the pattern `test_records_by_virtual_record_id` uses.
"""

import ast
import re
import textwrap

import logging

import pytest

from app.services.graph_db.common.utils import (
    CONTAINER_FILTER_MAX_TERMS,
    CONTAINER_INHERIT_MAX_DEPTH,
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

QUERY_SOURCES = {
    "arango": ("app/services/graph_db/arango/arango_http_provider.py", METHOD),
    "neo4j": ("app/services/graph_db/neo4j/neo4j_provider.py", METHOD),
}


def _method_source(path: str, name: str) -> str:
    import pathlib

    # Explicit encoding: the providers carry emoji in log strings, and the
    # platform default is cp1252 on Windows.
    text = pathlib.Path(path).read_text(encoding="utf-8")
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


@pytest.fixture(params=sorted(QUERY_SOURCES), ids=sorted(QUERY_SOURCES))
def source(request) -> str:
    """The method's code, docstring and comments removed."""
    path, name = QUERY_SOURCES[request.param]
    return _method_body(path, name)


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
# Cross-backend predicate parity
# ---------------------------------------------------------------------------


class TestSeedsAreGated:
    def test_seeds_are_gated_on_reachable_apps(self, source):
        """Without the gate a stale grant on a connector the user has lost still
        contributes a container."""
        assert "reachable_app" in source or "reachable_apps" in source

    def test_kb_containers_are_recognised(self, source):
        assert "kb_type" in source


class TestDoesNotInheritTheTreeUiAffordance:
    @pytest.mark.parametrize("backend", sorted(QUERY_SOURCES))
    def test_hide_children_is_not_copied(self, backend):
        """`hideChildren` hides children in the knowledge-base tree UI. It is
        not a permission, and honouring it here deletes search results the user
        is entitled to. The KH expansion this borrows from does filter on it."""
        assert "hideChildren" not in _method_body(*QUERY_SOURCES[backend])


class TestClosureDepth:
    def test_uses_the_shared_depth_constant(self, source):
        assert "CONTAINER_INHERIT_MAX_DEPTH" in source

    @pytest.mark.parametrize(
        "path,fragment",
        [
            ("app/services/graph_db/arango/arango_http_provider.py",
             "_get_record_permission_role_aql"),
            ("app/services/graph_db/neo4j/neo4j_provider.py",
             "_get_record_permission_role_cypher"),
        ],
        ids=["arango", "neo4j"],
    )
    def test_the_depth_is_at_least_the_verifier_depth(self, path, fragment):
        """`depth(closure) >= depth(verifier)` — a shallower closure omits
        containers whose records the verifier would then have admitted, which
        is silent recall loss.

        The verifier's depth is read out of the verifier rather than restated,
        so deepening its traversal fails here instead of quietly breaking the
        invariant.
        """
        src = _method_source(path, fragment)
        depths = [int(d) for d in re.findall(r"1\.\.(\d+)", src)]
        assert depths, f"no traversal depth found in {fragment}"
        assert CONTAINER_INHERIT_MAX_DEPTH >= max(depths)


class TestCollectionsAreServedByAppIds:
    @pytest.mark.parametrize("backend", sorted(QUERY_SOURCES))
    def test_kb_apps_land_in_the_app_id_set(self, backend):
        """A KB record carries connectorIds=[kbId] and an empty recordGroupIds
        by design, so routing KBs into the group sets makes every uploaded
        document invisible.

        Asserted as the single projection expression rather than as two
        independent substrings: `kb_app_ids` and `appIds` both survive dropping
        the KB half of the union, which is exactly the regression that hides
        every Collection.
        """
        body = _method_body(*QUERY_SOURCES[backend])
        expected = {
            "arango": "appIds: covered_app_ids",
            "neo4j": "app_level_ids + kb_app_ids AS appIds",
        }[backend]
        assert expected in body
        if backend == "arango":
            assert (
                "UNION_DISTINCT(app_level_ids, kb_app_ids)" in body
            ), "covered_app_ids must include the KB apps"


class TestOnlyAppLevelAppsAreTrusted:
    """`app_ids` is what the vector filter matches on; `app_ids_trusted` is what
    may skip per-record adjudication. They are deliberately different sets."""

    @pytest.mark.parametrize("backend", sorted(QUERY_SOURCES))
    def test_trusted_apps_is_the_declared_app_level_set(self, backend):
        """`kb_app_ids` is the wider set: admitted on type alone so records
        carrying no recordGroupIds still have a term to match on. Trust follows
        the declaration, not the type — a KB app whose permissionModel has not
        been written yet is reachable but not trusted."""
        body = _method_body(*QUERY_SOURCES[backend])
        expected = {
            "arango": "trustedApps: app_level_ids",
            "neo4j": "app_level_ids AS trustedApps",
        }[backend]
        assert expected in body
        assert "trustedApps: covered_app_ids" not in body
        assert "kb_app_ids AS trustedApps" not in body

    @pytest.mark.parametrize("backend", sorted(QUERY_SOURCES))
    def test_app_level_ids_is_gated_on_the_declared_permission_model(self, backend):
        """The projection above pins which *list* is trusted; this pins what
        that list means. Without the permissionModel filter, `app_level_ids`
        becomes every reachable app and the shortcut stops checking records for
        connectors that never declared APP_LEVEL — the over-share the flag's own
        comment warns about, and it passed the whole suite."""
        body = _method_body(*QUERY_SOURCES[backend])
        at = body.index("app_level_ids")
        clause = body[max(0, at - 400):at + 200]
        marker = {"arango": "@app_level", "neo4j": "$app_level"}[backend]
        assert marker in clause, (
            "app_level_ids must be filtered on the declared permission model"
        )

    def test_an_undeclared_app_is_reachable_but_not_trusted(self):
        """The projections above are only half the guarantee — this pins the
        resulting object, which is what the retrieval path actually consumes.
        An app that has not had its permissionModel backfilled yet reaches the
        filter without reaching the shortcut."""
        from app.services.graph_db.interface.graph_db_provider import (
            _containers_from_row,
        )

        containers = _containers_from_row(
            {
                "appIds": ["app-level-1", "not-declared-1"],
                "trustedApps": ["app-level-1"],
                "trusted": [],
                "verify": [],
                "rootGroups": [],
                "direct": [],
                "unsafeApps": [],
            },
            logger=logging.getLogger("test"),
            scope_connector_ids=None,
        )
        assert containers.app_ids == frozenset({"app-level-1", "not-declared-1"})
        assert containers.app_ids_trusted == frozenset({"app-level-1"})

    def test_trusted_is_bounded_by_reachable(self):
        """A backend that forgets the key, or returns a stale one, must not be
        able to widen trust beyond what the user actually reaches."""
        from app.services.graph_db.interface.graph_db_provider import (
            _containers_from_row,
        )

        containers = _containers_from_row(
            {
                "appIds": ["app-1"],
                "trustedApps": ["app-1", "app-not-reachable"],
                "trusted": [],
                "verify": [],
                "rootGroups": [],
                "direct": [],
                "unsafeApps": [],
            },
            logger=logging.getLogger("test"),
            scope_connector_ids=None,
        )
        assert containers.app_ids_trusted == frozenset({"app-1"})

    def test_a_backend_without_the_key_trusts_nothing(self):
        from app.services.graph_db.interface.graph_db_provider import (
            _containers_from_row,
        )

        containers = _containers_from_row(
            {
                "appIds": ["app-1"],
                "trusted": [],
                "verify": [],
                "rootGroups": [],
                "direct": [],
                "unsafeApps": [],
            },
            logger=logging.getLogger("test"),
            scope_connector_ids=None,
        )
        assert containers.app_ids == frozenset({"app-1"})
        assert containers.app_ids_trusted == frozenset()


class TestBackfillGate:
    def test_checks_both_backfill_flags(self, source):
        """`vectorMembershipBackfilled` is set to true on give-up as well as on
        success, so the exhausted flag is the half that matters."""
        assert "vectorMembershipBackfilled" in source
        assert "vectorMembershipBackfillExhausted" in source

    def test_neo4j_treats_a_missing_flag_as_unsafe(self):
        """`a.flag <> true` is null in Cypher when the property is absent, and
        WHERE drops nulls — so an app that never ran the backfill would look
        safe. coalesce is what makes absence mean unsafe."""
        src = _method_source(*QUERY_SOURCES["neo4j"])
        assert "coalesce(a.vectorMembershipBackfilled, false) = false" in src

    def test_arango_treats_a_missing_flag_as_unsafe(self):
        """AQL `null != true` is already true, so absence means unsafe."""
        src = _method_source(*QUERY_SOURCES["arango"])
        assert "vectorMembershipBackfilled != true" in src


class TestUnsupportedFiltersAreRoutedByBothBackends:
    def test_both_check_before_querying(self, source):
        assert "_unsupported_container_filters" in source

    def test_both_share_the_row_parser(self, source):
        """The bounds are correctness, not tuning — a backend that truncated
        where the other fell back would answer differently."""
        assert "_containers_from_row" in source


# ---------------------------------------------------------------------------
# Scope in the rendered queries
# ---------------------------------------------------------------------------


async def _render_containers(backend: str, filters=None) -> tuple:
    """(query, params, result, called) for one get_accessible_containers call."""
    from unittest.mock import MagicMock

    captured: dict = {}

    if backend == "neo4j":
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        provider = Neo4jProvider.__new__(Neo4jProvider)

        async def _execute(query, params, txn_id=None):
            captured["query"], captured["params"] = query, params
            return [{"appIds": [], "unsafeApps": []}]

        provider.client = MagicMock()
        provider.client.execute_query = _execute
    else:
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)

        async def _execute(query, bind_vars=None, txn_id=None, batch_size=1000):
            captured["query"], captured["params"] = query, bind_vars
            return [{"appIds": [], "unsafeApps": []}]

        provider.http_client = MagicMock()
        provider.http_client.execute_aql = _execute
    provider.logger = MagicMock()
    result = await provider.get_accessible_containers("user-1", "org-1", filters)
    return captured.get("query"), captured.get("params"), result, bool(captured)


SCOPE_CLAUSE = {
    "neo4j": "$scope_ids IS NULL OR",
    "arango": "@scope_ids == null OR",
}


class TestScopeInTheContainerQuery:
    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
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

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
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

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_every_result_set_is_scoped(self, backend):
        """3 app lists, the seed groups, their descendants, the root groups and
        3 direct-grant paths. Deleting any one widens that set back to the
        whole reach."""
        query, _, _, _ = await _render_containers(backend, {"kb": ["k"]})
        assert query.count(SCOPE_CLAUSE[backend]) == 9

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_the_walk_starts_from_every_grant(self, backend):
        """Inheritance can cross apps: a group of an in-scope app may inherit
        from a grant on another app. Scoping the seeds would drop it, which is
        a record the user can read and asked for, lost without an error."""
        query, _, _, _ = await _render_containers(backend, {"kb": ["k"]})
        if backend == "neo4j":
            seeds = query[query.index("OPTIONAL MATCH (u)-[:PERMISSION]->(rg:RecordGroup"):query.index("AS seed_rgs")]
        else:
            seeds = query[query.index("LET path1_seed_rgs"):query.index("LET seed_rgs")]
        assert "scope_ids" not in seeds

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_the_reachability_gates_are_not_scoped(self, backend):
        """Scope may only remove results. Scoping the reachable-app set would
        instead change which grants count, including for in-scope groups."""
        query, _, _, _ = await _render_containers(backend, {"kb": ["k"]})
        if backend == "neo4j":
            block = query[query.index("MATCH (u:User"):query.index("AS reachable_apps")]
        else:
            block = query[
                query.index("LET reachable_app_docs"):query.index("LET kb_app_ids")
            ]
        assert "scope_ids" not in block

    @pytest.mark.asyncio
    async def test_arango_seed_scope_is_its_own_filter(self):
        """The seed gates are bare `A OR B`; an appended AND would bind to B
        only and let every KB-typed group through regardless of scope."""
        query, _, _, _ = await _render_containers("arango", {"kb": ["k"]})
        for line in query.splitlines():
            if SCOPE_CLAUSE["arango"] in line:
                assert line.strip().startswith("FILTER @scope_ids == null OR"), line

    @pytest.mark.asyncio
    async def test_neo4j_scope_is_parenthesised_where_it_joins_a_predicate(self):
        query, _, _, _ = await _render_containers("neo4j", {"kb": ["k"]})
        for line in query.splitlines():
            if SCOPE_CLAUSE["neo4j"] in line and "AND" in line:
                assert "AND ($scope_ids IS NULL OR" in line, line

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_direct_grants_are_scoped_before_the_overflow_probe(self, backend):
        """Scoped after the probe, 2001 out-of-scope grants would use up the
        budget and silently drop the in-scope ones without tripping overflow."""
        query, _, _, _ = await _render_containers(backend, {"kb": ["k"]})
        probe = {
            "neo4j": "[0..$direct_probe_limit]",
            "arango": "LIMIT @direct_probe_limit",
        }[backend]
        record_var = {"neo4j": "r1.connectorId", "arango": "rec.connectorId IN @scope_ids"}[backend]
        assert query.index(record_var) < query.index(probe)

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_direct_grants_are_gated_on_reachable_apps(self, backend):
        """A grant that outlived the user's access to its connector would enter
        the filter only to be denied — and a scope holding nothing else would
        search, deny everything and return nothing instead of a clean 404."""
        query, params, _, _ = await _render_containers(backend, {"kb": ["k"]})
        probe = {
            "neo4j": "[0..$direct_probe_limit]",
            "arango": "LIMIT @direct_probe_limit",
        }[backend]
        gate = {
            "neo4j": ("rec.origin <> $connector_origin", "rec.connectorId IN reachable_apps"),
            "arango": ("rec.origin != @connector_origin", "rec.connectorId IN user_accessible_apps"),
        }[backend]
        for fragment in gate:
            assert fragment in query
            assert query.index(fragment) < query.index(probe)
        assert params["connector_origin"] == "CONNECTOR"


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

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_nothing_selected_reaches_nothing_without_querying(self, backend):
        _, _, result, called = await _render_containers(backend, {"strictScope": True})
        assert not called
        assert result.fallback_reason is None
        assert result.is_empty
        assert result.scope_connector_ids is None

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
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

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_both_app_id_lists_exclude_hidden_collections(self, backend):
        query, _, _, _ = await _render_containers(backend)
        guard = {
            "neo4j": "coalesce(a.isHidden, false) = false OR $scope_ids IS NOT NULL",
            "arango": "FILTER a.isHidden != true OR @scope_ids != null",
        }[backend]
        # Once for kb_app_ids (what puts a Collection in the filter) and once
        # for app_level_ids (what may skip per-record adjudication).
        assert query.count(guard) == 2

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
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
