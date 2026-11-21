"""
Skills Management API Routes (Custom Skills Builder — Phase 2 of the plan)

REST surface over `SkillManager` (see `agents/agent_loop/skills/manager_factory.py`
for how the manager is composed) for the Personal Settings > Skills UI:
CRUD, versioning/rollback, resources, learning-loop candidate review,
search, and the three-source package importer (npm / URL / upload).

Every route uses `build_management_skill_manager`, the creator-scoped
profile (see that factory's docstring) — a user only ever sees/edits their
own custom skills here, plus org-wide `builtin` ones. Content mutations
(update/patch/rollback/deprecate/delete) and resource write/delete of a
builtin still 403; enable/disable of a builtin is allowed for org admins
only (`fetch_caller_role`). A non-owner attempt to mutate someone else's
custom skill is invisible (404) because the store's `SkillVisibility`
filters on `createdBy`.

Authorization: `SKILL_READ`/`SKILL_WRITE` OAuth scopes (mirrors every other
resource in this service — see `AGENT_READ`/`AGENT_WRITE` in `agent.py`).
Safe-delete additionally checks REFERENTIAL integrity against
`AGENT_HAS_SKILL` (agents using this skill) and `agentSkillRelation`
`requires` edges (other skills depending on this one) — see
`SkillManager.delete` / `collect_referential_usage`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from logging import Logger
from typing import Annotated, Any, Literal

import yaml
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.agent_loop_lib.commands.render import (
    COMMAND_ARGUMENTS_RULE,
    EXPANDED_MAX_UNITS,
    CommandError,
    is_reserved,
    parse_arguments,
    render_command,
)
from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    CandidateEdits,
    Skill,
    SkillCandidate,
    SkillConflictError,
    SkillFilter,
    SkillInUseError,
    SkillMetadata,
    SkillSource,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.loader import render_skill_md
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agent_loop_lib.modules.providers.skills.validator import (
    SkillFormatError,
    SkillValidator,
)
from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.graph_store import (
    MAX_DISPLAY_NAME,
    GraphSkillStore,
    SharedSkillRights,
    SkillLabels,
    SkillScope,
    SkillSharing,
    SkillVisibility,
    collect_referential_usage,
    edge_source_key,
)
from app.agents.agent_loop.skills.manager_factory import (
    build_management_skill_manager,
    get_builtin_seeder,
    sync_builtin_skills,
)
from app.api.middlewares.auth import require_scopes
from app.api.middlewares.caller_role import fetch_caller_role
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes
from app.core.org_context import bound_org_id, current_org_id, org_of
from app.harness.supply_chain.skills import SkillTrust, with_imported_source
from app.services.featureflag.platform_settings import is_skills_enabled
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.skills.npm_command_parser import (
    CatalogSpec,
    NpmCommandParseError,
    UrlSpec,
    parse_npm_command,
)
from app.services.skills.package_importer import (
    MAX_UPLOAD_BYTES,
    ImportPreview,
    PackageImportError,
    SkillPackageImporter,
)
from app.telemetry.identity import domain_from_email
from app.utils.user_lookup import get_user_or_503
from harness_core.logs import HarnessLog
from harness_core.skills import SkillRef
from harness_core.supply_chain.skill_scan import scan_skill

_SUPPLY_LOG = HarnessLog.named("app.harness.supply_chain")

# The web app shows this word for word.
_IMPORT_FRONTMATTER_UNREADABLE = (
    "Could not read the imported SKILL.md: it needs YAML frontmatter of key: value pairs between --- lines."
)


async def _require_skills_enabled(request: Request) -> None:
    """FastAPI dependency — rejects the request with 403 when the
    ``ENABLE_SKILLS`` platform feature flag is disabled for the calling org.
    Applied to every Skills endpoint (router-level) so UI visibility, REST,
    and agent-runtime wiring (`factory.py`) are gated consistently. Mirrors
    `mcp_servers.py`'s `_require_mcp_enabled`; `request.app.container` is how
    `agent.py`'s `get_services` obtains `config_service` in this same
    service."""
    config_service = request.app.container.config_service()
    if not await is_skills_enabled(config_service):
        raise HTTPException(status_code=403, detail="Skills are disabled for this organization.")


router = APIRouter(dependencies=[Depends(_require_skills_enabled)])

# UI-B4: a version's change note, at most a line or two.
MAX_CHANGE_NOTE = 500


# ============================================================================
# Request/response models
# ============================================================================

class SkillWriteRequest(BaseModel):
    """Structured create/update payload — the frontend's metadata form +
    Tiptap markdown body, never a hand-assembled SKILL.md string. The
    backend is the single place YAML frontmatter gets rendered
    (`_build_content`), so the client never needs to know the on-disk
    format at all."""

    name: str | None = None  # required for create; ignored for update (path param is authoritative)
    description: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] = Field(default_factory=list)
    license: str | None = None
    compatibility: str | None = None
    allowed_tools: list[str] | None = None
    related: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    # A /command (SLASH1). Absent on an update: the skill keeps what it had.
    invocation: InvocationRequest | None = None
    arguments: list[dict[str, Any]] | None = None
    # UI-B4: what this save changed, in the author's words; shown beside the version in its history.
    change_note: str | None = Field(default=None, alias="changeNote", max_length=MAX_CHANGE_NOTE)

    model_config = {"populate_by_name": True}


class InvocationRequest(BaseModel):
    user: bool = False
    model: bool = True


class PublishRequest(BaseModel):
    """Who a /command is shared with (SLASH1 S4): only its author, one of the caller's teams, or the org."""

    scope: Literal["personal", "team", "org"]
    team_id: str | None = Field(default=None, alias="teamId", min_length=1, max_length=128)

    model_config = {"populate_by_name": True}


class ExpandRequest(BaseModel):
    """What the user typed after `/name`."""

    args: str = Field(default="", max_length=EXPANDED_MAX_UNITS)


class DeprecateRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    replaced_by: str | None = None


class RollbackRequest(BaseModel):
    version: str = Field(..., min_length=1)


class PatchBodyRequest(BaseModel):
    old_string: str
    new_string: str
    change_note: str | None = Field(default=None, alias="changeNote", max_length=MAX_CHANGE_NOTE)

    model_config = {"populate_by_name": True}


class RenameRequest(BaseModel):
    """The name people see (UI-B4); null or blank goes back to the skill's id. The id itself never changes."""

    display_name: str | None = Field(default=None, alias="displayName", max_length=MAX_DISPLAY_NAME)

    model_config = {"populate_by_name": True, "extra": "forbid"}


class ApproveCandidateRequest(BaseModel):
    """A proposal approved as the reviewer edited it (UI-B4): every field optional, absent keeps the proposal's.
    The skill is created once with these, so the approval is one version."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, min_length=1)
    body: str | None = Field(default=None, min_length=1)
    category: str | None = None
    subcategory: str | None = None
    tags: list[str] | None = None
    change_note: str | None = Field(default=None, alias="changeNote", max_length=MAX_CHANGE_NOTE)

    model_config = {"populate_by_name": True, "extra": "forbid"}

    def edits(self) -> CandidateEdits:
        return CandidateEdits(**self.model_dump(exclude={"change_note"}))


class ResourceWriteRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str


class NpmImportRequest(BaseModel):
    command_or_name: str = Field(..., min_length=1)


class UrlImportRequest(BaseModel):
    url: str = Field(..., min_length=1)


class FinalizeImportRequest(BaseModel):
    """Persists exactly what a prior `preview_*` call returned — see
    `package_importer.py`'s module docstring for why this is stateless
    (the client round-trips `content`/`resources` verbatim)."""

    content: str = Field(..., min_length=1)
    resources: dict[str, str] = Field(default_factory=dict)
    category: str | None = None
    subcategory: str | None = None
    name: str | None = Field(
        default=None,
        description=(
            "Optional kebab-case name to persist as. Rewrites SKILL.md "
            "frontmatter `name` so an import can avoid a reserved builtin "
            "(e.g. anthropics/skills pptx → pptx-anthropic)."
        ),
    )
    attestation: dict[str, Any] | None = Field(
        default=None,
        description=(
            "A publisher's signed attestation of the bundle (ADR-SEC-01), handed to the skill admission policy "
            "with the import."
        ),
    )


def _metadata_to_dict(m: SkillMetadata) -> dict[str, Any]:
    return {
        "name": m.name,
        "description": m.description,
        "version": m.version,
        "category": m.category,
        "subcategory": m.subcategory,
        "tags": m.tags,
        "status": m.status.value,
        "source": m.source.value,
        "license": m.license,
        "compatibility": m.compatibility,
        "allowedTools": m.allowed_tools,
        "related": m.related,
        "requires": m.requires,
        "concepts": m.concepts,
        "deprecatedReason": m.deprecated_reason,
        "replacedBy": m.replaced_by,
        "createdAt": m.created_at,
        "updatedAt": m.updated_at,
        "packName": m.pack_name,
        "packVersion": m.pack_version,
        "invocation": {"user": m.invocation_user is True, "model": m.invocation_model is not False},
        "arguments": [a.wire() for a in m.arguments],
    }


def _command_owner(m: SkillMetadata, sharing: SkillSharing | None) -> str:
    """Whose text a /command sends, as the menu's badge shows it (SLASH1 D4)."""
    if m.source == SkillSource.BUILTIN:
        return "pipeshub"
    if sharing is not None and sharing.scope in SkillScope.SHARED:
        return sharing.scope
    return "you"


def _person_name(user: dict[str, Any] | None, org_id: str) -> str | None:
    """A live member's name; None for a user who is gone, deleted or of another org (shown as a former member)."""
    if not isinstance(user, dict) or org_of(user) not in (None, org_id) or user.get("isActive") is False:
        return None
    full = user.get("fullName") or " ".join(p for p in (user.get("firstName"), user.get("lastName")) if p)
    return (full.strip() or None) if isinstance(full, str) else None


def _team_name(team: dict[str, Any] | None, org_id: str) -> str | None:
    if not isinstance(team, dict) or org_of(team) != org_id or team.get("isDeleted") is True:
        return None
    return team.get("name") or None


async def _author_user(graph: IGraphDBProvider, author_id: str) -> dict[str, Any] | None:
    """An author is stamped under the graph key (REST) or the user id (run time)."""
    user = await graph.get_document(author_id, CollectionNames.USERS.value)
    return user if user is not None else await graph.get_user_by_user_id(author_id)


async def _sharing_names(
    graph: IGraphDBProvider, org_id: str, sharings: list[SkillSharing],
) -> tuple[dict[str, dict[str, Any] | None], dict[str, str | None]]:
    """The authors (their user document, None when gone) and team names of shared skills, each read once."""
    authors: dict[str, dict[str, Any] | None] = {}
    teams: dict[str, str | None] = {}
    for sharing in sharings:
        if sharing.scope not in SkillScope.SHARED:
            continue
        author = sharing.author_id
        if author and author not in authors:
            user = await _author_user(graph, author)
            authors[author] = user if _person_name(user, org_id) is not None else None
        if sharing.team_id and sharing.team_id not in teams:
            teams[sharing.team_id] = _team_name(
                await graph.get_document(sharing.team_id, CollectionNames.TEAMS.value), org_id,
            )
    return authors, teams


def _sharing_to_dict(
    sharing: SkillSharing | None,
    authors: dict[str, dict[str, Any] | None],
    teams: dict[str, str | None],
    org_id: str,
) -> dict[str, Any]:
    """Names for display, read now (S4). ``authorId`` is the author's user id, which a sent message keeps instead
    of their name, so an erased author shows as a former member everywhere."""
    if sharing is None or sharing.scope not in SkillScope.SHARED:
        return {
            "scope": SkillScope.PERSONAL, "teamId": None, "team": None,
            "author": None, "authorId": None, "authorFormer": False,
        }
    user = authors.get(sharing.author_id) if sharing.author_id else None
    return {
        "scope": sharing.scope,
        "teamId": sharing.team_id,
        "team": teams.get(sharing.team_id) if sharing.team_id else None,
        "author": _person_name(user, org_id),
        "authorId": user.get("userId") if user else None,
        "authorFormer": user is None,
    }


def _labels_to_dict(labels: SkillLabels | None) -> dict[str, Any]:
    """UI-B4: the name people see (null: show the id) and the current version's change note."""
    return {
        "displayName": labels.display_name if labels is not None else None,
        "changeNote": labels.change_note if labels is not None else None,
        "approvedBy": labels.approved_by if labels is not None else None,
    }


def _skill_to_dict(skill: Skill) -> dict[str, Any]:
    return {**_metadata_to_dict(skill.metadata), "body": skill.body, "resources": skill.resources}


def _preview_to_dict(preview: ImportPreview) -> dict[str, Any]:
    return {
        "name": preview.name,
        "description": preview.description,
        "version": preview.version,
        "content": preview.content,
        "resources": preview.resources,
        "warnings": preview.warnings,
        "skippedBinaryResources": preview.skipped_binary_resources,
        "sourceLabel": preview.source_label,
        # SEC-SC-04: what the content scanner finds, shown before the import.
        "findings": [
            f.as_json()
            for f in scan_skill(
                {"SKILL.md": preview.content.encode(), **{p: str(r).encode() for p, r in (preview.resources or {}).items()}}
            )
        ],
    }


def _build_content(payload: SkillWriteRequest, *, name: str, existing: SkillMetadata | None = None) -> str:
    """Structured form fields -> a full, spec-compliant SKILL.md string.
    The one place a `SkillWriteRequest` becomes YAML frontmatter — mirrors
    `SkillMetadata.to_frontmatter_dict`'s shape exactly so validation
    (`SkillValidator`, invoked by the store on every create/update) sees
    the same document a hand-authored SKILL.md would produce.

    `existing`, when given (every `update_skill` call — never `create_skill`,
    which has no prior state), carries the current `source`/`status`/
    `deprecated_reason`/`replaced_by` forward into the rendered SKILL.md so
    a plain content/metadata edit through this structured form can never
    silently re-activate a disabled or deprecated skill: `GraphSkillStore.
    _skill_to_doc` writes the graph `status`/`source` columns straight from
    whatever metadata this function's caller passes to `manager.update`."""
    metadata = SkillMetadata(
        name=name,
        description=payload.description,
        license=payload.license,
        compatibility=payload.compatibility,
        allowed_tools=payload.allowed_tools,
        category=payload.category,
        subcategory=payload.subcategory,
        tags=payload.tags,
        related=payload.related,
        requires=payload.requires,
        concepts=payload.concepts,
        source=existing.source if existing is not None else SkillSource.MANUAL,
        status=existing.status if existing is not None else SkillStatus.ACTIVE,
        deprecated_reason=existing.deprecated_reason if existing is not None else None,
        replaced_by=existing.replaced_by if existing is not None else None,
        **_command_fields(payload, existing),
    )
    skill = Skill(metadata=metadata, body=payload.body)
    return render_skill_md(skill)


def _command_fields(payload: SkillWriteRequest, existing: SkillMetadata | None) -> dict[str, Any]:
    if payload.invocation is not None:
        invocation = {"invocation_user": payload.invocation.user, "invocation_model": payload.invocation.model}
    elif existing is not None:
        invocation = {"invocation_user": existing.invocation_user, "invocation_model": existing.invocation_model}
    else:
        invocation = {}
    if payload.arguments is None:
        return {**invocation, "arguments": list(existing.arguments) if existing is not None else []}
    try:
        return {**invocation, "arguments": parse_arguments(payload.arguments)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=COMMAND_ARGUMENTS_RULE) from e


def _handle_registry_error(e: RegistryError) -> HTTPException:
    message = str(e)
    lowered = message.lower()
    if "not writable" in lowered:
        return HTTPException(status_code=403, detail="You cannot change this skill.")
    status_code = 404 if "not found" in lowered else 409
    return HTTPException(status_code=status_code, detail=message)


def _handle_conflict_error(e: SkillConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "message": str(e),
            "currentUpdatedAt": e.current_updated_at,
            "currentVersion": e.current_version,
        },
    )


def _handle_in_use_error(e: SkillInUseError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "message": str(e),
            "usedByAgents": e.used_by_agents,
            "requiredBySkills": e.required_by_skills,
        },
    )


def _parse_if_match(raw: str | None) -> int | None:
    """Parse an HTTP If-Match token into the skill's `updatedAtTimestamp`.
    Absence, empty, or `*` means last-write-wins. Quoted / weak ETags
    (`W/"123"`) are accepted. Non-integer tokens are 400."""
    if raw is None or not isinstance(raw, str):
        return None
    token = raw.strip()
    if not token or token == "*":
        return None
    if token.startswith("W/"):
        token = token[2:].strip()
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        token = token[1:-1]
    try:
        return int(token)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="If-Match must be the skill's updatedAt timestamp",
        ) from None


def _handle_format_error(e: SkillFormatError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _reject_if_builtin_name(name: str) -> None:
    """Blocks a custom skill from being created under a name reserved for a
    builtin pack — a policy decision (which names are protected), so it
    lives in the router, not `GraphSkillStore`. Without this, a user could
    create a custom skill named e.g. `pdf` before `sync_builtin_skills` ever
    runs for their org; the seeder's `_is_unmodified` check would then treat
    the real builtin as "has org edits" and never seed it (see
    `builtin_seeder.py`)."""
    seeder = get_builtin_seeder()
    if seeder is not None and name in seeder.pack_versions:
        raise HTTPException(status_code=409, detail=f"{name!r} is a built-in skill name.")


def _annotate_reserved_import_name(preview: ImportPreview) -> ImportPreview:
    seeder = get_builtin_seeder()
    if seeder is None or preview.name not in seeder.pack_versions:
        return preview
    warning = (
        f"{preview.name!r} is a built-in skill name. Choose a different name before importing."
    )
    warnings = list(preview.warnings or [])
    if warning not in warnings:
        warnings.append(warning)
    preview.warnings = warnings
    return preview


def _apply_import_name(content: str, name_override: str | None) -> tuple[str, str]:
    """Resolve the persisted skill name and keep SKILL.md frontmatter in sync."""
    try:
        frontmatter: dict[str, Any] = {}
        body: str | None = None
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                loaded = yaml.safe_load(parts[1]) or {}
                if not isinstance(loaded, dict):
                    raise ValueError("YAML frontmatter must be a mapping of key: value pairs")
                frontmatter = loaded
                body = parts[2]
        original = frontmatter.get("name")
    except Exception as e:
        _SUPPLY_LOG.info("supply_chain.skill_import_unreadable", error=type(e).__name__)
        raise HTTPException(status_code=400, detail=_IMPORT_FRONTMATTER_UNREADABLE) from e

    override = name_override.strip() if name_override else None
    name = override or original
    if isinstance(name, str):
        name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Imported SKILL.md is missing a 'name' field.")
    try:
        SkillValidator().validate_name(name)
    except SkillFormatError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if override and body is not None and name != original:
        frontmatter["name"] = name
        dumped = yaml.safe_dump(frontmatter, sort_keys=False)
        content = f"---\n{dumped}---{body}"
    return name, content


async def _load_skill_metadata(manager: SkillManager, name: str) -> SkillMetadata:
    """Load-or-404 used by write routes. Honors the management store's
    creator `SkillVisibility`, so a co-worker's custom skill is
    indistinguishable from a missing one (404), while org-wide builtins
    remain loadable by every member. Uses `get_skill` (not
    `activate_skill`) so a disabled skill can still be inspected,
    updated, or re-enabled."""
    try:
        skill = await manager.get_skill(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return skill.metadata


def _require_manager_of(ctx: dict[str, Any], sharing: SkillSharing | None) -> None:
    """A team or org command is changed by those who govern it (an org admin; a team's owner), never by whoever
    happens to see it. Personal skills are already the caller's own: the store shows no one else's."""
    if sharing is None or sharing.scope not in SkillScope.SHARED:
        return
    if not ctx["rights"].manages(sharing.scope, sharing.team_id):
        who = "an organization admin" if sharing.scope == SkillScope.ORG else "an owner of its team or an organization admin"
        raise HTTPException(status_code=403, detail=f"This command is shared; only {who} can change it.")


async def _reject_if_builtin_skill(manager: SkillManager, name: str, ctx: dict[str, Any]) -> SkillMetadata:
    """Blocks a content `SKILL_WRITE` mutation (update/patch/rollback/
    deprecate/delete, or resource write/delete) against an EXISTING
    builtin-sourced skill — the counterpart to `_reject_if_builtin_name`
    above, which only blocks *creating* a new skill under a reserved
    name. Enable/disable is the exception: those routes call
    `_require_admin_for_builtin_availability` instead, so an org admin
    can mute a builtin without being able to edit its SKILL.md.
    Returns the fetched metadata so callers that also need it (e.g.
    `update_skill`, to preserve lifecycle fields — see `_build_content`)
    don't have to load the skill twice. Raises 404/409 (via
    `_handle_registry_error`) if the skill doesn't exist, or 403 if it's
    a builtin."""
    metadata = await _load_skill_metadata(manager, name)
    if metadata.source == SkillSource.BUILTIN:
        raise HTTPException(status_code=403, detail=f"{name!r} is a built-in skill and cannot be modified.")
    _require_manager_of(ctx, await ctx["store"].sharing(name))
    return metadata


async def _require_admin_for_builtin_availability(request: Request, metadata: SkillMetadata) -> None:
    """Enable/disable of a custom skill is creator-gated by the store's
    visibility scope (non-owners never load the doc). Enable/disable of a
    builtin is org-wide, so it additionally requires a live org-admin
    role from Node (`fetch_caller_role`) — members get 403. Fail closed
    if Node cannot answer."""
    if metadata.source != SkillSource.BUILTIN:
        return
    config_service = request.app.container.config_service()
    if not (await fetch_caller_role(request, config_service)).is_admin:
        raise HTTPException(
            status_code=403,
            detail=f"{metadata.name!r} is a built-in skill; only an organization admin can change its availability.",
        )


# ============================================================================
# Service/context helpers (self-contained — see toolsets.py for the same
# per-router pattern; deliberately not importing agent.py's module-private
# helpers, to keep this router loosely coupled from the agent routes)
# ============================================================================

async def _get_services(request: Request) -> dict[str, Any]:
    container = request.app.container
    return {
        "retrieval_service": await container.retrieval_service(),
        "graph_provider": await container.graph_provider(),
        "logger": container.logger(),
    }


def _get_user_context(request: Request) -> dict[str, Any]:
    user = getattr(request.state, "user", {})
    user_id = user.get("userId")
    # The org the auth middleware bound from the verified token, never a request field.
    org_id = bound_org_id()
    if not user_id or not org_id:
        raise HTTPException(status_code=401, detail="Authentication required. Please provide valid credentials.")
    return {"userId": user_id, "orgId": org_id, "email": user.get("email"), "domain": domain_from_email(user.get("email"))}


def _caller_org(request: Request) -> str:
    """The authenticated caller's org (401 without one), as the context binds it."""
    _ = _get_user_context(request)
    return current_org_id()


async def _get_user_key(user_id: str, graph_provider: IGraphDBProvider, logger: Logger) -> str:
    try:
        user = await get_user_or_503(graph_provider, user_id)
        if not user or not isinstance(user, dict):
            raise HTTPException(status_code=404, detail="User not found")
        return user["_key"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"skills: failed to resolve user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve user information") from e


async def _team_roles(graph_provider: IGraphDBProvider, user_key: str, org_id: str, logger: Logger) -> dict[str, str]:
    """The caller's teams in their org with their role there; an unreadable membership is 503, never none."""
    try:
        rows = await graph_provider.get_user_team_roles(user_key, org_id)
    except Exception as e:
        logger.error(f"skills: team membership could not be read: {type(e).__name__}")
        raise HTTPException(status_code=503, detail="Team membership could not be read.") from e
    return {str(r["teamId"]): str(r.get("role") or "").upper() for r in rows or []}


async def _is_admin(request: Request) -> bool:
    return (await fetch_caller_role(request, request.app.container.config_service())).is_admin


AskAdmin = Callable[[], Awaitable[bool]]


def _admin_question(request: Request) -> AskAdmin:
    """Asks Node whether the caller is an org admin, when called. A route that may need the answer passes this to
    ``_build_manager``; a route that never does passes nothing, so it can be seen not to ask."""
    return lambda: _is_admin(request)


async def _build_manager(
    request: Request,
    *,
    ask_admin: AskAdmin | None = None,
    manage: str | None = None,
    admin: bool = False,
    governs: bool = False,
) -> tuple[SkillManager, dict[str, Any]]:
    """The caller's skills manager. It reads their own skills, the builtins and the team and org commands shared
    with them (SLASH1 S4), and changes the shared commands it governs. ``ask_admin`` is called only when the answer
    matters: the route needs it (``admin``), or the skill it would change (``manage``), or any skill of the org
    (``governs``, the editor's listing), is shared and not the caller's team's."""
    if ask_admin is None and (admin or manage is not None or governs):
        raise TypeError("_build_manager needs ask_admin to decide shared-skill rights")
    services = await _get_services(request)
    user_context = _get_user_context(request)
    graph = services["graph_provider"]
    org_id = current_org_id()
    user_key = await _get_user_key(user_context["userId"], graph, services["logger"])
    teams = await _team_roles(graph, user_key, org_id, services["logger"])
    rights = SharedSkillRights(owned_teams=frozenset(t for t, role in teams.items() if role == "OWNER"))
    if not admin and (manage is not None or governs):
        docs = (
            [await graph.get_document(f"{org_id}_{manage}", CollectionNames.AGENT_SKILLS.value)]
            if manage is not None
            else await graph.get_nodes_by_filters(CollectionNames.AGENT_SKILLS.value, {"orgId": org_id})
        )
        admin = any(
            (sharing := SkillSharing.of(d)).scope in SkillScope.SHARED and not rights.manages(sharing.scope, sharing.team_id)
            for d in docs if d
        )
    if admin and ask_admin is not None:
        rights = replace(rights, org_admin=await ask_admin())
    visibility = SkillVisibility.of([user_key], teams=teams)
    manager = await build_management_skill_manager(
        graph, org_id, user_key, services["retrieval_service"], visibility=visibility, rights=rights,
    )
    store = GraphSkillStore(graph, org_id, user_key, visibility=visibility, rights=rights)
    return manager, {**services, **user_context, "userKey": user_key, "teams": teams, "rights": rights, "store": store}


# ============================================================================
# Catalog / CRUD
# ============================================================================

@router.get("/", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def list_skills(
    request: Request,
    category: str | None = None,
    subcategory: str | None = None,
    status: str | None = None,
    source: str | None = None,
    tag: str | None = None,
    q: str | None = None,
    invocation: Literal["user"] | None = None,
) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), governs=invocation != "user")
    # Only the read/list route seeds builtins (write routes stay pure): a
    # fresh org otherwise sees no builtin skills until its first chat, since
    # seeding is normally a runtime (agent-turn) side effect — see
    # `manager_factory.build_runtime_skill_manager`. Version-gated and
    # idempotent, so this is a no-op once the org's catalog is current.
    await sync_builtin_skills(ctx["graph_provider"], current_org_id(), manager)
    filt = SkillFilter(
        query=q,
        category=category,
        subcategory=subcategory,
        tags=[tag] if tag else None,
        status=SkillStatus(status) if status else None,
        source=SkillSource(source) if source else None,
    )
    metadatas = await manager.list_skills(filt)
    sharings = await ctx["store"].sharings()
    labels = await ctx["store"].labels()
    if invocation == "user":
        # The composer's / menu (SLASH1 D6): what this caller may send as a command, with whose it is.
        metadatas = [m for m in metadatas if m.invocation_user and m.status == SkillStatus.ACTIVE]
    else:
        # The editor lists what the caller may change; team and org commands they only use stay in the menu.
        rights: SharedSkillRights = ctx["rights"]
        metadatas = [
            m for m in metadatas
            if (s := sharings.get(m.name)) is None
            or s.scope not in SkillScope.SHARED
            or s.author_id == ctx["userKey"]
            or rights.manages(s.scope, s.team_id)
        ]
    authors, teams = await _sharing_names(ctx["graph_provider"], current_org_id(), [s for m in metadatas if (s := sharings.get(m.name))])
    reviews = await _review_standings(request, ctx, [m.name for m in metadatas if m.source == SkillSource.IMPORTED])
    return JSONResponse(status_code=200, content={"skills": [
        {
            **_metadata_to_dict(m),
            **_sharing_to_dict(sharings.get(m.name), authors, teams, current_org_id()),
            "owner": _command_owner(m, sharings.get(m.name)),
            **_labels_to_dict(labels.get(m.name)),
            **({"review": reviews[m.name]} if m.name in reviews else {}),
        }
        for m in metadatas
    ]})


async def _review_standings(request: Request, ctx: dict[str, Any], names: list[str]) -> dict[str, Any]:
    """Whether agents may use each imported skill, and why not (ADR-SEC-01), for the skills page."""
    if not names:
        return {}
    trust = SkillTrust(request.app.container.config_service())
    out: dict[str, Any] = {}
    for name in names:
        digest, files = await _current_bundle(ctx["graph_provider"], current_org_id(), name)
        if digest is not None:
            try:
                out[name] = await trust.standing(current_org_id(), name, digest, files)
            except Exception:
                out[name] = {"state": "untrusted", "reasonCode": "review_unreadable", "digest": digest, "findings": []}
    return out


@router.get("/categories", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_categories(request: Request) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    categories = await manager.get_categories()
    tags = await manager.get_tags()
    return JSONResponse(status_code=200, content={"categories": categories, "tags": tags})


@router.get("/search", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def search_skills(
    request: Request, q: str = "", category: str | None = None, limit: int = Query(10, ge=1, le=100),
) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    matches = await manager.search(q, category=category, limit=limit)
    return JSONResponse(
        status_code=200,
        content={
            "results": [
                {"skill": _metadata_to_dict(m.skill), "relevance": m.relevance, "matchReason": m.match_reason}
                for m in matches
            ]
        },
    )


@router.get("/{name}", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_skill(request: Request, name: str) -> JSONResponse:
    manager, ctx = await _build_manager(request)
    try:
        skill = await manager.get_skill(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    sharing = await ctx["store"].sharing(name)
    authors, teams = await _sharing_names(ctx["graph_provider"], current_org_id(), [sharing] if sharing else [])
    return JSONResponse(
        status_code=200,
        content={
            **_skill_to_dict(skill),
            **_sharing_to_dict(sharing, authors, teams, current_org_id()),
            "owner": _command_owner(skill.metadata, sharing),
            **_labels_to_dict(await ctx["store"].label(name)),
        },
    )


@router.post("/{name}/expand", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def expand_command(request: Request, name: str, payload: ExpandRequest) -> JSONResponse:
    """The text `/name args` sends (SLASH1 D2): Node calls this before a turn reaches any executor
    and makes the result the turn's input. Read as the caller, so only their commands resolve."""
    manager, ctx = await _build_manager(request)
    try:
        skill = await manager.get_skill(name)
    except RegistryError:
        skill = None
    m = skill.metadata if skill is not None else None
    if skill is None or m is None or not m.invocation_user or m.status != SkillStatus.ACTIVE or is_reserved(name):
        raise HTTPException(
            status_code=404,
            detail={"code": "command_not_found", "message": f"/{name} is not one of your commands."},
        )
    try:
        rendered = render_command(skill.body, m.arguments, payload.args)
    except CommandError as e:
        raise HTTPException(
            status_code=422, detail={"code": e.code, "message": e.user_message, "names": e.names},
        ) from e
    # The digest a skill pin names (SKP1): the run's command is the content it was admitted with, which
    # `PinnedSkills` resolves from history whatever the command became since (D4).
    digest = await GraphSkillSource(ctx["graph_provider"]).content_digest(current_org_id(), SkillRef(name, m.version))
    if digest is None:
        raise HTTPException(status_code=503, detail="This command cannot be pinned right now.")
    sharing = await ctx["store"].sharing(name)
    authors, teams = await _sharing_names(ctx["graph_provider"], current_org_id(), [sharing] if sharing else [])
    shown = _sharing_to_dict(sharing, authors, teams, current_org_id())
    return JSONResponse(
        status_code=200,
        content={
            "name": name,
            "version": m.version,
            "contentDigest": digest,
            "owner": _command_owner(m, sharing),
            "args": rendered.values,
            "expanded": rendered.text,
            # Ids, never names: the sent message keeps them and names are read when shown (S4, erasure).
            "authorId": shown["authorId"],
            "authorFormer": shown["authorFormer"],
            "teamId": shown["teamId"],
        },
    )


@router.get("/commands/share-targets", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def command_share_targets(request: Request) -> JSONResponse:
    """Where the caller may share a /command (SLASH1 S4): the teams they own, and the org when they are its admin."""
    _manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), admin=True)
    rights: SharedSkillRights = ctx["rights"]
    teams = []
    for team_id in sorted(rights.owned_teams):
        team = await ctx["graph_provider"].get_document(team_id, CollectionNames.TEAMS.value)
        teams.append({"id": team_id, "name": (team or {}).get("name") or team_id})
    return JSONResponse(status_code=200, content={"org": rights.org_admin, "teams": teams})


@router.get("/commands/provenance", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def command_provenance(
    request: Request,
    author_id: Annotated[str | None, Query(alias="authorId", max_length=128)] = None,
    team_id: Annotated[str | None, Query(alias="teamId", max_length=128)] = None,
) -> JSONResponse:
    """The names a sent team or org command shows in "What was sent", read now (SLASH1 S4): a message keeps only
    ids. A user who is gone, deleted or of another org reads as a former member; a team of another org has no
    name. Only the caller's org is ever read."""
    services = await _get_services(request)
    org_id = _caller_org(request)
    graph = services["graph_provider"]
    author = _person_name(await graph.get_user_by_user_id(author_id), org_id) if author_id else None
    team = _team_name(await graph.get_document(team_id, CollectionNames.TEAMS.value), org_id) if team_id else None
    return JSONResponse(status_code=200, content={"author": author, "authorFormer": author is None, "team": team})


@router.post("/{name}/publish", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def publish_command(request: Request, name: str, payload: PublishRequest) -> JSONResponse:
    """Share a /command with one of the caller's teams or the org, or take it back (SLASH1 D4 ``publish``).

    An org command is an org admin's to publish and change; a team command its team's owners' (or an org
    admin's). The team must be one of the caller's org: its id comes from the body, so it is checked here."""
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), admin=True)
    metadata = await _reject_if_builtin_skill(manager, name, ctx)
    team_id = payload.team_id if payload.scope == SkillScope.TEAM else None
    if payload.scope == SkillScope.TEAM and team_id is None:
        raise HTTPException(status_code=400, detail="A team command needs teamId.")
    if payload.scope in SkillScope.SHARED and not metadata.invocation_user:
        raise HTTPException(status_code=400, detail="Only a /command can be shared with a team or the organization.")
    if team_id is not None and team_id not in ctx["rights"].owned_teams:
        team = await ctx["graph_provider"].get_document(team_id, CollectionNames.TEAMS.value)
        if not isinstance(team, dict) or org_of(team) != current_org_id():
            raise HTTPException(status_code=404, detail="Team not found.")
    try:
        sharing = await ctx["store"].share(name, payload.scope, team_id)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    authors, teams = await _sharing_names(ctx["graph_provider"], current_org_id(), [sharing])
    return JSONResponse(status_code=200, content={"name": name, **_sharing_to_dict(sharing, authors, teams, current_org_id())})


@router.get("/{name}/export", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def export_skill(request: Request, name: str) -> PlainTextResponse:
    manager, _ctx = await _build_manager(request)
    try:
        skill = await manager.get_skill(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return PlainTextResponse(
        content=render_skill_md(skill), media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{name}.SKILL.md"'},
    )


@router.post("/", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def create_skill(request: Request, payload: SkillWriteRequest) -> JSONResponse:
    if not payload.name or not payload.name.strip():
        raise HTTPException(status_code=400, detail="'name' is required to create a skill.")
    name = payload.name.strip()
    _reject_if_builtin_name(name)
    manager, _ctx = await _build_manager(request)
    content = _build_content(payload, name=name)
    try:
        metadata = await manager.create(
            name, content, payload.category, payload.subcategory, change_note=payload.change_note,
        )
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    return JSONResponse(status_code=201, content=_metadata_to_dict(metadata))


@router.put("/{name}", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def update_skill(
    request: Request,
    name: str,
    payload: SkillWriteRequest,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    existing_metadata = await _reject_if_builtin_skill(manager, name, ctx)
    content = _build_content(payload, name=name, existing=existing_metadata)
    try:
        metadata = await manager.update(
            name, content, expected_updated_at=_parse_if_match(if_match), change_note=payload.change_note,
        )
    except SkillConflictError as e:
        raise _handle_conflict_error(e) from e
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


@router.patch("/{name}/body", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def patch_skill_body(
    request: Request,
    name: str,
    payload: PatchBodyRequest,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    try:
        ok = await manager.patch(
            name, payload.old_string, payload.new_string,
            expected_updated_at=_parse_if_match(if_match), change_note=payload.change_note,
        )
    except SkillConflictError as e:
        raise _handle_conflict_error(e) from e
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Patch failed — the skill doesn't exist, or 'old_string' wasn't found exactly once in its body.",
        )
    return JSONResponse(status_code=200, content={"status": "success"})


@router.post("/{name}/rename", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def rename_skill(
    request: Request,
    name: str,
    payload: RenameRequest,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    """UI-B4: the name people see. The skill's id (``name``) stays, so agents that use it, its installation, its
    /command and its history keep reaching it; a name another skill of the org shows is refused (409)."""
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    store: GraphSkillStore = ctx["store"]
    try:
        labels = await store.rename(name, payload.display_name, expected_updated_at=_parse_if_match(if_match))
    except SkillConflictError as e:
        raise _handle_conflict_error(e) from e
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    shown = _labels_to_dict(labels)
    return JSONResponse(status_code=200, content={"name": name, "displayName": shown["displayName"], "changeNote": shown["changeNote"]})


@router.post("/{name}/deprecate", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def deprecate_skill(request: Request, name: str, payload: DeprecateRequest) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    ok = await manager.deprecate(name, payload.reason, payload.replaced_by)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill {name!r} not found.")
    return JSONResponse(status_code=200, content={"status": "success"})


@router.post("/{name}/disable", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def disable_skill(request: Request, name: str) -> JSONResponse:
    """Reversible mute — see `SkillManager.disable`. Custom skills: the
    creator (store visibility). Builtin skills: org admin only. An
    illegal transition (already disabled, or deprecated) surfaces as 409
    via `_handle_registry_error`."""
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    existing = await _load_skill_metadata(manager, name)
    await _require_admin_for_builtin_availability(request, existing)
    _require_manager_of(ctx, await ctx["store"].sharing(name))
    try:
        metadata = await manager.disable(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


@router.post("/{name}/enable", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def enable_skill(request: Request, name: str) -> JSONResponse:
    """Reverses `disable`. Never undeprecates — see `SkillManager.enable`.
    Same authorization as `disable_skill`."""
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    existing = await _load_skill_metadata(manager, name)
    await _require_admin_for_builtin_availability(request, existing)
    _require_manager_of(ctx, await ctx["store"].sharing(name))
    try:
        metadata = await manager.enable(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


# ============================================================================
# Safe delete
# ============================================================================

def _edge_source_key(edge: dict[str, Any]) -> str:
    return edge_source_key(edge)


async def _check_usage(name: str, org_id: str, graph_provider: IGraphDBProvider) -> dict[str, Any]:
    """Referential-integrity snapshot for `GET /{name}/usage` and the
    historical unit tests that call this helper directly. The *delete*
    guard lives on `SkillManager.delete` so every caller (REST, tools)
    gets it."""
    usage = await collect_referential_usage(graph_provider, org_id, name)
    return {"usedByAgents": usage.used_by_agents, "requiredBySkills": usage.required_by_skills}


@router.get("/{name}/usage", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_skill_usage(request: Request, name: str) -> JSONResponse:
    _manager, ctx = await _build_manager(request)
    usage = await _check_usage(name, current_org_id(), ctx["graph_provider"])
    return JSONResponse(status_code=200, content=usage)


@router.delete("/{name}", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def delete_skill(request: Request, name: str, detach: Annotated[bool, Query()] = False) -> JSONResponse:
    """Refuses to delete a skill that's in use (409, with structured
    `usedByAgents`/`requiredBySkills`) unless `detach=true` — and even
    then, a skill another skill `requires` can NEVER be force-deleted
    (only deprecated: `requires` is a content dependency, detaching it
    would leave the dependent skill's instructions pointing at nothing).
    The guard lives on `SkillManager.delete` so the `skill_manage` tool
    cannot bypass it."""
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    try:
        ok = await manager.delete(name, detach=detach)
    except SkillInUseError as e:
        raise _handle_in_use_error(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill {name!r} not found.")
    return JSONResponse(status_code=200, content={"status": "success"})


# ============================================================================
# Version history
# ============================================================================

@router.get("/{name}/versions", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def list_versions(request: Request, name: str) -> JSONResponse:
    """The current version first, then the archived ones newest first, each author named as people see them.
    The current version is listed because its save's note lives on the skill, not in the archive, and authors are
    named here because the stored ``updatedBy`` is a graph key no screen can show (UIADOPT2-FU-1)."""
    manager, ctx = await _build_manager(request)
    try:
        versions = await manager.list_versions(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    store: GraphSkillStore = ctx["store"]
    entries: list[dict[str, Any]] = [
        {"version": v.version, "updatedBy": v.updated_by, "createdAt": v.created_at, "summary": v.summary, "current": False}
        for v in versions
    ]
    current = await _current_version_entry(manager, store, name)
    if current is not None:
        entries = [current, *(e for e in entries if e["version"] != current["version"])]
    org_id = current_org_id()
    graph = ctx["graph_provider"]
    names: dict[str, str | None] = {}
    for author in {e["updatedBy"] for e in entries if e["updatedBy"]}:
        names[author] = _person_name(await _author_user(graph, author), org_id)
    for e in entries:
        by = e["updatedBy"]
        e["updatedByName"] = names.get(by) if by else None
        e["updatedByFormer"] = bool(by) and names.get(by) is None
    return JSONResponse(status_code=200, content={"versions": entries})


async def _current_version_entry(manager: Any, store: GraphSkillStore, name: str) -> dict[str, Any] | None:  # noqa: ANN401
    """The live version as a history entry, or None when the caller cannot see the skill."""
    try:
        skill = await manager.get_skill(name)
    except RegistryError:
        return None
    if skill is None:
        return None
    provenance = await store.get_provenance(name) or {}
    labels = await store.label(name)
    return {
        "version": skill.metadata.version,
        "updatedBy": provenance.get("updated_by") or provenance.get("created_by"),
        "createdAt": skill.metadata.updated_at or "",
        "summary": (labels.change_note if labels is not None else None) or "",
        "current": True,
    }


@router.get("/{name}/versions/{version}", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_version(request: Request, name: str, version: str) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    try:
        skill = await manager.get_version(name, version)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Version {version!r} of skill {name!r} not found.")
    return JSONResponse(status_code=200, content=_skill_to_dict(skill))


@router.post("/{name}/rollback", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def rollback_skill(request: Request, name: str, payload: RollbackRequest) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    try:
        metadata = await manager.rollback(name, payload.version)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


# ============================================================================
# Bundled resources
# ============================================================================

@router.get("/{name}/resource", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_resource(request: Request, name: str, path: str = Query(..., min_length=1)) -> PlainTextResponse:
    manager, _ctx = await _build_manager(request)
    try:
        content = await manager.load_resource(name, path)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return PlainTextResponse(content=content)


@router.put("/{name}/resource", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def write_resource(request: Request, name: str, payload: ResourceWriteRequest) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    try:
        ok = await manager.write_resource(name, payload.path, payload.content)
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill {name!r} not found.")
    return JSONResponse(status_code=200, content={"status": "success"})


@router.delete("/{name}/resource", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def remove_resource(request: Request, name: str, path: str = Query(..., min_length=1)) -> JSONResponse:
    manager, ctx = await _build_manager(request, ask_admin=_admin_question(request), manage=name)
    await _reject_if_builtin_skill(manager, name, ctx)
    ok = await manager.remove_resource(name, path)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Resource {path!r} not found for skill {name!r}.")
    return JSONResponse(status_code=200, content={"status": "success"})


# ============================================================================
# Learning-loop candidates (governance review queue)
# ============================================================================

async def _decides_any_candidate(request: Request) -> bool:
    """An org admin by the edition's own answer, the check the admin-only candidate routes made before UI-B4."""
    from app.edition_config import check_user_is_admin

    user = getattr(request.state, "user", None) or {}
    return bool(await check_user_is_admin(
        user.get("userId", ""), user.get("orgId"), request, request.app.container.config_service(),
    ))


def _proposes_to(candidate: SkillCandidate, ctx: dict[str, Any]) -> bool:
    """The candidate was learned from a run of the caller's: once approved it is their skill (UI-B4)."""
    return candidate.proposed_by is not None and candidate.proposed_by in {ctx["userKey"], ctx["userId"]}


@router.get("/candidates/pending", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_pending_candidates(request: Request) -> JSONResponse:
    """The proposals the caller may decide: those learned from their own runs, or, for an org admin, every one.
    Each names the agent that learned it (``source_agent_key``, null for the assistant) and its run."""
    manager, ctx = await _build_manager(request)
    candidates = await manager.get_pending_candidates()
    admin = any(not _proposes_to(c, ctx) for c in candidates) and await _decides_any_candidate(request)
    return JSONResponse(status_code=200, content={"candidates": [
        {**c.model_dump(mode="json"), "mine": _proposes_to(c, ctx)}
        for c in candidates
        if admin or _proposes_to(c, ctx)
    ]})


async def _require_decider(request: Request, manager: SkillManager, ctx: dict[str, Any], candidate_id: str) -> None:
    """A proposal is decided by its owner-to-be (the person whose run learned it) or an org admin (UI-B4). Another
    member gets 403, as before; an admin check Node cannot answer is 403 too (fail closed)."""
    candidate = await manager.find_candidate(candidate_id)
    if candidate is not None and _proposes_to(candidate, ctx):
        return
    if not await _decides_any_candidate(request):
        raise HTTPException(status_code=403, detail="Only the person it was learned for or an admin can decide this.")
    if candidate is None:
        raise HTTPException(status_code=404, detail=f"Candidate {candidate_id!r} not found")


async def _learners_manager(ctx: dict[str, Any], learner_key: str) -> SkillManager:
    """A skills manager acting as ``learner_key``: what it creates is theirs (``createdBy`` and the OWNER edge),
    with their own visibility, so an admin deciding a proposal never becomes its owner."""
    graph = ctx["graph_provider"]
    org_id = current_org_id()
    teams = await _team_roles(graph, learner_key, org_id, ctx["logger"])
    return await build_management_skill_manager(
        graph, org_id, learner_key, ctx["retrieval_service"],
        visibility=SkillVisibility.of([learner_key], teams=teams),
        rights=SharedSkillRights(owned_teams=frozenset(t for t, role in teams.items() if role == "OWNER")),
    )


_CANDIDATE_DECISION = [Depends(require_scopes(OAuthScopes.SKILL_WRITE))]


@router.post("/candidates/{candidate_id}/approve", dependencies=_CANDIDATE_DECISION)
async def approve_candidate(
    request: Request, candidate_id: str, payload: ApproveCandidateRequest | None = None,
) -> JSONResponse:
    """Approve a proposal, as it is or with the reviewer's edits (UI-B4): one create, so one version. The skill is
    the learner's (the person whose run learned it) whoever approves it; the approver is recorded apart
    (``approvedBy``, UI-ADOPT2). A proposal with no recorded learner is the approver's."""
    manager, ctx = await _build_manager(request)
    await _require_decider(request, manager, ctx, candidate_id)
    edits = payload.edits() if payload is not None else None
    if edits is not None and edits.name is not None:
        _reject_if_builtin_name(edits.name)
    candidate = await manager.find_candidate(candidate_id)
    if candidate is not None and candidate.proposed_by is not None and not _proposes_to(candidate, ctx):
        manager = await _learners_manager(ctx, candidate.proposed_by)
    try:
        metadata = await manager.approve_candidate(
            candidate_id, edits, change_note=payload.change_note if payload is not None else None,
            approved_by=ctx["userId"],
        )
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


@router.post("/candidates/{candidate_id}/reject", dependencies=_CANDIDATE_DECISION)
async def reject_candidate(request: Request, candidate_id: str) -> JSONResponse:
    manager, ctx = await _build_manager(request)
    await _require_decider(request, manager, ctx, candidate_id)
    await manager.reject_candidate(candidate_id)
    return JSONResponse(status_code=200, content={"status": "success"})


# ============================================================================
# Package import (npm / URL / upload) — stateless preview + finalize
# ============================================================================

@router.post("/import/npm/preview", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def preview_npm_import(request: Request, payload: NpmImportRequest) -> JSONResponse:
    _manager, _ctx = await _build_manager(request)  # auth/scope check only — nothing persisted yet
    try:
        spec = parse_npm_command(payload.command_or_name)
    except NpmCommandParseError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    try:
        importer = SkillPackageImporter()
        if isinstance(spec, UrlSpec):
            preview = await importer.preview_url(spec.url, skill_filter=spec.skill_filter)
        elif isinstance(spec, CatalogSpec):
            preview = await importer.preview_catalog_slug(spec.slug, skill_filter=spec.skill_filter)
        else:
            preview = await importer.preview_npm(spec)
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(_annotate_reserved_import_name(preview)))


@router.post("/import/url/preview", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def preview_url_import(request: Request, payload: UrlImportRequest) -> JSONResponse:
    _manager, _ctx = await _build_manager(request)
    try:
        preview = await SkillPackageImporter().preview_url(payload.url)
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(_annotate_reserved_import_name(preview)))


@router.post("/import/upload/preview", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def preview_upload_import(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    _manager, _ctx = await _build_manager(request)
    # One byte past the cap is enough to refuse: an upload is never read whole before it is bounded.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        preview = await asyncio.to_thread(
            SkillPackageImporter().preview_upload, file.filename or "upload.zip", data
        )
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(_annotate_reserved_import_name(preview)))


@router.post("/import/finalize", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def finalize_import(request: Request, payload: FinalizeImportRequest) -> JSONResponse:
    """Persists a preview from ANY of the three sources — source-agnostic
    by design (DRY): the preview step already normalized npm/URL/upload
    into the same `content`/`resources` shape.

    The skill is recorded as imported whatever the package claims, so the skill admission policy decides
    whether agents use its exact content (ADR-SEC-01, SEC-SC-04)."""
    manager, ctx = await _build_manager(request)
    name, content = _apply_import_name(payload.content, payload.name)
    _reject_if_builtin_name(name)
    try:
        content = with_imported_source(content)
    except (ValueError, yaml.YAMLError) as e:
        _SUPPLY_LOG.info("supply_chain.skill_import_unreadable", skill=name, error=type(e).__name__)
        raise HTTPException(status_code=400, detail=_IMPORT_FRONTMATTER_UNREADABLE) from e

    try:
        metadata = await manager.create(name, content, payload.category, payload.subcategory)
        for path, resource in payload.resources.items():
            await manager.write_resource(name, path, resource)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    review = await _import_review(request, ctx, name, payload.attestation)
    return JSONResponse(status_code=201, content={**_metadata_to_dict(metadata), "review": review})


# ============================================================================
# Trust of imported skills (ADR-SEC-01: C3.57, C6.34, AD-05)
# ============================================================================


async def _current_bundle(graph: IGraphDBProvider, org_id: str, name: str) -> tuple[str | None, dict[str, bytes]]:
    """The skill's current content and the digest admission would pin for it."""
    source = GraphSkillSource(graph)
    ref = SkillRef(name=name, version=None)
    files = await source.files(org_id, ref)
    digest = await source.content_digest(org_id, ref) if files is not None else None
    return digest, dict(files or {})


async def _import_review(
    request: Request, ctx: dict[str, Any], name: str, attestation: dict[str, Any] | None
) -> dict[str, Any] | None:
    """A new import's standing, once the admission policy has seen what the importer sent with it."""
    config_service = request.app.container.config_service()
    digest, files = await _current_bundle(ctx["graph_provider"], current_org_id(), name)
    if digest is None:
        return None
    trust = SkillTrust(config_service)
    await trust.imported(current_org_id(), name, digest, attestation=attestation, by=ctx["userKey"])
    return await trust.standing(current_org_id(), name, digest, files)
