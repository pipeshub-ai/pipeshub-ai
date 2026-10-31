"""Reading and setting a person's own tool approval rules. Company and per-agent rules are not
part of this edition, so neither has a route."""
from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.agents.agent_loop.tool_approvals import args_hash
from app.agents.constants.mcp_server_constants import (
    get_mcp_tool_approval_claim_path,
    get_mcp_tool_approval_path,
)
from app.api.routes import mcp_servers
from app.api.routes.mcp_servers import (
    ToolRulesBody,
    get_my_tool_rules,
    update_my_tool_rules,
)
from app.core.org_context import OrgContext, bind
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


class TestValidateEdit:
    _ID = "ap-1"
    _SCHEMA = {"type": "object", "properties": {"q": {"type": "string"}}}

    def _store(self, **over: object) -> FakeConfigService:
        record = {
            "approvalId": self._ID, "orgId": "org-1", "userId": "user-1", "conversationId": "c", "source": "mcp",
            "instanceId": "inst-1", "serverName": "Jira", "toolName": "t", "arguments": {"q": "a"},
            "toolPath": "/tools/jira/t", "kind": "write", "inputSchema": self._SCHEMA,
            "createdAt": time.time(), "expiresAt": time.time() + 60, **over,
        }
        return FakeConfigService({get_mcp_tool_approval_path(self._ID): record})

    async def _validate(self, store: FakeConfigService, arguments: dict[str, Any], *, user_id: str = "user-1", approval_id: str = "ap-1", extra_user: dict[str, Any] | None = None) -> Any:  # noqa: ANN401
        request = route_request(store, user_id=user_id)
        request.state.user.update(extra_user or {})
        body = mcp_servers.ToolApprovalValidateBody(args_hash=args_hash({"q": "a"}), arguments=arguments)
        with bind(OrgContext("org-1", user_id, "user", "jwt", "req-test")):
            return await mcp_servers.validate_tool_approval_edit(request, body, approval_id)

    async def test_valid_edit_has_no_errors_and_is_not_claimed(self) -> None:
        store = self._store()
        assert await self._validate(store, {"q": "b"}) == {"errors": []}
        assert store.writes == []

    async def test_invalid_edit_lists_errors(self) -> None:
        out = await self._validate(self._store(), {"q": 5})
        assert out["errors"][0].path == "/q"

    async def test_unknown_and_foreign_approvals_are_not_found(self) -> None:
        for store, kw in ((self._store(), {"approval_id": "nope"}), (self._store(), {"user_id": "user-2"})):
            with pytest.raises(HTTPException) as caught:
                await self._validate(store, {"q": "b"}, **kw)
            assert caught.value.status_code == 404

    async def test_expired_or_answered_is_gone(self) -> None:
        answered = self._store()
        answered.data[get_mcp_tool_approval_claim_path(self._ID)] = {"claimedAt": 1}
        for store in (self._store(expiresAt=1.0), answered):
            with pytest.raises(HTTPException) as caught:
                await self._validate(store, {"q": "b"})
            assert caught.value.status_code == 410

    async def test_an_oauth_or_pat_caller_is_refused(self) -> None:
        with pytest.raises(HTTPException) as caught:
            await self._validate(self._store(), {"q": "b"}, extra_user={"isOAuth": True})
        assert caught.value.status_code == 403

    async def test_project_chat_call_with_no_rule_validates_unchanged_arguments(self) -> None:
        store = self._store(kind="destructive", enabledOnProject=True)
        assert await self._validate(store, {"q": "a"}) == {"errors": []}
        blocked = self._store(kind="destructive")
        assert (await self._validate(blocked, {"q": "a"}))["errors"][0].message == "A rule blocks this action with these details."
