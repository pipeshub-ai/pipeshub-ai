"""An agent edit carries whether its caller administers the org, for a build where only an admin
changes a service agent's tools. The role is the live one Node reports, asked only when it matters."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import edition_config
from app.api.middlewares.caller_role import CallerRole, CallerRoleStatus
from app.api.routes import agent as routes
from app.modules.agents.service.models import AgentPatch

TOOL_EDIT = AgentPatch.model_validate({"toolsets": [{"name": "jira", "tools": [{"name": "create_issue"}]}]})


@pytest.fixture
def admin_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "agent_credentials_admin_only", True, raising=False)


def _role(answer: CallerRole | Exception) -> AsyncMock:
    return AsyncMock(side_effect=answer) if isinstance(answer, Exception) else AsyncMock(return_value=answer)


@pytest.mark.usefixtures("admin_only")
class TestTheRoleOfAToolEdit:
    @pytest.mark.parametrize(("answer", "admin"), [
        (CallerRole(CallerRoleStatus.VALID, "admin"), True),
        (CallerRole(CallerRoleStatus.VALID, "member"), False),
        (CallerRole(CallerRoleStatus.UNKNOWN), False),
        (CallerRole(CallerRoleStatus.REJECTED), False),
        (RuntimeError("node is down"), False),
    ])
    async def test_is_the_live_role_and_anything_unconfirmed_is_not_an_admin(
        self, monkeypatch: pytest.MonkeyPatch, answer: Any, admin: bool,  # noqa: ANN401
    ) -> None:
        monkeypatch.setattr(routes, "fetch_caller_role", _role(answer))
        assert await routes._administers_for_a_tool_edit(MagicMock(), {"config_service": MagicMock()}, TOOL_EDIT) is admin

    async def test_an_edit_that_names_no_tools_asks_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        lookup = _role(CallerRole(CallerRoleStatus.VALID, "admin"))
        monkeypatch.setattr(routes, "fetch_caller_role", lookup)
        edit = AgentPatch.model_validate({"name": "Renamed"})
        assert await routes._administers_for_a_tool_edit(MagicMock(), {}, edit) is False
        lookup.assert_not_awaited()

    async def test_the_edit_reaches_the_service_with_the_callers_role(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(routes, "fetch_caller_role", _role(CallerRole(CallerRoleStatus.VALID, "admin")))
        service = MagicMock()
        service.update = AsyncMock(return_value={"agentKey": "a1"})
        request = MagicMock()
        request.body = AsyncMock(return_value=b'{"toolsets": [{"name": "jira", "tools": [{"name": "create_issue"}]}]}')
        services = {"graph_provider": AsyncMock(), "logger": MagicMock(), "config_service": MagicMock()}
        with patch.object(routes, "get_services", new=AsyncMock(return_value=services)), \
             patch.object(routes, "_get_user_context", return_value={"userId": "u1", "orgId": "o1"}), \
             patch.object(routes, "_get_user_document", new=AsyncMock(return_value={"_key": "k1"})), \
             patch.object(routes, "_agent_service", return_value=service):
            response = await routes.update_agent(request, "a1")
        assert response.status_code == 200
        actor = service.update.await_args.args[0]
        assert actor.is_admin is True and actor.user_key == "k1"


async def test_a_build_that_lets_editors_choose_the_tools_asks_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(edition_config, "agent_credentials_admin_only", False, raising=False)
    lookup = _role(CallerRole(CallerRoleStatus.VALID, "admin"))
    monkeypatch.setattr(routes, "fetch_caller_role", lookup)
    assert await routes._administers_for_a_tool_edit(MagicMock(), {}, TOOL_EDIT) is False
    lookup.assert_not_awaited()
