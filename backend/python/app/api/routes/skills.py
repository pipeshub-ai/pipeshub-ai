"""
Skills Management API Routes (Custom Skills Builder — Phase 2 of the plan)

REST surface over `SkillManager` (see `agents/agent_loop/skills/manager_factory.py`
for how the manager is composed) for the Personal Settings > Skills UI:
CRUD, versioning/rollback, resources, learning-loop candidate review,
search, and the three-source package importer (npm / URL / upload).

Every route uses `build_management_skill_manager`, the creator-scoped
profile (see that factory's docstring) — a user only ever sees/edits their
own skills here, plus org-wide `builtin` ones (read-only in practice: the
underlying store still enforces ownership on write, so a non-owner attempt
to edit a builtin surfaces as a 404/403 from the store's `RegistryError`,
never silently succeeds).

Authorization: `SKILL_READ`/`SKILL_WRITE` OAuth scopes (mirrors every other
resource in this service — see `AGENT_READ`/`AGENT_WRITE` in `agent.py`).
Safe-delete additionally checks REFERENTIAL integrity against
`AGENT_HAS_SKILL` (agents using this skill) and `agentSkillRelation`
`requires` edges (other skills depending on this one) — see `_check_usage`.
"""

from __future__ import annotations

from logging import Logger
from typing import Any

import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from app.agent_loop_lib.core.exceptions import RegistryError
from app.agent_loop_lib.modules.providers.skills.base import (
    Skill,
    SkillFilter,
    SkillMetadata,
    SkillSource,
    SkillStatus,
)
from app.agent_loop_lib.modules.providers.skills.loader import render_skill_md
from app.agent_loop_lib.modules.providers.skills.manager import SkillManager
from app.agent_loop_lib.modules.providers.skills.validator import SkillFormatError
from app.agents.agent_loop.skills.manager_factory import build_management_skill_manager
from app.api.middlewares.auth import require_scopes
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.skills.npm_command_parser import NpmCommandParseError, parse_npm_command
from app.services.skills.package_importer import ImportPreview, PackageImportError, SkillPackageImporter
from app.telemetry.identity import domain_from_email

router = APIRouter()

_SKILLS = CollectionNames.AGENT_SKILLS.value
_AGENT_HAS_SKILL = CollectionNames.AGENT_HAS_SKILL.value
_AGENT_SKILL_RELATION = CollectionNames.AGENT_SKILL_RELATION.value
_AGENT_INSTANCES = CollectionNames.AGENT_INSTANCES.value


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


def _build_content(payload: SkillWriteRequest, *, name: str) -> str:
    """Structured form fields -> a full, spec-compliant SKILL.md string.
    The one place a `SkillWriteRequest` becomes YAML frontmatter — mirrors
    `SkillMetadata.to_frontmatter_dict`'s shape exactly so validation
    (`SkillValidator`, invoked by the store on every create/update) sees
    the same document a hand-authored SKILL.md would produce."""
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
        source=SkillSource.MANUAL,
    )
    skill = Skill(metadata=metadata, body=payload.body)
    return render_skill_md(skill)


def _handle_registry_error(e: RegistryError) -> HTTPException:
    message = str(e)
    status_code = 404 if "not found" in message.lower() else 409
    return HTTPException(status_code=status_code, detail=message)


def _handle_format_error(e: SkillFormatError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


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
    manager, _ctx = await _build_manager(request)
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
        skill = await manager.activate_skill(name)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    return JSONResponse(status_code=200, content=_skill_to_dict(skill))


@router.get("/{name}/export", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def export_skill(request: Request, name: str) -> PlainTextResponse:
    manager, _ctx = await _build_manager(request)
    try:
        skill = await manager.activate_skill(name)
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
async def update_skill(request: Request, name: str, payload: SkillWriteRequest) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    content = _build_content(payload, name=name)
    try:
        metadata = await manager.update(name, content)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    return JSONResponse(status_code=200, content=_metadata_to_dict(metadata))


@router.patch("/{name}/body", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def patch_skill_body(request: Request, name: str, payload: PatchBodyRequest) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    ok = await manager.patch(name, payload.old_string, payload.new_string)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Patch failed — the skill doesn't exist, or 'old_string' wasn't found exactly once in its body.",
        )
    return JSONResponse(status_code=200, content={"status": "success"})


@router.post("/{name}/deprecate", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def deprecate_skill(request: Request, name: str, payload: DeprecateRequest) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
    ok = await manager.deprecate(name, payload.reason, payload.replaced_by)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill {name!r} not found.")
    return JSONResponse(status_code=200, content={"status": "success"})


# ============================================================================
# Safe delete
# ============================================================================

def _edge_source_key(edge: dict[str, Any]) -> str:
    """Extract an edge's source-node bare key, regardless of graph provider
    shape: ArangoDB's `get_edges_to_node` returns raw documents with a full
    `_from` id (`"collection/key"`), while Neo4j's returns a generic
    `from_id` that is already the bare key (see `Neo4jProvider.get_edges_to_node`).
    Returns "" if neither field is present/non-empty."""
    from_id = edge.get("from_id")
    if from_id:
        return str(from_id)
    return (edge.get("_from") or "").split("/", 1)[-1]


async def _check_usage(name: str, org_id: str, graph_provider: IGraphDBProvider) -> dict[str, Any]:
    """Referential-integrity check for `DELETE /{name}` — who's using this
    skill right now. Two independent edges: `AGENT_HAS_SKILL` (an agent was
    explicitly assigned this skill in Agent Builder) and `agentSkillRelation`
    `type == "requires"` (another skill's frontmatter declares it depends on
    this one). Either non-empty blocks a plain delete (see `delete_skill`
    below)."""
    skill_full_id = f"{_SKILLS}/{org_id}_{name}"

    agent_edges = await graph_provider.get_edges_to_node(skill_full_id, _AGENT_HAS_SKILL)
    used_by_agents: list[dict[str, Any]] = []
    for edge in agent_edges:
        agent_id = _edge_source_key(edge)
        if not agent_id:
            continue
        agent_doc = await graph_provider.get_document(agent_id, _AGENT_INSTANCES)
        if agent_doc:
            used_by_agents.append({"id": agent_id, "name": agent_doc.get("name", agent_id)})

    relation_edges = await graph_provider.get_edges_to_node(skill_full_id, _AGENT_SKILL_RELATION)
    required_by_skills = sorted({
        _edge_source_key(edge).removeprefix(f"{org_id}_")
        for edge in relation_edges
        if edge.get("type") == "requires" and _edge_source_key(edge)
    })

    return {"usedByAgents": used_by_agents, "requiredBySkills": required_by_skills}


@router.get("/{name}/usage", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_READ))])
async def get_skill_usage(request: Request, name: str) -> JSONResponse:
    _manager, ctx = await _build_manager(request)
    usage = await _check_usage(name, ctx["orgId"], ctx["graph_provider"])
    return JSONResponse(status_code=200, content=usage)


@router.delete("/{name}", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def delete_skill(request: Request, name: str, detach: bool = Query(False)) -> JSONResponse:
    """Refuses to delete a skill that's in use (409, with structured
    `usedByAgents`/`requiredBySkills`) unless `detach=true` — and even
    then, a skill another skill `requires` can NEVER be force-deleted
    (only deprecated: `requires` is a content dependency, detaching it
    would leave the dependent skill's instructions pointing at nothing).
    `detach=true` removes the `AGENT_HAS_SKILL` edges from any assigned
    agents before deleting, after the frontend has shown the user exactly
    which agents those are (via `GET /{name}/usage`) and gotten explicit
    confirmation."""
    manager, ctx = await _build_manager(request)
    usage = await _check_usage(name, ctx["orgId"], ctx["graph_provider"])

    if usage["requiredBySkills"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Skill {name!r} is required by other skill(s) and cannot be deleted. "
                    "Deprecate it instead so dependents still resolve."
                ),
                **usage,
            },
        )
    if usage["usedByAgents"] and not detach:
        raise HTTPException(
            status_code=409,
            detail={
                "message": (
                    f"Skill {name!r} is assigned to {len(usage['usedByAgents'])} agent(s). "
                    "Retry with detach=true to unassign it from them first, or deprecate it instead."
                ),
                **usage,
            },
        )

    if usage["usedByAgents"] and detach:
        skill_full_id = f"{_SKILLS}/{ctx['orgId']}_{name}"
        edges_to_delete = [
            {
                "from_id": agent["id"], "from_collection": _AGENT_INSTANCES,
                "to_id": f"{ctx['orgId']}_{name}", "to_collection": _SKILLS,
            }
            for agent in usage["usedByAgents"]
        ]
        await ctx["graph_provider"].batch_delete_edges(edges_to_delete, _AGENT_HAS_SKILL)
        ctx["logger"].info(f"Detached skill {name!r} from {len(edges_to_delete)} agent(s) before delete: {skill_full_id}")

    ok = await manager.delete(name)
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
    ok = await manager.write_resource(name, payload.path, payload.content)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Skill {name!r} not found.")
    return JSONResponse(status_code=200, content={"status": "success"})


@router.delete("/{name}/resource", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def remove_resource(request: Request, name: str, path: str = Query(..., min_length=1)) -> JSONResponse:
    manager, _ctx = await _build_manager(request)
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
        preview = await SkillPackageImporter().preview_npm(spec)
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(preview))


@router.post("/import/url/preview", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def preview_url_import(request: Request, payload: UrlImportRequest) -> JSONResponse:
    _manager, _ctx = await _build_manager(request)
    try:
        preview = await SkillPackageImporter().preview_url(payload.url)
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(preview))


@router.post("/import/upload/preview", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def preview_upload_import(request: Request, file: UploadFile = File(...)) -> JSONResponse:
    _manager, _ctx = await _build_manager(request)
    data = await file.read()
    try:
        preview = SkillPackageImporter().preview_upload(file.filename or "upload.zip", data)
    except PackageImportError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(status_code=200, content=_preview_to_dict(preview))


@router.post("/import/finalize", dependencies=[Depends(require_scopes(OAuthScopes.SKILL_WRITE))])
async def finalize_import(request: Request, payload: FinalizeImportRequest) -> JSONResponse:
    """Persists a preview from ANY of the three sources — source-agnostic
    by design (DRY): the preview step already normalized npm/URL/upload
    into the same `content`/`resources` shape."""
    manager, _ctx = await _build_manager(request)
    try:
        frontmatter = yaml.safe_load(payload.content.split("---", 2)[1]) if payload.content.startswith("---") else {}
        name = (frontmatter or {}).get("name")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read 'name' from the imported SKILL.md: {e}") from e
    if not name:
        raise HTTPException(status_code=400, detail="Imported SKILL.md is missing a 'name' field.")

    try:
        metadata = await manager.create(name, payload.content, payload.category, payload.subcategory)
        for path, content in payload.resources.items():
            await manager.write_resource(name, path, content)
    except RegistryError as e:
        raise _handle_registry_error(e) from e
    except SkillFormatError as e:
        raise _handle_format_error(e) from e
    return JSONResponse(status_code=201, content=_metadata_to_dict(metadata))
