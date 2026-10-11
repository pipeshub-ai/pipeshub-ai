"""Reading and setting tool approval rules: company rules (admins, org servers), a person's own
rules, and an agent's rules (its editors)."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.mcp.lifecycle import delete_instance_data
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    ToolPolicyBody,
    ToolRulesBody,
    get_agent_tool_rules,
    get_my_tool_rules,
    get_tool_policy,
    update_agent_tool_rules,
    update_my_tool_rules,
    update_tool_policy,
)
from tests.unit.api.routes.mcp_route_fakes import FakeConfigService, route_request

_ORG = "/services/mcp/instances/inst-1"
_PERSONAL = "/services/mcp/user-instances/org-1/user-1/inst-p"


def _store() -> FakeConfigService:
    base = {"name": "Jira", "typeId": None, "transport": "streamable_http", "url": "https://mcp.example.com/mcp",
            "authMode": "none", "isCustom": True, "createdAt": 1, "updatedAt": 1, "orgId": "org-1"}
    return FakeConfigService({
        _ORG: {**base, "_id": "inst-1", "createdBy": "admin-1"},
        _PERSONAL: {**base, "_id": "inst-p", "createdBy": "user-1", "scope": "personal"},
    })


def _admin(is_admin: bool) -> Any:  # noqa: ANN401
    return patch.object(mcp_servers, "_check_user_is_admin", new=AsyncMock(return_value=is_admin))


class TestCompanyRules:
    async def test_an_admin_sets_and_reads_them(self) -> None:
        store = _store()
        body = ToolPolicyBody(tools={
            "delete_issue": {"rule": "block"}, "create_issue": {"rule": "ask", "unattended": True}, "search": {},
        })
        with _admin(True):
            saved = await update_tool_policy(route_request(store, user_id="admin-1"), "inst-1", body)
            read = await get_tool_policy(route_request(store, user_id="admin-1"), "inst-1")

        # A tool with no rule and nothing allowed unattended isn't kept.
        assert saved == read == {"tools": {
            "delete_issue": {"rule": "block", "unattended": False}, "create_issue": {"rule": "ask", "unattended": True},
        }}

    async def test_someone_else_cant_read_or_set_them(self) -> None:
        with _admin(False), pytest.raises(HTTPException) as caught:
            await update_tool_policy(route_request(_store(), user_id="user-1"), "inst-1", ToolPolicyBody(tools={}))
        assert caught.value.status_code == 403
        with _admin(False), pytest.raises(HTTPException) as caught:
            await get_tool_policy(route_request(_store(), user_id="user-1"), "inst-1")
        assert caught.value.status_code == 403

    async def test_a_personal_server_has_no_company_rules(self) -> None:
        with _admin(True), pytest.raises(HTTPException) as caught:
            await get_tool_policy(route_request(_store(), user_id="user-1"), "inst-p")
        assert caught.value.status_code == 400

    async def test_an_unknown_server_is_not_found(self) -> None:
        with _admin(True), pytest.raises(HTTPException) as caught:
            await get_tool_policy(route_request(_store(), user_id="admin-1"), "nope")
        assert caught.value.status_code == 404

    async def test_deleting_the_server_drops_them(self) -> None:
        store = _store()
        with _admin(True):
            await update_tool_policy(route_request(store, user_id="admin-1"), "inst-1", ToolPolicyBody(tools={"x": {"rule": "block"}}))
        await delete_instance_data(store, store.data[_ORG])  # type: ignore[arg-type]
        assert "/services/mcp/tool-policies/inst-1" not in store.data


class TestValidation:
    @pytest.mark.parametrize("tools", [{"": "allow"}, {"x" * 201: "allow"}, {f"t{i}": "allow" for i in range(501)}, {"t": "maybe"}])
    def test_bad_rules_are_refused(self, tools: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ToolRulesBody(tools=tools)

    def test_a_bad_company_rule_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ToolPolicyBody(tools={"t": {"rule": "allow"}})


class TestMyRules:
    async def test_a_person_sets_their_own_on_any_server_they_can_see(self) -> None:
        store = _store()
        for instance_id in ("inst-1", "inst-p"):
            await update_my_tool_rules(route_request(store, user_id="user-1"), instance_id, ToolRulesBody(tools={"create_issue": "allow"}))
            assert await get_my_tool_rules(route_request(store, user_id="user-1"), instance_id) == {"tools": {"create_issue": "allow"}}

    async def test_theirs_are_nobody_elses(self) -> None:
        store = _store()
        await update_my_tool_rules(route_request(store, user_id="user-1"), "inst-1", ToolRulesBody(tools={"create_issue": "allow"}))
        assert await get_my_tool_rules(route_request(store, user_id="user-2"), "inst-1") == {"tools": {}}

    async def test_a_server_they_cant_see_is_not_found(self) -> None:
        with pytest.raises(HTTPException) as caught:
            await get_my_tool_rules(route_request(_store(), user_id="user-2"), "inst-p")
        assert caught.value.status_code == 404


def _agent(*, can_edit: bool) -> Any:  # noqa: ANN401
    return patch(
        "app.api.routes.toolsets._resolve_agent_with_permission",
        new=AsyncMock(return_value={"_key": "agent-1", "can_edit": can_edit}),
    )


class TestAgentRules:
    async def test_an_editor_sets_them_and_anyone_with_access_reads_them(self) -> None:
        store = _store()
        with _agent(can_edit=True):
            await update_agent_tool_rules(route_request(store, user_id="user-1"), "agent-1", "inst-1", ToolRulesBody(tools={"delete_issue": "block"}))
        with _agent(can_edit=False):
            read = await get_agent_tool_rules(route_request(store, user_id="user-2"), "agent-1", "inst-1")
        assert read == {"tools": {"delete_issue": "block"}}
        assert "/services/mcp/agent-tool-rules/agent-1/inst-1" in store.data

    async def test_a_viewer_cant_change_them(self) -> None:
        with _agent(can_edit=False), pytest.raises(HTTPException) as caught:
            await update_agent_tool_rules(route_request(_store(), user_id="user-2"), "agent-1", "inst-1", ToolRulesBody(tools={}))
        assert caught.value.status_code == 403

    async def test_no_access_to_the_agent_is_not_found(self) -> None:
        refused = AsyncMock(side_effect=HTTPException(status_code=404, detail="Agent 'agent-1' not found."))
        with patch("app.api.routes.toolsets._resolve_agent_with_permission", new=refused), pytest.raises(HTTPException) as caught:
            await get_agent_tool_rules(route_request(_store(), user_id="user-3"), "agent-1", "inst-1")
        assert caught.value.status_code == 404
