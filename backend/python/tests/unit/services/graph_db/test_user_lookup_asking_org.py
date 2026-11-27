"""A user lookup by ``userId`` can be bound to the organisation that asks.

``get_user_by_user_id`` takes an optional ``org_id``: given, only a user of that organisation matches, and an empty
one matches no user, on every graph store. The callers below have their organisation in scope and pass it, so a
lookup never resolves a user of any other organisation.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import for_org
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.identity import IdentityMixin

USER_ID = "ext-user-1"
ORG = "org-a"
OTHER_ORG = "org-b"


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    p.logger = MagicMock()
    p.http_client = MagicMock(execute_aql=AsyncMock(return_value=[]))
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = MagicMock(execute_query=AsyncMock(return_value=[]))
    return p


def _postgres(row: dict | None = None) -> IdentityMixin:
    p = object.__new__(IdentityMixin)
    p.logger = logging.getLogger("t")
    p._one = AsyncMock(return_value=row)  # pyright: ignore[reportAttributeAccessIssue]
    return p


class TestEachStoreBindsTheLookupToTheOrg:
    async def test_arango_filters_by_org_when_given(self) -> None:
        p = _arango()
        await p.get_user_by_user_id(USER_ID, org_id=ORG)
        call = p.http_client.execute_aql.await_args
        assert "user.orgId == @org_id" in call.args[0]
        assert call.kwargs["bind_vars"] == {"user_id": USER_ID, "org_id": ORG}

    async def test_arango_without_org_is_unchanged(self) -> None:
        p = _arango()
        await p.get_user_by_user_id(USER_ID)
        call = p.http_client.execute_aql.await_args
        assert "orgId" not in call.args[0]
        assert call.kwargs["bind_vars"] == {"user_id": USER_ID}

    async def test_neo4j_filters_by_org_when_given(self) -> None:
        p = _neo4j()
        await p.get_user_by_user_id(USER_ID, org_id=ORG)
        call = p.client.execute_query.await_args
        assert "u.orgId = $org_id" in call.args[0]
        assert call.kwargs["parameters"] == {"user_id": USER_ID, "org_id": ORG}

    async def test_neo4j_without_org_is_unchanged(self) -> None:
        p = _neo4j()
        await p.get_user_by_user_id(USER_ID)
        call = p.client.execute_query.await_args
        assert "orgId" not in call.args[0]
        assert call.kwargs["parameters"] == {"user_id": USER_ID}

    async def test_postgres_reads_nothing_for_an_org_other_than_the_bound_one(self) -> None:
        p = _postgres()
        with for_org(ORG, job="t"):
            assert await p.get_user_by_user_id(USER_ID, org_id=OTHER_ORG) is None
            p._one.assert_not_awaited()
            await p.get_user_by_user_id(USER_ID, org_id=ORG)
            p._one.assert_awaited_once()

    @pytest.mark.parametrize("store", ["arango", "neo4j", "postgres"])
    async def test_an_empty_org_matches_no_user_without_a_read(self, store: str) -> None:
        if store == "arango":
            p = _arango()
            read = p.http_client.execute_aql
        elif store == "neo4j":
            p = _neo4j()
            read = p.client.execute_query
        else:
            p = _postgres({"node_id": "k", "doc": {"userId": USER_ID}})
            read = p._one
        with for_org(ORG, job="t"):
            assert await p.get_user_by_user_id(USER_ID, org_id="") is None
        read.assert_not_awaited()


_APP = Path(__file__).resolve().parents[4] / "app"
# (file under app/, function, lookup): each has its organisation in scope and binds its lookup to it.
_BOUND_LOOKUPS = [
    ("api/routes/control_plane_drivers.py", "user_teams", "get_user_by_user_id"),
    ("harness/knowledge/read_gate.py", "_readable", "get_user_by_user_id"),
    ("harness/knowledge/service.py", "_user_key", "get_user_by_user_id"),
    ("harness/knowledge/service.py", "principal", "get_user_by_user_id"),
    ("agents/chat_modes/setup.py", "fetch_available_connectors", "get_caller_user"),
]


def _lookups(path: str, function: str, lookup: str) -> list[ast.Call]:
    tree = ast.parse((_APP / path).read_text())
    [fn] = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function]
    return [
        c for c in ast.walk(fn)
        if isinstance(c, ast.Call) and lookup in {getattr(c.func, "attr", None), getattr(c.func, "id", None)}
    ]


@pytest.mark.parametrize(("path", "function", "lookup"), _BOUND_LOOKUPS)
def test_a_caller_with_its_org_in_scope_binds_the_lookup(path: str, function: str, lookup: str) -> None:
    calls = _lookups(path, function, lookup)
    assert calls, f"{path}:{function} no longer calls {lookup}; update the list"
    for call in calls:
        assert "org_id" in {k.arg for k in call.keywords}, f"{path}:{function}:{call.lineno} looks the user up in no org"


@pytest.mark.parametrize("store", ["arango", "neo4j"])
async def test_without_an_org_the_lookup_needs_no_bound_org_as_before(
    store: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The org is an optional filter: a caller that names none (a job that resolves the user before it knows the
    org) reads as it did before the filter existed; one that names an org is held to the bound one."""
    from app.core.org_context import MissingOrgContext

    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")  # this folder's conftest runs in shadow mode
    p = _arango() if store == "arango" else _neo4j()
    assert await p.get_user_by_user_id(USER_ID) is None
    assert await p.get_user_by_user_id(USER_ID, org_id="") is None  # matches no user, refused by no check
    with pytest.raises(MissingOrgContext):
        await p.get_user_by_user_id(USER_ID, org_id=ORG)
