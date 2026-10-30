"""CodeGraph toolset — read the code knowledge graph.

Four tools over the blocks + recordLinks graph the indexing pipeline builds:

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

Registered as an internal class-based toolset so it participates in lazy tool
disclosure alongside connector toolsets. The toolset is only loaded when both
``has_code_connector`` and ``has_code_knowledge`` are true — see the gate in
``tool_loader.py``.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory

from .neighbours import (
    DEFAULT_IMPACT_DEPTH,
    DEFAULT_NEIGHBOR_LIMIT,
    IMPACT_RELATIONS,
    MAX_IMPACT_DEPTH,
    MAX_NEIGHBOUR_DEPTH,
    get_neighbour_impl,
    impact_impl,
)
from .ops import (
    CODE_RELATIONS,
    AmbiguousRepoError,
    _record_in_connector,
    _user_can_read,
    path_for_record,
    repo_names,
)
from .path import DEFAULT_MAX_DEPTH, find_symbol_path_impl
from .query import (
    DEFAULT_ENTRY_LIMIT,
    DEFAULT_QUERY_LIMIT,
    entry_points_impl,
    query_code_graph_impl,
)
from .read import DEFAULT_MAX_LINES, outline_impl, read_code_impl
from .results import as_untrusted, attach_graph_status, without_marker
from .search import DEFAULT_SEARCH_LIMIT, search_code_impl
from .semantic import DEFAULT_SEMANTIC_LIMIT, search_semantic_impl

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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
_REPO_DESC = (
    "The repository, as results show it in `repo` (e.g. `acme/shop`, or just "
    "`shop`). Only needed when one connector holds several repos and a path "
    "exists in more than one -- the tool then says which repos to choose from."
)

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
# "symbol", "edges") while questions are phrased behaviourally ("what calls",
# "what breaks"). The tokenizer keeps every word, so these hold only words a
# code question uses: "what happens when" matched every question asked (A-10).
_BEHAVIOUR = Tag(
    key="answers",
    value="code codebase source repository repo runtime behaviour behavior implementation",
)

_QUERY_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="module modules structure layout classes functions methods directory defines",
)]

_NEIGHBOUR_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="answers",
    value="calls call called imports import references depends dependency breaks impact",
), Tag(
    key="finds",
    value="caller callers callee callees usages references dependencies impact blast radius wiring edges",
)]

_READ_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="source body definition implementation symbol function method class",
)]

_PATH_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="answers",
    value="reaches reach propagates propagate flows flow path between symbols",
), Tag(
    key="finds",
    value="path chain route connection connects through callers callees",
)]

_SEARCH_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="where defined definition identifier symbol name grep text string regex occurrences usages literal",
)]

_OUTLINE_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="outline signatures functions methods classes declared file overview api",
)]

_IMPACT_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="answers",
    value="breaks impact blast radius affected callers dependents change refactor rename",
)]

_ENTRY_TAGS = [*_TAGS, _BEHAVIOUR, Tag(
    key="finds",
    value="entry points entrypoint main handlers routes cli jobs start starts",
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
        self._scope_unknown = False
        self._bounded = False

        try:
            from app.agents.actions.knowledge_graph.ops.scope import derive_scope
            from app.modules.retrieval.selection_scope import allowed_filters

            allowed = list(derive_scope(state).app_ids)
            # The universal agent's apps are every app the user reads; a
            # connected app's limit, a saved agent's or a project's sources
            # admit only the repositories they name whole.
            bounds = allowed_filters(state.get("filters") or {})
            for bound in bounds:
                allowed = [app_id for app_id in allowed if app_id in bound["apps"]]
            self._allowed_connector_ids: tuple[str, ...] = tuple(allowed)
            self._bounded = bool(bounds)
        except Exception:
            # Fails closed: an empty list means "every connector", and a scope
            # the agent was meant to have must not widen because it could not
            # be read (S-6).
            self._log.warning("codegraph: connector scope derivation failed", exc_info=True)
            self._allowed_connector_ids = ()
            self._scope_unknown = True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _to_output(self, result: dict[str, Any]) -> tuple[bool, str]:
        is_error = isinstance(result, dict) and "error" in result
        # No spaces after separators: ~10% of every result, for nothing (A-8).
        return not is_error, json.dumps(result, default=str, separators=(",", ":"))

    async def _run(
        self, name: str, connector_id: str, call: Callable[[], Awaitable[dict[str, Any]]], failure: str,
    ) -> dict[str, Any]:
        denied = self._in_scope(connector_id or "")
        if denied:
            return denied
        try:
            result = await call()
        except AmbiguousRepoError as exc:
            return {"error": str(exc), "repos": exc.repos}
        except Exception:
            # The exception text can name hosts, queries and internal paths;
            # the model gets an id to quote, the log keeps the detail (S-9).
            error_id = uuid4().hex[:12]
            self._log.exception("codegraph %s failed (error id %s)", name, error_id)
            return {"error": f"{failure} (error id {error_id})"}
        try:
            return await attach_graph_status(
                result, self._graph_provider, self._org_id,
                lambda groups: repo_names(self._graph_provider, self._org_id, groups),
            )
        except Exception:
            # The answer stands without its status note.
            self._log.warning("codegraph %s: graph status unavailable", name, exc_info=True)
            return without_marker(result)

    async def _anchor_path(
        self, connector_id: str, file_path: str | None, record_id: str | None,
        repo: str | None = None,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Resolve the file a call starts from, given a path OR a `Record ID`.

        Returns ``(path, repo, error)``. A record id names one repo, which is
        returned so a path two repos share still resolves to that record's. Search results carry a Record ID and no path,
        so accepting one removes the listing step that used to be the only way
        to turn a search hit into an argument these tools accept.

        Scope is checked before the id is resolved -- this runs ahead of
        ``_run``, and looking a record up first would answer "does this exist"
        for a connector the caller cannot read.
        """
        if file_path:
            return file_path, repo, None
        denied = self._in_scope(connector_id or "")
        if denied:
            return None, None, denied
        if not record_id:
            return None, None, {"error": (
                "Give either `file_path` (repo-relative) or `record_id` (the "
                "`Record ID` from a knowledge-search result)."
            )}
        not_code = {"error": (
            f"record_id {record_id!r} is not an indexed code file. Code files "
            "show `Type: CODE_FILE` and a `Path:` in search results."
        )}
        # One answer for unreadable, other-connector and unknown records: the
        # path of a record the caller cannot read is itself a leak.
        if not await _user_can_read(
            self._graph_provider, self._user_id, self._org_id, record_id
        ):
            return None, None, not_code
        try:
            in_connector = await _record_in_connector(
                self._graph_provider, self._org_id, [record_id], connector_id
            )
            record = await self._graph_provider.get_document(
                record_id, CollectionNames.RECORDS.value, raise_on_error=True
            )
        except Exception:
            self._log.warning("codegraph record connector check failed", exc_info=True)
            return None, None, not_code
        if in_connector is None:
            return None, None, not_code
        resolved = await path_for_record(self._graph_provider, self._org_id, record_id)
        if not resolved:
            return None, None, not_code
        return resolved, (record or {}).get("recordGroupId") or repo, None

    def _in_scope(self, connector_id: str) -> dict[str, Any] | None:
        if self._scope_unknown:
            return {"error": "This agent's knowledge sources could not be read; try again."}
        # An empty list means every connector, unless a bound emptied it.
        if (self._allowed_connector_ids or self._bounded) and connector_id not in self._allowed_connector_ids:
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
            "Explore directory/file shape and the symbols under a path.\n\n"
            "Search the knowledge base FIRST — `connector_id` comes from a search "
            "result or Knowledge Sources. Paths are repo-relative and several repos "
            "can be indexed, so without it `src/main.py` is ambiguous.\n\n"
            "`select` is a directory (lists children; pick the parent for siblings), "
            "a file (its symbols as 'path#function:main'), or a glob. It does not take "
            "a symbol or free text; a search hit already has Path: and Record ID — "
            "read_code takes either.\n\n"
            "Do not glob a tree to list every implementer of a type — that is inbound "
            "get_neighbour on the factory or base. One listing that names the families "
            "is enough; `truncated`/`scan_capped` means sample, not keep paging.\n\n"
            "Results are ranked by `degree` — read the top of the list rather than "
            "sampling it. Then get_neighbour the top few and read_code what you quote. "
            "This tool cannot show an edge."
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
            ToolParameter(
                name="repo", type=ParameterType.STRING, required=False, default=None,
                description=_REPO_DESC,
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
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        result = await self._run(
            "query_code_graph",
            connector_id,
            lambda: query_code_graph_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id, select=select,
                kinds=kinds, limit=limit, include_tests=include_tests, repo=repo,
            ),
            "Failed to query the code graph",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/get_neighbour",
        short_description=(
            "Walk a symbol's edges: CALLS, METHOD/CONTAINS, INHERITS/"
            "IMPLEMENTS, OVERRIDES, IMPORTS_FROM. A read shows none of these."
        ),
        description=(
            "Walk edges of a file or symbol: METHOD/CONTAINS (its class), CALLS, "
            "INHERITS/IMPLEMENTS, OVERRIDES, IMPORTS_FROM. A read shows none "
            "of these.\n\n"
            "Give it something you already hold — a `record_id`, a `file_path` on its "
            "own, or `(file_path, qualified_name)`. Direction: 'inbound', 'outbound', "
            "or 'any' (default). Omit `qualified_name` to walk every symbol in the "
            "file. A bare name ('ConnectorFactory', 'BaseConnector') resolves across "
            "the repo; several matches return `candidates`.\n\n"
            "First call: omit `edge_types`, direction 'any'. Do not open with "
            "`edge_types=['CALLS']` — that hides METHOD/CONTAINS/INHERITS and every "
            "`chain`. Follow a neighbour with another get_neighbour; "
            "read_code(lines=...) only for a body. Prefer another hop over raising "
            "`depth`.\n\n"
            "A set (\"what implements X\") starts here: the factory, registry, or "
            "base type, then inbound heritage. Do not list directories. 'inbound' "
            "has no substitute — callers leave no trace in the code you are reading.\n\n"
            "If `truncated`, page with the `next` offset; do not raise `limit`."
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
                    "Give this, `record_id`, or `qualified_name` alone. " + _ADDRESS_RULE
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
                    "yet which symbol matters. Without `file_path` or `record_id` this "
                    "may be a bare name you have not seen written down ('notify'), and "
                    "is resolved across the repo — the one case where you may compose "
                    "it rather than copy it. " + _ADDRESS_RULE
                ),
            ),
            ToolParameter(
                name="direction", type=ParameterType.STRING, required=False, default="any",
                description=(
                    "'any' (both directions — the default, and usually what you "
                    "want), 'inbound' (what reaches it), or 'outbound' (what it "
                    "reaches). Every row's `direction` says which way its edge "
                    "points: an inbound CALLS row is a caller, an outbound one a "
                    "callee. Rows past the first hop carry `via`, the node they "
                    "were reached through."
                ),
            ),
            ToolParameter(
                name="edge_types", type=ParameterType.ARRAY, required=False, default=None,
                description=(
                    "Leave unset on the first walk. Setting ['CALLS'] alone hides "
                    "METHOD/CONTAINS/INHERITS so you cannot chain. Narrow only "
                    "after a full walk, e.g. ['CALLS'] for call sites, "
                    "['INHERITS','IMPLEMENTS'] for heritage. Full set "
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
                description="Maximum neighbours to return per call",
            ),
            ToolParameter(
                name="offset", type=ParameterType.INTEGER, required=False, default=0,
                description=(
                    "Skip this many neighbours first. When a result says "
                    "`truncated: true`, its `next` line gives the offset to pass "
                    "with the same arguments to get the following page."
                ),
            ),
            ToolParameter(
                name="include_tests", type=ParameterType.BOOLEAN, required=False,
                default=False, description=_INCLUDE_TESTS_DESC,
            ),
            ToolParameter(
                name="repo", type=ParameterType.STRING, required=False, default=None,
                description=_REPO_DESC,
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
        offset: int = 0,
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        # A name with no file is the one call that needs no anchor: the impl
        # resolves it against the repo and asks which match was meant.
        if file_path or record_id or not qualified_name:
            path, repo, error = await self._anchor_path(connector_id, file_path, record_id, repo)
            if error is not None:
                return self._to_output(error)
        else:
            path, error = None, self._in_scope(connector_id or "")
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
                limit=limit, offset=offset, include_tests=include_tests, repo=repo,
            ),
            "Failed to walk the code graph",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/read_code",
        short_description="Read source: one symbol, one line range, or a whole file",
        description=(
            "Read source: one symbol, a line range, or a whole file. Do NOT use "
            "fetch_full_record for code files. A bare path is enough — you never "
            "need to list a file before reading it.\n\n"
            "Give `qualified_name` for one symbol, `lines` for a window ('380-420'), "
            "or neither for the whole file (bounded by `max_lines`; continue from "
            "the stop line until covered).\n\n"
            "Cost trade-offs: one file read beats five symbol reads, but a whole "
            "file fills context fast. Prefer `qualified_name` when you already know "
            "the definition; read the file when the answer depends on what else is "
            "in it.\n\n"
            "This does not resolve where calls go: identifiers in a body are "
            "expressions, not addresses. Use get_neighbour instead of guessing "
            "filenames. A read shows a body; only an edge walk shows what it is "
            "wired to — get_neighbour that same `(file_path, qualified_name)` "
            "before describing calls, imports, or heritage."
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
                    "Read a line range, e.g. '380-420'. Two uses: landing on a place you "
                    "already know — pair it with the `line` an edge reports to hit a call "
                    "site — and paging a file too long for one read, by passing successive "
                    "ranges from the line the previous read stopped at until the file is "
                    "covered. To bound a single read's size, use max_lines instead."
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
            ToolParameter(
                name="repo", type=ParameterType.STRING, required=False, default=None,
                description=_REPO_DESC,
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
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        path, repo, error = await self._anchor_path(connector_id, file_path, record_id, repo)
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
                max_lines=max_lines, include_tests=include_tests, repo=repo,
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
        return self._to_output(as_untrusted(result))

    @tool(
        path="/tools/codegraph/find_symbol_path",
        short_description="Trace how two symbols in the same language are connected",
        description=(
            "Trace how two symbols in the same language are connected. Walks every "
            "edge type (calls, imports, inheritance, containment), not just calls, "
            "and follows each the way it points: a path means the first symbol "
            "reaches the second. If only the second reaches the first, that path is "
            "returned with `reversed: true`. Each hop carries an `explain` "
            "sentence.\n\n"
            "Only edges the parser could prove — one language and one repository. "
            "Two layers talking over HTTP have no path here. Read the route handler "
            "instead.\n\n"
            "Needs both ends addressed. With one end only, get_neighbour from it "
            "(and from the far end) usually finds the same connection with less "
            "setup."
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
            ToolParameter(
                name="repo", type=ParameterType.STRING, required=False, default=None,
                description=_REPO_DESC,
            ),
            ToolParameter(
                name="undirected", type=ParameterType.BOOLEAN, required=False,
                default=False,
                description=(
                    "Ignore edge direction. Only for \"are these related at all\": "
                    "two helpers that merely share a caller count as connected."
                ),
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
        *,
        include_tests: bool = False,
        undirected: bool = False,
        repo: str | None = None,
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
                include_tests=include_tests, undirected=undirected, repo=repo,
            ),
            "Failed to search for a path",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/search_code",
        short_description="Find code by a symbol name, or by exact text or a regex in file bodies",
        description=(
            "Find code without knowing its file.\n\n"
            "`mode='identifier'` (default): every definition with that name, exactly "
            "(case-insensitive), across the repo — `parse_config`, `Client.fetch` and "
            "`method:Client.fetch` all match the `fetch`/`parse_config` definitions.\n\n"
            "`mode='text'` or `mode='regex'`: lines in file bodies that contain the text or "
            "match the regex — a string literal, a call site, a config key. These read "
            "file contents, so they need `path` (a directory, file or glob) and stop after "
            "100 files.\n\n"
            "Results are addresses: read_code reads one, get_neighbour walks from one."
        ),
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING,
                          description=_CONNECTOR_ID_DESC),
            ToolParameter(name="pattern", type=ParameterType.STRING,
                          description="A symbol name, a literal piece of text, or a regex."),
            ToolParameter(name="mode", type=ParameterType.STRING, required=False,
                          default="identifier",
                          description="'identifier' (default), 'text' or 'regex'."),
            ToolParameter(name="path", type=ParameterType.STRING, required=False, default=None,
                          description=("Directory, file or glob to search in. Required for "
                                       "'text' and 'regex'; narrows 'identifier'.")),
            ToolParameter(name="limit", type=ParameterType.INTEGER, required=False,
                          default=DEFAULT_SEARCH_LIMIT, description="Maximum matches."),
            ToolParameter(name="include_tests", type=ParameterType.BOOLEAN, required=False,
                          default=False, description=_INCLUDE_TESTS_DESC),
            ToolParameter(name="repo", type=ParameterType.STRING, required=False, default=None,
                          description=_REPO_DESC),
        ],
        tags=_SEARCH_TAGS,
    )
    async def search_code(
        self,
        connector_id: str,
        pattern: str,
        mode: str = "identifier",
        path: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        result = await self._run(
            "search_code",
            connector_id,
            lambda: search_code_impl(
                graph_provider=self._graph_provider, blob_store=self._blob_store,
                org_id=self._org_id, user_id=self._user_id, connector_id=connector_id,
                pattern=pattern, mode=mode, path=path, limit=limit,
                include_tests=include_tests, repo=repo,
            ),
            "Failed to search the code",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/search_semantic",
        short_description="Find code by what it does, described in plain words",
        description=(
            "Find the code behind a feature, a behaviour or something on screen when you "
            "do not know its names: 'where a failed job is retried', "
            "'what decides which config value wins'. Ranks symbols by meaning, "
            "so near-synonyms match.\n\n"
            "Results are addresses, best first: read_code reads one, get_neighbour and "
            "impact walk from one. For a name you already know, search_code is exact."
        ),
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING,
                          description=_CONNECTOR_ID_DESC),
            ToolParameter(name="query", type=ParameterType.STRING,
                          description="What the code does, in plain words."),
            ToolParameter(name="limit", type=ParameterType.INTEGER, required=False,
                          default=DEFAULT_SEMANTIC_LIMIT, description="Maximum symbols."),
            ToolParameter(name="include_tests", type=ParameterType.BOOLEAN, required=False,
                          default=False, description=_INCLUDE_TESTS_DESC),
            ToolParameter(name="repo", type=ParameterType.STRING, required=False, default=None,
                          description=_REPO_DESC),
        ],
        tags=_SEARCH_TAGS,
    )
    async def search_semantic(
        self,
        connector_id: str,
        query: str,
        limit: int = DEFAULT_SEMANTIC_LIMIT,
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        result = await self._run(
            "search_semantic",
            connector_id,
            lambda: search_semantic_impl(
                graph_provider=self._graph_provider, retrieval_service=self._state.get("retrieval_service"),
                org_id=self._org_id, user_id=self._user_id, connector_id=connector_id,
                query=query, limit=limit, include_tests=include_tests, repo=repo,
            ),
            "Failed to search the code",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/outline",
        short_description="A file's definitions with their signatures and line ranges",
        description=(
            "What a file declares, without its bodies: each class, function and method "
            "with its signature, decorators, first doc line and line range. Read this "
            "before read_code to pick what to read."
        ),
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING,
                          description=_CONNECTOR_ID_DESC),
            ToolParameter(name="file_path", type=ParameterType.STRING, required=False,
                          default=None, description="Repo-relative path of the file."),
            ToolParameter(name="record_id", type=ParameterType.STRING, required=False,
                          default=None,
                          description="A code file's `Record ID` from a search result."),
            ToolParameter(name="include_tests", type=ParameterType.BOOLEAN, required=False,
                          default=False, description=_INCLUDE_TESTS_DESC),
            ToolParameter(name="repo", type=ParameterType.STRING, required=False, default=None,
                          description=_REPO_DESC),
        ],
        tags=_OUTLINE_TAGS,
    )
    async def outline(
        self,
        connector_id: str,
        file_path: str | None = None,
        record_id: str | None = None,
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        path, repo, error = await self._anchor_path(connector_id, file_path, record_id, repo)
        if error is not None:
            return self._to_output(error)
        result = await self._run(
            "outline",
            connector_id,
            lambda: outline_impl(
                graph_provider=self._graph_provider, blob_store=self._blob_store,
                org_id=self._org_id, user_id=self._user_id, connector_id=connector_id,
                file_path=path, include_tests=include_tests, repo=repo,
            ),
            "Failed to outline the file",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/impact",
        short_description="What a change to a symbol can break, grouped by file",
        description=(
            "The blast radius of changing a symbol: everything that calls it, imports "
            "it, or builds on it (inherits, implements, overrides), followed backwards "
            f"up to `depth` hops (default {DEFAULT_IMPACT_DEPTH}), grouped by file with "
            "the nearest hop. Symbols that build on it rather than call it are also named "
            "in each file's `built_on`; `edge_types=['CALLS']` follows calls only (callers, "
            "their callers, ...). Test code it reaches is affected too and is listed apart "
            "in `impacted_tests`. Only edges the parser could prove; `coverage` says when a "
            "language has none."
        ),
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING,
                          description=_CONNECTOR_ID_DESC),
            ToolParameter(name="file_path", type=ParameterType.STRING, required=False,
                          default=None, description="Repo-relative path of the symbol's file."),
            ToolParameter(name="record_id", type=ParameterType.STRING, required=False,
                          default=None,
                          description="A code file's `Record ID` from a search result."),
            ToolParameter(name="qualified_name", type=ParameterType.STRING, required=False,
                          default=None,
                          description=f"The symbol, e.g. `function:parse_config`. {_ADDRESS_RULE}"),
            ToolParameter(name="depth", type=ParameterType.INTEGER, required=False,
                          default=DEFAULT_IMPACT_DEPTH,
                          description=f"Hops to follow backwards, up to {MAX_IMPACT_DEPTH}."),
            ToolParameter(name="include_tests", type=ParameterType.BOOLEAN, required=False,
                          default=False, description=_INCLUDE_TESTS_DESC),
            ToolParameter(name="repo", type=ParameterType.STRING, required=False, default=None,
                          description=_REPO_DESC),
            ToolParameter(
                name="edge_types", type=ParameterType.ARRAY, required=False, default=None,
                description=("Relationships to follow backwards; omit for all of them "
                             f"({', '.join(IMPACT_RELATIONS)}). ['CALLS'] for callers only."),
                items={"type": "string"},
            ),
        ],
        tags=_IMPACT_TAGS,
    )
    async def impact(
        self,
        connector_id: str,
        file_path: str | None = None,
        record_id: str | None = None,
        qualified_name: str | None = None,
        depth: int = DEFAULT_IMPACT_DEPTH,
        *,
        include_tests: bool = False,
        repo: str | None = None,
        edge_types: list[str] | None = None,
    ) -> tuple[bool, str]:
        path, repo, error = await self._anchor_path(connector_id, file_path, record_id, repo)
        if error is not None:
            return self._to_output(error)
        result = await self._run(
            "impact",
            connector_id,
            lambda: impact_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id, file_path=path,
                qualified_name=qualified_name, depth=depth,
                include_tests=include_tests, repo=repo, edge_types=edge_types,
            ),
            "Failed to compute the impact",
        )
        return self._to_output(result)

    @tool(
        path="/tools/codegraph/entry_points",
        short_description="Where control enters a repo: functions nothing in it calls",
        description=(
            "Functions and methods no code in the repo calls — routes, CLIs, jobs, "
            "framework hooks — with conventional names (main, handler, ...) and files "
            "(main.py, routes/, ...) first. A place to start reading an unfamiliar repo. "
            "Python, JavaScript, TypeScript and Java calls are resolved through imports; "
            "other languages' calls are matched by name, and `coverage` says when that applies."
        ),
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING,
                          description=_CONNECTOR_ID_DESC),
            ToolParameter(name="limit", type=ParameterType.INTEGER, required=False,
                          default=DEFAULT_ENTRY_LIMIT, description="Maximum entry points."),
            ToolParameter(name="include_tests", type=ParameterType.BOOLEAN, required=False,
                          default=False, description=_INCLUDE_TESTS_DESC),
            ToolParameter(name="repo", type=ParameterType.STRING, required=False, default=None,
                          description=_REPO_DESC),
        ],
        tags=_ENTRY_TAGS,
    )
    async def entry_points(
        self,
        connector_id: str,
        limit: int = DEFAULT_ENTRY_LIMIT,
        *,
        include_tests: bool = False,
        repo: str | None = None,
    ) -> tuple[bool, str]:
        result = await self._run(
            "entry_points",
            connector_id,
            lambda: entry_points_impl(
                graph_provider=self._graph_provider, org_id=self._org_id,
                user_id=self._user_id, connector_id=connector_id, limit=limit,
                include_tests=include_tests, repo=repo,
            ),
            "Failed to list entry points",
        )
        return self._to_output(result)
