"""AG1 WV / UI-2: the agents routes store an engine agent's settings, checked under the org ceiling,
through the real ``ConfiguredEngineCeilings`` over a fake ``ConfigurationService``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (edition seams load before any router)
from tests.support.bound_org import bind_test_org

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """``authMiddleware`` binds the caller's org; these tests call the route functions directly, as that caller."""
    yield from bind_test_org(ORG)

MODELS = [{"modelKey": "mk1", "modelName": "mn1", "isReasoning": True}]


def _config(ceiling: object = None, *, down: bool = False) -> AsyncMock:
    config = AsyncMock()
    if down:
        config.get_config = AsyncMock(side_effect=RuntimeError("kv down"))
    else:
        document = None if ceiling is None else {"orgs": {ORG: {"ceiling": ceiling}}}
        config.get_config = AsyncMock(return_value=document)
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


def _request(body: dict[str, Any], role: str = "admin") -> MagicMock:
    request = MagicMock()
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    request.state.user = {"userId": "u1", "orgId": ORG, "role": role}
    return request


def _patches(graph: AsyncMock, config: AsyncMock):  # noqa: ANN202
    services = {"graph_provider": graph, "config_service": config, "logger": MagicMock()}
    return (
        patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services),
        patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": ORG}),
        patch("app.api.routes.agent._get_user_document", new_callable=AsyncMock, return_value={"_key": "k1"}),
    )


async def _create(
    body: dict[str, Any], graph: AsyncMock, config: AsyncMock, role: str = "admin"
) -> dict[str, Any]:
    from app.api.routes.agent import create_agent

    services, context, user = _patches(graph, config)
    with services, context, user:
        response = await create_agent(_request({"name": "A", "models": MODELS, **body}, role))
    return json.loads(response.body)


async def _update(body: dict[str, Any], graph: AsyncMock, config: AsyncMock, role: str = "admin") -> None:
    from app.api.routes.agent import update_agent

    services, context, user = _patches(graph, config)
    with services, context, user:
        await update_agent(_request(body, role), "agent-1")


def _written(graph: AsyncMock) -> dict[str, Any]:
    nodes = graph.batch_upsert_nodes.await_args.args[0]
    return nodes[0]


class TestCreate:
    @pytest.mark.asyncio
    async def test_wv1_an_engine_agent_is_stored_with_its_canonical_narrowing_and_cloud_pin(self) -> None:
        graph = _graph()
        answer = await _create(
            {"engineProfile": {"accountKinds": ["org_cloud", "org_api_key"]}, "modelAccountId": "cloud/acc-1"},
            graph,
            _config(),
        )
        document = _written(graph)
        assert document["engineProfile"] == '{"accountKinds":["org_api_key","org_cloud"]}'
        assert document["modelAccountId"] == "cloud/acc-1"
        assert answer["agent"]["engineProfile"] == {"accountKinds": ["org_api_key", "org_cloud"]}

    @pytest.mark.asyncio
    async def test_wv7_a_loop_agent_reads_no_ceiling_and_its_document_is_unchanged(self) -> None:
        graph, config = _graph(), _config()
        await _create({}, graph, config)
        document = _written(graph)
        assert "engineProfile" not in document and "modelAccountId" not in document
        config.get_config.assert_not_awaited()
        graph = _graph()
        await _create({"engineProfile": None}, graph, config)
        assert "engineProfile" not in _written(graph)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("body", "ceiling", "status", "code"),
        [
            ({"engineProfile": {"bogus": 1}}, None, 400, "ENGINE_PROFILE_INVALID"),
            ({"engineProfile": {"accountKinds": ["user_api_key"]}}, {"accountKinds": ["org_api_key"]}, 400, "ENGINE_PROFILE_WIDENING"),
            ({"engineProfile": {}, "modelAccountId": "users/u1/openAI"}, None, 400, "MODEL_ACCOUNT_INVALID"),
        ],
        ids=["invalid", "widening", "person-key"],
    )
    async def test_wv2_wv3_wv5_a_refused_create_writes_nothing(
        self, body: dict[str, Any], ceiling: object, status: int, code: str
    ) -> None:
        graph = _graph()
        with pytest.raises(HTTPException) as refused:
            await _create(body, graph, _config(ceiling))
        assert refused.value.status_code == status
        assert f"[{code}]" in str(refused.value.detail)
        graph.begin_transaction.assert_not_awaited()
        graph.batch_upsert_nodes.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wv4_an_unreadable_ceiling_refuses_503_and_writes_nothing(self) -> None:
        graph = _graph()
        with pytest.raises(HTTPException) as refused:
            await _create({"engineProfile": {}}, graph, _config(down=True))
        assert refused.value.status_code == 503
        graph.batch_upsert_nodes.assert_not_awaited()


class TestUpdate:
    @pytest.mark.asyncio
    async def test_ui2_the_editors_save_body_stores_the_narrowing_it_carries(self) -> None:
        """The agent builder's PUT (U2 AG-4): every field it sends, plus the engine profile with the chosen kind."""
        graph = _graph({"engineProfile": {"mode": "plan"}})
        editor_body = {
            "name": "A",
            "description": "d",
            "startMessage": "s",
            "systemPrompt": "p",
            "instructions": "",
            "models": MODELS,
            "tags": [],
            "shareWithOrg": False,
            "webSearch": None,
            "defaultReasoningEffort": None,
            "sendUserContext": True,
            "engineProfile": {"mode": "plan", "accountKinds": ["user_api_key"]},
        }
        await _update(editor_body, graph, _config())
        sent = graph.update_agent.await_args.args[1]
        assert sent["engineProfile"] == {"accountKinds": ["user_api_key"], "mode": "plan"}
        assert sent["modelAccountId"] is None

    @pytest.mark.asyncio
    async def test_wv3_a_widening_update_changes_nothing(self) -> None:
        graph = _graph({"engineProfile": {}})
        with pytest.raises(HTTPException) as refused:
            await _update(
                {"engineProfile": {"accountKinds": ["user_api_key"]}, "shareWithOrg": True},
                graph,
                _config({"accountKinds": ["org_api_key"]}),
            )
        assert "[ENGINE_PROFILE_WIDENING]" in str(refused.value.detail)
        graph.update_agent.assert_not_awaited()
        graph.batch_create_edges.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wv6_a_narrowing_without_org_cloud_clears_the_stored_pin(self) -> None:
        graph = _graph({"engineProfile": {"accountKinds": ["org_cloud"]}, "modelAccountId": "cloud/acc-1"})
        await _update({"engineProfile": {"accountKinds": ["user_api_key"]}}, graph, _config())
        sent = graph.update_agent.await_args.args[1]
        assert (sent["engineProfile"], sent["modelAccountId"]) == ({"accountKinds": ["user_api_key"]}, None)

    @pytest.mark.asyncio
    async def test_wv7_an_update_without_the_fields_reads_no_ceiling_and_writes_neither(self) -> None:
        graph, config = _graph({"engineProfile": {"accountKinds": ["org_cloud"]}}), _config()
        await _update({"name": "B"}, graph, config)
        sent = graph.update_agent.await_args.args[1]
        assert "engineProfile" not in sent and "modelAccountId" not in sent
        config.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_wv8_an_account_alone_is_checked_against_the_stored_narrowing(self) -> None:
        graph = _graph({"engineProfile": {"accountKinds": ["org_api_key"]}})
        with pytest.raises(HTTPException) as refused:
            await _update({"modelAccountId": "cloud/acc-1"}, graph, _config())
        assert "[MODEL_ACCOUNT_INVALID]" in str(refused.value.detail)
        graph.update_agent.assert_not_awaited()


class TestWhoMakesAnEngineAgent:
    """P2.11e AE-1..AE-4 through the routes: the role is the one the auth middleware resolved."""

    @pytest.mark.asyncio
    async def test_ae1_a_member_cannot_create_an_engine_agent(self) -> None:
        graph, config = _graph(), _config()
        with pytest.raises(HTTPException) as refused:
            await _create({"engineProfile": {}}, graph, config, role="member")
        assert refused.value.status_code == 403
        assert "[ENGINE_AGENT_ADMIN_REQUIRED]" in str(refused.value.detail)
        graph.batch_upsert_nodes.assert_not_awaited()
        config.get_config.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ae2_a_member_editor_cannot_turn_a_loop_agent_into_one(self) -> None:
        graph = _graph()
        with pytest.raises(HTTPException) as refused:
            await _update({"engineProfile": {"mode": "plan"}}, graph, _config(), role="member")
        assert refused.value.status_code == 403
        graph.update_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ae3_ae4_an_admin_converts_it_and_an_editor_then_narrows_it(self) -> None:
        graph = _graph()
        await _update({"engineProfile": {"mode": "plan"}}, graph, _config(), role="admin")
        assert graph.update_agent.await_args is not None
        graph = _graph({"engineProfile": {"mode": "plan"}})
        await _update({"engineProfile": {"mode": "plan", "accountKinds": ["org_api_key"]}}, graph, _config(), role="member")
        graph.update_agent.assert_awaited_once()


class TestCodingEnginePicker:
    """ENGPICK: what the agent builder's "Coding engine" select saves, through the same routes."""

    @pytest.mark.asyncio
    async def test_ep1_picking_an_engine_the_org_allows_stores_it(self) -> None:
        graph = _graph()
        await _update({"engineProfile": {"engines": ["codex"]}}, graph, _config({"engines": ["codex", "opencode"]}))
        assert graph.update_agent.await_args.args[1]["engineProfile"] == {"engines": ["codex"]}

    @pytest.mark.asyncio
    async def test_ep2_an_engine_the_org_ceiling_leaves_out_is_refused_and_nothing_is_written(self) -> None:
        graph = _graph()
        with pytest.raises(HTTPException) as refused:
            await _update({"engineProfile": {"engines": ["claude_code"]}}, graph, _config({"engines": ["opencode"]}))
        assert refused.value.status_code == 400
        assert "[ENGINE_PROFILE_WIDENING]" in str(refused.value.detail)
        graph.update_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ep3_a_caller_who_may_not_edit_the_agent_is_refused_before_any_write(self) -> None:
        graph = _graph({"engineProfile": {"engines": ["codex"]}})
        graph.check_agent_permission = AsyncMock(return_value={"can_edit": False, "can_share": False, "role": "READER"})
        with pytest.raises(HTTPException) as refused:
            await _update({"engineProfile": {"engines": ["opencode"]}}, graph, _config())
        assert refused.value.status_code == 403
        graph.update_agent.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ep4_none_removes_the_engine(self) -> None:
        graph = _graph({"engineProfile": {"engines": ["codex"]}, "modelAccountId": "cloud/acc-1"})
        await _update({"engineProfile": None}, graph, _config())
        sent = graph.update_agent.await_args.args[1]
        assert (sent["engineProfile"], sent["modelAccountId"]) == (None, None)
