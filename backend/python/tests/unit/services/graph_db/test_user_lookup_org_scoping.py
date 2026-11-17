"""User lookups are bound to the organisation that is asking.

Hardening: a lookup by userId alone matches the first user with that id, so a
caller that knows its organisation binds the lookup to it, and a lookup can never
resolve a user outside the asking organisation. Covered here, on each provider:

* the virtual-record-id queries (knowledge bases and connectors) and the
  accessible-KB id lookup match the user within the org they were asked for;
* ``get_user_by_user_id`` takes an optional ``org_id`` and, when given, only
  returns a user of that organisation; an empty ``org_id`` matches no user, on
  every store;
* the reindex paths pass the org they were asked for to that lookup;
* every caller in ``app/`` that has the organisation in scope passes it on
  (reverting any one of them fails ``TestEveryCallerWithTheOrgPassesIt``).
"""
from __future__ import annotations

import ast
import logging
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import for_org
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.identity import IdentityMixin

USER_ID = "ext-user-1"
ORG_A = "org-a"
ORG_B = "org-b"

# The same external userId, one user per organisation.
USERS = [
    {"_key": "key-a", "id": "key-a", "userId": USER_ID, "orgId": ORG_A},
    {"_key": "key-b", "id": "key-b", "userId": USER_ID, "orgId": ORG_B},
]

_NEO4J_ORG_SCOPED_USER = re.compile(r"MATCH \(\w+:User \{userId: \$userId, orgId: \$orgId\}\)")


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.execute_query = AsyncMock(return_value=[])
    p.http_client = MagicMock(execute_aql=AsyncMock(return_value=[]))
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = MagicMock(execute_query=AsyncMock(return_value=[]))
    return p


async def _user_lookup_by_org(user_id: str, *, raise_on_error: bool = False, org_id: str | None = None) -> dict | None:
    """What a store holding USERS answers; an unscoped lookup takes the first match."""
    return next((u for u in USERS if u["userId"] == user_id and org_id in (None, u["orgId"])), None)


def _aql(p: ArangoHTTPProvider) -> tuple[str, dict]:
    call = p.execute_query.await_args
    return call.args[0], call.kwargs.get("bind_vars") or call.args[1]


class TestVirtualIdQueriesMatchTheUserWithinTheOrg:
    async def test_arango_kb_query_filters_the_user_by_org(self) -> None:
        p = _arango()
        await p._get_kb_virtual_ids(USER_ID, ORG_B)
        query, bind_vars = _aql(p)
        assert "FILTER user.orgId == @orgId" in query
        assert bind_vars["orgId"] == ORG_B
        assert bind_vars["userId"] == USER_ID

    async def test_arango_connector_query_filters_the_user_by_org(self) -> None:
        p = _arango()
        await p._get_virtual_ids_for_connector(USER_ID, ORG_B, "conn-1")
        query, bind_vars = _aql(p)
        assert "FILTER user.orgId == @orgId" in query
        assert bind_vars["orgId"] == ORG_B

    async def test_neo4j_kb_query_matches_the_user_within_the_org(self) -> None:
        p = _neo4j()
        await p._get_kb_virtual_ids(USER_ID, ORG_B)
        call = p.client.execute_query.await_args
        assert _NEO4J_ORG_SCOPED_USER.search(call.args[0])
        assert call.kwargs["parameters"]["orgId"] == ORG_B

    async def test_neo4j_connector_query_matches_the_user_within_the_org(self) -> None:
        p = _neo4j()
        await p._get_virtual_ids_for_connector(USER_ID, ORG_B, "conn-1")
        call = p.client.execute_query.await_args
        assert _NEO4J_ORG_SCOPED_USER.search(call.args[0])
        assert call.kwargs["parameters"]["orgId"] == ORG_B

    async def test_neo4j_accessible_kb_ids_match_the_user_within_the_org(self) -> None:
        p = _neo4j()
        await p._get_accessible_kb_ids(USER_ID, ORG_B)
        call = p.client.execute_query.await_args
        assert _NEO4J_ORG_SCOPED_USER.search(call.args[0])
        assert call.kwargs["parameters"]["orgId"] == ORG_B

    async def test_neo4j_cached_kb_lookup_passes_the_org_to_the_kb_ids(self) -> None:
        p = _neo4j()
        p._get_accessible_kb_ids = AsyncMock(return_value=[])
        await p._get_kb_virtual_ids_cached(USER_ID, ORG_B, None)
        assert p._get_accessible_kb_ids.await_args.args[:2] == (USER_ID, ORG_B)


class TestGetUserByUserIdOrgScope:
    async def test_arango_filters_by_org_when_given(self) -> None:
        p = _arango()
        await p.get_user_by_user_id(USER_ID, org_id=ORG_B)
        call = p.http_client.execute_aql.await_args
        assert "user.orgId == @org_id" in call.args[0]
        assert call.kwargs["bind_vars"] == {"user_id": USER_ID, "org_id": ORG_B}

    async def test_arango_without_org_is_unchanged(self) -> None:
        p = _arango()
        await p.get_user_by_user_id(USER_ID)
        call = p.http_client.execute_aql.await_args
        assert "orgId" not in call.args[0]
        assert call.kwargs["bind_vars"] == {"user_id": USER_ID}

    async def test_neo4j_filters_by_org_when_given(self) -> None:
        p = _neo4j()
        await p.get_user_by_user_id(USER_ID, org_id=ORG_B)
        call = p.client.execute_query.await_args
        assert "u.orgId = $org_id" in call.args[0]
        assert call.kwargs["parameters"] == {"user_id": USER_ID, "org_id": ORG_B}

    async def test_neo4j_without_org_is_unchanged(self) -> None:
        p = _neo4j()
        await p.get_user_by_user_id(USER_ID)
        call = p.client.execute_query.await_args
        assert "orgId" not in call.args[0]
        assert call.kwargs["parameters"] == {"user_id": USER_ID}

    async def test_postgres_reads_nothing_for_another_org(self) -> None:
        p = object.__new__(IdentityMixin)
        p.logger = logging.getLogger("t")
        p._one = AsyncMock(return_value=None)
        with for_org(ORG_A, job="t"):
            assert await p.get_user_by_user_id(USER_ID, org_id=ORG_B) is None
            p._one.assert_not_awaited()
            await p.get_user_by_user_id(USER_ID, org_id=ORG_A)
            p._one.assert_awaited_once()


class TestReindexPathsPassTheOrg:
    """Two users share a userId; the caller of org B must not be resolved to the user of org A."""

    @pytest.mark.parametrize("make", [_arango, _neo4j])
    async def test_reindex_single_record_resolves_the_user_in_the_asked_org(self, make) -> None:
        p = make()
        p.get_document = AsyncMock(return_value={"id": "r1", "_key": "r1", "origin": "CONNECTOR", "connectorName": "X", "connectorId": "c1"})
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p.reindex_single_record("r1", USER_ID, ORG_B, MagicMock())
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B
        # A user of the org that is not asked about is never the caller.
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p.reindex_single_record("r1", USER_ID, "org-without-the-user", MagicMock())
        assert result["code"] == 404
        assert result["reason"] == f"User not found: {USER_ID}"

    @pytest.mark.parametrize("make", [_arango, _neo4j])
    async def test_reindex_record_group_records_resolves_the_user_in_the_asked_org(self, make) -> None:
        p = make()
        p.get_document = AsyncMock(return_value={"id": "g1", "_key": "g1", "connectorId": "c1", "connectorName": "X"})
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p.reindex_record_group_records("g1", 0, USER_ID, "org-without-the-user")
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == "org-without-the-user"
        assert result["code"] == 404
        assert result["reason"] == f"User not found: {USER_ID}"


class TestCallSitesPassTheOrg:
    """A method that is given the org passes it on to the user lookup, so the caller is never taken from another org."""

    async def test_arango_check_record_access_looks_the_user_up_in_the_asked_org(self) -> None:
        p = _arango()
        p.get_user_by_user_id = AsyncMock(return_value=None)
        assert await p.check_record_access_with_details(USER_ID, ORG_B, "r1") is None
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B

    @pytest.mark.parametrize("make", [_arango, _neo4j])
    async def test_upload_validation_looks_the_user_up_in_the_asked_org(self, make) -> None:
        p = make()
        p.get_user_by_user_id = AsyncMock(side_effect=_user_lookup_by_org)
        result = await p._validate_upload_context("kb1", USER_ID, "org-without-the-user")
        assert p.get_user_by_user_id.await_args.kwargs["org_id"] == "org-without-the-user"
        assert result["valid"] is False
        assert result["code"] == 404


class TestEmptyOrgMatchesNoUser:
    """``org_id=""`` is an organisation that has no users: all three stores answer None without a read."""

    async def test_arango(self) -> None:
        p = _arango()
        assert await p.get_user_by_user_id(USER_ID, org_id="") is None
        p.http_client.execute_aql.assert_not_awaited()

    async def test_neo4j(self) -> None:
        p = _neo4j()
        assert await p.get_user_by_user_id(USER_ID, org_id="") is None
        p.client.execute_query.assert_not_awaited()

    async def test_postgres(self) -> None:
        p = object.__new__(IdentityMixin)
        p.logger = logging.getLogger("t")
        p._one = AsyncMock(return_value={"node_id": "k", "doc": {"userId": USER_ID}})
        with for_org(ORG_A, job="t"):
            assert await p.get_user_by_user_id(USER_ID, org_id="") is None
        p._one.assert_not_awaited()


_APP = Path(__file__).resolve().parents[4] / "app"
_ORG_NAMES = {"org_id", "orgId", "organization_id"}
# An ``Actor`` carries the org as ``actor.org_id``, so a function that takes one has the org in scope.
_ORG_CARRIERS = {"actor"}
# These derive the organisation from the requester's own user, and their routers hand them no organisation to bind to.
# This one reads the identity first and refuses a wrong-org identity itself, with its own error.
_ORG_CHECKED_AFTER_THE_LOOKUP = {
    ("api/routes/agent.py", "_enrich_user_info_for_service_account_agent_chat"),
    # Takes no organisation: the record's own is only read to stamp the event it writes.
    ("services/graph_db/arango/arango_http_provider.py", "delete_knowledge_base_record"),
}
_ORG_DERIVED_FROM_THE_LOOKUP = {("connectors/sources/localKB/handlers/kb_service.py", "update_kb_permission"),
                                ("connectors/sources/localKB/handlers/kb_service.py", "remove_kb_permission")}


_LOOKUPS = {"get_user_by_user_id", "get_caller_user"}

# Every lookup below passes the org today. The set may grow, never shrink: a caller that stops scoping its lookup
# fails here even when the org is no longer named anywhere else in the function.
_SCOPED_LOOKUP_CALLERS = frozenset({
    ('agents/actions/knowledge_graph/ops/scope.py', '_catalog_fallback'),
    ('agents/chat_modes/bridge.py', '_fetch_available_connectors'),
    ('api/routes/agent.py', '_resolve_service_account_binding'),
    ('api/routes/agent.py', 'get_assistant_agent'),
    ('api/routes/ai_models_registry.py', 'preview_identity_forwarding'),
    ('api/routes/chatbot.py', '_attachment_caller'),
    ('api/routes/chatbot.py', '_org_user_keys'),
    ('connectors/core/base/data_processor/data_source_entities_processor.py', 'get_user_by_user_id'),
    ('connectors/core/base/data_store/graph_data_store.py', 'get_user_by_user_id'),
    ('connectors/sources/atlassian/confluence_datacenter_personal/connector.py', 'run_sync'),
    ('connectors/sources/localKB/handlers/kb_service.py', '_resolve_user_ids_to_graph_keys'),
    ('connectors/sources/localKB/handlers/kb_service.py', '_teams_not_in_requester_org'),
    ('connectors/sources/localKB/handlers/kb_service.py', 'create_knowledge_base'),
    ('connectors/sources/localKB/handlers/kb_service.py', 'list_user_knowledge_bases'),
    ('connectors/sources/localKB/handlers/knowledge_hub_service.py', '_resolve_user'),
    ('connectors/sources/local_folder/connector.py', '_owner_and_group'),
    ('connectors/sources/local_fs/connector.py', '_ensure_owner_and_record_group'),
    ('modules/agents/knowledge_scope.py', 'admit_caller_project_collections'),
    ('services/artifact_registry/access.py', 'resolve_user_key'),
    ('services/artifact_registry/gallery.py', '_resolve_user_key'),
    ('services/artifact_registry/sharing.py', '_notify'),
    ('services/artifact_registry/sharing.py', '_resolve_targets'),
    ('services/artifact_registry/sharing.py', 'unshare'),
    ('services/graph_db/arango/arango_http_provider.py', '_get_user_app_ids'),
    ('services/graph_db/arango/arango_http_provider.py', '_validate_upload_context'),
    ('services/graph_db/arango/arango_http_provider.py', 'apply_verified_user_email'),
    ('services/graph_db/arango/arango_http_provider.py', 'check_record_access_with_details'),
    ('services/graph_db/arango/arango_http_provider.py', 'get_accessible_connector_types'),
    ('services/graph_db/arango/arango_http_provider.py', 'get_accessible_containers'),
    ('services/graph_db/arango/arango_http_provider.py', 'reindex_record_group_records'),
    ('services/graph_db/arango/arango_http_provider.py', 'reindex_single_record'),
    ('services/graph_db/interface/graph_db_provider.py', 'filter_accessible_record_ids'),
    ('services/graph_db/interface/graph_db_provider.py', 'filter_accessible_virtual_record_ids'),
    ('services/graph_db/interface/graph_db_provider.py', 'get_ref_access'),
    ('services/graph_db/neo4j/neo4j_provider.py', '_validate_upload_context'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'apply_verified_user_email'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'delete_record'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'get_accessible_connector_types'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'get_accessible_containers'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'get_accessible_virtual_record_ids'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'reindex_record_group_records'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'reindex_single_record'),
    ('services/graph_db/neo4j/neo4j_provider.py', 'update_agent_permission'),
    ('services/graph_db/postgres/delete.py', 'delete_record'),
    ('services/graph_db/postgres/entity_access.py', 'get_entity_access_context'),
    ('services/graph_db/postgres/permissions.py', 'check_record_access_with_details'),
    ('services/graph_db/postgres/permissions.py', 'get_accessible_connector_types'),
    ('services/graph_db/postgres/permissions.py', 'get_accessible_containers'),
    ('services/graph_db/postgres/permissions.py', 'get_accessible_virtual_record_ids'),
    ('services/graph_db/postgres/trash.py', 'reindex_record_group_records'),
    ('services/graph_db/postgres/trash.py', 'reindex_single_record'),
    ('services/messaging/kafka/handlers/entity.py', '_handle_user_added'),
    ('services/messaging/kafka/handlers/entity.py', '_handle_user_deleted'),
    ('services/messaging/kafka/handlers/entity.py', '_handle_user_updated'),
    ('utils/cache_helpers.py', 'get_cached_user_info'),
    ('utils/chat_helpers.py', 'accessible_node_ids'),
    ('utils/pattern_match.py', 'merge_pattern_match_results'),
    ('utils/user_lookup.py', 'get_caller_user'),
})


def _names_an_org(node: ast.AST) -> bool:
    """A local, an argument, an attribute (``self.org_id``, ``actor.org_id``) or a payload key (``payload["orgId"]``,
    ``user.get("orgId")``) that carries an organisation."""
    if isinstance(node, ast.arg | ast.Attribute | ast.Name):
        return (getattr(node, "arg", None) or getattr(node, "attr", None) or getattr(node, "id", None)) in (
            _ORG_NAMES | _ORG_CARRIERS
        )
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value in _ORG_NAMES
    if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get" and node.args:
        return isinstance(node.args[0], ast.Constant) and node.args[0].value in _ORG_NAMES
    return False


def _spread_is_empty(fn: ast.AST, value: ast.expr) -> bool:
    """``**{}``, or ``**name`` where every assignment to the name in the function is an empty dict."""
    if isinstance(value, ast.Dict):
        return not value.keys
    if isinstance(value, ast.Name):
        assigned = [
            n.value
            for n in ast.walk(fn)
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == value.id for t in n.targets)
        ]
        return bool(assigned) and all(isinstance(v, ast.Dict) and not v.keys for v in assigned)
    return False


def _scopes_the_lookup(fn: ast.AST, call: ast.Call) -> bool:
    for k in call.keywords:
        if k.arg == "org_id":
            # ``org_id=None`` is the unscoped lookup, spelled out.
            if not (isinstance(k.value, ast.Constant) and k.value.value is None):
                return True
        elif k.arg is None and not _spread_is_empty(fn, k.value):
            # ``**scope`` is the optional-org spread used by wrappers that keep their old call shape.
            return True
    return False


def _scoped_lookup_callers() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for call in ast.walk(fn):
                if (
                    isinstance(call, ast.Call)
                    and (getattr(call.func, "attr", None) or getattr(call.func, "id", "")) in _LOOKUPS
                    and _scopes_the_lookup(fn, call)
                ):
                    found.add((rel, fn.name))
    return found


def _unscoped_lookups_with_the_org_in_scope() -> list[str]:
    found = []
    for path in sorted(_APP.rglob("*.py")):
        rel = path.relative_to(_APP).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if (rel, fn.name) in _ORG_DERIVED_FROM_THE_LOOKUP | _ORG_CHECKED_AFTER_THE_LOOKUP or not any(_names_an_org(n) for n in ast.walk(fn)):
                continue
            for call in ast.walk(fn):
                if not isinstance(call, ast.Call):
                    continue
                if (getattr(call.func, "attr", None) or getattr(call.func, "id", "")) not in _LOOKUPS:
                    continue
                if not _scopes_the_lookup(fn, call):
                    found.append(f"{rel}:{call.lineno} in {fn.name}")
    return found


class TestEveryCallerWithTheOrgPassesIt:
    def test_no_lookup_with_the_org_in_scope_is_left_unscoped(self) -> None:
        assert _unscoped_lookups_with_the_org_in_scope() == []

    def test_no_caller_that_scopes_its_lookup_today_stops_doing_so(self) -> None:
        """Dropping the org argument can also remove the function's only mention of the org, which hides it from the
        scan above. This pins the callers that pass it."""
        assert sorted(_SCOPED_LOOKUP_CALLERS - _scoped_lookup_callers()) == []

    @pytest.mark.parametrize(
        "source",
        [
            "async def f(p, actor):\n    return await get_caller_user(p, actor.user_id, org_id=actor.org_id)\n",
            "async def f(p, user_id, org_id):\n    return await p.get_user_by_user_id(user_id, org_id=org_id)\n",
        ],
    )
    def test_the_pin_sees_a_scoped_caller_and_notices_when_it_stops(self, tmp_path, monkeypatch, source) -> None:
        (tmp_path / "m.py").write_text(source)
        monkeypatch.setattr(f"{__name__}._APP", tmp_path)
        assert _scoped_lookup_callers() == {("m.py", "f")}
        (tmp_path / "m.py").write_text(source.replace(", org_id=actor.org_id", "").replace(", org_id=org_id", ""))
        assert _scoped_lookup_callers() == set()

    @pytest.mark.parametrize(
        "source",
        [
            # the org is a local or an argument
            "async def f(p, user_id, org_id):\n    return await p.get_user_by_user_id(user_id)\n",
            # the wrapper counts as a lookup
            "async def f(p, user_id, org_id):\n    return await get_caller_user(p, user_id)\n",
            # the org is an attribute, a payload key or a dict read
            "async def f(self, user_id):\n    return await get_caller_user(self.p, user_id) if self.org_id else None\n",
            "async def f(p, payload):\n    x = payload['orgId']\n    return await p.get_user_by_user_id(payload['userId'])\n",
            "async def f(p, user, uid):\n    o = user.get('orgId')\n    return await p.get_user_by_user_id(uid)\n",
            # the org is carried by an actor, and the lookup's own argument is the only place it is named
            "async def f(p, actor):\n    return await get_caller_user(p, actor.user_id)\n",
            # an org that is None, or a spread that is always empty, scopes nothing
            "async def f(p, user_id, org_id):\n    return await p.get_user_by_user_id(user_id, org_id=None)\n",
            "async def f(p, user_id, org_id):\n    return await p.get_user_by_user_id(user_id, **{})\n",
            "async def f(p, user_id, org_id):\n    scope = {}\n    return await p.get_user_by_user_id(user_id, **scope)\n",
        ],
    )
    def test_the_scan_sees_an_unscoped_lookup(self, tmp_path, monkeypatch, source) -> None:
        """The scan itself works: it flags a lookup that drops the org, whatever way the org is in scope."""
        (tmp_path / "m.py").write_text(source)
        monkeypatch.setattr(f"{__name__}._APP", tmp_path)
        assert len(_unscoped_lookups_with_the_org_in_scope()) == 1

    @pytest.mark.parametrize(
        "source",
        [
            "async def f(p, user_id, org_id):\n    return await p.get_user_by_user_id(user_id, org_id=org_id)\n",
            "async def f(p, user_id, org_id):\n    scope = {} if org_id is None else {'org_id': org_id}\n    return await get_caller_user(p, user_id, **scope)\n",
            "async def f(p, user_id):\n    return await p.get_user_by_user_id(user_id)\n",
        ],
    )
    def test_the_scan_accepts_a_scoped_lookup_and_one_with_no_org(self, tmp_path, monkeypatch, source) -> None:
        (tmp_path / "m.py").write_text(source)
        monkeypatch.setattr(f"{__name__}._APP", tmp_path)
        assert _unscoped_lookups_with_the_org_in_scope() == []


class TestHandlersPassTheOrg:
    async def test_a_user_added_event_looks_the_user_up_in_its_own_org(self) -> None:
        from tests.unit.services.messaging.test_entity_handler import (
            _make_entity_service,
        )

        svc = _make_entity_service()
        svc._adopt_existing_person = AsyncMock()
        svc._get_or_create_knowledge_base = AsyncMock()
        svc._get_or_create_all_team_and_add_user = AsyncMock()
        svc._schedule_connector_link = MagicMock()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        svc.graph_provider.get_document = AsyncMock(return_value={"_key": ORG_B})
        await svc._handle_user_added({"orgId": ORG_B, "userId": USER_ID, "email": "a@test.com"})
        assert svc.graph_provider.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B

    async def test_a_user_updated_event_looks_the_user_up_in_its_own_org(self) -> None:
        from tests.unit.services.messaging.test_entity_handler import (
            _make_entity_service,
        )

        svc = _make_entity_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        await svc._handle_user_updated({"orgId": ORG_B, "userId": USER_ID, "email": "a@test.com"})
        assert svc.graph_provider.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B

    async def test_a_user_deleted_event_looks_the_user_up_in_its_own_org(self) -> None:
        from tests.unit.services.messaging.test_entity_handler import (
            _make_entity_service,
        )

        svc = _make_entity_service()
        svc.graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
        svc.graph_provider.get_user_by_email = AsyncMock(return_value=None)
        await svc._handle_user_deleted({"orgId": ORG_B, "userId": USER_ID, "email": "a@test.com"})
        assert svc.graph_provider.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B

    async def test_the_graph_transaction_store_hands_the_org_to_the_provider(self) -> None:
        from app.connectors.core.base.data_store.graph_data_store import (
            GraphTransactionStore,
        )

        store = object.__new__(GraphTransactionStore)
        store.graph_provider = MagicMock(get_user_by_user_id=AsyncMock(return_value=None))
        await store.get_user_by_user_id(USER_ID, org_id=ORG_B)
        assert store.graph_provider.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B
        store.graph_provider.get_user_by_user_id.reset_mock()
        await store.get_user_by_user_id(USER_ID)
        assert "org_id" not in store.graph_provider.get_user_by_user_id.await_args.kwargs

    async def test_the_entities_processor_hands_the_org_to_the_store(self) -> None:
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        tx = MagicMock(get_user_by_user_id=AsyncMock(return_value=None))
        provider = MagicMock()
        provider.transaction.return_value.__aenter__ = AsyncMock(return_value=tx)
        provider.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
        processor = object.__new__(DataSourceEntitiesProcessor)
        processor.data_store_provider = provider
        await processor.get_user_by_user_id(USER_ID, org_id=ORG_B)
        assert tx.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B
        tx.get_user_by_user_id.reset_mock()
        await processor.get_user_by_user_id(USER_ID)
        assert "org_id" not in tx.get_user_by_user_id.await_args.kwargs

    async def test_the_cached_user_info_is_fetched_in_the_asked_org(self) -> None:
        from app.utils import cache_helpers

        provider = MagicMock(get_user_by_user_id=AsyncMock(return_value=None), get_document=AsyncMock(return_value=None))
        await cache_helpers.get_cached_user_info(provider, "cache-miss-user", ORG_B)
        assert provider.get_user_by_user_id.await_args.kwargs["org_id"] == ORG_B
