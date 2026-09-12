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

    def test_kb_and_apps_are_declined_until_the_queries_apply_them(self):
        """They *are* containers and could be honoured — but neither provider
        query intersects against them yet. Accepting a filter and not applying
        it silently returns the user's whole corpus while the response still
        reports `appliedFilters`, which for a configured agent scope is a
        boundary break rather than a missed optimisation."""
        filters = {"kb": ["kb-1"], "apps": ["app-1"]}
        reason = _unsupported_container_filters(filters, None)
        assert reason and "kb" in reason and "apps" in reason

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
        for key in ("topics", "languages", "kb"):
            assert key in reason


# ---------------------------------------------------------------------------
# Turning a provider row into containers, and the bounds that refuse to
# ---------------------------------------------------------------------------


class TestContainersFromRow:
    def test_missing_row_is_not_an_empty_result(self):
        """No row means the user did not resolve. Returning empty sets with no
        reason would read as "this user can search nothing"."""
        result = _containers_from_row(None, logger=_Logger())
        assert result.fallback_reason == "user_not_found"
        assert not result.usable

    def test_happy_path(self):
        row = {
            "appIds": ["kb-1", "s3-1"],
            "trusted": ["rg-trusted"],
            "verify": ["rg-verify"],
            "direct": [{"vid": "v1", "rid": "r1"}],
            "unsafeApps": [],
        }
        result = _containers_from_row(row, logger=_Logger())
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
        result = _containers_from_row(row, logger=_Logger())
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
        result = _containers_from_row(row, logger=logger)
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
        result = _containers_from_row(row, logger=_Logger())
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
        result = _containers_from_row(row, logger=logger)
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
        result = _containers_from_row(row, logger=_Logger())
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
