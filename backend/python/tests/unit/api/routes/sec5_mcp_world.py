"""SEC5 world: one configuration store holding three orgs' MCP instances, and two edition seams.

Org A owns the instances under test, org B is an unrelated org with its own instance, and org C is a child
of org A. Under the community seam org C is just another org; under ``InheritingEdition`` it inherits org A's
instances the way an edition with an org hierarchy shares them: visible for use, refused for edit and delete.
``InheritingEdition`` models such an edition from its seam alone: the caller's org reaches it the way an
org-scoped request context does, through a context variable the app's middleware sets.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException

from app import edition_config
from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_instance_path,
    get_mcp_oauth_client_config_path,
)
from app.api.routes import mcp_resolvers
from app.api.routes import mcp_servers as server_routes
from app.api.routes import toolsets as toolset_routes
from app.config.constants.http_status_code import HttpStatusCode
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.mcp_hub.support import http_instance

if TYPE_CHECKING:
    import pytest

ORG_A, ORG_B, ORG_C = "org-a", "org-b", "org-c"
PARENTS = {ORG_C: ORG_A}


@dataclass(frozen=True)
class Caller:
    user_id: str
    org_id: str
    admin: bool

    @property
    def agent_key(self) -> str:
        return f"agent-{self.org_id}"

    def user(self) -> dict[str, str]:
        return {"userId": self.user_id, "orgId": self.org_id}


CALLERS = {
    "A_ADMIN": Caller("admin-a", ORG_A, admin=True),
    "A_USER": Caller("user-a", ORG_A, admin=False),
    "B_ADMIN": Caller("admin-b", ORG_B, admin=True),
    "B_USER": Caller("user-b", ORG_B, admin=False),
    "C_ADMIN": Caller("admin-c", ORG_C, admin=True),
    "C_USER": Caller("user-c", ORG_C, admin=False),
}

A_HTTP, A_OAUTH, A_SHARED, A_STDIO, B_HTTP = "a-http", "a-oauth", "a-shared", "a-stdio", "b-http"
A_SECRET_NAME = "Org A private server"


def _a(instance_id: str, **changes: Any) -> dict[str, Any]:  # noqa: ANN401
    return http_instance(_id=instance_id, orgId=ORG_A, createdBy="admin-a", name=A_SECRET_NAME, **changes)


def seed() -> DictConfig:
    """Org A's four instances (with credentials for every user of org A and org C), and org B's own."""
    instances = {
        A_HTTP: _a(A_HTTP),
        A_OAUTH: _a(A_OAUTH, authMode="oauth", url=None, authorizationUrl="https://idp.example.test/authorize",
                    tokenUrl="https://idp.example.test/token"),
        A_SHARED: _a(A_SHARED, useAdminAuth=True),
        A_STDIO: _a(A_STDIO, transport="stdio", authMode="none", url=None, typeId=None, command="python3",
                    args=["/opt/echo.py"], requiredEnv=[], isCustom=True),
        B_HTTP: http_instance(_id=B_HTTP, orgId=ORG_B, createdBy="admin-b", name="Org B server"),
    }
    values: dict[str, Any] = {get_mcp_instance_path(i): record for i, record in instances.items()}
    owners = [c.user_id for c in CALLERS.values() if c.org_id in (ORG_A, ORG_C)]
    owners += [CALLERS[name].agent_key for name in ("A_ADMIN", "C_ADMIN")]
    for owner in owners:
        values[get_mcp_credentials_path(A_HTTP, owner)] = {"isAuthenticated": True, "credentials": {"apiToken": f"tok-{owner}"}}
    values[get_mcp_credentials_path(A_SHARED, "admin-a")] = {"isAuthenticated": True, "credentials": {"apiToken": "shared-a"}}
    values[get_mcp_credentials_path(B_HTTP, "user-b")] = {"isAuthenticated": True, "credentials": {"apiToken": "tok-b"}}
    values[get_mcp_oauth_client_config_path(A_OAUTH)] = {"clientId": "client-org-a-1234", "clientSecret": "secret-org-a-5678"}
    return DictConfig(values)


CURRENT_ORG: contextvars.ContextVar[str] = contextvars.ContextVar("sec5_current_org")


class InheritingEdition:
    """An edition seam where an org also sees, but may not change, the instances of its parent org."""

    @staticmethod
    async def visible(instance: dict[str, Any], org_id: str, _config: object) -> bool:
        owner = instance.get("orgId")
        return bool(org_id) and owner in (org_id, PARENTS.get(org_id))

    @staticmethod
    def forbid_mutation(instance: dict[str, Any]) -> None:
        if instance.get("orgId") != CURRENT_ORG.get():
            raise HTTPException(status_code=HttpStatusCode.FORBIDDEN.value, detail="Inherited MCP server instances are read-only.")

    @staticmethod
    async def instances(config: Any, org_id: str, user_id: str | None = None) -> list[dict[str, Any]]:  # noqa: ANN401
        # Main's store reads one org's instances; an inheriting edition adds the parent's.
        own = await mcp_resolvers.load_mcp_instances(config, org_id, user_id)
        parent = PARENTS.get(org_id)
        return own + (await mcp_resolvers.load_mcp_instances(config, parent) if parent else [])

    @staticmethod
    async def instance(
        instance_id: str, config: Any, org_id: str, user_id: str | None = None  # noqa: ANN401
    ) -> dict[str, Any] | None:
        found = await mcp_resolvers.get_mcp_instance(instance_id, config, org_id, user_id)
        parent = PARENTS.get(org_id)
        return found if found is not None or not parent else await mcp_resolvers.get_mcp_instance(instance_id, config, parent)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(edition_config, "mcp_instance_visible_to_org", self.visible)
        # An edition replaces the resolvers where they are bound: the seam module and the routes that imported them.
        monkeypatch.setattr(edition_config, "resolve_mcp_instances_with_inheritance", self.instances)
        monkeypatch.setattr(edition_config, "get_mcp_instance_resolved", self.instance)
        monkeypatch.setattr(server_routes, "resolve_mcp_instances_with_inheritance", self.instances)
        monkeypatch.setattr(server_routes, "_get_org_instance", self.instance)
        monkeypatch.setattr(server_routes, "forbid_inherited_mcp_mutation", self.forbid_mutation)


def install_roles(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin oracle answers from the caller's own identity, as Node's role lookup does."""

    async def is_admin_server(_user_id: str, request: Any, _config: object) -> bool:  # noqa: ANN401
        return _caller_of(request.state.user).admin

    async def agent_access(agent_key: str, request: Any) -> dict[str, Any]:  # noqa: ANN401
        if agent_key != _caller_of(request.state.user).agent_key:
            raise HTTPException(status_code=HttpStatusCode.NOT_FOUND.value, detail="Agent not found.")
        return {"_key": agent_key, "isServiceAccount": True}

    monkeypatch.setattr(server_routes, "_check_user_is_admin", is_admin_server)
    monkeypatch.setattr(server_routes, "_require_mcp_agent_edit_access", agent_access)
    monkeypatch.setattr(toolset_routes, "_resolve_agent_with_permission", agent_access)


def _caller_of(user: dict[str, str]) -> Caller:
    return next(c for c in CALLERS.values() if c.user_id == user["userId"] and c.org_id == user["orgId"])
