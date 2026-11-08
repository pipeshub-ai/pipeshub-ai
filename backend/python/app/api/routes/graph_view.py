"""The graph explorer's reads: find a node, expand its neighbours, and revalidate nodes a client kept.

Every node and edge in a response went through ``VisibilityPolicy``. The scope
is the source's own (``code:read`` for code), checked per request because it
depends on the body. Nothing here is stored by a client or a proxy.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.agents.actions.code_graph.ops import RecordAccess
from app.api.connection_limit import groups_outside_limit, ids_outside_connection
from app.api.middlewares.auth import deny_service_tokens, require_scopes
from app.api.routes.code_graph import (
    _caller,  # the code-graph routes' own reading of the caller
    _target,  # and of the repo a request names, with its connected-app limit
)
from app.modules.graph_view.models import (  # noqa: TC001 -- FastAPI reads the annotations at runtime
    ExpandRequest,
    ResolveRequest,
    RevalidateRequest,
)
from app.modules.graph_view.policy import VisibilityPolicy
from app.modules.graph_view.service import NOT_FOUND, GraphViewError, GraphViewService
from app.modules.graph_view.sources.code import CodeSource
from app.telemetry.modules import graph_view_metrics

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "private, no-store"}
UNAVAILABLE = "Graph unavailable."


class _NoStoreRoute(APIRoute):
    """Every answer from this router, refusals and validation errors included, carries ``NO_STORE``."""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Any]]:
        handler = super().get_route_handler()

        async def no_store(request: Request) -> Any:  # noqa: ANN401
            try:
                response = await handler(request)
            except RequestValidationError as exc:
                response = JSONResponse(
                    {"detail": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]}, status_code=422,
                )
            except HTTPException as exc:
                response = JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)
            response.headers.update(NO_STORE)
            return response

        return no_store


router = APIRouter(prefix="/graph", route_class=_NoStoreRoute, dependencies=[Depends(deny_service_tokens)])


async def _run(
    request: Request, operation: str, body: ExpandRequest | ResolveRequest | RevalidateRequest,
    call: Callable[[GraphViewService, RecordAccess, VisibilityPolicy], Awaitable[dict[str, Any]]],
) -> JSONResponse:
    started, outcome, items = time.perf_counter(), "ok", {}
    try:
        graph_provider = await request.app.container.graph_provider()
        org_id, user_id = _caller(request)
        service = GraphViewService({"code": CodeSource(graph_provider, org_id, user_id)})
        await require_scopes(service.scope_for(body.source))(request)
        access = RecordAccess(graph_provider, org_id, user_id)
        policy = VisibilityPolicy(
            graph_provider, org_id, access, include_tests=body.includeTests,
            outside_limit=lambda ids: ids_outside_connection(request, graph_provider, org_id, user_id, ids),
            groups_outside=lambda groups: groups_outside_limit(request, graph_provider, org_id, user_id, groups),
        )
        payload = await call(service, access, policy)
        items = {"nodes": len(payload.get("nodes", ())), "edges": len(payload.get("edges", ())),
                 "ids": len(payload.get("visible", ()))}
        return JSONResponse(payload)
    except GraphViewError as exc:
        outcome = "not_found" if exc.status == 404 else "bad_request"
        raise HTTPException(exc.status, exc.detail) from None
    except HTTPException:
        outcome = "refused"
        raise
    except Exception as exc:  # EntityAccessError, PermissionVerificationUnavailableError, a store error
        # Never an empty graph: "could not check" and "nothing there" need opposite handling.
        # The class only: a store's message can carry ids or query text.
        outcome = "unavailable"
        logger.error("graph view %s failed: %s", operation, type(exc).__name__)
        raise HTTPException(503, UNAVAILABLE) from None
    finally:
        graph_view_metrics.record(operation, outcome, time.perf_counter() - started, **items)


@router.post("/expand")
async def expand(request: Request, body: ExpandRequest) -> JSONResponse:
    return await _run(request, "expand", body, lambda s, access, policy: s.expand(body, access=access, policy=policy))


@router.post("/resolve")
async def resolve(request: Request, body: ResolveRequest) -> JSONResponse:
    async def call(service: GraphViewService, access: RecordAccess, policy: VisibilityPolicy) -> dict[str, Any]:
        graph_provider = await request.app.container.graph_provider()
        org_id, user_id = _caller(request)
        repo = await _target(request, graph_provider, org_id, user_id, body.repo, body.recordId)
        if isinstance(repo, JSONResponse):
            # An ambiguous repo names only repos the caller can read; every other refusal is one answer.
            if repo.status_code == 409:
                raise GraphViewError(409, json.loads(repo.body)["error"])
            raise GraphViewError(404, NOT_FOUND)
        return await service.resolve(body, repo.connector_id, repo.group_id, access=access, policy=policy)

    return await _run(request, "resolve", body, call)


@router.post("/revalidate")
async def revalidate(request: Request, body: RevalidateRequest) -> JSONResponse:
    return await _run(request, "revalidate", body, lambda s, access, policy: s.revalidate(body, policy=policy))
