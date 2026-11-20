"""The access check runs each statement under a time limit when its caller sets one, on every
backend: Neo4j's server-side limit (`Query(timeout=)`), ArangoDB's `maxRuntime` with the client
giving up a moment later, and PostgreSQL's statement limit for the units the check runs in. A
check past its limit fails as unavailable, which every caller reads as "hide", never as "show".

The record access route a shared chat's read calls sets the read limit, and answers 503 within
its own budget when the graph stops answering at all, where no server-side limit can fire."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from fastapi import HTTPException

from app.api.routes import chatbot
from app.api.routes.chatbot import check_viewer_record_access
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import (
    ACCESS_CHECK_READ_TIMEOUT_SECONDS,
    AccessCheck,
)
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres import permissions as pg_permissions
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from app.utils.chat_helpers import accessible_node_names

ORG = "org-1"


class TestCheckAccess:
    async def test_the_limit_reaches_the_backends_rows(self) -> None:
        provider = Neo4jProvider.__new__(Neo4jProvider)
        provider.logger = MagicMock()
        seen: dict[str, Any] = {}

        async def rows(user_key: str, org_id: str, node_ids: list, vrids: list, *, access: Any, transaction: Any,  # noqa: ANN401
                       timeout: float | None = None) -> list:
            seen["timeout"] = timeout
            return []

        provider._kh_v3_accessible_rows = rows
        await provider.check_access("user-key", ORG, node_ids=["n1"], timeout=2.0)
        assert seen == {"timeout": 2.0}

    async def test_with_no_limit_a_backend_without_the_keyword_still_answers(self) -> None:
        provider = Neo4jProvider.__new__(Neo4jProvider)
        provider.logger = MagicMock()

        async def rows(user_key: str, org_id: str, node_ids: list, vrids: list, *, access: Any, transaction: Any) -> list:  # noqa: ANN401
            return [{"id": "n1"}]

        provider._kh_v3_accessible_rows = rows
        assert (await provider.check_access("user-key", ORG, node_ids=["n1"])).node_ids == frozenset({"n1"})

    def test_a_read_waits_two_seconds_a_statement(self) -> None:
        assert ACCESS_CHECK_READ_TIMEOUT_SECONDS == 2.0


class TestNeo4j:
    async def test_the_grants_and_the_rows_statements_run_under_the_limit(self) -> None:
        provider = Neo4jProvider.__new__(Neo4jProvider)
        provider.logger = MagicMock()
        provider.client = MagicMock()
        provider.client.execute_query = AsyncMock(side_effect=[
            [{"grantees": ["user-key"], "gatedApps": ["app"], "grantSets": []}],
            [{"items": []}],
        ])

        await provider._kh_v3_accessible_rows("user-key", ORG, ["n1"], [], access=None, transaction=None, timeout=2.0)

        assert [call.kwargs.get("timeout") for call in provider.client.execute_query.await_args_list] == [2.0, 2.0]


def _arango() -> tuple[ArangoHTTPProvider, list[dict[str, Any]]]:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = MagicMock()
    provider.http_client = MagicMock()
    stages = {text: name for name, text in ArangoHTTPProvider._kh_v3_check_aql().items()}
    calls: list[dict[str, Any]] = []

    async def execute_aql(query: str, bind_vars: dict | None = None, **kwargs: Any) -> list:  # noqa: ANN401
        stage = stages.get(query, "grants")
        calls.append({"stage": stage, **kwargs})
        if stage == "grants":
            return [{"grantees": ["u"], "gatedApps": ["app"], "grants": []}]
        if stage == "targets":
            return [{"id": i, "vrid": None, "connectorId": "app", "indexingStatus": "COMPLETED", "isInternal": False,
                     "ok": False, "walk": True} for i in bind_vars["kh_ids"]]
        if stage == "seeds":
            return [{"node": i, "seed": "s1"} for i in bind_vars["kh_ids"]]
        return []

    provider.http_client.execute_aql = AsyncMock(side_effect=execute_aql)
    return provider, calls


class TestArango:
    @pytest.mark.parametrize("concurrent_max", [0, 50], ids=["staged", "side by side"])
    async def test_every_statement_of_the_check_runs_under_the_limit(self, concurrent_max: int) -> None:
        provider, calls = _arango()
        provider._KH_CHECK_CONCURRENT_MAX = concurrent_max

        await provider._kh_v3_accessible_rows("u", ORG, ["a", "b"], [], access=None, transaction=None, timeout=2.0)

        assert {call["stage"] for call in calls} >= {"grants", "targets", "from_app", "declared", "seeds"}
        assert all((call.get("max_runtime"), call.get("client_timeout")) == (2.0, 3.0) for call in calls), calls

    async def test_with_no_limit_the_statements_keep_their_own(self) -> None:
        provider, calls = _arango()
        await provider._kh_v3_accessible_rows("u", ORG, ["a"], [], access=None, transaction=None)
        assert all("client_timeout" not in call and "max_runtime" not in call for call in calls), calls


class _Response:
    def __init__(self, body: dict) -> None:
        self.status, self._body = 201, body

    async def json(self) -> dict:
        return self._body

    async def text(self) -> str:
        return ""

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class TestArangoClient:
    @pytest.fixture
    def client(self) -> ArangoHTTPClient:
        return ArangoHTTPClient(base_url="http://localhost:8529", username="root", password="x", database="db", logger=MagicMock())

    async def test_the_client_stops_waiting_at_its_own_limit_on_every_page(self, client: ArangoHTTPClient) -> None:
        session = MagicMock()
        session.post.return_value = _Response({"result": [1], "hasMore": True, "id": "c1"})
        session.put.return_value = _Response({"result": [2], "hasMore": False})

        with patch.object(client, "_get_session", AsyncMock(return_value=session)):
            assert await client.execute_aql("RETURN 1", max_runtime=2.0, client_timeout=3.0) == [1, 2]

        assert session.post.call_args.kwargs["timeout"] == aiohttp.ClientTimeout(total=3.0)
        assert session.post.call_args.kwargs["json"]["options"] == {"maxRuntime": 2.0}
        assert session.put.call_args.kwargs["timeout"] == aiohttp.ClientTimeout(total=3.0)

    async def test_without_a_client_limit_the_session_default_applies(self, client: ArangoHTTPClient) -> None:
        session = MagicMock()
        session.post.return_value = _Response({"result": [], "hasMore": False})

        with patch.object(client, "_get_session", AsyncMock(return_value=session)):
            await client.execute_aql("RETURN 1")

        assert "timeout" not in session.post.call_args.kwargs


class _Db:
    """`KernelDB.run` as the provider calls it: records each unit's options and runs its body."""

    def __init__(self) -> None:
        self.units: list[dict[str, Any]] = []

    async def run(self, fn: Any, **opts: Any) -> Any:  # noqa: ANN401
        self.units.append(opts)
        return await fn(MagicMock())


def _postgres() -> PostgresGraphProvider:
    provider = PostgresGraphProvider.__new__(PostgresGraphProvider)
    provider.logger = MagicMock()
    provider._db = _Db()
    provider._txns = {}
    return provider


@pytest.fixture
def pg_request() -> Any:  # noqa: ANN401
    user = SimpleNamespace(kind="user")
    with (
        patch.object(pg_permissions, "current_org_id", return_value=ORG),
        patch.object(pg_permissions, "current_or_system", return_value=user),
        patch("app.services.graph_db.postgres.base.current_or_system", return_value=user),
        patch.object(pg_permissions.access_sql, "accessible_rows", AsyncMock(return_value=[])),
    ):
        yield


class TestPostgres:
    async def test_the_grants_and_the_walk_run_in_units_limited_per_statement(self, pg_request: Any) -> None:  # noqa: ANN401
        provider = _postgres()
        with patch.object(pg_permissions.kg_read, "enabled", return_value=False), \
                patch.object(PostgresGraphProvider, "_gate", AsyncMock(return_value=None)):
            await provider._kh_v3_accessible_rows("user-key", ORG, ["n1"], [], access=None, transaction=None, timeout=2.0)

        assert [unit.get("timeout_ms") for unit in provider._db.units] == [2000, 2000]

    async def test_the_kg_read_runs_in_a_limited_unit_too(self, pg_request: Any) -> None:  # noqa: ANN401
        provider = _postgres()

        async def kg_rows(run: Any, *_a: Any, **_k: Any) -> list:  # noqa: ANN401
            await run(AsyncMock(return_value=None))
            return []

        with patch.object(pg_permissions.kg_read, "enabled", return_value=True), \
                patch.object(pg_permissions.kg_read, "accessible_rows", kg_rows):
            await provider._kh_v3_accessible_rows(
                "user-key", ORG, ["n1"], [], access={"grantee_ids": [], "gated_app_ids": [], "by_connector": {}},
                transaction=None, timeout=2.0,
            )

        assert [unit.get("timeout_ms") for unit in provider._db.units] == [2000]

    async def test_with_no_limit_the_units_keep_the_class_limit(self, pg_request: Any) -> None:  # noqa: ANN401
        provider = _postgres()
        with patch.object(pg_permissions.kg_read, "enabled", return_value=False), \
                patch.object(PostgresGraphProvider, "_gate", AsyncMock(return_value=None)):
            await provider._kh_v3_accessible_rows("user-key", ORG, ["n1"], [], access=None, transaction=None)

        assert all("timeout_ms" not in unit for unit in provider._db.units)


def _request(body: dict) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    return request


class TestTheRecordAccessRoute:
    async def test_the_check_behind_a_read_runs_with_the_read_limit(self) -> None:
        with patch("app.api.routes.chatbot.readable_for_reader", AsyncMock(return_value={})) as check:
            await check_viewer_record_access(_request({"recordIds": ["a"]}), AsyncMock(), {"orgId": ORG, "userId": "viewer"})
        assert check.await_args.kwargs["timeout"] == ACCESS_CHECK_READ_TIMEOUT_SECONDS

    async def test_a_graph_that_stops_answering_is_a_503_within_the_routes_budget(self) -> None:
        async def never(*_a: object, **_k: object) -> dict:
            await asyncio.Event().wait()
            return {}

        with patch("app.api.routes.chatbot.readable_for_reader", never), \
                patch.object(chatbot, "RECORD_ACCESS_BUDGET_SECONDS", 0.05, create=True):
            with pytest.raises(HTTPException) as err:
                await asyncio.wait_for(
                    check_viewer_record_access(_request({"recordIds": ["a"]}), AsyncMock(), {"orgId": ORG, "userId": "viewer"}),
                    2,
                )
        assert err.value.status_code == 503

    async def test_an_address_lookup_that_stops_answering_is_a_503_too(self) -> None:
        graph = AsyncMock()

        async def never(*_a: object, **_k: object) -> list:
            await asyncio.Event().wait()
            return []

        graph.get_records_by_weburls = never
        with patch.object(chatbot, "RECORD_ACCESS_BUDGET_SECONDS", 0.05, create=True):
            with pytest.raises(HTTPException) as err:
                await asyncio.wait_for(
                    check_viewer_record_access(_request({"webUrls": ["https://x/r"]}), graph, {"orgId": ORG, "userId": "viewer"}),
                    2,
                )
        assert err.value.status_code == 503


class TestAccessibleNodeNames:
    async def test_the_limit_reaches_the_check(self) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        graph.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"a"}), names={"a": "A"}))

        assert await accessible_node_names(graph, {"a"}, "viewer", ORG, timeout=2.0) == {"a": "A"}
        assert graph.check_access.await_args.kwargs["timeout"] == 2.0

    async def test_with_no_limit_the_check_is_called_as_before(self) -> None:
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key"})
        graph.check_access = AsyncMock(return_value=AccessCheck())

        await accessible_node_names(graph, {"a"}, "viewer", ORG)
        assert "timeout" not in graph.check_access.await_args.kwargs
