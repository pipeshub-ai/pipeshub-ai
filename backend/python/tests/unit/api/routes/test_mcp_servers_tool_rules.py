"""Reading and setting a person's own tool approval rules. Company and per-agent rules are not
part of this edition, so neither has a route."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    ToolRulesBody,
    get_my_tool_rules,
    update_my_tool_rules,
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


class TestValidation:
    @pytest.mark.parametrize("tools", [{"": "allow"}, {"x" * 201: "allow"}, {f"t{i}": "allow" for i in range(501)}, {"t": "maybe"}])
    def test_bad_rules_are_refused(self, tools: dict[str, Any]) -> None:
        with pytest.raises(ValidationError):
            ToolRulesBody(tools=tools)


class TestMyRules:
    async def test_a_person_sets_their_own_on_any_server_they_can_see(self) -> None:
        store = _store()
        for instance_id in ("inst-1", "inst-p"):
            await update_my_tool_rules(route_request(store, user_id="user-1"), instance_id, ToolRulesBody(tools={"create_issue": "allow"}))
            assert await get_my_tool_rules(route_request(store, user_id="user-1"), instance_id) == {"tools": {"create_issue": "allow"}}
        assert "/services/mcp/user-tool-rules/user-1/inst-1" in store.data

    async def test_theirs_are_nobody_elses(self) -> None:
        store = _store()
        await update_my_tool_rules(route_request(store, user_id="user-1"), "inst-1", ToolRulesBody(tools={"create_issue": "allow"}))
        assert await get_my_tool_rules(route_request(store, user_id="user-2"), "inst-1") == {"tools": {}}

    async def test_a_server_they_cant_see_is_not_found(self) -> None:
        with pytest.raises(HTTPException) as caught:
            await get_my_tool_rules(route_request(_store(), user_id="user-2"), "inst-p")
        assert caught.value.status_code == 404


class TestNotInThisEdition:
    @pytest.mark.parametrize("path", [
        "/instances/{instance_id}/tool-policy",
        "/agents/{agent_key}/instances/{instance_id}/tool-rules",
    ])
    def test_company_and_agent_rules_have_no_route(self, path: str) -> None:
        paths = {getattr(route, "path", "") for route in mcp_servers.router.routes}
        assert not any(p.endswith(path) for p in paths)

    def test_nothing_else_reads_them(self) -> None:
        from app.agents.agent_loop import tool_approvals

        for name in ("CompanyPolicy", "CompanyToolRule", "load_company_policy", "rules_owner", "can_always_allow"):
            assert not hasattr(tool_approvals, name)
