"""WP-51 (WP-31 D, GA-03, GA-04): an agent's own run gates. The agents API stores plan approval and checks as
``gates`` (never a dry run), both graph providers hold them as text, a rollback restores them, and the run definition
carries them to Node so every run of the agent is held to them."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.agents.agent_gates import parse_agent_gates, stored_agent_gates
from app.agents.run_definition import build_run_definition
from app.config.constants.service import config_node_constants
from app.schema.arango.documents import agent_schema
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org; these tests call the route functions directly, as that caller."""
    yield from bind_test_org(ORG)

MODELS = [{"modelKey": "mk-a", "modelName": "gpt-5.4", "isReasoning": True}]
CONFIGURED = [{"modelKey": "mk-a", "provider": "openAI", "configuration": {"model": "gpt-5.4", "apiKey": "sk-a"}}]
GATES = {
    "planApproval": "required",
    "verify": {
        "checks": [
            {"kind": "file", "path": "out/report.pdf", "format": "pdf"},
            {"kind": "command", "argv": ["pytest", "-q"], "timeoutS": 120},
            {"kind": "citations"},
        ],
        "onFail": "continue",
        "maxRounds": 2,
    },
}
STORED = json.dumps(GATES, sort_keys=True, separators=(",", ":"))


def _config() -> AsyncMock:
    config = AsyncMock()

    async def get_config(key: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return {"llm": CONFIGURED} if key == config_node_constants.AI_MODELS.value else None

    config.get_config = AsyncMock(side_effect=get_config)
    return config


def _graph(stored: dict[str, Any] | None = None) -> AsyncMock:
    graph = AsyncMock()
    graph.begin_transaction = AsyncMock(return_value="txn-1")
    graph.batch_upsert_nodes = AsyncMock(return_value=True)
    graph.batch_create_edges = AsyncMock(return_value=True)
    graph.commit_transaction = AsyncMock()
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": True, "can_share": True, "role": "OWNER"})
    graph.get_agent = AsyncMock(return_value={"_key": "agent-1", "name": "A", "shareWithOrg": False, **(stored or {})})
    graph.update_agent = AsyncMock(return_value=True)
    return graph


def _request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.state.user = {"userId": "u1", "orgId": ORG, "role": "member"}
    return request


def _patches(graph: AsyncMock):  # noqa: ANN202
    services = {"graph_provider": graph, "config_service": _config(), "logger": MagicMock()}
    return (
        patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services),
        patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": ORG}),
        patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "k1"}),
    )


async def _create(body: dict[str, Any], graph: AsyncMock) -> dict[str, Any]:
    from app.api.routes.agent import create_agent

    services, context, user = _patches(graph)
    with services, context, user:
        response = await create_agent(_request({"name": "A", "models": MODELS, **body}))
    return json.loads(response.body)


async def _update(body: dict[str, Any], graph: AsyncMock) -> dict[str, Any]:
    from app.api.routes.agent import update_agent

    services, context, user = _patches(graph)
    with services, context, user:
        await update_agent(_request(body), "agent-1")
    return graph.update_agent.await_args.args[1]


BAD = [
    pytest.param({"dryRun": True}, "GATES_DRY_RUN", id="a_dry_run_is_never_an_agent_default"),
    pytest.param({"planApproval": "sometimes"}, "GATES_INVALID", id="plan_approval_not_required"),
    pytest.param({"verify": {"checks": [], "onFail": "flag"}}, "GATES_INVALID", id="no_checks"),
    pytest.param({"verify": {"checks": [{"kind": "file", "path": "../etc/passwd"}], "onFail": "flag"}}, "GATES_INVALID", id="path_outside_workspace"),
    pytest.param({"verify": {"checks": [{"kind": "command", "argv": []}], "onFail": "flag"}}, "GATES_INVALID", id="empty_command"),
    pytest.param({"verify": {"checks": [{"kind": "citations"}], "onFail": "flag", "maxRounds": 4}}, "GATES_INVALID", id="too_many_rounds"),
    pytest.param({"approve": True}, "GATES_INVALID", id="unknown_field"),
    pytest.param(["planApproval"], "GATES_INVALID", id="not_an_object"),
]


class TestSave:
    async def test_wp51_create_stores_the_gates_as_canonical_text_and_answers_them(self) -> None:
        graph = _graph()

        answer = await _create({"gates": GATES}, graph)

        document = graph.batch_upsert_nodes.await_args.args[0][0]
        assert document["gates"] == STORED
        jsonschema.validate({k: v for k, v in document.items() if k != "_key"}, agent_schema["rule"])
        assert answer["agent"]["gates"] == GATES

    @pytest.mark.parametrize("body", [{}, {"gates": {}}, {"gates": None}], ids=["absent", "empty", "null"])
    async def test_wp51_an_agent_without_gates_is_stored_as_before(self, body: dict[str, Any]) -> None:
        plain, graph = _graph(), _graph()
        _ = await _create({}, plain)

        answer = await _create(body, graph)

        def written(g: AsyncMock) -> dict[str, Any]:
            document = dict(g.batch_upsert_nodes.await_args.args[0][0])
            return {k: v for k, v in document.items() if k not in ("_key", "createdAtTimestamp", "updatedAtTimestamp")}

        assert written(graph) == written(plain)
        assert "gates" not in answer["agent"]

    @pytest.mark.parametrize(("gates", "code"), BAD)
    async def test_wp51_a_refused_save_answers_400_with_the_reason_and_writes_nothing(self, gates: object, code: str) -> None:
        created, updated = _graph(), _graph({"gates": GATES})

        with pytest.raises(HTTPException) as refused_create:
            _ = await _create({"gates": gates}, created)
        with pytest.raises(HTTPException) as refused_update:
            _ = await _update({"gates": gates}, updated)

        for refused in (refused_create, refused_update):
            assert refused.value.status_code == 400
            assert f"[{code}]" in str(refused.value.detail)
        created.batch_upsert_nodes.assert_not_awaited()
        updated.update_agent.assert_not_awaited()

    async def test_wp51_an_update_that_omits_the_field_keeps_them_and_an_empty_one_clears_them(self) -> None:
        assert "gates" not in await _update({"name": "B"}, _graph({"gates": GATES}))
        assert (await _update({"gates": {}}, _graph({"gates": GATES})))["gates"] is None
        assert (await _update({"gates": {"planApproval": "required"}}, _graph()))["gates"] == {"planApproval": "required"}


@pytest.fixture
def neo4j() -> Neo4jProvider:
    provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    provider.client = AsyncMock()
    provider.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
    return provider


@pytest.fixture
def arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.http_client = AsyncMock()
    provider.check_agent_permission = AsyncMock(return_value={"can_edit": True})  # type: ignore[method-assign]
    return provider


class TestBothGraphBackends:
    async def test_wp51_both_providers_write_the_gates_as_text_and_null_to_clear(
        self, neo4j: Neo4jProvider, arango: ArangoHTTPProvider
    ) -> None:
        async def neo(updates: dict[str, Any]) -> dict[str, Any]:
            neo4j.client.execute_query = AsyncMock(return_value=[{"n": {"id": "a1"}}])
            assert await neo4j.update_agent("a1", updates, "u1", "o1") is True
            return neo4j.client.execute_query.await_args.kwargs["parameters"]["updates"]

        async def aran(updates: dict[str, Any]) -> dict[str, Any]:
            arango.http_client.update_document = AsyncMock(return_value={"_key": "a1"})
            assert await arango.update_agent("a1", updates, "u1", "o1") is True
            return arango.http_client.update_document.await_args.args[2]

        for written in (await neo({"gates": GATES}), await aran({"gates": GATES})):
            assert written["gates"] == STORED
        for written in (await neo({"gates": None}), await aran({"gates": None})):
            assert written["gates"] is None

    async def test_wp51_arango_get_agent_reads_the_gates_back(self, arango: ArangoHTTPProvider) -> None:
        arango.http_client.execute_aql = AsyncMock(return_value=[{"_key": "a1", "name": "A", "gates": STORED}])

        agent = await arango.get_agent("a1", "o1")

        assert agent is not None and agent["gates"] == GATES


def _definition(agent: dict[str, Any]) -> dict[str, Any]:
    definition = build_run_definition(
        {"updatedAtTimestamp": 7, **agent}, agent_key="agent-1", actions_enabled=True, mcp_enabled=True, web_search_provider=None
    )
    return definition.model_dump(by_alias=True, mode="json")


class TestRunDefinition:
    def test_wp51_ga03_ga04_the_run_definition_carries_the_agents_gates(self) -> None:
        assert _definition({"gates": GATES})["gates"] == GATES

    @pytest.mark.parametrize("stored", [None, {}], ids=["null", "empty"])
    def test_wp51_an_agent_without_gates_has_the_definition_it_had(self, stored: object) -> None:
        assert _definition({"gates": stored}) == _definition({})
        assert "gates" not in _definition({})

    @pytest.mark.parametrize("stored", ["{not json", ["planApproval"], {"dryRun": True}], ids=["unparsed", "list", "dry_run"])
    def test_wp51_stored_gates_that_do_not_read_are_reported_for_node_to_refuse_never_dropped(self, stored: object) -> None:
        assert _definition({"gates": stored})["gates"] == {"invalid": True}
        assert stored_agent_gates(stored) == {"invalid": True}

    async def test_wp51_a_rollback_restores_the_versions_gates_and_clears_ones_the_draft_added(self) -> None:
        from app.api.routes.agent import (
            _draft_body,  # pyright: ignore[reportPrivateUsage]
        )

        services = {"config_service": _config(), "logger": MagicMock()}
        restored = await _draft_body(services, {"name": "A", "gates": GATES}, {"name": "A"})
        cleared = await _draft_body(services, {"name": "A"}, {"name": "A", "gates": GATES})
        untouched = await _draft_body(services, {"name": "A"}, {"name": "A"})

        assert restored["gates"] == GATES
        assert cleared["gates"] == {}
        assert "gates" not in untouched

    def test_wp51_the_stored_shape_is_the_runspecs_without_dry_run(self) -> None:
        assert parse_agent_gates({"verify": {"checks": [{"kind": "citations"}], "onFail": "flag"}}) == {
            "verify": {"checks": [{"kind": "citations"}], "onFail": "flag"}
        }


@pytest.fixture
def _agent_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    """The routes' agent versions (OWD-14) beside this file's mocked graph."""
    from tests.support.agent_versions import versions_beside

    versions_beside(monkeypatch, "app.api.routes.agent")


async def _legacy_chat(agent: dict[str, Any]) -> bool:
    """Run today's loop for ``agent``; whether the loop ran."""
    import logging
    from contextlib import ExitStack

    from app.api.routes.agent import chat_stream

    ran: list[bool] = []

    async def _loop(*_args: object, **_kwargs: object):  # noqa: ANN202
        ran.append(True)
        return
        yield  # pragma: no cover - marks this an async generator

    graph = AsyncMock()
    graph.get_agent = AsyncMock(return_value={
        "_key": "agent-1", "name": "agent", "knowledge": [], "toolsets": [], "models": [],
        "createdBy": "caller-key", "isServiceAccount": False, **agent,
    })
    graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "role": "viewer"})
    services = {"graph_provider": graph, "retrieval_service": MagicMock(), "reranker_service": MagicMock(),
                "config_service": AsyncMock(), "logger": logging.getLogger("wp51-legacy")}
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps({"query": "q"}).encode())
    request.headers = {}
    route_patches = [
        ("get_services", {"new_callable": AsyncMock, "return_value": services}),
        ("_get_user_context", {"return_value": {"userId": "caller", "orgId": ORG}}),
        ("_get_user_document", {"new_callable": AsyncMock, "return_value": {"email": "u@x.com", "_key": "caller-key"}}),
        ("_get_org_info", {"new_callable": AsyncMock, "return_value": {"orgId": ORG, "accountType": "enterprise"}}),
        ("get_llm_for_chat", {"new_callable": AsyncMock, "return_value": (MagicMock(), {}, {})}),
        ("load_entity_vector_store", {"new_callable": AsyncMock, "return_value": None}),
        ("resolve_web_search", {"new_callable": AsyncMock, "return_value": None}),
        ("run_agent_loop_stream", {"new": _loop}),
    ]
    with ExitStack() as stack:
        for target, kwargs in route_patches:
            stack.enter_context(patch(f"app.api.routes.agent.{target}", **kwargs))
        for target in ("app.services.featureflag.platform_settings.is_actions_enabled",
                       "app.services.featureflag.platform_settings.is_user_context_enabled",
                       "app.agents.mcp.service.is_mcp_enabled"):
            stack.enter_context(patch(target, new_callable=AsyncMock, return_value=False))
        response = await chat_stream(request, "agent-1")
        [chunk async for chunk in response.body_iterator]
    return bool(ran)


@pytest.mark.usefixtures("_agent_versions")
class TestTodaysLoop:
    async def test_wp51_a_gated_agent_never_runs_on_todays_loop_which_has_no_gates(self) -> None:
        with pytest.raises(HTTPException) as refused:
            _ = await _legacy_chat({"gates": GATES})

        assert refused.value.status_code == 409
        # WP-64: the same plain words as Node's `GATES_NEED_RUNTIME`, from the error catalogue.
        from app.harness.contracts.generated.pipeshub.errors import ERROR_CODES

        assert str(refused.value.detail) == ERROR_CODES["GATES_NEED_RUNTIME"].next_action.text

    async def test_wp51_an_agent_without_gates_runs_there_as_before(self) -> None:
        assert await _legacy_chat({}) is True
