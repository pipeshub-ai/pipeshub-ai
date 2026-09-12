"""`filter_accessible_virtual_record_ids` — the authority for container-filtered search.

Container filtering scopes a search by connector and record group, which admits
more than the user may read; this method is what narrows the result back to the
truth. So it is the only thing standing between a widened vector filter and a
disclosure, and its predicates have to match the per-record checker
(`check_record_access_with_details`) that the old exact-id path relied on.

Three of those predicates are easy to omit and fail in opposite directions:

- the **app-reachability gate** (`origin != CONNECTOR OR connectorId IN reachable`)
  — without it a record whose connector the user has lost still passes on a
  stale PERMISSION edge. This is the gap that makes
  `filter_nodes_with_permission_role` unusable here, and omitting it over-shares;
- **`anyone`** — legacy grants nothing writes any more but that still confer
  access. Omitting it under-shares;
- **org scope** — a virtualRecordId is a *content* identity and is not unique
  across tenants, so this is a boundary, not a filter. Note the deliberate
  contrast with `get_records_by_virtual_record_id`, which must NOT be org-scoped.

Both backends are checked because retrieval reaches this through
`IGraphDBProvider`, and a divergence would mean one deployment quietly answers
a permission question differently from the other.
"""

import ast
import re
import textwrap

import pytest

from app.services.graph_db.common.utils import MAX_RECORD_CANDIDATES_PER_VRID
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

METHOD = "filter_accessible_virtual_record_ids"

QUERY_SOURCES = {
    "arango": ("app/services/graph_db/arango/arango_http_provider.py", METHOD),
    "neo4j": ("app/services/graph_db/neo4j/neo4j_provider.py", METHOD),
}


def _method_source(path: str, name: str) -> str:
    """The text of one method, for asserting on the query it builds.

    These providers compose query strings rather than exposing them, and both
    need a live database to execute. Reading the source is what lets the two
    backends be held to the same predicate without one.
    """
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
    """`_method_source` minus the prose.

    Every assertion here is a substring check, so a token appearing only in the
    method's own docstring or in a comment would satisfy it while the query says
    nothing of the kind. Unparsing the AST drops both, leaving only code.
    """
    src = textwrap.dedent(_method_source(path, name))
    fn = ast.parse(src).body[0]
    body = fn.body[1:] if ast.get_docstring(fn) is not None else fn.body
    return chr(10).join(ast.unparse(node) for node in body)


@pytest.fixture(params=sorted(QUERY_SOURCES), ids=sorted(QUERY_SOURCES))
def backend_body(request) -> tuple:
    """(backend, code) — for predicates whose expression is backend-specific."""
    return request.param, _method_body(*QUERY_SOURCES[request.param])


@pytest.fixture(params=sorted(QUERY_SOURCES), ids=sorted(QUERY_SOURCES))
def source(request) -> str:
    """The method's code, docstring and comments removed."""
    path, name = QUERY_SOURCES[request.param]
    return _method_body(path, name)


class TestAppReachabilityGate:
    """The gate whose absence would over-share, and the reason this method
    exists rather than reusing `filter_nodes_with_permission_role`."""

    def test_the_query_gates_connector_records_on_reachable_apps(self, source):
        assert "reachable_apps" in source

    def test_the_gate_exempts_non_connector_records(self, source):
        """Collections have origin UPLOAD and no connector to reach; gating them
        on the app set would make every uploaded document invisible."""
        assert "connector_origin" in source

    def test_the_reachable_set_covers_team_granted_apps(self, source):
        """An app reached only through a team is still reached. Building the set
        from direct USER_APP_RELATION alone silently drops shared connectors."""
        assert "USER_APP_RELATION" in source
        assert "Teams" in source or "TEAMS" in source or "teams" in source


class TestExcludesRecordsTheOldPathExcluded:
    def test_filters_soft_deleted(self, source):
        assert "isDeleted" in source

    def test_arango_uses_a_null_safe_comparison(self):
        # AQL: `!= true` is already null-safe, so records predating the field pass.
        assert "isDeleted != true" in _method_source(*QUERY_SOURCES["arango"])

    def test_neo4j_uses_a_null_safe_comparison(self):
        """`<> true` is NOT null-safe in Cypher — `null <> true` is null, which
        WHERE treats as false, silently dropping every record that predates the
        field."""
        src = _method_source(*QUERY_SOURCES["neo4j"])
        assert "isDeleted IS NULL OR" in src
        assert "isDeleted <> true" not in src

    def test_requires_completed_indexing(self, source):
        """Vector points can exist before a record finishes indexing; the old
        path filtered these out and this one has to match."""
        assert "indexingStatus" in source
        assert "completed" in source


class TestTenantBoundary:
    def test_scoped_by_org(self, backend_body):
        """A virtualRecordId is content identity — identical content in two orgs
        shares one. Unscoped, this leaks across tenants.

        Checks the value is both bound *and* referenced: binding alone leaves a
        parameter the query never reads, which filters nothing."""
        backend, body = backend_body
        assert "'org_id': org_id" in body
        assert {"arango": "@org_id", "neo4j": "$org_id"}[backend] in body


class TestOneRecordPerVirtualId:
    """The cross-connector disambiguation the old intersection did for free."""

    def test_caps_candidates_per_virtual_id(self, source):
        assert "max_candidates" in source

    def test_both_backends_use_the_shared_cap(self, source):
        assert "MAX_RECORD_CANDIDATES_PER_VRID" in source

    def test_the_shared_cap_is_positive(self):
        """Zero would deny everything; negative is a scan in AQL."""
        assert MAX_RECORD_CANDIDATES_PER_VRID > 0


class TestReusesThePermissionFragment:
    """Hand-rolling the permission paths here would drift from
    `check_record_access_with_details` on the next edit to either."""

    def test_arango_reuses_the_aql_fragment(self):
        src = _method_source(*QUERY_SOURCES["arango"])
        assert '_get_permission_role_aql("record"' in src

    def test_neo4j_reuses_the_cypher_fragment(self):
        src = _method_source(*QUERY_SOURCES["neo4j"])
        assert '_get_permission_role_cypher("record"' in src


class TestFailsClosed:
    def test_returns_an_empty_map_on_error(self, backend_body):
        """Denying everything is recoverable; admitting on error is not. The
        caller is responsible for telling the two apart.

        Asserted against the exception handlers specifically: both methods open
        with an empty-input guard that also returns {}, so a plain substring
        check passes even with no error handling whatsoever.
        """
        backend, _ = backend_body
        src = textwrap.dedent(_method_source(*QUERY_SOURCES[backend]))
        handlers = [
            node for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.ExceptHandler)
        ]
        assert handlers, "no exception handler — a graph failure would propagate"
        for handler in handlers:
            returns = [
                node for node in ast.walk(handler)
                if isinstance(node, ast.Return)
            ]
            assert returns, "handler falls through instead of failing closed"
            for node in returns:
                assert isinstance(node.value, ast.Dict) and not node.value.keys, (
                    "an error path must deny, never admit"
                )

    def test_logs_failure_at_error(self, source):
        """An empty map is indistinguishable from total denial, so the log line
        is the only way an operator learns verification broke."""
        assert "logger.error" in source


class TestInterfaceContract:
    def test_declared_abstract_on_the_interface(self):
        """No safe default exists: an empty map denies every search result and
        anything permissive leaks, so both providers must be forced to supply one."""
        assert getattr(
            IGraphDBProvider.filter_accessible_virtual_record_ids,
            "__isabstractmethod__",
            False,
        )

    def test_both_providers_implement_it(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert METHOD in vars(cls), cls.__name__

    def test_neither_provider_is_left_abstract(self):
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        for cls in (ArangoHTTPProvider, Neo4jProvider):
            assert not getattr(cls, "__abstractmethods__", frozenset()), (
                f"{cls.__name__} is abstract"
            )
