"""Code-graph reads over HTTP, for coding agents outside PipesHub (the MCP server).

The same four reads the in-product agent has (``app/agents/actions/code_graph``)
plus a repo listing. Callers name a repo (``acme/shop``) instead of a
connector id, and every result is checked against the caller's access to the
owning record, exactly as in-product.
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.agents.actions.code_graph.code_graph import CodeGraph
from app.agents.actions.code_graph.neighbours import DEFAULT_NEIGHBOR_LIMIT
from app.agents.actions.code_graph.path import DEFAULT_MAX_DEPTH
from app.agents.actions.code_graph.query import DEFAULT_QUERY_LIMIT
from app.agents.actions.code_graph.repos import (
    CodeRepo,
    RepoNotFoundError,
    ambiguous_repo_message,
    list_code_repos,
    matching_code_repos,
    repo_for_record,
    repo_not_found_message,
)
from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.modules.code_graph.build_state import BuildStateStore, graph_status
from app.modules.transformers.blob_storage import BlobStorage

if TYPE_CHECKING:
    from app.containers.query import QueryAppContainer
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

ToolCall = Callable[[CodeGraph, CodeRepo], Awaitable[tuple[bool, str]]]

router = APIRouter(
    prefix="/code-graph",
    dependencies=[Depends(require_scopes(OAuthScopes.CODE_READ))],
)

# Claude Code warns on a tool result past 10k tokens and cuts it at 25k; at
# ~4 characters a token this keeps a result near 8k.
MAX_RESULT_CHARS = 32_000


class RepoRef(BaseModel):
    repo: str | None = Field(default=None, description="owner/name, name, or <connector>:owner/name")
    include_tests: bool = False


class QueryBody(RepoRef):
    select: str
    kinds: list[str] | None = None
    limit: int = Field(default=DEFAULT_QUERY_LIMIT, ge=1, le=500)


class NeighboursBody(RepoRef):
    file_path: str | None = None
    record_id: str | None = None
    qualified_name: str | None = None
    direction: Literal["inbound", "outbound", "any"] = "any"
    edge_types: list[str] | None = None
    depth: int = Field(default=1, ge=1, le=3)
    limit: int = Field(default=DEFAULT_NEIGHBOR_LIMIT, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ReadBody(RepoRef):
    file_path: str | None = None
    record_id: str | None = None
    qualified_name: str | None = None
    lines: str | None = None
    max_lines: int | None = Field(default=None, ge=1, le=2000)


class PathBody(RepoRef):
    file_path_a: str
    qualified_name_a: str
    file_path_b: str
    qualified_name_b: str
    max_depth: int = Field(default=DEFAULT_MAX_DEPTH, ge=1, le=8)
    edge_types: list[str] | None = None
    undirected: bool = False


class SearchBody(RepoRef):
    pattern: str = Field(min_length=1, max_length=200)
    mode: Literal["identifier", "text", "regex"] = "identifier"
    path: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class OutlineBody(RepoRef):
    file_path: str | None = None
    record_id: str | None = None


class ImpactBody(RepoRef):
    file_path: str | None = None
    record_id: str | None = None
    qualified_name: str | None = None
    depth: int = Field(default=2, ge=1, le=3)


class EntryPointsBody(RepoRef):
    limit: int = Field(default=30, ge=1, le=200)


def _caller(request: Request) -> tuple[str, str]:
    user = request.state.user
    return user.get("orgId") or "", user.get("userId") or ""


def fit_to_budget(result: dict[str, Any], max_chars: int = MAX_RESULT_CHARS) -> dict[str, Any]:
    """Drop trailing rows from the longest list, then clip the longest string, until it fits.

    The tools already page and cap; this is the last guard that keeps one
    answer inside an MCP client's tool-result limit. What was cut is stated.
    """
    if len(json.dumps(result, default=str)) <= max_chars:
        return result
    out = dict(result)
    lists = [k for k, v in out.items() if isinstance(v, list) and v]
    if lists:
        key = max(lists, key=lambda k: len(json.dumps(out[k], default=str)))
        rows = list(out[key])
        dropped = 0
        while rows and len(json.dumps({**out, key: rows}, default=str)) > max_chars:
            cut = max(1, len(rows) // 4)
            rows, dropped = rows[:-cut], dropped + cut
        out[key] = rows
        out["truncated"] = True
        out["omitted_for_size"] = {key: dropped}
        if len(json.dumps(out, default=str)) <= max_chars:
            return out
    strings = [k for k, v in out.items() if isinstance(v, str)]
    if strings:
        key = max(strings, key=lambda k: len(out[k]))
        over = len(json.dumps(out, default=str)) - max_chars
        keep = max(0, len(out[key]) - over - 200)
        out[key] = out[key][:keep] + "\n… [cut to fit the result size; read a narrower `lines` range]"
        out["truncated"] = True
    return out


def _response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(fit_to_budget(payload), status_code=status_code)


async def _deps(request: Request) -> tuple["QueryAppContainer", "IGraphDBProvider", str, str]:
    container: QueryAppContainer = request.app.container
    graph_provider = await container.graph_provider()
    org_id, user_id = _caller(request)
    return container, graph_provider, org_id, user_id


async def _target(
    graph_provider: "IGraphDBProvider", org_id: str, user_id: str, repo: str | None, record_id: str | None = None,
) -> CodeRepo | JSONResponse:
    """The repo a request names, or the 404/409 that says why there is none.

    The messages are built from the request, not from an exception.
    """
    if not repo and record_id:
        try:
            return await repo_for_record(graph_provider, org_id, user_id, record_id)
        except RepoNotFoundError:
            return _response({"error": repo_not_found_message(repo, record_id)}, 404)
    matches = await matching_code_repos(graph_provider, org_id, user_id, repo or "")
    if not matches:
        return _response({"error": repo_not_found_message(repo, record_id)}, 404)
    if len(matches) > 1:
        names = sorted(r.qualified for r in matches)
        return _response({"error": ambiguous_repo_message(repo or "", names), "repos": names}, 409)
    return matches[0]


def _toolset(
    container: "QueryAppContainer", graph_provider: "IGraphDBProvider",
    org_id: str, user_id: str, repo: CodeRepo,
) -> CodeGraph:
    logger = container.logger()
    return CodeGraph({  # type: ignore[arg-type]
        "graph_provider": graph_provider,
        "org_id": org_id,
        "user_id": user_id,
        "logger": logger,
        "apps": [repo.connector_id],
        "blob_store": BlobStorage(
            logger=logger, config_service=container.config_service(), graph_provider=graph_provider,
        ),
    })


async def _run(request: Request, body: RepoRef, record_id: str | None, call: ToolCall) -> JSONResponse:
    container, graph_provider, org_id, user_id = await _deps(request)
    try:
        repo = await _target(graph_provider, org_id, user_id, body.repo, record_id)
    except Exception:
        container.logger().exception("code-graph repo lookup failed")
        return _response({"error": "graph unavailable"}, 503)
    if isinstance(repo, JSONResponse):
        return repo
    try:
        toolset = _toolset(container, graph_provider, org_id, user_id, repo)
        _ok, raw = await call(toolset, repo)
        result = json.loads(raw)
    except Exception:
        container.logger().exception("code-graph tool call failed")
        return _response({"error": "graph unavailable"}, 503)
    if isinstance(result, dict) and "error" not in result:
        result.setdefault("repo", repo.name)
    return _response(result if isinstance(result, dict) else {"result": result})


@router.get("/repos")
async def code_repos(request: Request) -> JSONResponse:
    container, graph_provider, org_id, user_id = await _deps(request)
    try:
        repos = await list_code_repos(graph_provider, org_id, user_id)
    except Exception:
        container.logger().exception("code-graph repo listing failed")
        return _response({"error": "graph unavailable"}, 503)
    names = [r.name for r in repos]
    store = BuildStateStore(graph_provider)
    out = []
    for r in repos:
        entry: dict[str, Any] = {
            "repo": r.qualified if names.count(r.name) > 1 else r.name,
            "source": r.connector_name,
            # Lets a connector's page show only its own repos.
            "connector_id": r.connector_id,
        }
        try:
            state = await store.read(org_id, r.group_id)
        except Exception:
            container.logger().warning("code-graph build state unreadable for %s", r.group_id,
                                       exc_info=True)
            entry["graph_status"] = "unknown"
        else:
            entry["graph_status"] = graph_status(state).value
            if state is not None and state.last_build is not None:
                entry["last_built_at"] = state.last_build
            if state is not None and state.last_error:
                entry["last_error"] = state.last_error
        out.append(entry)
    return _response({"repos": out})


@router.post("/query")
async def code_query(request: Request, body: QueryBody) -> JSONResponse:
    return await _run(request, body, None, lambda t, repo: t.query_code_graph(
        connector_id=repo.connector_id, select=body.select, kinds=body.kinds,
        limit=body.limit, include_tests=body.include_tests, repo=repo.group_id,
    ))


@router.post("/neighbours")
async def code_neighbours(request: Request, body: NeighboursBody) -> JSONResponse:
    return await _run(request, body, body.record_id, lambda t, repo: t.get_neighbour(
        connector_id=repo.connector_id, file_path=body.file_path, record_id=body.record_id,
        qualified_name=body.qualified_name, direction=body.direction,
        edge_types=body.edge_types, depth=body.depth, limit=body.limit, offset=body.offset,
        include_tests=body.include_tests, repo=repo.group_id,
    ))


@router.post("/read")
async def code_read(request: Request, body: ReadBody) -> JSONResponse:
    return await _run(request, body, body.record_id, lambda t, repo: t.read_code(
        connector_id=repo.connector_id, file_path=body.file_path, record_id=body.record_id,
        qualified_name=body.qualified_name, lines=body.lines, max_lines=body.max_lines,
        include_tests=body.include_tests, repo=repo.group_id,
    ))


@router.post("/path")
async def code_path(request: Request, body: PathBody) -> JSONResponse:
    return await _run(request, body, None, lambda t, repo: t.find_symbol_path(
        connector_id=repo.connector_id, file_path_a=body.file_path_a,
        qualified_name_a=body.qualified_name_a, file_path_b=body.file_path_b,
        qualified_name_b=body.qualified_name_b, max_depth=body.max_depth,
        edge_types=body.edge_types, include_tests=body.include_tests,
        undirected=body.undirected, repo=repo.group_id,
    ))


@router.post("/search")
async def code_search(request: Request, body: SearchBody) -> JSONResponse:
    return await _run(request, body, None, lambda t, repo: t.search_code(
        connector_id=repo.connector_id, pattern=body.pattern, mode=body.mode, path=body.path,
        limit=body.limit, include_tests=body.include_tests, repo=repo.group_id,
    ))


@router.post("/outline")
async def code_outline(request: Request, body: OutlineBody) -> JSONResponse:
    return await _run(request, body, body.record_id, lambda t, repo: t.outline(
        connector_id=repo.connector_id, file_path=body.file_path, record_id=body.record_id,
        include_tests=body.include_tests, repo=repo.group_id,
    ))


@router.post("/impact")
async def code_impact(request: Request, body: ImpactBody) -> JSONResponse:
    return await _run(request, body, body.record_id, lambda t, repo: t.impact(
        connector_id=repo.connector_id, file_path=body.file_path, record_id=body.record_id,
        qualified_name=body.qualified_name, depth=body.depth,
        include_tests=body.include_tests, repo=repo.group_id,
    ))


@router.post("/entry-points")
async def code_entry_points(request: Request, body: EntryPointsBody) -> JSONResponse:
    return await _run(request, body, None, lambda t, repo: t.entry_points(
        connector_id=repo.connector_id, limit=body.limit,
        include_tests=body.include_tests, repo=repo.group_id,
    ))
