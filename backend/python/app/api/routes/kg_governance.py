"""EE-only knowledge-graph governance API (KG Clean Rebuild plan, Phase 7 /
Part E governance: "merge, list_suggestions, promote_type, deprecate_type").

Every route requires both an admin caller (:func:`require_admin`) and the
``ENABLE_KG_GOVERNANCE`` feature flag (:func:`_require_governance_enabled`,
which 404s rather than 403s when the flag is off — CE deployments should see
these routes as if they don't exist, not as a permission wall someone might
probe). See ``app.services.featureflag`` for the flag mechanism.

Business logic lives entirely in ``app.modules.knowledge_graph.governance.*``
— this module is routing/validation/auth glue only, per CLAUDE.md's single-
responsibility guidance.
"""

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.middlewares.admin import require_admin
from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.modules.knowledge_graph.contracts.ontology import OntologyStatus
from app.modules.knowledge_graph.governance.merge import EntityMergeService, MergeError
from app.modules.knowledge_graph.governance.ontology_store import (
    OntologyGovernanceError,
    OntologyRegistryStore,
)
from app.modules.knowledge_graph.governance.suggestions import MergeSuggestionStore
from app.modules.knowledge_graph.indexing.temporal import NodeRef
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.featureflag import FeatureFlagService

router = APIRouter(
    prefix="/api/v1/admin/kg-governance",
    tags=["Knowledge Graph Governance (EE)"],
    dependencies=[
        Depends(require_scopes(OAuthScopes.KG_GOVERNANCE)),
        Depends(require_admin),
    ],
)


async def _require_governance_enabled() -> None:
    if not FeatureFlagService.get_service().is_feature_enabled(CONFIG.ENABLE_KG_GOVERNANCE, default=False):
        raise HTTPException(status_code=404, detail="Not found")


async def _get_services(request: Request) -> dict[str, Any]:
    container = request.app.container
    logger = container.logger()
    graph_provider = None
    if hasattr(request.app.state, "graph_provider"):
        graph_provider = request.app.state.graph_provider
    if graph_provider is None and hasattr(container, "graph_provider"):
        graph_provider = await container.graph_provider()
    return {"logger": logger, "graph_provider": graph_provider}


def _require_org_and_user(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "user", {})
    org_id = user.get("orgId", "")
    user_id = user.get("userId") or request.headers.get("X-User-Id") or ""
    if not org_id:
        raise HTTPException(status_code=401, detail="Authenticated org_id required")
    return org_id, user_id


# ----------------------------------------------------------------------
# Merge
# ----------------------------------------------------------------------

class MergeRequest(BaseModel):
    survivor_node_id: str
    survivor_collection: str
    duplicate_node_id: str
    duplicate_collection: str
    reason: str = ""


@router.post(
    "/merge",
    summary="Merge a duplicate entity node into a survivor",
    dependencies=[Depends(_require_governance_enabled)],
)
async def merge_entities(request: Request, body: MergeRequest) -> JSONResponse:
    org_id, user_id = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")

    service = EntityMergeService(services["graph_provider"], services["logger"])
    try:
        outcome = await service.merge(
            org_id,
            NodeRef(body.survivor_node_id, body.survivor_collection),
            NodeRef(body.duplicate_node_id, body.duplicate_collection),
            reason=body.reason,
            merged_by=user_id,
        )
    except MergeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        services["logger"].error("kg_governance.merge failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Merge failed: {exc}") from exc
    return JSONResponse(content={"status": "success", "data": outcome})


# ----------------------------------------------------------------------
# Merge suggestions (ambiguous LLM adjudications awaiting review)
# ----------------------------------------------------------------------

class ResolveSuggestionRequest(BaseModel):
    outcome: Literal["approved", "rejected"]
    duplicate_node_id: str | None = Field(
        default=None,
        description=(
            "Optional, only used when outcome='approved': if the ambiguous "
            "mention already resolved to a canonical graph node elsewhere "
            "(e.g. as a new distinct entity), pass its id + collection here "
            "and this call also merges it into the suggestion's "
            "candidateNodeId in one step. A suggestion's own localId is a "
            "document-scoped extraction id, not a graph node id, so it "
            "can't be merged directly — reviewing without this still "
            "records the decision for audit."
        ),
    )
    duplicate_collection: str | None = None


@router.get(
    "/suggestions",
    summary="List pending (or reviewed) LLM merge-adjudication suggestions",
    dependencies=[Depends(_require_governance_enabled)],
)
async def list_suggestions(
    request: Request,
    status: str | None = Query("pending", description="pending | approved | rejected | omit for all"),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    org_id, _ = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")

    store = MergeSuggestionStore(services["graph_provider"], services["logger"])
    suggestions = await store.list_suggestions(org_id, status=status, limit=limit)
    return JSONResponse(content={"status": "success", "data": suggestions})


@router.post(
    "/suggestions/{suggestion_id}/resolve",
    summary="Approve (merge) or reject a pending suggestion",
    dependencies=[Depends(_require_governance_enabled)],
)
async def resolve_suggestion(
    suggestion_id: str, request: Request, body: ResolveSuggestionRequest,
) -> JSONResponse:
    org_id, user_id = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")
    logger = services["logger"]
    graph_provider = services["graph_provider"]

    store = MergeSuggestionStore(graph_provider, logger)
    suggestion = await store.resolve(org_id, suggestion_id, body.outcome, resolved_by=user_id)
    if suggestion is None:
        raise HTTPException(status_code=404, detail="Suggestion not found for this organisation.")

    merge_outcome = None
    if body.outcome == "approved" and body.duplicate_node_id and body.duplicate_collection:
        candidate_id = suggestion.get("candidateNodeId")
        candidate_collection = suggestion.get("entityType") or body.duplicate_collection
        try:
            service = EntityMergeService(graph_provider, logger)
            merge_outcome = await service.merge(
                org_id,
                NodeRef(candidate_id, candidate_collection),
                NodeRef(body.duplicate_node_id, body.duplicate_collection),
                reason="approved merge suggestion",
                merged_by=user_id,
            )
        except MergeError as exc:
            logger.info("resolve_suggestion: merge skipped (%s)", exc)

    return JSONResponse(
        content={"status": "success", "data": {"suggestion": suggestion, "merge": merge_outcome}}
    )


# ----------------------------------------------------------------------
# Ontology promote / deprecate
# ----------------------------------------------------------------------

class PromoteTypeRequest(BaseModel):
    domain: str
    type_name: str
    description: str = ""
    ontology_id: str | None = None


class DeprecateTypeRequest(BaseModel):
    ontology_id: str
    type_name: str


class UpdateOntologyStatusRequest(BaseModel):
    status: OntologyStatus


@router.get(
    "/ontology",
    summary="List this organisation's ontology definitions",
    dependencies=[Depends(_require_governance_enabled)],
)
async def list_ontologies(request: Request) -> JSONResponse:
    org_id, _ = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")
    store = OntologyRegistryStore(services["graph_provider"], services["logger"])
    definitions = await store.list_ontologies(org_id)
    return JSONResponse(
        content={"status": "success", "data": [d.model_dump(mode="json") for d in definitions]}
    )


@router.post(
    "/ontology/promote-type",
    summary="Promote a novel/schema-free type into a closed ontology type",
    dependencies=[Depends(_require_governance_enabled)],
)
async def promote_type(request: Request, body: PromoteTypeRequest) -> JSONResponse:
    org_id, _ = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")
    store = OntologyRegistryStore(services["graph_provider"], services["logger"])
    try:
        definition = await store.promote_type(
            org_id, body.domain, body.type_name,
            description=body.description, ontology_id=body.ontology_id,
        )
    except OntologyGovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"status": "success", "data": definition.model_dump(mode="json")})


@router.post(
    "/ontology/deprecate-type",
    summary="Remove a type from an ontology's active type list",
    dependencies=[Depends(_require_governance_enabled)],
)
async def deprecate_type(request: Request, body: DeprecateTypeRequest) -> JSONResponse:
    org_id, _ = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")
    store = OntologyRegistryStore(services["graph_provider"], services["logger"])
    try:
        definition = await store.deprecate_type(org_id, body.ontology_id, body.type_name)
    except OntologyGovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"status": "success", "data": definition.model_dump(mode="json")})


@router.post(
    "/ontology/{ontology_id}/status",
    summary="Activate/deprecate a whole ontology (e.g. draft -> active)",
    dependencies=[Depends(_require_governance_enabled)],
)
async def update_ontology_status(
    ontology_id: str, request: Request, body: UpdateOntologyStatusRequest,
) -> JSONResponse:
    org_id, _ = _require_org_and_user(request)
    services = await _get_services(request)
    if services["graph_provider"] is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")
    store = OntologyRegistryStore(services["graph_provider"], services["logger"])
    try:
        definition = await store.update_status(org_id, ontology_id, body.status)
    except OntologyGovernanceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(content={"status": "success", "data": definition.model_dump(mode="json")})
