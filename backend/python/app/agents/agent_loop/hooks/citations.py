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
tool should only be added once the model actually holds Record IDs it could
pass to it, but `PipesHubToolLoader.load()` runs once before any tool has
executed, so `fetch_full_record` is never in the initial `ToolRegistry`.
`citation_tracking` is the POST_TOOL_USE hook that registers a
`_FetchFullRecordTool` into the live registry the moment retrieval
populates `virtual_record_id_to_result` OR a navigational tool records IDs
in `known_record_ids`, mid-run — and grants the resulting tool NAME to
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

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

from app.agents.actions.knowledge_graph.ops.fetch import DEFAULT_FETCH_REASON as _DEFAULT_FETCH_REASON
from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_TOOL_NAME as _FETCH_FULL_RECORD_TOOL_NAME

# ---------------------------------------------------------------------------
# Shared description — used by _FetchFullRecordTool (agent-loop) and kept in
# sync with the langchain docstring in utils/fetch_full_record.py.
#
# Leads with the action, not a decision test: without a gate/judge backstop,
# a balanced "decide whether you need this" framing reads to the model as
# permission to conclude it already has enough — the cheaper path it is
# already biased toward. Naming the two fetch cases as directives ("call
# this before answering") and demoting the skip case to a single
# parenthetical after them keeps "don't fetch" a narrow, explicitly-marked
# exception instead of a co-equal third option.
#
# Still states illustrations rather than an exhaustive checklist: no
# enumeration covers every request shape, and a model matching against a
# checklist fails on anything the checklist missed ("summarize this", "what
# are the risks here"). The reasoning generalizes because it turns on what
# the ANSWER depends on, which the model can always evaluate for itself.
#
# The zero-content case follows the whole-document case because this tool is
# reachable from lookup/navigate/list_files (see `citation_tracking`), where
# the model holds an ID and no blocks.
# ---------------------------------------------------------------------------
_FETCH_FULL_RECORD_DESCRIPTION = (
    "Read one or more records end to end. Search gives you a few matching "
    "blocks per record; lookup_record/navigate/list_files give you an ID and "
    "metadata and no content at all; this gives you everything.\n"
    "Call this BEFORE answering whenever what you currently hold is "
    "incomplete for what the question needs:\n"
    "- The answer is a property of the whole document — a summary or "
    "overview, its risks/gaps/obligations/key points, a review or "
    "assessment, a comparison of documents, whether it mentions something "
    "anywhere, anything asking for all of something. A handful of blocks "
    "CANNOT support that answer, however relevant they look, because the "
    "parts you were not given are exactly what you would be implying are "
    "unimportant.\n"
    "- You hold no passage at all — the record came from lookup, navigation "
    "or listing, so you have its ID and metadata and nothing it says. Never "
    "infer content from a title.\n"
    "(Skip only when the exact fact needed — a date, a name, a number, a "
    "status, one clause — is already visible in a block you hold, or "
    "metadata alone settles the question outright, e.g. a ticket's status "
    "or assignee.)\n"
    "Those are illustrations, not a checklist — apply the same reasoning to "
    "whatever was actually asked.\n"
    "Pass every record_id you need in ONE call, taken from a candidate list, a "
    "'Record ID' field, or a record_id=/node_id= shown by navigation — use it "
    "exactly as shown (it may be a short label like 'R3') and never invent "
    "IDs. Large records return a continuation hint giving the start_block for "
    "the next slice."
)

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

    @property
    def known_record_ids(self) -> set[str]:
        """Record IDs surfaced to the model by a knowledge tool that has no
        citation payload to contribute — `knowledgegraph.navigate`,
        `knowledgegraph.lookup_record`, `knowledgehub.list_files` (written
        via `remember_record_ids()`). Retrieval proves the same thing
        through `virtual_records`; without this, an agent that resolved an
        epic and walked its stories would hold Record IDs and have no tool
        able to read them."""
        ids = self._context.tool_state.get("known_record_ids")
        return ids if isinstance(ids, set) else set()

    @property
    def web_records(self) -> list[dict[str, Any]]:
        """Citation-ready web_search/fetch_url records, accumulated by
        `WebToolAdapter.execute()` (see `web_tool_adapter.py`) as each call
        completes — the web-tool analogue of `final_results`/`tool_records`
        above."""
        return self._context.tool_state.get("web_records") or []


class _FetchFullRecordTool(Tool):
    """Rebuilds the underlying `create_fetch_full_record_tool()` LangChain
    tool from `collector.virtual_records` fresh on every `execute()` call,
    rather than freezing the map at registration time — `retrieval.py`
    *replaces* (not mutates in place) `tool_state["virtual_record_id_to_result"]`
    on every call (`self.state[...] = {**existing, **new}`), so a later
    retrieval call within the same run would otherwise be invisible to a
    tool instance built once from the first snapshot.
    """

    _ACCEPTED_ARGS = ("record_ids", "reason", "start_block", "max_blocks")

    def __init__(self, collector: CitationCollector, context: AgentContext) -> None:
        self._collector = collector
        self._context = context

    @property
    def name(self) -> str:
        return _FETCH_FULL_RECORD_TOOL_NAME

    @property
    def display_name(self) -> str | None:
        return "Fetching additional data"

    @property
    def short_description(self) -> str:
        return "Fetch the complete content of one or more records by ID"

    @property
    def description(self) -> str:
        return _FETCH_FULL_RECORD_DESCRIPTION

    @property
    def path(self) -> str:
        return "/dynamic/dynamic/fetch_full_record"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="record_ids",
                type=ParameterType.ARRAY,
                description=(
                    "Record IDs to fetch — use the exact Record ID values shown in the "
                    "candidate list or context metadata (may be short labels like 'R1', "
                    "'R2'). Do NOT invent IDs."
                ),
                required=True,
                items={"type": "string"},
            ),
            ToolParameter(
                name="reason",
                type=ParameterType.STRING,
                description="Brief explanation of why the full records are needed",
                required=False,
                default=_DEFAULT_FETCH_REASON,
            ),
            ToolParameter(
                name="start_block",
                type=ParameterType.INTEGER,
                description=(
                    "Block index to start from (inclusive). Use the value from a previous "
                    "truncation hint to continue reading a large record. Default 0."
                ),
                required=False,
                default=0,
            ),
            ToolParameter(
                name="max_blocks",
                type=ParameterType.INTEGER,
                description=(
                    "Maximum number of blocks to return per record. Leave unset to use the "
                    "server default. Set only when you need a smaller slice."
                ),
                required=False,
            ),
        ]

    def validate(self, kwargs: dict[str, Any]) -> None:
        return

    @staticmethod
    def _coerce_start_block(raw: Any) -> int:  # noqa: ANN401
        """`start_block` is model input. A float or a numeric string is an
        honest mistake and is accepted; anything else is a tool error rather
        than a traceback, and a negative offset means the beginning."""
        if raw is None or raw == "":
            return 0
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"start_block must be a whole number, got {raw!r}."
            ) from exc

    def _live_virtual_records(self) -> dict[str, Any]:
        """The mapping `_fetch_multiple_records_impl` writes downloaded records
        back into, so it must be the object in `tool_state` and NOT
        `CitationCollector.virtual_records`, whose `or {}` returns a throwaway
        dict while the map is empty — losing the write-back and re-downloading
        on every repeat fetch.

        Records persisting here skip the ACL re-check a fresh id gets; safe
        because `tool_state` is per HTTP request, hence per user.
        """
        state = self._context.tool_state
        records = state.get("virtual_record_id_to_result")
        if not isinstance(records, dict):
            records = {}
            state["virtual_record_id_to_result"] = records
        return records

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record

        # Not forwarded as `**kwargs`: a model sending `record_id=` (singular)
        # must get a correctable error, not a silent empty fetch.
        unexpected = sorted(set(kwargs) - set(self._ACCEPTED_ARGS))
        if unexpected:
            return ToolOutput(
                success=False,
                error=(
                    f"Unexpected argument(s): {', '.join(unexpected)}. "
                    f"Accepted: {', '.join(self._ACCEPTED_ARGS)}."
                ),
            )

        record_ids = kwargs.get("record_ids") or []
        if isinstance(record_ids, str):
            record_ids = [record_ids]

        try:
            start_block = self._coerce_start_block(kwargs.get("start_block"))
        except ValueError as exc:
            return ToolOutput(success=False, error=str(exc))

        # One offset cannot continue several records: applied to all of them it
        # re-reads whichever stopped later and skips the start of the others.
        # Continuation is therefore one record at a time.
        if start_block > 0 and len(record_ids) > 1:
            return ToolOutput(
                success=False,
                error=(
                    "start_block continues one record at a time — it would be applied "
                    f"to all {len(record_ids)} ids. Call again with a single record_id "
                    "(the one named in the truncation hint) plus its start_block."
                ),
            )

        ref_mapper_in = self._collector.citation_ref_mapper
        output, ref_mapper = await execute_fetch_record(
            context=self._context,
            virtual_records=self._live_virtual_records(),
            citation_ref_mapper=ref_mapper_in,
            record_ids=record_ids,
            reason=kwargs.get("reason") or _DEFAULT_FETCH_REASON,
            start_block=start_block,
            max_blocks=kwargs.get("max_blocks"),
        )
        if ref_mapper is not ref_mapper_in:
            self._context.tool_state["citation_ref_mapper"] = ref_mapper
        return output


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
    """POST_TOOL_USE hook: registers `_FetchFullRecordTool` once the model
    holds Record IDs it could pass to it, and (re-)grants its tool name
    every call thereafter — deliberately not scoped to any tool's path,
    since checking the shared `tool_state` dict after every call is
    equivalent and stays correct as more tools surface records.

    Two things prove the model holds IDs: `collector.virtual_records`
    (retrieval, attachments) and `collector.known_record_ids` (navigate,
    lookup_record, list_files). The second is what makes hierarchy walking
    useful — the underlying impl resolves any accessible Record ID from the
    graph and blob store, not just ones a search produced
    (`_fetch_multiple_records_impl` in `utils/fetch_full_record.py`)."""

    async def _middleware(ctx: ToolResultContext, next_fn: "Next") -> None:
        await next_fn()

        run_scope = ctx.scope.turn.run if ctx.scope is not None else None
        registry = run_scope.runtime.tool_registry if run_scope is not None else None
        if registry is None:
            return

        if not collector.virtual_records and not collector.known_record_ids:
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
            if getattr(run_scope, "visible_tools", None) is not None:
                run_scope.visible_tools.add(_FETCH_FULL_RECORD_TOOL_NAME)
        _grant(context.root_agent_spec, require_internal_search_reference=True)

    return _middleware


def ensure_fetch_full_record_available(
    context: AgentContext,
    *,
    run_scope: object | None = None,
    registry: object | None = None,
) -> None:
    """Register ``_FetchFullRecordTool`` and grant it — callable outside POST_TOOL_USE.

    ``citation_tracking`` handles this reactively (after a tool call), but
    ``attachment_rehydration`` needs the tool available BEFORE the model's
    first action on a follow-up turn.  This function extracts the shared
    registration + grant logic so both call sites stay in sync.

    ``run_scope`` and ``registry`` are optional — if omitted the caller is
    responsible for ensuring the tool is visible (e.g. via ``_grant``
    directly).  When provided they mirror ``citation_tracking``'s behaviour.
    """
    collector = CitationCollector(context)
    if registry is not None:
        registry.register_tool_if_absent(_FetchFullRecordTool(collector, context))
    if run_scope is not None and hasattr(run_scope, "spec"):
        _grant(run_scope.spec, require_internal_search_reference=False)
    _grant(context.root_agent_spec, require_internal_search_reference=False)


__all__ = ["CitationCollector", "citation_tracking", "ensure_fetch_full_record_available"]
