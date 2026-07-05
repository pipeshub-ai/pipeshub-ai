"""`CitationCollector` + `citation_tracking`: the retrieval-tool ->
citation-pipeline bridge described in the migration plan's Phase 3 "Special
Tool Categories" note and implemented here in Phase 5.

`retrieval.search_internal_knowledge` (via `PipesHubToolAdapter.execute()` ->
`RegistryToolWrapper.arun()`) already mutates `AgentContext.tool_state`
in place — `final_results` (appended), `virtual_record_id_to_result`
(merged), `tool_records` (deduped append), `citation_ref_mapper` (updated) —
exactly as it does for the legacy `ChatState` path, since `tool_state` IS a
`ChatState`-shaped dict (see `context.py`). `CitationCollector` is therefore
just a read-only, named view over those four fields for Phase 6's
`RespondPipeline` to consume, not a second accumulation mechanism.

The one piece of real work left for a hook: `get_agent_tools_with_schemas()`
(`tool_system.py`) only adds the dynamic `fetch_full_record` tool when
`virtual_record_id_to_result` is ALREADY non-empty at load time — but
`PipesHubToolLoader.load()` (Phase 3) runs once, before any tool has
executed, so `fetch_full_record` is never in the initial `ToolRegistry`.
`citation_tracking` is the POST_TOOL_USE hook that registers a
`_FetchFullRecordTool` into the live registry the moment retrieval
populates that map, mid-run.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agents.agent_loop.tool_adapter import _to_tool_output

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

_FETCH_FULL_RECORD_TOOL_NAME = "dynamic_fetch_full_record"


class CitationCollector:
    """Read-only view over the citation-related fields of `AgentContext.tool_state`."""

    def __init__(self, context: AgentContext) -> None:
        self._context = context

    @property
    def final_results(self) -> list[Any]:
        return self._context.tool_state.get("final_results") or []

    @property
    def virtual_records(self) -> dict[str, Any]:
        return self._context.tool_state.get("virtual_record_id_to_result") or {}

    @property
    def tool_records(self) -> list[Any]:
        return self._context.tool_state.get("tool_records") or []

    @property
    def citation_ref_mapper(self) -> Any:  # noqa: ANN401
        return self._context.tool_state.get("citation_ref_mapper")


class _FetchFullRecordTool(Tool):
    """Rebuilds the underlying `create_fetch_full_record_tool()` LangChain
    tool from `collector.virtual_records` fresh on every `execute()` call,
    rather than freezing the map at registration time — `retrieval.py`
    *replaces* (not mutates in place) `tool_state["virtual_record_id_to_result"]`
    on every call (`self.state[...] = {**existing, **new}`), so a later
    retrieval call within the same run would otherwise be invisible to a
    tool instance built once from the first snapshot.
    """

    def __init__(self, collector: CitationCollector, context: AgentContext) -> None:
        self._collector = collector
        self._context = context

    @property
    def name(self) -> str:
        return _FETCH_FULL_RECORD_TOOL_NAME

    @property
    def short_description(self) -> str:
        return "Fetch the complete content of one or more records by ID"

    @property
    def description(self) -> str:
        return (
            "Fetch the complete content of one or more records when the provided blocks are "
            "insufficient to answer the query. Pass ALL record IDs in a SINGLE call using the "
            "record_ids parameter. record_ids must be taken directly from the 'Record ID :' "
            "field shown in the context metadata for each record — do NOT invent IDs."
        )

    @property
    def path(self) -> str:
        return "/dynamic/dynamic/fetch_full_record"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="record_ids",
                type=ParameterType.ARRAY,
                description="Record IDs to fetch — use the exact 'Record ID :' values from the context",
                required=True,
                items={"type": "string"},
            ),
            ToolParameter(
                name="reason",
                type=ParameterType.STRING,
                description="Brief explanation of why the full records are needed",
                required=False,
                default="Fetching full record content for comprehensive answer",
            ),
        ]

    def validate(self, kwargs: dict[str, Any]) -> None:
        return

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        structured_tool = create_fetch_full_record_tool(
            self._collector.virtual_records,
            org_id=self._context.org_id,
            graph_provider=self._context.graph_provider,
        )
        try:
            result = await structured_tool.coroutine(**kwargs)
        except Exception as exc:
            return ToolOutput(success=False, error=str(exc))
        return _to_tool_output(result)


def citation_tracking(
    context: AgentContext, collector: CitationCollector
) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook: registers `_FetchFullRecordTool` once
    `collector.virtual_records` first becomes non-empty. Deliberately not
    scoped to the retrieval tool's path — checking the shared `tool_state`
    dict after every call is equivalent (only retrieval populates that map
    today) and stays correct if another tool ever contributes virtual
    records too."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        run_scope = ctx.scope.turn.run if ctx.scope is not None else None
        registry = run_scope.runtime.tool_registry if run_scope is not None else None
        if registry is None or registry.has(_FETCH_FULL_RECORD_TOOL_NAME):
            return

        if not collector.virtual_records:
            return

        registry.register_tool(_FetchFullRecordTool(collector, context))
        # `tool_schemas_for_turn` (`agent/tool_loop.py`) resolves
        # `registry.schemas(spec.tool_names or None)` every turn when no
        # toolset groups are registered (true for the PipesHub loader) —
        # `None` means "all registered names" so a non-empty explicit grant
        # would otherwise permanently hide a tool registered after spec
        # construction.
        if run_scope is not None and run_scope.spec.tool_names:
            run_scope.spec.tool_names.append(_FETCH_FULL_RECORD_TOOL_NAME)

    return _middleware


__all__ = ["CitationCollector", "citation_tracking"]
