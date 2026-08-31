"""Code-graph tools, built per request — read the code knowledge graph.

Four tools over the blocks + recordRelations graph the indexing pipeline builds:

  query_code_graph    — find symbols, relationships, module structure
  find_call_neighbors — who calls a symbol, or what it calls
  read_code           — source for a symbol, a line range, or a whole file
  find_symbol_path    — how two same-language symbols are connected

A symbol is addressed as ``(file_path, qualified_name)`` — written
``path/to/file.py#function:main`` where one argument has to carry both. Both
halves appear in the code blocks rendered into the model's context, so it can
name a symbol it has just read.

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
    DEFAULT_MAX_LINES,
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


def _brief(params: dict[str, Any]) -> str:
    """Arguments as one short line, long values clipped."""
    return " ".join(
        f"{key}={str(value)[:80]}"
        for key, value in params.items()
        if value is not None
    )


def _outcome(result: Any) -> str:
    """What came back, by shape -- never the payload itself."""
    if not isinstance(result, dict):
        return type(result).__name__
    if "error" in result:
        return f"error: {str(result['error'])[:120]}"
    parts = [f"{k}={len(v)}" for k, v in result.items() if isinstance(v, list)]
    parts += [
        f"{k}={result[k]}"
        for k in ("matches", "modules", "dependencies", "truncated", "found")
        if k in result
    ]
    return ", ".join(parts) or "ok"


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
            "('backend/python/app/**') spans a subtree; 'path/to/file.py#"
            "function:main' is that one symbol — the form every result prints, "
            "and the only unambiguous way to name a symbol, since a qualified "
            "name alone is unique only within its file; anything else is free "
            "text matched against symbol names."
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
            "Dropped when group_by is set, which always rolls up direct "
            "dependencies."
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
            "Read a line range instead, e.g. '380-420'. Use when you already "
            "know WHICH part you want — pair it with the `line` an edge reports "
            "to land on a call site. To bound size, use max_lines instead."
        ),
    )
    max_lines: int | None = Field(
        default=None,
        description=(
            f"Budget for a whole-file read (default {DEFAULT_MAX_LINES}). Whole "
            "blocks are kept and the result says where it stopped, so you can "
            "read a large file without knowing its size first."
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
    request_logger: "logging.Logger | None" = None,
) -> list[Callable]:
    """Build the code-graph tools with runtime deps injected.

    ``allowed_connector_ids`` is this agent's own knowledge scope. The model
    supplies `connector_id` from its retrieval results, which narrows; this
    constrains, so a stale or invented id cannot reach a repo the agent was
    never scoped to. Empty means the agent has no app scope, and the org-wide
    behaviour (still gated per record) applies.

    ``request_logger`` is the per-request service logger. The module logger this
    file would otherwise use does not propagate to the service log handler, so
    tool calls left no trace there at all — a run with 34 code-tool calls and a
    run with none produced identical logs.

    Returns an empty list when there is no graph provider — the tools cannot do
    anything without one, and offering a tool that always fails is worse than
    not offering it.
    """
    log = request_logger or logger
    if graph_provider is None:
        logger.debug("Code graph tools not built: no graph provider")
        return []

    async def _run(name: str, params: dict[str, Any], call, failure: str) -> dict[str, Any]:
        """Scope-check, invoke, and log -- whatever the outcome.

        Only failures used to be logged, which made the tools unfalsifiable
        from a log alone: a run that never called them and a run whose calls
        all succeeded left the same trace. Every branch here logs at info.
        """
        detail = _brief(params)
        denied = _in_scope(params.get("connector_id") or "")
        if denied:
            log.info("codegraph %s: %s -> connector out of scope", name, detail)
            return denied
        try:
            result = await call()
        except Exception as exc:
            log.exception("codegraph %s: %s -> raised", name, detail)
            return {"error": f"{failure}: {exc}"}
        log.info("codegraph %s: %s -> %s", name, detail, _outcome(result))
        return result

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

        `select` picks by shape:
          - free text ('payment webhook') — ranked symbol names. Use this first
            when you do not yet know any file or symbol.
          - a directory ('backend/python/app/agents/') — what is in it.
          - a path or glob ('backend/python/app/**') — everything under it.
          - 'path/to/file.py#function:main' — that one symbol. This is the form
            results print, and the one to copy back in.

        `depth` walks relationships outward; `relations` and `direction` narrow
        which. `group_by='directory'` instead rolls every dependency under the
        selected path up into modules with weighted edges between them — that is
        how you get an architecture-level view, since a raw expansion returns
        thousands of symbols.

        Every result is ranked by `degree` — how many edges touch a symbol or a
        module. That is what separates an entry point from a helper, so read the
        top of the list rather than sampling it, and quote the number when you
        say something is central.

        Grouping is measured from the path you give, so it drills down:
        select='**' returns top-level directories, 'backend/**' the layers inside
        backend, 'backend/python/app/**' the modules inside app. For a high-level
        design, start broad with group_by='directory' and re-ask with a longer
        path for each module worth opening. Use read_code to read anything
        it names.

        When the question asks HOW or WHY something works, a rollup alone
        is not enough — after identifying relevant modules, use read_code
        on the highest-degree symbols to understand the design. For tracing
        a specific call chain, use find_call_neighbors rather than expanding
        with depth, since it returns exact call-site lines.
        """
        return await _run(
            "query_code_graph",
            {"connector_id": connector_id, "select": select, "relations": relations,
             "direction": direction, "depth": depth, "group_by": group_by,
             "kinds": kinds, "limit": limit},
            lambda: query_code_graph_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id, select=select, relations=relations,
                direction=direction, depth=depth, group_by=group_by,
                kinds=kinds, limit=limit,
            ),
            "Failed to query the code graph",
        )

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
        return await _run(
            "find_call_neighbors",
            {"connector_id": connector_id, "file_path": file_path,
             "qualified_name": qualified_name, "direction": direction, "limit": limit},
            lambda: find_call_neighbors_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id, file_path=file_path, qualified_name=qualified_name,
                direction=direction, limit=limit,
            ),
            "Failed to walk the call graph",
        )

    @tool("read_code", args_schema=ReadCodeArgs)
    async def read_code(
        connector_id: str,
        file_path: str,
        qualified_name: str | None = None,
        lines: str | None = None,
        max_lines: int | None = None,
    ) -> dict[str, Any]:
        """Read source: one symbol, one line range, or a whole file.

        Give `qualified_name` for a single symbol, `lines` for a window you have
        a reason to want ('380-420'), or neither to read the whole file as its
        symbols in source order.

        Cost trade-offs: reading a file once beats reading five of its symbols
        separately, but a whole file also fills context fast. Prefer
        `qualified_name` when you need one definition, a whole-file read when
        you need to understand a file's structure, and `lines` only when you
        already know the exact range (e.g. a call-site line from
        find_call_neighbors).

        A whole-file read is bounded by `max_lines` and tells you where it
        stopped, so you never need to guess a line window just to keep a file
        from being too large.
        """
        return await _run(
            "read_code",
            {"connector_id": connector_id, "file_path": file_path,
             "qualified_name": qualified_name, "lines": lines, "max_lines": max_lines},
            lambda: read_code_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id, blob_store=blob_store,
                file_path=file_path, qualified_name=qualified_name, lines=lines,
                max_lines=max_lines,
            ),
            "Failed to read the code",
        )

    @tool("find_symbol_path", args_schema=FindSymbolPathArgs)
    async def find_symbol_path(
        connector_id: str,
        file_path_a: str,
        qualified_name_a: str,
        file_path_b: str,
        qualified_name_b: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
    ) -> dict[str, Any]:
        """Trace how two symbols in the same language are connected.

        Searches undirected across every relationship the code graph holds — not
        just calls — and returns each hop with its relation type and direction,
        so you can see whether two parts of the codebase are related through
        calls, imports, inheritance, or containment.

        Only edges the parser could prove exist, which means only edges within
        one language and one repository. Two layers that talk over HTTP — a
        frontend calling a backend endpoint — have no path here, and a query
        for one returns nothing. Read the route handler instead.
        """
        return await _run(
            "find_symbol_path",
            {"connector_id": connector_id, "file_path_a": file_path_a,
             "qualified_name_a": qualified_name_a, "file_path_b": file_path_b,
             "qualified_name_b": qualified_name_b, "max_depth": max_depth},
            lambda: find_symbol_path_impl(
                graph_provider=graph_provider, org_id=org_id, user_id=user_id,
                connector_id=connector_id,
                file_path_a=file_path_a, qualified_name_a=qualified_name_a,
                file_path_b=file_path_b, qualified_name_b=qualified_name_b, max_depth=max_depth,
            ),
            "Failed to search for a path",
        )

    return [query_code_graph, find_call_neighbors, read_code, find_symbol_path]
