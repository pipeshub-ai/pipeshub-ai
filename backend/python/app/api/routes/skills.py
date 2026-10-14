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
custom skill is invisible (404) because the store's `visibility_scope`
filters on `createdBy`.

Authorization: `SKILL_READ`/`SKILL_WRITE` OAuth scopes (mirrors every other
resource in this service — see `AGENT_READ`/`AGENT_WRITE` in `agent.py`).
Safe-delete additionally checks REFERENTIAL integrity against
`AGENT_HAS_SKILL` (agents using this skill) and `agentSkillRelation`
`requires` edges (other skills depending on this one) — see
`SkillManager.delete` / `collect_referential_usage`.
"""

from __future__ import annotations

from logging import Logger
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillConflictError,
    SkillFilter,
    SkillInUseError,
    SkillMetadata,
    SkillSource,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.loader import render_skill_md
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError, SkillValidator
from app.agents.agent_loop.skills.manager_factory import (
    build_management_skill_manager,
    get_builtin_seeder,
    sync_builtin_skills,
)
from app.agents.agent_loop.skills.graph_store import collect_referential_usage, edge_source_key
from app.api.middlewares.auth import require_scopes
from app.api.middlewares.caller_role import fetch_caller_role
from app.config.constants.service import OAuthScopes
from app.services.featureflag.platform_settings import is_skills_enabled
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.skills.npm_command_parser import (
    CatalogSpec,
    NpmCommandParseError,
    UrlSpec,
    parse_npm_command,
)
from app.services.skills.package_importer import (
    ImportPreview,
    PackageImportError,
    SkillPackageImporter,
)
from app.telemetry.identity import domain_from_email


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


class DeprecateRequest(BaseModel):
    reason: str = Field(..., min_length=1)
    replaced_by: str | None = None


class RollbackRequest(BaseModel):
    version: str = Field(..., min_length=1)


class PatchBodyRequest(BaseModel):
    old_string: str
    new_string: str


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
    )
    skill = Skill(metadata=metadata, body=payload.body)
    return render_skill_md(skill)


def _handle_registry_error(e: RegistryError) -> HTTPException:
    message = str(e)
    status_code = 404 if "not found" in message.lower() else 409
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
        raise HTTPException(
            status_code=400,
            detail=f"Could not read 'name' from the imported SKILL.md: {e}",
        ) from e

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
    creator `visibility_scope`, so a co-worker's custom skill is
    indistinguishable from a missing one (404), while org-wide builtins
    remain loadable by every member. Uses `get_skill` (not
    `activate_skill`) so a disabled skill can still be inspected,
    updated, or re-enabled."""
    try:
        skill = await manager.get_skill(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return skill.metadata


async def _reject_if_builtin_skill(manager: SkillManager, name: str) -> SkillMetadata:
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
    org_id = user.get("orgId")
    if not user_id or not org_id:
        raise HTTPException(status_code=401, detail="Authentication required. Please provide valid credentials.")
    return {"userId": user_id, "orgId": org_id, "email": user.get("email"), "domain": domain_from_email(user.get("email"))}


async def _get_user_key(user_id: str, graph_provider: IGraphDBProvider, logger: Logger) -> str:
    try:
        user = await graph_provider.get_user_by_user_id(user_id)
        if not user or not isinstance(user, dict):
            raise HTTPException(status_code=404, detail="User not found")
        return user["_key"]
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"skills: failed to resolve user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve user information") from e


async def _build_manager(request: Request) -> tuple[SkillManager, dict[str, Any]]:
    services = await _get_services(request)
    user_context = _get_user_context(request)
    user_key = await _get_user_key(user_context["userId"], services["graph_provider"], services["logger"])
    manager = await build_management_skill_manager(
        services["graph_provider"], user_context["orgId"], user_key, services["retrieval_service"],
    )
    return manager, {**services, **user_context, "userKey": user_key}


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
) -> JSONResponse:
    manager, ctx = await _build_manager(request)
    # Only the read/list route seeds builtins (write routes stay pure): a
    # fresh org otherwise sees no builtin skills until its first chat, since
    # seeding is normally a runtime (agent-turn) side effect — see
    # `manager_factory.build_runtime_skill_manager`. Version-gated and
    # idempotent, so this is a no-op once the org's catalog is current.
    await sync_builtin_skills(ctx["graph_provider"], ctx["orgId"], manager)
    filt = SkillFilter(
        query=q,
        category=category,
        subcategory=subcategory,
        tags=[tag] if tag else None,
        status=SkillStatus(status) if status else None,
        source=SkillSource(source) if source else None,
    )
    metadatas = await manager.list_skills(filt)
    return JSONResponse(status_code=200, content={"skills": [_metadata_to_dict(m) for m in metadatas]})


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
    manager, _ctx = await _build_manager(request)
    try:
        skill = await manager.get_skill(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_skill_to_dict(skill))


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
        metadata = await manager.create(name, content, payload.category, payload.subcategory)
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
    manager, _ctx = await _build_manager(request)
    existing_metadata = await _reject_if_builtin_skill(manager, name)
    content = _build_content(payload, name=name, existing=existing_metadata)
    try:
        metadata = await manager.update(
            name, content, expected_updated_at=_parse_if_match(if_match),
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
    manager, _ctx = await _build_manager(request)
    await _reject_if_builtin_skill(manager, name)
    try:
        ok = await manager.patch(
            name, payload.old_string, payload.new_string,
            expected_updated_at=_parse_if_match(if_match),
        )
    except SkillConflictError as e:
        raise _handle_conflict_error(e) from e
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Patch failed — the skill doesn't exist, or 'old_string' wasn't found exactly once in its body.",
        )
    return JSONResponse(status_code=200, content={"status": "success"})


@router.post("/{name}/deprecate", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def deprecate_skill(request: Request, name: str, payload: DeprecateRequest) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    await _reject_if_builtin_skill(manager, name)
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
    manager, _ctx = await _build_manager(request)
    existing = await _load_skill_metadata(manager, name)
    await _require_admin_for_builtin_availability(request, existing)
    try:
        metadata = await manager.disable(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


@router.post("/{name}/enable", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def enable_skill(request: Request, name: str) -> JSONResponse:
    """Reverses `disable`. Never undeprecates — see `SkillManager.enable`.
    Same authorization as `disable_skill`."""
    manager, _ctx = await _build_manager(request)
    existing = await _load_skill_metadata(manager, name)
    await _require_admin_for_builtin_availability(request, existing)
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
    usage = await _check_usage(name, ctx["orgId"], ctx["graph_provider"])
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
    manager, _ctx = await _build_manager(request)
    await _reject_if_builtin_skill(manager, name)
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
    manager, _ctx = await _build_manager(request)
    try:
        versions = await manager.list_versions(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(
        status_code=200,
        content={"versions": [
            {"version": v.version, "updatedBy": v.updated_by, "createdAt": v.created_at, "summary": v.summary}
            for v in versions
        ]},
    )


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
    manager, _ctx = await _build_manager(request)
    await _reject_if_builtin_skill(manager, name)
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
    manager, _ctx = await _build_manager(request)
    await _reject_if_builtin_skill(manager, name)
    try:
        ok = await manager.write_resource(name, payload.path, payload.content)
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill {name!r} not found.")
    return JSONResponse(status_code=200, content={"status": "success"})


@router.delete("/{name}/resource", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def remove_resource(request: Request, name: str, path: str = Query(..., min_length=1)) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    await _reject_if_builtin_skill(manager, name)
    ok = await manager.remove_resource(name, path)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Resource {path!r} not found for skill {name!r}.")
    return JSONResponse(status_code=200, content={"status": "success"})


# ============================================================================
# Learning-loop candidates (governance review queue)
# ============================================================================

@router.get("/candidates/pending", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_pending_candidates(request: Request) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    candidates = await manager.get_pending_candidates()
    return JSONResponse(status_code=200, content={"candidates": [c.model_dump(mode="json") for c in candidates]})


@router.post("/candidates/{candidate_id}/approve", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def approve_candidate(request: Request, candidate_id: str) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    try:
        metadata = await manager.approve_candidate(candidate_id)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


@router.post("/candidates/{candidate_id}/reject", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def reject_candidate(request: Request, candidate_id: str) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
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
    data = await file.read()
    try:
        preview = SkillPackageImporter().preview_upload(file.filename or "upload.zip", data)
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(_annotate_reserved_import_name(preview)))


@router.post("/import/finalize", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def finalize_import(request: Request, payload: FinalizeImportRequest) -> JSONResponse:
    """Persists a preview from ANY of the three sources — source-agnostic
    by design (DRY): the preview step already normalized npm/URL/upload
    into the same `content`/`resources` shape."""
    manager, _ctx = await _build_manager(request)
    name, content = _apply_import_name(payload.content, payload.name)
    _reject_if_builtin_name(name)

    try:
        metadata = await manager.create(name, content, payload.category, payload.subcategory)
        for path, resource in payload.resources.items():
            await manager.write_resource(name, path, resource)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    return JSONResponse(status_code=201, content=_metadata_to_dict(metadata))
