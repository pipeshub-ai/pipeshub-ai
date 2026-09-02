"""CodeGraph toolset — read the code knowledge graph.

Four tools over the blocks + recordRelations graph the indexing pipeline builds:

  query_code_graph    — find symbols, relationships, module structure
  get_neighbour       — what a symbol reaches, or what reaches it
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
    CROSS_FILE_RELATIONS,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_LINES,
    DEFAULT_NEIGHBOR_LIMIT,
    MAX_NEIGHBOUR_DEPTH,
    find_symbol_path_impl,
    get_neighbour_impl,
    read_code_impl,
)
from .query import (
    DEFAULT_QUERY_LIMIT,
    GROUP_BY_CHOICES,
    MAX_QUERY_DEPTH,
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

_TAGS = [Tag(key="category", value="code_graph"), Tag(key="type", value="action")]


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
        short_description=(
            "Explore the code graph: find symbols, their relationships, and module structure"
        ),
        description=(
            "Explore the code graph: find symbols, their relationships, and module structure.\n\n"
            "Search the knowledge base FIRST — this needs a `connector_id`, and the "
            "only place to get one is the `Connector ID` shown on a record in your "
            "search results. Several repositories can be indexed at once and their "
            "file paths are repo-relative, so without it `src/main.py` is ambiguous.\n\n"
            "Then call this repeatedly, narrowing or widening as you go — one call "
            "rarely answers a broad question.\n\n"
            "`select` picks by shape:\n"
            "  - free text ('payment webhook') — ranked symbol names. Use this first "
            "when you do not yet know any file or symbol.\n"
            "  - a directory ('backend/python/app/agents/') — what is in it.\n"
            "  - a path or glob ('backend/python/app/**') — everything under it.\n"
            "  - 'path/to/file.py#function:main' — that one symbol. This is the form "
            "results print, and the one to copy back in.\n\n"
            "`depth` walks relationships outward; `relations` and `direction` narrow "
            "which. `group_by='directory'` instead rolls every dependency under the "
            "selected path up into modules with weighted edges between them — that is "
            "how you get an architecture-level view, since a raw expansion returns "
            "thousands of symbols.\n\n"
            "Every result is ranked by `degree` — how many edges touch a symbol or a "
            "module. That is what separates an entry point from a helper, so read the "
            "top of the list rather than sampling it, and quote the number when you "
            "say something is central.\n\n"
            "Grouping is measured from the path you give, so it drills down: "
            "select='**' returns top-level directories, 'backend/**' the layers inside "
            "backend, 'backend/python/app/**' the modules inside app. For a high-level "
            "design, start broad with group_by='directory' and re-ask with a longer "
            "path for each module worth opening. Use read_code to read anything "
            "it names.\n\n"
            "When the question asks HOW or WHY something works, a rollup alone "
            "is not enough — after identifying relevant modules, use read_code "
            "on the highest-degree symbols to understand the design.\n\n"
            "This tool finds symbols by location. It does not follow what they "
            "connect to: once you hold a symbol, get_neighbour resolves what it "
            "reaches and what reaches it. Do not chase a flow by guessing which "
            "file a call lands in and selecting that path — the graph already "
            "stores the answer, and inbound edges cannot be guessed at all."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="select", type=ParameterType.STRING,
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
            ),
            ToolParameter(
                name="relations", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "Relationship types to follow, e.g. ['CALLS'], ['IMPORTS_FROM'], "
                    "['INHERITS','EXTENDS']. Omit for all."
                ),
                items={"type": "string"},
            ),
            ToolParameter(
                name="direction", type=ParameterType.STRING, required=False, default="any",
                description="'outbound' (what it uses), 'inbound' (what uses it), or 'any'",
            ),
            ToolParameter(
                name="depth", type=ParameterType.INTEGER, required=False, default=0,
                description=(
                    f"Hops to expand: 0 = just the matches, up to {MAX_QUERY_DEPTH}. "
                    "Dropped when group_by is set, which always rolls up direct "
                    "dependencies."
                ),
            ),
            ToolParameter(
                name="group_by", type=ParameterType.STRING, required=False, default="none",
                description=(
                    "Roll results up: 'directory' for module-level architecture (grouped "
                    f"one level below the path you select), 'file', or 'none'. "
                    f"One of: {', '.join(GROUP_BY_CHOICES)}"
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
        tags=_TAGS,
    )
    async def query_code_graph(
        self,
        connector_id: str,
        select: str,
        relations: list[str] | None = None,
        direction: str = "any",
        depth: int = 0,
        group_by: str = "none",
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
                relations=relations, direction=direction, depth=depth,
                group_by=group_by, kinds=kinds, limit=limit,
                include_tests=include_tests,
            ),
            "Failed to query the code graph",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/get_neighbour",
        short_description=(
            "Trace a flow: resolve what a symbol reaches or what reaches it, as "
            "addresses you can read. The only way to find callers"
        ),
        description=(
            "Resolve what a symbol connects to — the step reading the code cannot do.\n\n"
            "Call this whenever you are following something THROUGH the codebase: "
            "tracing a flow end to end, finding every caller before changing a "
            "signature, asking where a thing is used, or working out which layers "
            "touch a module. If your next thought is \"and then what happens\", this "
            "is the tool.\n\n"
            "Reading a symbol shows you `self.orchestrator.index(ctx)` — an "
            "expression, not an address. It does not tell you WHICH file that lands "
            "in, and you cannot read a symbol you cannot address. This returns the "
            "resolved `(file_path, qualified_name)` for each neighbour, ready to "
            "pass straight to read_code. Guessing the file from the call name and "
            "listing it costs three calls, only works when the name happens to match "
            "a filename, and does not work at all for imports, inheritance or "
            "exports, which never appear in the body you read.\n\n"
            "`direction='inbound'` — what reaches this symbol. There is NO other way "
            "to get this: callers leave no trace in the code you are reading, and a "
            "knowledge search cannot find them because nothing names them. Use it "
            "before you conclude you have seen a whole flow.\n\n"
            "`direction='outbound'` — what this symbol reaches. 'any' for both.\n\n"
            "`edge_types` picks the relationships: ['CALLS'] for callers and callees, "
            "['INHERITS','EXTENDS'] for a type hierarchy, ['IMPORTS_FROM'] for module "
            "dependencies. Omit it for every cross-file relation at once.\n\n"
            "`depth` follows the chain further in one call — depth=2 answers \"what "
            "does this reach, and what do those reach\" without a round trip per hop.\n\n"
            "Each neighbour carries the `line` of the reference, so pair it with "
            "read_code(lines=...) to see the call site itself."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="file_path", type=ParameterType.STRING,
                description="Repo-relative path of the file holding the symbol",
            ),
            ToolParameter(
                name="qualified_name", type=ParameterType.STRING,
                description=(
                    "Qualified name, as shown after '#' in a code block header — e.g. "
                    "'function:parse_config' or 'method:Client.fetch'. Case-sensitive, "
                    "though a differently-cased spelling still resolves."
                ),
            ),
            ToolParameter(
                name="direction", type=ParameterType.STRING, required=False, default="any",
                description="'outbound' (what it reaches), 'inbound' (what reaches it), or 'any'",
            ),
            ToolParameter(
                name="edge_types", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "Relationships to follow, e.g. ['CALLS'] for callers/callees only, "
                    "['INHERITS','EXTENDS'] for a type hierarchy. Omit for every "
                    f"cross-file relation ({', '.join(CROSS_FILE_RELATIONS)}). Also "
                    f"accepts {', '.join(CODE_RELATIONS[:3])}, though those describe how "
                    "one file is built and `list_code` shows them better."
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
        tags=_TAGS,
    )
    async def get_neighbour(
        self,
        connector_id: str,
        file_path: str,
        qualified_name: str,
        direction: str = "any",
        edge_types: list[str] | None = None,
        depth: int = 1,
        limit: int = DEFAULT_NEIGHBOR_LIMIT,
        include_tests: bool = False,
    ) -> tuple[bool, str]:
        result = await self._run(
            "get_neighbour",
            connector_id,
            lambda: get_neighbour_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id,
                file_path=file_path, qualified_name=qualified_name,
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
            "Read source: one symbol, one line range, or a whole file.\n\n"
            "Give `qualified_name` for a single symbol, `lines` for a window you have "
            "a reason to want ('380-420'), or neither to read the whole file as its "
            "symbols in source order.\n\n"
            "Cost trade-offs: reading a file once beats reading five of its symbols "
            "separately, but a whole file also fills context fast. Prefer "
            "`qualified_name` when you need one definition, a whole-file read when "
            "you need to understand a file's structure, and `lines` only when you "
            "already know the exact range (e.g. a call-site line from "
            "get_neighbour).\n\n"
            "A whole-file read is bounded by `max_lines` and tells you where it "
            "stopped, so you never need to guess a line window just to keep a file "
            "from being too large.\n\n"
            "What this does NOT give you: where the calls in the body actually go. "
            "The source shows `self.orchestrator.index(ctx)`, never which file "
            "defines it, and nothing about what calls the symbol you just read. When "
            "the next step is following the flow rather than reading more of it, "
            "call get_neighbour on this symbol instead of guessing filenames."
        ),
        parameters=[
            ToolParameter(
                name="connector_id", type=ParameterType.STRING,
                description=_CONNECTOR_ID_DESC,
            ),
            ToolParameter(
                name="file_path", type=ParameterType.STRING,
                description="Repo-relative path of the file to read",
            ),
            ToolParameter(
                name="qualified_name", type=ParameterType.STRING, required=False, default=None,
                description=(
                    "Read one symbol — e.g. 'function:parse_config', 'method:Client.fetch', "
                    "as shown after '#' in a code block header. Omit to read the file."
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
        tags=_TAGS,
    )
    async def read_code(
        self,
        connector_id: str,
        file_path: str,
        qualified_name: str | None = None,
        lines: str | None = None,
        max_lines: int | None = None,
        include_tests: bool = False,
    ) -> tuple[bool, str]:
        result = await self._run(
            "read_code",
            connector_id,
            lambda: read_code_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id,
                blob_store=self._blob_store, file_path=file_path,
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
            "Searches undirected across every relationship the code graph holds — not "
            "just calls — and returns each hop with its relation type and direction, "
            "so you can see whether two parts of the codebase are related through "
            "calls, imports, inheritance, or containment.\n\n"
            "Only edges the parser could prove exist, which means only edges within "
            "one language and one repository. Two layers that talk over HTTP — a "
            "frontend calling a backend endpoint — have no path here, and a query "
            "for one returns nothing. Read the route handler instead."
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
        tags=_TAGS,
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
