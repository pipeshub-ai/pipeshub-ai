"""The control plane's Python drivers (03-interfaces §3.1): what Node asks of the stores only Python reads.

``GET /api/v1/control-plane/drivers/legacy/capabilities`` answers one org's legacy capability facts for
CPD-7's migration and dual-write sync. Only Node's ``capabilities:read`` service token passes, and the org is
the token's, never a parameter. MCP instances come through SEC5's org-scoped lookup only.
"""

from __future__ import annotations

from collections.abc import (
    Mapping,  # noqa: TC003 - FastAPI resolves route annotations at runtime
)
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.agents.legacy_capabilities import LegacyCapabilityReader
from app.api.middlewares.auth import (
    get_config_service,
    require_service_token,
    service_token_org,
)
from app.api.routes.chatbot import get_graph_provider
from app.config.configuration_service import (
    ConfigurationService,  # noqa: TC001 - as above
)
from app.config.constants.service import TokenScopes
from app.core.org_context import org_of
from app.services.graph_db.interface.graph_db_provider import (
    IGraphDBProvider,  # noqa: TC001 - as above
)
from harness_core.logs import HarnessLog

router = APIRouter()
_log = HarnessLog.named("app.api.routes.control_plane_drivers")
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"


def legacy_capability_reader(
    graph: IGraphDBProvider = Depends(get_graph_provider),
    config_service: ConfigurationService = Depends(get_config_service),
) -> LegacyCapabilityReader:
    """The production reader: this service's graph, KV, toolset registry, SEC5's MCP lookup and the skill store."""
    from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
    from app.agents.mcp.instance_scope import list_instances_for_org
    from app.agents.registry.toolset_registry import get_toolset_registry
    from app.harness.mcp_hub.tool_admission import mcp_tool_admission
    from app.harness.supply_chain.skills import SkillTrust, SkillUntrusted
    from harness_core.skills import SkillRef

    registry = get_toolset_registry()
    skills = GraphSkillSource(graph)

    def provider_of(kind: str) -> str | None:
        metadata = registry.get_toolset_metadata(kind) or {}
        provider = metadata.get("provider")
        return provider if isinstance(provider, str) else None

    trust = SkillTrust(config_service)

    async def skill_digest(tenant: str, name: str, version: str) -> str | None:
        """The digest admission pins; None leaves the skill out of the run: no valid content, or an imported skill
        not signed or approved at it (ADR-SEC-01 D3), which the skills page shows as waiting for review."""
        ref = SkillRef(name=name, version=version)
        digest = await skills.content_digest(tenant, ref)
        files = await skills.files(tenant, ref) if digest is not None else None
        if digest is None or files is None:
            return None
        try:
            await trust.check(tenant, name, digest, files)
        except SkillUntrusted:
            return None
        return digest

    return LegacyCapabilityReader(
        graph,
        config_service,
        provider_of=provider_of,
        mcp_instances=list_instances_for_org,
        read_manifest=mcp_tool_admission().approved_document,
        skill_digest=skill_digest,
    )


@router.get("/legacy/capabilities")
async def legacy_capabilities(
    after: str | None = Query(None, pattern=KEY_PATTERN),
    agentKey: str | None = Query(None, pattern=KEY_PATTERN),
    agents: bool = Query(True),  # noqa: FBT001 - a query parameter
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.CAPABILITIES_READ)),
    reader: LegacyCapabilityReader = Depends(legacy_capability_reader),
) -> dict[str, Any]:
    org_id = service_token_org(claims)
    try:
        facts = await reader.read(org_id, after=after, agent_key=agentKey, agents=agents)
    except Exception as exc:
        _log.warning("control_plane.legacy_read_failed", orgId=org_id, reasonCode="legacy_source_unavailable",
                     errorType=type(exc).__name__)
        raise HTTPException(status_code=503, detail="The legacy capability facts could not be read") from exc
    return facts.model_dump(mode="json", by_alias=True)


@router.get("/directory/users/{userId}/teams")
async def user_teams(
    userId: str = Path(..., pattern=KEY_PATTERN),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.DIRECTORY_READ)),
    graph: IGraphDBProvider = Depends(get_graph_provider),
) -> dict[str, Any]:
    """The user's teams in the token's org with their role (CPD-11): what Node's MembershipPort reads.

    An unknown user is in no team; an unreadable graph is 503, never an empty list.
    """
    org_id = service_token_org(claims)
    try:
        user = await graph.get_user_by_user_id(userId, raise_on_error=True)
        teams = [] if user is None else await graph.get_user_team_roles(user["_key"], org_id)
    except Exception as exc:
        _log.warning("control_plane.membership_read_failed", orgId=org_id, reasonCode="membership_unavailable",
                     errorType=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Team membership could not be read") from exc
    return {"teams": [{"teamId": str(t["teamId"]), "role": str(t["role"])} for t in teams]}


@router.get("/directory/teams/{teamId}/owners")
async def team_owners(
    teamId: str = Path(..., pattern=KEY_PATTERN),
    claims: Mapping[str, Any] = Depends(require_service_token(TokenScopes.DIRECTORY_READ)),
    graph: IGraphDBProvider = Depends(get_graph_provider),
    ids: bool = Query(False),  # noqa: FBT001 - a query parameter
) -> dict[str, Any]:
    """How many people administer (OWNER) a team of the token's org (OWD-15), and with ``ids`` who (AP-08).

    Node asks whether an admin other than the requester can approve a classification ask, and whom to send the
    ask's approval link. A team of another org, or none, has no owners; an unreadable graph is 503, never zero
    or an empty list.
    """
    org_id = service_token_org(claims)
    try:
        found = await graph.get_team_permissions_and_owner_count(teamId, [])
    except Exception as exc:
        _log.warning("control_plane.team_owners_read_failed", orgId=org_id, reasonCode="membership_unavailable",
                     errorType=type(exc).__name__)
        raise HTTPException(status_code=503, detail="Team owners could not be read") from exc
    named = ids is True
    team = found.get("team") if isinstance(found, dict) else None
    if not isinstance(team, dict) or org_of(team) != org_id:
        return {"owners": 0, "ownerIds": []} if named else {"owners": 0}
    count = found.get("owner_count", 0)
    owners = count if isinstance(count, int) and count > 0 else 0
    if not named:
        return {"owners": owners}
    return {"owners": owners, "ownerIds": await _owner_ids(graph, teamId, org_id)}


_MEMBERS_PAGE = 100


async def _owner_ids(graph: IGraphDBProvider, team_id: str, org_id: str) -> list[str]:
    """The OWNERs' user ids, every page; a page the graph cannot answer is 503, so nobody is silently left out."""
    found: list[str] = []
    page = 1
    while True:
        try:
            answer = await graph.get_team_users(team_id, org_id, "", page=page, limit=_MEMBERS_PAGE)
        except Exception as exc:
            answer = None
            _log.warning("control_plane.team_owners_read_failed", orgId=org_id, reasonCode="membership_unavailable",
                         errorType=type(exc).__name__)
        if not isinstance(answer, dict):
            raise HTTPException(status_code=503, detail="Team owners could not be read")
        members = answer.get("members") or []
        for member in members:
            user_id = member.get("userId") if isinstance(member, dict) else None
            if isinstance(user_id, str) and user_id and member.get("isOwner") is True and user_id not in found:
                found.append(user_id)
        total = answer.get("memberCount")
        if len(members) < _MEMBERS_PAGE or not isinstance(total, int) or page * _MEMBERS_PAGE >= total:
            return found
        page += 1


__all__ = ["legacy_capabilities", "legacy_capability_reader", "router", "team_owners", "user_teams"]
