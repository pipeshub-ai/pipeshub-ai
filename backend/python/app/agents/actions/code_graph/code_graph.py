"""CodeGraph toolset — read the code knowledge graph.

Four tools over the blocks + recordRelations graph the indexing pipeline builds:

  query_code_graph    — module structure, and the symbols a directory or file holds
  get_neighbour       — what reaches a file or symbol, and what it reaches
  read_code           — source for a symbol, a line range, or a whole file
  find_symbol_path    — how two same-language symbols are connected

A symbol is addressed as ``(file_path, qualified_name)`` — written
``path/to/file.py#function:main`` where one argument has to carry both. Both
halves appear in the code blocks rendered into the model's context, so it can
name a symbol it has just read.

Every tool is scoped to the caller's organisation *and* checked against the
caller's access to the owning record. A denial is returned as an empty result,
never as an error: an error would tell the agent that a record it may not read
exists.
fetch record 
Registered as an internal class-based toolset so it participates in lazy tool
disclosure alongside connector toolsets. The toolset is only loaded when both
``has_code_connector`` and ``has_code_knowledge`` are true — see the gate in
``tool_loader.py``.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory

from .ops import (
    CODE_RELATIONS,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_LINES,
    DEFAULT_NEIGHBOR_LIMIT,
    MAX_NEIGHBOUR_DEPTH,
    find_symbol_path_impl,
    get_neighbour_impl,
    path_for_record,
    read_code_impl,
)
from .query import (
    DEFAULT_QUERY_LIMIT,
    query_code_graph_impl,
)

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

__all__ = ["CODE_GRAPH_APP_NAME", "CODE_GRAPH_TOOLSET_DESCRIPTION", "CodeGraph"]

CODE_GRAPH_APP_NAME = "codegraph"

CODE_GRAPH_TOOLSET_DESCRIPTION = (
    "Indexed call/import/inheritance graph over the organization's repositories"
)

# ---------------------------------------------------------------------------
# Shared ToolParameter fragments — avoids repeating the same field across
# four tools that all take a connector_id and an include_tests flag.
# ---------------------------------------------------------------------------

_CONNECTOR_ID_DESC = (
    "The `Connector ID` of the repository, copied from a record in your "
    "knowledge-search results. Search first — the code graph spans every "
    "indexed repo and has to be told which one."
)

_INCLUDE_TESTS_DESC = "Include test files and test symbols in results. False by default."

# Every failed lookup in the traces was a composed address, not a wrong one: a
# real symbol under an invented path, or the right path with `function:` where
# the indexer wrote `method:`. Neither half is knowable from reading a source
# body, so the rule has to be "copy", not "spell it correctly".
_ADDRESS_RULE = (
    "Copy this from a listing, a code block header, a get_neighbour result or a "
    "search hit — never compose it. Neither the file nor the `function:`/`method:` "
    "prefix is guessable, and a wrong one fails outright."
)

_TAGS = [Tag(key="category", value="code_graph"), Tag(key="type", value="action")]

# `search_tools` scores by keyword overlap over name + short_description +
# tags (`tools/index.py::_tool_haystack`), normalised by QUERY length — so
# extra vocabulary here can only raise a match, never dilute one. It is
# needed because the descriptions are phrased structurally ("directory",
# "symbol", "edges") while the questions these tools answer are phrased
# behaviourally ("what happens when", "what breaks if"), sharing no tokens.
_BEHAVIOUR = Tag(
    key="answers",
    value="what happens happen behaves behaviour at runtime when running, from indexed repository source code",
)

_QUERY_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="module structure layout contents which classes functions a directory or file defines",
)]

_NEIGHBOUR_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="answers",
    value="who what calls call uses use imports import references reference depends depend on this, and what breaks break affects affect impact if it changes change",
), Tag(
    key="finds",
    value="caller callers callee callees usage usages reference references dependency dependencies impact blast radius wiring edges",
)]

_READ_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="source body definition implementation of a symbol or file",
)]

_PATH_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="answers",
    value="how one part reach reaches another, what sits between them, how something propagates propagate flows flow travels through the code",
), Tag(
    key="finds",
    value="path chain route connection connects between through two symbols end to end",
)]


# ---------------------------------------------------------------------------
# Toolset class
# ---------------------------------------------------------------------------


@ToolsetBuilder("Code Graph")\
    .in_group("Internal Tools")\
    .with_description(CODE_GRAPH_TOOLSET_DESCRIPTION)\
    .with_category(ToolsetCategory.APP)\
    .with_auth([AuthBuilder.type("NONE").fields([])])\
    .as_internal()\
    .build_decorator()
class CodeGraph:
    """Code knowledge graph exposed to agents.

    Instantiated once per request by ``PipesHubToolLoader`` via
    ``ToolInstanceCreator._fallback_creation(state=tool_state)``.
    """

    def __init__(self, state: "ChatState") -> None:
        self._state: dict[str, Any] = state
        self._log = state.get("logger") or logger
        self._graph_provider = state.get("graph_provider")
        self._org_id: str = state.get("org_id", "")
        self._user_id: str = state.get("user_id", "")
        self._blob_store = state.get("blob_store")

        try:
            from app.agents.actions.knowledge_graph.ops.scope import derive_scope
            self._allowed_connector_ids: tuple[str, ...] = tuple(derive_scope(state).app_ids)
        except Exception:
            self._allowed_connector_ids = ()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_output(self, result: dict[str, Any]) -> tuple[bool, str]:
        is_error = isinstance(result, dict) and "error" in result
        return not is_error, json.dumps(result, default=str)

    async def _run(
        self, name: str, connector_id: str, call: Any, failure: str,
    ) -> dict[str, Any]:
        denied = self._in_scope(connector_id or "")
        if denied:
            return denied
        try:
            return await call()
        except Exception as exc:
            self._log.exception("codegraph %s failed", name)
            return {"error": f"{failure}: {exc}"}

    async def _anchor_path(
        self, connector_id: str, file_path: str | None, record_id: str | None
    ) -> tuple[str | None, dict[str, Any] | None]:
        """Resolve the file a call starts from, given a path OR a `Record ID`.

        Returns ``(path, error)``. Search results carry a Record ID and no path,
        so accepting one removes the listing step that used to be the only way
        to turn a search hit into an argument these tools accept.

        Scope is checked before the id is resolved -- this runs ahead of
        ``_run``, and looking a record up first would answer "does this exist"
        for a connector the caller cannot read.
        """
        if file_path:
            return file_path, None
        denied = self._in_scope(connector_id or "")
        if denied:
            return None, denied
        if not record_id:
            return None, {"error": (
                "Give either `file_path` (repo-relative) or `record_id` (the "
                "`Record ID` from a knowledge-search result)."
            )}
        resolved = await path_for_record(self._graph_provider, self._org_id, record_id)
        if not resolved:
            return None, {"error": (
                f"record_id {record_id!r} is not an indexed code file. Code files "
                "show `Type: CODE_FILE` and a `Path:` in search results."
            )}
        return resolved, None

    def _in_scope(self, connector_id: str) -> dict[str, Any] | None:
        if self._allowed_connector_ids and connector_id not in self._allowed_connector_ids:
            return {"error": (
                f"connector_id {connector_id!r} is not one this agent can read. "
                "Use a `Connector ID` from your own knowledge-search results."
            )}
        return None

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    @tool(
        path="/tools/codegraph/query_code_graph",
        short_description="Explore the code graph: what a directory holds, what a file defines",
        description=(
            "Explore the code graph: the module structure, and the symbols a directory "
            "or file holds.\n\n"
            "Search the knowledge base FIRST — this needs a `connector_id`, and the "
            "only place to get one is the `Connector ID` on a search result. Paths are "
            "repo-relative and several repos can be indexed at once, so without it "
            "`src/main.py` is ambiguous.\n\n"
            "Call it repeatedly, narrowing as you go — one call rarely answers a broad "
            "question.\n\n"
            "`select` takes a directory ('backend/python/app/agents/'), listing its "
            "children exactly, each with its own `select`; it looks only downward, so "
            "select the parent to reach a sibling tree. Or a path/glob "
            "('backend/python/app/**'), returning every symbol under it as "
            "'path/to/file.py#function:main'. A glob may cap and set `scan_capped` — "
            "treat that as a sample.\n\n"
            "It does not take a symbol, and it does not take free text. Use it for a "
            "directory's shape or a file's symbols, not to turn a search hit into a "
            "path: a hit already carries `Path:` and `Record ID`, and read_code takes "
            "either.\n\n"
            "Results are ranked by `degree`, the edges touching a symbol — what "
            "separates an entry point from a helper, so read the top of the list rather "
            "than sampling it.\n\n"
            "Treat the result as a worklist, not an answer: get_neighbour the top few "
            "before you describe them, read_code the ones you need to quote. This tool "
            "cannot show an edge, so a connection asserted from a listing alone is a "
            "guess."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="select", type=ParameterType.STRING,
                description=(
                    "A directory or a file path, nothing else. A directory "
                    "('backend/python/app/agents/') lists its children exactly — "
                    "select the PARENT to see sibling trees; a file "
                    "('.../router.py') lists the symbols it defines; a glob "
                    "('backend/python/app/**') spans a subtree and may cap. "
                    "A symbol ('.../router.py#function:main') is rejected — read_code "
                    "reads it and get_neighbour walks its edges. So is free text: "
                    "search the knowledge base to turn a description into a path."
                ),
            ),
            ToolParameter(
                name="kinds", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "Keep only these block kinds, e.g. ['function','class','method']. "
                    "A file otherwise returns the spans that tile it — imports, "
                    "statements, header — alongside its definitions."
                ),
                items={"type": "string"},
            ),
            ToolParameter(
                name="limit", type=ParameterType.INTEGER, required=False,
                default=DEFAULT_QUERY_LIMIT,
                description=f"Maximum results (default {DEFAULT_QUERY_LIMIT})",
            ),
            ToolParameter(
                name="include_tests", type=ParameterType.BOOLEAN, required=False,
                default=False, description=_INCLUDE_TESTS_DESC,
            ),
        ],
        tags=_QUERY_TAGS,
    )
    async def query_code_graph(
        self,
        connector_id: str,
        select: str,
        kinds: list[str] | None = None,
        limit: int = DEFAULT_QUERY_LIMIT,
        include_tests: bool = False,
    ) -> tuple[bool, str]:
        result = await self._run(
            "query_code_graph",
            connector_id,
            lambda: query_code_graph_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id, select=select,
                kinds=kinds, limit=limit, include_tests=include_tests,
            ),
            "Failed to query the code graph",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/get_neighbour",
        short_description=(
            "Walk a symbol's edges: CALLS (callers and callees), METHOD/CONTAINS "
            "(its class), INHERITS/EXTENDS/IMPLEMENTS (heritage), OVERRIDES (the "
            "contract it implements), IMPORTS_FROM (dependencies). A read shows "
            "none of these — call it before describing how anything connects."
        ),
        description=(
            "Find the neighbours of a file or symbol: what reaches it and what it "
            "reaches — which class a method lives under (METHOD/CONTAINS), who calls it "
            "(CALLS), what a class inherits (INHERITS/EXTENDS).\n\n"
            "This is how you traverse the graph, not a one-shot callee lookup. First "
            "call: omit `edge_types`, direction 'any', so you see the nature of the "
            "node. When a result shows a `chain` entry or a METHOD/CONTAINS/INHERITS "
            "neighbour, call get_neighbour again on that address, still without "
            "`edge_types`. Do not stop after one hop and guess the rest from a read. "
            "read_code is for a body you already addressed; edges stay on this tool.\n\n"
            "Do not open with `edge_types=['CALLS']`. That filter hides the class a "
            "method lives under and every heritage edge, leaving nothing to walk. "
            "Narrow to CALLS after a full walk.\n\n"
            "Give it something you already hold — a `record_id` from a search hit, a "
            "`file_path` on its own, or `(file_path, qualified_name)` from a code block "
            "header — plus a direction: 'inbound' for what reaches it, 'outbound' for "
            "what it reaches, 'any' for both. Every neighbour is an address for the "
            "next get_neighbour or for read_code(lines=...) on a call site.\n\n"
            "Omit `qualified_name` to walk every symbol the file defines at once.\n\n"
            "'inbound' is the direction with no substitute: callers leave no trace in "
            "the code you are reading, and a knowledge search cannot find them because "
            "nothing names them.\n\n"
            "Prefer another get_neighbour on a returned neighbour over raising `depth`; "
            "depth multiplies noise."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="file_path", type=ParameterType.STRING, required=False,
                default=None,
                description=(
                    "Repo-relative path of the file to walk from, or of the file "
                    "holding `qualified_name` — the `Path:` line of a search hit. "
                    "Give this or `record_id`. " + _ADDRESS_RULE
                ),
            ),
            ToolParameter(
                name="record_id", type=ParameterType.STRING, required=False,
                default=None,
                description=(
                    "The `Record ID` of a code file, copied from a knowledge-search "
                    "result. Use it when you have a search hit and no path — it "
                    "resolves to the same file, so a search result is walkable "
                    "without a listing step in between. Ignored when `file_path` "
                    "is given."
                ),
            ),
            ToolParameter(
                name="qualified_name", type=ParameterType.STRING, required=False,
                default=None,
                description=(
                    "Qualified name, as shown after '#' in a code block header — e.g. "
                    "'function:parse_config' or 'method:Client.fetch'. Case-sensitive, "
                    "though a differently-cased spelling still resolves. OMIT IT to "
                    "walk every symbol the file defines at once, which is what you "
                    "want when a search just handed you the path and you do not know "
                    "yet which symbol matters. " + _ADDRESS_RULE
                ),
            ),
            ToolParameter(
                name="direction", type=ParameterType.STRING, required=False, default="any",
                description=(
                    "'any' (both directions — the default, and usually what you "
                    "want), 'inbound' (what reaches it), or 'outbound' (what it "
                    "reaches)"
                ),
            ),
            ToolParameter(
                name="edge_types", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "Leave unset on the first walk. Setting ['CALLS'] alone hides "
                    "METHOD/CONTAINS/INHERITS so you cannot chain. Narrow only "
                    "after a full walk, e.g. ['CALLS'] for call sites, "
                    "['INHERITS','EXTENDS','IMPLEMENTS'] for heritage. Full set "
                    f"when omitted: {', '.join(CODE_RELATIONS)}."
                ),
                items={"type": "string"},
            ),
            ToolParameter(
                name="depth", type=ParameterType.INTEGER, required=False, default=1,
                description=(
                    f"Hops to follow, up to {MAX_NEIGHBOUR_DEPTH}. Each hop multiplies "
                    "the result, so raise it to trace a chain, not to survey."
                ),
            ),
            ToolParameter(
                name="limit", type=ParameterType.INTEGER, required=False,
                default=DEFAULT_NEIGHBOR_LIMIT,
                description="Maximum neighbours to return",
            ),
            ToolParameter(
                name="include_tests", type=ParameterType.BOOLEAN, required=False,
                default=False, description=_INCLUDE_TESTS_DESC,
            ),
        ],
        tags=_NEIGHBOUR_TAGS,
    )
    async def get_neighbour(
        self,
        connector_id: str,
        file_path: str | None = None,
        record_id: str | None = None,
        qualified_name: str | None = None,
        direction: str = "any",
        edge_types: list[str] | None = None,
        depth: int = 1,
        limit: int = DEFAULT_NEIGHBOR_LIMIT,
        include_tests: bool = False,
    ) -> tuple[bool, str]:
        path, error = await self._anchor_path(connector_id, file_path, record_id)
        if error is not None:
            return self._to_output(error)
        result = await self._run(
            "get_neighbour",
            connector_id,
            lambda: get_neighbour_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id,
                file_path=path, qualified_name=qualified_name,
                direction=direction, edge_types=edge_types, depth=depth,
                limit=limit, include_tests=include_tests,
            ),
            "Failed to walk the code graph",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/read_code",
        short_description="Read source: one symbol, one line range, or a whole file",
        description=(
            "Read source: one symbol, one line range, or a whole file. Do NOT use "
            "fetch_full_record for code files.\n\n"
            "Give `qualified_name` for one symbol, `lines` for a window you have a "
            "reason to want ('380-420'), or neither to read the whole file as its "
            "symbols in source order. A whole-file read is bounded by `max_lines` and "
            "says where it stopped, and a bare path is enough — you never need to list "
            "a file before reading it.\n\n"
            "Cost trade-offs: one file read beats five symbol reads, but a whole file "
            "also fills context fast. Prefer `qualified_name` for a single definition, "
            "a whole-file read to understand structure, `lines` only when you know the "
            "range (e.g. a call-site line from get_neighbour).\n\n"
            "This does not resolve where calls go: the identifiers in a body are "
            "expressions, not addresses. Use get_neighbour to follow the flow instead "
            "of guessing filenames.\n\n"
            "A read is never the last step for a question about how something works. "
            "The moment you mean to describe this symbol as calling, importing, "
            "depending on or inheriting from anything, get_neighbour that same "
            "`(file_path, qualified_name)` first — 'inbound' for what reaches it, "
            "'outbound' for what it reaches. A read shows a body; only an edge walk "
            "shows what that body is wired to, and four reads in a row still show "
            "none of it."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="file_path", type=ParameterType.STRING, required=False, default=None,
                description=(
                    "Repo-relative path of the file to read — the `Path:` line of a "
                    "search hit. Give this or `record_id`. " + _ADDRESS_RULE
                ),
            ),
            ToolParameter(
                name="record_id", type=ParameterType.STRING, required=False, default=None,
                description=(
                    "The `Record ID` of a code file, copied from a knowledge-search "
                    "result. Use it when you have a search hit and no path. Ignored "
                    "when `file_path` is given."
                ),
            ),
            ToolParameter(
                name="qualified_name", type=ParameterType.STRING, required=False, default=None,
                description=(
                    "Read one symbol — e.g. 'function:parse_config', 'method:Client.fetch', "
                    "as shown after '#' in a code block header. Omit to read the file. "
                    + _ADDRESS_RULE
                ),
            ),
            ToolParameter(
                name="lines", type=ParameterType.STRING, required=False, default=None,
                description=(
                    "Read a line range instead, e.g. '380-420'. Use when you already "
                    "know WHICH part you want — pair it with the `line` an edge reports "
                    "to land on a call site. To bound size, use max_lines instead."
                ),
            ),
            ToolParameter(
                name="max_lines", type=ParameterType.INTEGER, required=False, default=None,
                description=(
                    f"Budget for a whole-file read (default {DEFAULT_MAX_LINES}). Whole "
                    "blocks are kept and the result says where it stopped, so you can "
                    "read a large file without knowing its size first."
                ),
            ),
            ToolParameter(
                name="include_tests", type=ParameterType.BOOLEAN, required=False,
                default=False, description=_INCLUDE_TESTS_DESC,
            ),
        ],
        tags=_READ_TAGS,
    )
    async def read_code(
        self,
        connector_id: str,
        file_path: str | None = None,
        record_id: str | None = None,
        qualified_name: str | None = None,
        lines: str | None = None,
        max_lines: int | None = None,
        include_tests: bool = False,
    ) -> tuple[bool, str]:
        path, error = await self._anchor_path(connector_id, file_path, record_id)
        if error is not None:
            return self._to_output(error)
        result = await self._run(
            "read_code",
            connector_id,
            lambda: read_code_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id,
                blob_store=self._blob_store, file_path=path,
                qualified_name=qualified_name, lines=lines,
                max_lines=max_lines, include_tests=include_tests,
            ),
            "Failed to read the code",
        )
        record_id = result.pop("_record_id", None) if isinstance(result, dict) else None
        if (
            record_id
            and not qualified_name
            and not lines
            and not result.get("truncated")
        ):
            self._state.setdefault("full_records_fetched", set()).add(record_id)
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/find_symbol_path",
        short_description="Trace how two symbols in the same language are connected",
        description=(
            "Trace how two symbols in the same language are connected.\n\n"
            "Searches undirected across every edge the code graph holds — not just "
            "calls — returning each hop with its type and direction, so you can see "
            "whether two parts are related through calls, imports, inheritance, or "
            "containment.\n\n"
            "Only edges the parser could prove, which means one language and one "
            "repository. Two layers talking over HTTP — a frontend calling a backend "
            "endpoint — have no path here, and a query for one returns nothing. Read "
            "the route handler instead.\n\n"
            "Needs both ends addressed. Holding only one, get_neighbour walks outward "
            "from it, and repeating that from the far end usually finds the same "
            "connection with less setup."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="file_path_a", type=ParameterType.STRING,
                description="Repo-relative path of the first symbol's file",
            ),
            ToolParameter(
                name="qualified_name_a", type=ParameterType.STRING,
                description="Qualified name of the first symbol",
            ),
            ToolParameter(
                name="file_path_b", type=ParameterType.STRING,
                description="Repo-relative path of the second symbol's file",
            ),
            ToolParameter(
                name="qualified_name_b", type=ParameterType.STRING,
                description="Qualified name of the second symbol",
            ),
            ToolParameter(
                name="max_depth", type=ParameterType.INTEGER, required=False,
                default=DEFAULT_MAX_DEPTH,
                description="Maximum hops to search before giving up",
            ),
            ToolParameter(
                name="edge_types", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "Relationships to walk. Omit for all of them, which is almost always "
                    "right: two methods of the same class are connected only through "
                    "their container, so narrowing this usually returns no path at all."
                ),
                items={"type": "string"},
            ),
            ToolParameter(
                name="include_tests", type=ParameterType.BOOLEAN, required=False,
                default=False, description=_INCLUDE_TESTS_DESC,
            ),
        ],
        tags=_PATH_TAGS,
    )
    async def find_symbol_path(
        self,
        connector_id: str,
        file_path_a: str,
        qualified_name_a: str,
        file_path_b: str,
        qualified_name_b: str,
        max_depth: int = DEFAULT_MAX_DEPTH,
        edge_types: list[str] | None = None,
        include_tests: bool = False,
    ) -> tuple[bool, str]:
        result = await self._run(
            "find_symbol_path",
            connector_id,
            lambda: find_symbol_path_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id,
                file_path_a=file_path_a, qualified_name_a=qualified_name_a,
                file_path_b=file_path_b, qualified_name_b=qualified_name_b,
                max_depth=max_depth, edge_types=edge_types,
                include_tests=include_tests,
            ),
            "Failed to search for a path",
        )
        return self._to_output(result)
