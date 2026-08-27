"""Code-graph tools, built per request — read the code knowledge graph.

Four tools over the blocks + recordRelations graph the indexing pipeline builds:

  query_code_graph    — find symbols, relationships, module structure
  find_call_neighbors — who calls a symbol, or what it calls
  read_code           — source for a symbol, a line range, or a whole file
  find_symbol_path    — how two symbols are connected, by any relation

A symbol is addressed as ``(file_path, qualified_name)``. Both appear in the code
blocks rendered into the model's context, so it can name one it has just read.

These are **dynamic** tools rather than a registered toolset, built by
``_build_dynamic_tools`` only when a repo connector is present — the same shape
and the same gate as Slack's ``fetch_slack_thread``/``fetch_slack_nearby_messages``.
The code graph is not a source of its own; it is a view over files a repo
connector ingested, so the tools follow that connector.

Every tool is scoped to the caller's organisation *and* checked against the
caller's access to the owning record. A denial is returned as an empty result,
never as an error: an error would tell the agent that a record it may not read
exists.

See ``code-graph-agent-integration.md`` at the repo root for how this is wired
into the agent loop, and what was removed to get here.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from .ops import (
    DEFAULT_MAX_DEPTH,
    DEFAULT_NEIGHBOR_LIMIT,
    find_call_neighbors_impl,
    find_symbol_path_impl,
    read_code_impl,
)
from .query import (
    DEFAULT_QUERY_LIMIT,
    GROUP_BY_CHOICES,
    MAX_QUERY_DEPTH,
    query_code_graph_impl,
)

if TYPE_CHECKING:
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

logger = logging.getLogger(__name__)

__all__ = ["CODE_GRAPH_APP_NAME", "create_code_graph_tools"]

# The `<app>.<tool>` prefix `split_original_tool_name` reads, which becomes the
# `codegraph__<tool>` name the model calls.
CODE_GRAPH_APP_NAME = "codegraph"


_CONNECTOR_FIELD = Field(
    ...,
    description=(
        "The `Connector ID` of the repository, copied from a record in your "
        "knowledge-search results. Search first — the code graph spans every "
        "indexed repo and has to be told which one."
    ),
)


class QueryCodeGraphArgs(BaseModel):
    connector_id: str = _CONNECTOR_FIELD
    select: str = Field(
        ...,
        description=(
            "What to start from, resolved by shape: a directory "
            "('backend/python/app/agents/') lists its files and subdirectories; "
            "a file ('.../router.py') lists the symbols it defines; a glob "
            "('backend/python/app/**') spans a subtree; a qualified name "
            "('function:analyze_game') is that exact symbol; anything else is "
            "free text matched against symbol names."
        ),
    )
    relations: list[str] | None = Field(
        default=None,
        description=(
            "Relationship types to follow, e.g. ['CALLS'], ['IMPORTS_FROM'], "
            "['INHERITS','EXTENDS']. Omit for all."
        ),
    )
    direction: str = Field(
        default="any",
        description="'outbound' (what it uses), 'inbound' (what uses it), or 'any'",
    )
    depth: int = Field(
        default=0,
        description=(
            f"Hops to expand: 0 = just the matches, up to {MAX_QUERY_DEPTH}. "
            "Ignored when group_by is set, which always rolls up direct dependencies."
        ),
    )
    group_by: str = Field(
        default="none",
        description=(
            "Roll results up: 'directory' for module-level architecture (grouped "
            f"one level below the path you select), 'file', or 'none'. "
            f"One of: {', '.join(GROUP_BY_CHOICES)}"
        ),
    )
    kinds: list[str] | None = Field(
        default=None,
        description=(
            "Keep only these block kinds, e.g. ['function','class','method']. "
            "A file otherwise returns the spans that tile it — imports, "
            "statements, header — alongside its definitions."
        ),
    )
    limit: int = Field(
        default=DEFAULT_QUERY_LIMIT, description=f"Maximum results (default {DEFAULT_QUERY_LIMIT})"
    )


class FindCallNeighborsArgs(BaseModel):
    connector_id: str = _CONNECTOR_FIELD
    file_path: str = Field(..., description="Repo-relative path of the file holding the symbol")
    qualified_name: str = Field(
        ...,
        description=(
            "Qualified name, as shown after '#' in a code block header — e.g. "
            "'function:parse_config' or 'method:Client.fetch'. Case-sensitive, "
            "though a differently-cased spelling still resolves."
        ),
    )
    direction: str = Field(..., description="'caller' (who calls it) or 'callee' (what it calls)")
    limit: int = Field(default=DEFAULT_NEIGHBOR_LIMIT, description="Maximum neighbours to return")


class ReadCodeArgs(BaseModel):
    connector_id: str = _CONNECTOR_FIELD
    file_path: str = Field(..., description="Repo-relative path of the file to read")
    qualified_name: str | None = Field(
        default=None,
        description=(
            "Read one symbol — e.g. 'function:parse_config', 'method:Client.fetch', "
            "as shown after '#' in a code block header. Omit to read the file."
        ),
    )
    lines: str | None = Field(
        default=None,
        description=(
            "Read a line range instead, e.g. '380-420'. Useful on large files — "
            "pair it with the `line` an edge reports to land on a call site."
        ),
    )


class FindSymbolPathArgs(BaseModel):
    connector_id: str = _CONNECTOR_FIELD
    file_path_a: str = Field(..., description="Repo-relative path of the first symbol's file")
    qualified_name_a: str = Field(..., description="Qualified name of the first symbol")
    file_path_b: str = Field(..., description="Repo-relative path of the second symbol's file")
    qualified_name_b: str = Field(..., description="Qualified name of the second symbol")
    max_depth: int = Field(
        default=DEFAULT_MAX_DEPTH, description="Maximum hops to search before giving up"
    )


def create_code_graph_tools(
    org_id: str,
    user_id: str,
    graph_provider: "IGraphDBProvider | None" = None,
    blob_store: "BlobStorage | None" = None,
    allowed_connector_ids: tuple[str, ...] = (),
) -> list[Callable]:
    """Build the code-graph tools with runtime deps injected.

    ``allowed_connector_ids`` is this agent's own knowledge scope. The model
    supplies `connector_id` from its retrieval results, which narrows; this
    constrains, so a stale or invented id cannot reach a repo the agent was
    never scoped to. Empty means the agent has no app scope, and the org-wide
    behaviour (still gated per record) applies.

    Returns an empty list when there is no graph provider — the tools cannot do
    anything without one, and offering a tool that always fails is worse than
    not offering it.
    """
    if graph_provider is None:
        logger.debug("Code graph tools not built: no graph provider")
        return []

    def _in_scope(connector_id: str) -> dict[str, Any] | None:
        """Reject an out-of-scope connector rather than silently widening.

        Returned as an error, not an empty result: an id the agent is not scoped
        to is a caller mistake, and reporting it as "nothing found" would send
        the model looking for data that is simply out of reach.
        """
        if allowed_connector_ids and connector_id not in allowed_connector_ids:
            return {"error": (
                f"connector_id {connector_id!r} is not one this agent can read. "
                "Use a `Connector ID` from your own knowledge-search results."
            )}
        return None

    @tool("query_code_graph", args_schema=QueryCodeGraphArgs)
    async def query_code_graph(
        connector_id: str,
        select: str,
        relations: list[str] | None = None,
        direction: str = "any",
        depth: int = 0,
        group_by: str = "none",
        kinds: list[str] | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
    ) -> dict[str, Any]:
        """Explore the code graph: find symbols, their relationships, and module structure.

        Search the knowledge base FIRST — this needs a `connector_id`, and the
        only place to get one is the `Connector ID` shown on a record in your
        search results. Several repositories can be indexed at once and their
        file paths are repo-relative, so without it `src/main.py` is ambiguous.

        Then call this repeatedly, narrowing or widening as you go — one call
        rarely answers a broad question.

        `select` accepts three things and picks by shape:
          - free text ('payment webhook') — ranked symbol names. Use this first
            when you do not yet know any file or symbol.
          - a path or glob ('backend/python/app/**') — everything under it.
          - a qualified name ('function:analyze_game', 'method:Client.fetch')
            — that exact symbol.

        `depth` walks relationships outward; `relations` and `direction` narrow
        which. `group_by='directory'` instead rolls every dependency under the
        selected path up into modules with weighted edges between them — that is
        how you get an architecture-level view, since a raw expansion returns
        thousands of symbols.

        Grouping is measured from the path you give, so it drills down:
        select='**' returns top-level directories, 'backend/**' the layers inside
        backend, 'backend/python/app/**' the modules inside app. For a high-level
        design, start broad with group_by='directory' and re-ask with a longer
        path for each module worth opening. Use read_code to read anything
        it names.
        """
        denied = _in_scope(connector_id)
        if denied:
            return denied
        try:
            return await query_code_graph_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id, select=select, relations=relations,
                direction=direction, depth=depth, group_by=group_by,
                kinds=kinds, limit=limit,
            )
        except Exception as exc:
            logger.exception("query_code_graph failed")
            return {"error": f"Failed to query the code graph: {exc}"}

    @tool("find_call_neighbors", args_schema=FindCallNeighborsArgs)
    async def find_call_neighbors(
        connector_id: str,
        file_path: str,
        qualified_name: str,
        direction: str,
        limit: int = DEFAULT_NEIGHBOR_LIMIT,
    ) -> dict[str, Any]:
        """Find the callers or callees of one symbol.

        Give it a symbol you already hold — `(file_path, qualified_name)` as shown in
        a code block header — and a direction: 'caller' for what calls it,
        'callee' for what it calls. Returns the addresses of the neighbours,
        which you can then read with read_code — each carries the `line` of the
        call site, so pair it with read_code(lines=...) to see the call itself.
        """
        denied = _in_scope(connector_id)
        if denied:
            return denied
        try:
            return await find_call_neighbors_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id, file_path=file_path, qualified_name=qualified_name,
                direction=direction, limit=limit,
            )
        except Exception as exc:
            logger.exception("find_call_neighbors failed")
            return {"error": f"Failed to walk the call graph: {exc}"}

    @tool("read_code", args_schema=ReadCodeArgs)
    async def read_code(
        connector_id: str,
        file_path: str,
        qualified_name: str | None = None,
        lines: str | None = None,
    ) -> dict[str, Any]:
        """Read source: one symbol, one line range, or a whole file.

        Give `qualified_name` for a single symbol, `lines` for a window
        ('380-420'), or neither to read the whole file as its symbols in source
        order. All three cost the same single fetch, so reading a file once
        beats reading five of its symbols separately.
        """
        denied = _in_scope(connector_id)
        if denied:
            return denied
        try:
            return await read_code_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id, blob_store=blob_store,
                file_path=file_path, qualified_name=qualified_name, lines=lines,
            )
        except Exception as exc:
            logger.exception("read_code failed")
            return {"error": f"Failed to read the code: {exc}"}

    @tool("find_symbol_path", args_schema=FindSymbolPathArgs)
    async def find_symbol_path(
        connector_id: str,
        file_path_a: str,
        qualified_name_a: str,
        file_path_b: str,
        qualified_name_b: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> dict[str, Any]:
        """Trace how two symbols are connected, by any relation.

        Searches undirected across every relationship the code graph holds — not
        just calls — and returns each hop with its relation type and direction,
        so you can see whether two parts of the codebase are related through
        calls, imports, inheritance, or containment.
        """
        denied = _in_scope(connector_id)
        if denied:
            return denied
        try:
            return await find_symbol_path_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id,
                file_path_a=file_path_a, qualified_name_a=qualified_name_a,
                file_path_b=file_path_b, qualified_name_b=qualified_name_b, max_depth=max_depth,
            )
        except Exception as exc:
            logger.exception("find_symbol_path failed")
            return {"error": f"Failed to search for a path: {exc}"}

    return [query_code_graph, find_call_neighbors, read_code, find_symbol_path]
