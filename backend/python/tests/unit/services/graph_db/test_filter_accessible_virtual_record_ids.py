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


async def _render_neo4j_query(**kwargs) -> tuple:
    """(query, bind params) the Neo4j provider would send.

    The providers compose their query and need a live database to run it, so
    capturing the text through a stub client is the only way to assert on the
    shape the shortcut actually produces rather than on the source that builds it.
    """
    from unittest.mock import MagicMock

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()
    captured = {}

    async def _execute(query, params, txn_id=None):
        captured["query"] = query
        captured["params"] = params
        return []

    provider.client = MagicMock()
    provider.client.execute_query = _execute
    await provider.filter_accessible_virtual_record_ids(["vrid-1"], "user-1", "org-1", **kwargs)
    return captured["query"], captured["params"]


async def _render_arango_query(**kwargs) -> tuple:
    """(query, bind vars) the Arango provider would send.

    The Neo4j twin of this already existed; without it every assertion about
    the shortcut's *shape* could only be made against Neo4j, and the module
    docstring's claim that both backends are held to the same predicate was
    not true for the shortcut itself.
    """
    from unittest.mock import MagicMock

    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    captured = {}

    async def _execute(query, bind_vars=None, txn_id=None, batch_size=1000):
        captured["query"] = query
        captured["bind_vars"] = bind_vars
        return []

    provider.http_client = MagicMock()
    provider.http_client.execute_aql = _execute
    provider._anyone_populated = None
    await provider.filter_accessible_virtual_record_ids(
        ["vrid-1"], "user-1", "org-1", **kwargs
    )
    return captured["query"], captured["bind_vars"]


def _candidate_block(query: str, backend: str) -> str:
    """Just the candidate scan, so a gate cannot be 'found' elsewhere.

    `reachable_apps` appears three times per Neo4j leg and `connector_origin`
    appears in the params dict, so a bare substring check on the whole method
    passed with the entire reachability clause deleted.
    """
    if backend == "neo4j":
        start = query.index("MATCH (candidate:Record")
        return query[start:query.index("RETURN candidate AS record", start)]
    start = query.index("LET candidates = (")
    return query[start:query.index("RETURN record", start)]


class TestAppReachabilityGate:
    """The gate whose absence would over-share, and the reason this method
    exists rather than reusing `filter_nodes_with_permission_role`."""

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_the_query_gates_connector_records_on_reachable_apps(self, backend):
        """Asserted inside the candidate scan, not anywhere in the method: the
        word `reachable_apps` also names the CTE that builds the set, so a
        method-wide substring check passed with the gate itself deleted."""
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        query, _ = await render()
        block = _candidate_block(query, backend)
        expected = {
            "neo4j": "candidate.connectorId IN reachable_apps",
            "arango": "record.connectorId IN reachable_apps",
        }[backend]
        assert expected in block

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_the_gate_exempts_non_connector_records(self, backend):
        """Collections have origin UPLOAD and no connector to reach; gating them
        on the app set would make every uploaded document invisible."""
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        query, _ = await render()
        block = _candidate_block(query, backend)
        expected = {
            "neo4j": "candidate.origin <> $connector_origin",
            "arango": "record.origin != @connector_origin",
        }[backend]
        assert expected in block

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_the_gate_survives_when_the_shortcut_is_active(self, backend):
        """The shortcut adds a second leg; the gate has to be on both, or a
        trusted container becomes a way around connector reachability."""
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        query, _ = await render(
            trusted_app_ids=frozenset({"app-1"}),
            trusted_group_ids=frozenset({"rg-1"}),
        )
        gate = {
            "neo4j": "candidate.connectorId IN reachable_apps",
            "arango": "record.connectorId IN reachable_apps",
        }[backend]
        # Neo4j renders the candidate scan once per union leg; Arango computes
        # `candidates` once and both branches consume it.
        expected_count = 2 if backend == "neo4j" else 1
        assert query.count(gate) == expected_count

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

    def test_candidates_are_not_truncated_before_adjudication(self, source):
        """A cap on the candidate *input* silently denies a readable record that
        falls outside it. There is no ordering to make the kept subset the
        interesting one, so the shortfall is arbitrary and invisible — the exact
        under-admission this design exists to avoid. Bound the granted output
        instead, never the set being judged."""
        assert "max_candidates" not in source
        assert "MAX_RECORD_CANDIDATES_PER_VRID" not in source

    @pytest.mark.asyncio
    async def test_no_limit_inside_the_candidate_match(self):
        """Asserted on the rendered query, not the source: a hardcoded `LIMIT 20`
        reintroduces the truncation without mentioning the constant, so a
        name-based check alone passes while the bug is back."""
        query, _ = await _render_neo4j_query()
        start = query.index("MATCH (candidate:Record")
        # To the end of the CALL subquery, not to its RETURN: Cypher writes
        # the cap as `RETURN ... LIMIT n`, so a window stopping at RETURN
        # misses exactly the truncation being guarded against.
        end = query.index("\n        }", start)
        candidate_block = query[start:end]
        assert "LIMIT" not in candidate_block, candidate_block


class TestReusesThePermissionFragment:
    """Hand-rolling the permission paths here would drift from
    `check_record_access_with_details` on the next edit to either."""

    def test_arango_reuses_the_aql_fragment(self):
        src = _method_source(*QUERY_SOURCES["arango"])
        assert '_get_permission_role_aql("record"' in src

    def test_neo4j_reuses_the_cypher_fragment(self):
        src = _method_source(*QUERY_SOURCES["neo4j"])
        assert '_get_permission_role_cypher("record"' in src


class TestFailureIsNotDenial:
    """An empty map is the honest answer when every candidate is denied, which a
    narrow scope makes common. A graph failure has to look different, or the
    caller either reports an outage for a healthy graph or "no results" for a
    broken one."""

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_a_query_failure_raises(self, backend):
        from unittest.mock import MagicMock

        from app.exceptions.graph_db_exceptions import (
            PermissionVerificationUnavailableError,
        )

        async def _boom(*args, **kwargs):
            raise RuntimeError("connection refused")

        if backend == "neo4j":
            from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

            provider = Neo4jProvider.__new__(Neo4jProvider)
            provider.client = MagicMock()
            provider.client.execute_query = _boom
        else:
            from app.services.graph_db.arango.arango_http_provider import (
                ArangoHTTPProvider,
            )

            provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
            provider.http_client = MagicMock()
            provider.http_client.execute_aql = _boom
        provider.logger = MagicMock()

        with pytest.raises(PermissionVerificationUnavailableError):
            await provider.filter_accessible_virtual_record_ids(["v1"], "u1", "o1")

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_no_client_raises(self, backend):
        from unittest.mock import MagicMock

        from app.exceptions.graph_db_exceptions import (
            PermissionVerificationUnavailableError,
        )

        if backend == "neo4j":
            from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

            provider = Neo4jProvider.__new__(Neo4jProvider)
            provider.client = None
        else:
            from app.services.graph_db.arango.arango_http_provider import (
                ArangoHTTPProvider,
            )

            provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
            provider.http_client = None
        provider.logger = MagicMock()

        with pytest.raises(PermissionVerificationUnavailableError):
            await provider.filter_accessible_virtual_record_ids(["v1"], "u1", "o1")

    @pytest.mark.asyncio
    async def test_an_arango_result_that_is_not_a_list_raises(self):
        from unittest.mock import MagicMock

        from app.exceptions.graph_db_exceptions import (
            PermissionVerificationUnavailableError,
        )
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
        provider.logger = MagicMock()

        async def _execute(query, bind_vars=None, txn_id=None, batch_size=1000):
            return None

        provider.http_client = MagicMock()
        provider.http_client.execute_aql = _execute

        with pytest.raises(PermissionVerificationUnavailableError):
            await provider.filter_accessible_virtual_record_ids(["v1"], "u1", "o1")

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_a_healthy_empty_answer_is_an_empty_map(self, backend):
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        await render()  # the stub returns no rows; not raising is the assertion

    def test_logs_failure_at_error(self, source):
        assert "logger.error" in source


class TestScope:
    """Vector membership arrays are unioned per virtualRecordId, so a search
    scoped to one app still returns content shared with another. Only this
    method sees individual records, so it is the only place a scope can pick
    the in-scope copy — or deny the VRID when there is none."""

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_scope_is_always_bound(self, backend):
        """Arango rejects a declared bind variable it is not sent, and Neo4j a
        referenced parameter it is not sent. Either one turns every search into
        a verification failure."""
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        _, unscoped = await render()
        _, scoped = await render(scope_connector_ids=frozenset({"b", "a"}))
        assert "scope_ids" in unscoped and unscoped["scope_ids"] is None
        assert scoped["scope_ids"] == ["a", "b"]

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.parametrize("shortcut", [False, True], ids=["no-shortcut", "shortcut"])
    @pytest.mark.asyncio
    async def test_scope_gates_every_candidate_leg(self, backend, shortcut):
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        kwargs = {"scope_connector_ids": frozenset({"a"})}
        if shortcut:
            kwargs.update(
                trusted_app_ids=frozenset({"app-1"}),
                trusted_group_ids=frozenset({"rg-1"}),
            )
        query, _ = await render(**kwargs)
        gate = {
            "neo4j": "candidate.connectorId IN $scope_ids",
            "arango": "record.connectorId IN @scope_ids",
        }[backend]
        assert gate in _candidate_block(query, backend)
        legs = 2 if (backend == "neo4j" and shortcut) else 1
        assert query.count(gate) == legs

    @pytest.mark.asyncio
    async def test_neo4j_scope_is_not_coalesced(self):
        """coalesce(connectorId, '') would admit a record with no connector
        whenever the scope held an empty string. Plain IN drops it, which is
        also what Arango's `null IN [...]` does."""
        query, _ = await _render_neo4j_query(scope_connector_ids=frozenset({"a"}))
        assert "coalesce(candidate.connectorId, '') IN $scope_ids" not in query

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_an_empty_scope_grants_nothing_without_querying(self, backend):
        from unittest.mock import MagicMock

        called = []

        async def _execute(*args, **kwargs):
            called.append(1)
            return [{"vid": "v1", "rid": "r1"}]

        if backend == "neo4j":
            from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

            provider = Neo4jProvider.__new__(Neo4jProvider)
            provider.client = MagicMock()
            provider.client.execute_query = _execute
        else:
            from app.services.graph_db.arango.arango_http_provider import (
                ArangoHTTPProvider,
            )

            provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
            provider.http_client = MagicMock()
            provider.http_client.execute_aql = _execute
        provider.logger = MagicMock()

        granted = await provider.filter_accessible_virtual_record_ids(
            ["v1"], "u1", "o1", scope_connector_ids=frozenset()
        )
        assert granted == {}
        assert not called

    def test_signatures_match_the_interface(self):
        """A provider missing the keyword raises TypeError on every scoped
        search, which the service only sees as a generic failure."""
        import inspect

        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        expected = inspect.signature(IGraphDBProvider.filter_accessible_virtual_record_ids)
        for cls in (ArangoHTTPProvider, Neo4jProvider):
            got = inspect.signature(getattr(cls, METHOD))
            assert list(got.parameters) == list(expected.parameters), cls.__name__
            assert got.parameters["scope_connector_ids"].default is None


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


async def _map_neo4j_rows(rows, **kwargs) -> dict:
    """The {vid: rid} map the Neo4j provider builds from these result rows."""
    from unittest.mock import MagicMock

    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = MagicMock()

    async def _execute(query, params, txn_id=None):
        return rows

    provider.client = MagicMock()
    provider.client.execute_query = _execute
    return await provider.filter_accessible_virtual_record_ids(
        ["v1"], "user-1", "org-1", **kwargs
    )


class TestOneRecordPerVridAcrossBothLegs:
    """A VRID can have one candidate in a trusted container and another that had
    to be adjudicated, so UNION returns a row per leg. Both cite a readable
    record, but the choice has to be stable and the same on both backends."""

    @pytest.mark.asyncio
    async def test_trusted_row_wins_regardless_of_row_order(self):
        trusted_first = await _map_neo4j_rows(
            [{"vid": "v1", "rid": "r-trusted", "via": "trusted"},
             {"vid": "v1", "rid": "r-adjudicated", "via": "adjudicated"}],
            trusted_app_ids=frozenset({"app-1"}),
        )
        adjudicated_first = await _map_neo4j_rows(
            [{"vid": "v1", "rid": "r-adjudicated", "via": "adjudicated"},
             {"vid": "v1", "rid": "r-trusted", "via": "trusted"}],
            trusted_app_ids=frozenset({"app-1"}),
        )
        assert trusted_first == adjudicated_first == {"v1": "r-trusted"}

    @pytest.mark.asyncio
    async def test_still_one_entry_per_vrid(self):
        granted = await _map_neo4j_rows(
            [{"vid": "v1", "rid": "r-a", "via": "adjudicated"},
             {"vid": "v1", "rid": "r-b", "via": "adjudicated"}],
        )
        assert list(granted) == ["v1"]


class TestTrustedContainerShortcut:
    """Records reached through a container the user wholly owns skip the 10-path
    role resolution. Everything else the method enforces must survive that, and
    the gate has to be keyed on the relation that actually confers permission."""

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_both_backends_take_the_trusted_sets(self, backend):
        """Bound *and* referenced. Asserting the Python name alone is satisfied
        by the params dict, so a query that never reads the parameter passed."""
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        query, bound = await render(
            trusted_app_ids=frozenset({"app-1"}),
            trusted_group_ids=frozenset({"rg-1"}),
        )
        sigil = "@" if backend == "arango" else "$"
        assert f"{sigil}trusted_app_ids" in query
        assert f"{sigil}trusted_group_ids" in query
        assert bound["trusted_app_ids"] == ["app-1"]
        assert bound["trusted_group_ids"] == ["rg-1"]

    def test_the_group_gate_follows_inheritance_not_membership(self, backend_body):
        """`belongsTo` is written for every record in a group; inheritance is
        conditional. Keying the shortcut on membership would grant a record with
        inherit_permissions=false purely for sitting in a trusted group."""
        backend, body = backend_body
        edge = {"arango": "inheritPermissions", "neo4j": "INHERIT_PERMISSIONS"}[backend]
        # Anchor on the bind-parameter form so this finds the query clause, not
        # the Python variable of the same name.
        marker = {"arango": "@trusted_group_ids", "neo4j": "$trusted_group_ids"}[backend]
        at = body.index(marker)
        trusted_clause = body[max(0, at - 500):at + 100]
        assert edge in trusted_clause
        # Spelled differently per backend: the lowercase check alone can never
        # fire against Neo4j's BELONGS_TO.
        membership = {"arango": "belongsTo", "neo4j": "BELONGS_TO"}[backend]
        assert membership not in trusted_clause
        assert "recordGroupId" not in trusted_clause

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_the_trusted_walk_matches_the_verifier_depth(self, backend):
        """Asserted on the rendered query, not on the constant's name. The
        constant stays referenced by the substitution and the bind var however
        deep the walk actually goes, so hardcoding 1..1 passed the name check —
        the same 'constant present, literal missed' hole that already bit this
        branch once."""
        from app.services.graph_db.common.utils import CONTAINER_INHERIT_MAX_DEPTH

        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        query, bound = await render(trusted_group_ids=frozenset({"rg-1"}))
        # Scoped to the trusted clause: the role-resolution fragment walks the
        # same relationship at the same depth, so a query-wide check matched
        # that occurrence and passed with the trusted walk cut to 1..1.
        marker = {"arango": "@trusted_group_ids", "neo4j": "$trusted_group_ids"}[backend]
        at = query.index(marker)
        clause = query[max(0, at - 500):at + 100]
        if backend == "neo4j":
            assert f"INHERIT_PERMISSIONS*1..{CONTAINER_INHERIT_MAX_DEPTH}" in clause
        else:
            assert "FOR anc IN 1..@inherit_max_depth" in clause
            assert bound["inherit_max_depth"] == CONTAINER_INHERIT_MAX_DEPTH

    @pytest.mark.parametrize("backend", ["neo4j", "arango"])
    @pytest.mark.asyncio
    async def test_each_leg_tags_which_path_granted_the_record(self, backend):
        """The row mapping prefers the trusted row to keep the citation stable.
        That is only meaningful if the query actually emits the two tags — with
        both legs tagged the same, the preference silently does nothing."""
        render = _render_neo4j_query if backend == "neo4j" else _render_arango_query
        query, _ = await render(
            trusted_app_ids=frozenset({"app-1"}),
            trusted_group_ids=frozenset({"rg-1"}),
        )
        assert "'trusted'" in query or '"trusted"' in query
        assert "'adjudicated'" in query or '"adjudicated"' in query


class TestTrustedShortcutIsOptional:
    """Empty trusted sets must reproduce full adjudication exactly — that is the
    rollback path, and what every other test in this module assumes."""

    @pytest.mark.asyncio
    async def test_no_trusted_sets_means_no_shortcut_in_the_query(self):
        query, params = await _render_neo4j_query()
        assert "trusted_app_ids" not in query
        assert "UNION" not in query
        assert params["trusted_app_ids"] == []
        assert params["trusted_group_ids"] == []

    @pytest.mark.asyncio
    async def test_trusted_sets_add_a_leg_that_skips_the_role_resolution(self):
        query, params = await _render_neo4j_query(
            trusted_app_ids=frozenset({"app-1"}),
            trusted_group_ids=frozenset({"rg-1"}),
        )
        assert query.count("UNION") == 1
        trusted_leg = query.split("UNION")[0]
        # The point of the split: the trusted leg must not run the fragment.
        assert "permission_role" not in trusted_leg
        assert "$trusted_app_ids" in trusted_leg
        assert params["trusted_app_ids"] == ["app-1"]

    @pytest.mark.asyncio
    async def test_the_adjudicated_leg_excludes_what_the_trusted_leg_took(self):
        """Without the NOT, trusted records would be adjudicated anyway and the
        shortcut would cost a second leg while saving nothing."""
        query, _ = await _render_neo4j_query(trusted_app_ids=frozenset({"app-1"}))
        adjudicated_leg = query.split("UNION")[1]
        # Not `"NOT" in leg` — that matches `permission_role IS NOT NULL` and so
        # passes even when the exclusion is dropped entirely.
        assert "$trusted_app_ids" in adjudicated_leg
        assert "AND NOT" in adjudicated_leg
        assert "permission_role" in adjudicated_leg

    @pytest.mark.asyncio
    async def test_every_non_permission_gate_survives_in_both_legs(self):
        """The shortcut replaces permission only. Org scope, soft-delete,
        indexing state and app reachability have no container equivalent."""
        query, _ = await _render_neo4j_query(
            trusted_app_ids=frozenset({"app-1"}),
            trusted_group_ids=frozenset({"rg-1"}),
        )
        for leg in query.split("UNION"):
            assert "orgId: $org_id" in leg
            assert "isDeleted" in leg
            assert "$completed" in leg
            assert "reachable_apps" in leg
