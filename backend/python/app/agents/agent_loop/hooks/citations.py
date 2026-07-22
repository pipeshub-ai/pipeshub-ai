"""`CitationCollector` + `citation_tracking`: the retrieval-tool ->
citation-pipeline bridge described in the migration plan's Phase 3 "Special
Tool Categories" note and implemented here in Phase 5.

`retrieval.search_internal_knowledge` (via `BoundMethodTool.execute()` from
agent_loop_lib) already mutates `AgentContext.tool_state` in place —
`final_results` (appended), `virtual_record_id_to_result` (merged),
`tool_records` (deduped append), `citation_ref_mapper` (updated) — exactly
as it does for the legacy `ChatState` path, since `tool_state` IS a
`ChatState`-shaped dict (see `context.py`). `CitationCollector` is therefore
just a read-only, named view over those four fields for Phase 6's
`RespondPipeline` to consume, not a second accumulation mechanism.

The one piece of real work left for a hook: the dynamic `fetch_full_record`
tool should only be added when `virtual_record_id_to_result` is non-empty,
but `PipesHubToolLoader.load()` runs once before any tool has executed, so
`fetch_full_record` is never in the initial `ToolRegistry`.
`citation_tracking` is the POST_TOOL_USE hook that registers a
`_FetchFullRecordTool` into the live registry the moment retrieval
populates that map, mid-run — and grants the resulting tool NAME to
whichever `AgentSpec`(s) should be able to call it, since registering it on
the registry alone does not make it visible to any agent whose
`spec.tool_names` is an explicit (non-empty) grant (see `tool_schemas_for_turn`
in `agent/tool_loop.py`).

Under domain-agent composition (`domain_agents.py`), retrieval always runs
inside the `internal_exploration_agent` CHILD, never the top-level agent —
so the grant needs to reach two specs, not one: the child's own spec (so
IT can keep fetching full records across its remaining turns) and the
top-level/`root_agent_spec` (so the agent that delegated the search can
also fetch a full record directly once it has a Record ID, e.g. from the
child's summarized findings, without a second round-trip delegation just
to get more detail on something already found).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agents.agent_loop.hooks._tool_naming import INTERNAL_SEARCH_TOOL_NAMES
from app.agents.agent_loop.tool_adapter import _to_tool_output

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
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
        from app.utils.chat_helpers import record_to_message_content
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

        # Mirror the chatbot path's formatting (`RecordsHandler` +
        # `record_to_message_content()` in streaming.py) instead of handing
        # the LLM a raw JSON dict of block_containers/context_metadata — the
        # same records, rendered as the `<record>` text blocks the model
        # already knows how to read from `retrieval_search_internal_knowledge`.
        if isinstance(result, dict) and result.get("ok") and result.get("records"):
            ref_mapper = self._collector.citation_ref_mapper
            parts: list[str] = []
            for record in result["records"]:
                content_list, ref_mapper = record_to_message_content(record, ref_mapper=ref_mapper)
                parts.append("".join(
                    item["text"] for item in content_list if item.get("type") == "text"
                ))
            self._context.tool_state["citation_ref_mapper"] = ref_mapper
            text = "\n".join(parts)
            not_available = result.get("not_available_ids", [])
            if not_available:
                ids_str = ", ".join(f"'{rid}'" for rid in not_available)
                text += f"\n\nNote: The following record(s) are not available: {ids_str}"
            return ToolOutput(success=True, data=text)
        return _to_tool_output(result)


def _grant(spec: "AgentSpec | None", *, require_internal_search_reference: bool) -> None:
    """Appends `_FETCH_FULL_RECORD_TOOL_NAME` onto `spec.tool_names` if it
    isn't there already. `tool_schemas_for_turn` (`agent/tool_loop.py`)
    resolves `registry.schemas(spec.tool_names or None)` every turn when no
    toolset groups are registered (true for the PipesHub loader) — `None`
    means "all registered names", so a non-empty explicit grant would
    otherwise permanently hide a tool registered after spec construction.
    A spec with an EMPTY `tool_names` already sees every registered tool
    (including this one) without needing the append.

    `require_internal_search_reference` gates the grant to `root_agent_spec`
    (see `citation_tracking` below): only an agent whose OWN grant already
    references the internal-search surface — i.e. the one that actually
    delegated to/called it — should also get direct fetch-full-record
    access. Without this guard, an unrelated top-level spec (e.g. deep
    mode's `OrchestratorLoop`, whose grant is deliberately restricted to
    four coordination tools — see `factory.py`) would leak a tool it has
    no business calling."""
    if spec is None or not spec.tool_names:
        return
    if _FETCH_FULL_RECORD_TOOL_NAME in spec.tool_names:
        return
    if require_internal_search_reference and not (set(spec.tool_names) & INTERNAL_SEARCH_TOOL_NAMES):
        return
    spec.tool_names.append(_FETCH_FULL_RECORD_TOOL_NAME)


def citation_tracking(
    context: AgentContext, collector: CitationCollector
) -> "Middleware[ToolResultContext]":
    """POST_TOOL_USE hook: registers `_FetchFullRecordTool` once
    `collector.virtual_records` first becomes non-empty, and (re-)grants its
    tool name every call thereafter — deliberately not scoped to the
    retrieval tool's path, since checking the shared `tool_state` dict after
    every call is equivalent (only retrieval populates that map today) and
    stays correct if another tool ever contributes virtual records too."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        run_scope = ctx.scope.turn.run if ctx.scope is not None else None
        registry = run_scope.runtime.tool_registry if run_scope is not None else None
        if registry is None:
            return

        if not collector.virtual_records:
            return

        # Idempotent: two concurrent tool calls in the same gathered wave
        # can both reach this point believing the tool isn't registered
        # yet. A plain check-then-`register_tool` would raise
        # `DuplicateToolNameError` on the losing side and abort ITS OWN
        # `_grant` calls below — `register_tool_if_absent` never raises
        # for "already registered", so both sides always reach `_grant`.
        registry.register_tool_if_absent(_FetchFullRecordTool(collector, context))

        # The immediate caller (typically the `internal_exploration_agent`
        # child under composition, or the top-level agent itself in flat
        # mode) always gets it — this is who just proved it has records to
        # fetch more of. The request's `root_agent_spec` gets it too, so
        # the agent that DELEGATED the search can also fetch a full record
        # directly on a later turn — see module docstring.
        if run_scope is not None:
            _grant(run_scope.spec, require_internal_search_reference=False)
        _grant(context.root_agent_spec, require_internal_search_reference=True)

    return _middleware


__all__ = ["CitationCollector", "citation_tracking"]
