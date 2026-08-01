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
from app.agents.agent_loop.tool_adapter import _to_tool_output

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec
    from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext

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

# Conservative default: enough for most models but safe for small/local ones.
# Only reduced (never raised) by the known context window.
# Override with PIPESHUB_FULL_RECORD_MAX_BLOCKS (int > 0) for deployment tuning.
_DEFAULT_FULL_RECORD_MAX_BLOCKS = 200


def _resolve_block_cap(model_name: str, requested_max: int | None) -> int:
    """
    Resolve the effective block cap for a fetch.

    The cap is the minimum of the configured default and the caller's explicit
    request. Never exceeds _DEFAULT_FULL_RECORD_MAX_BLOCKS unless the env var
    is set higher (which is the operator's choice, not ours).

    `get_context_window()` returns 128k for unknown/local models — too optimistic
    for a small LLM — so we do NOT blindly raise the cap from the context window.
    """
    import os

    env_raw = os.getenv("PIPESHUB_FULL_RECORD_MAX_BLOCKS", "")
    try:
        env_cap = int(env_raw) if env_raw.strip() else _DEFAULT_FULL_RECORD_MAX_BLOCKS
        if env_cap <= 0:
            env_cap = _DEFAULT_FULL_RECORD_MAX_BLOCKS
    except ValueError:
        import logging
        logging.getLogger(__name__).warning(
            "Invalid PIPESHUB_FULL_RECORD_MAX_BLOCKS=%r, using %d",
            env_raw, _DEFAULT_FULL_RECORD_MAX_BLOCKS,
        )
        env_cap = _DEFAULT_FULL_RECORD_MAX_BLOCKS

    if requested_max is not None and requested_max > 0:
        return min(env_cap, requested_max)
    return env_cap


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
                default="Fetching full record content for comprehensive answer",
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

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        from app.utils.chat_helpers import record_to_message_content
        from app.utils.fetch_full_record import create_fetch_full_record_tool

        start_block: int = int(kwargs.pop("start_block", 0) or 0)
        requested_max: int | None = kwargs.pop("max_blocks", None)
        block_cap = _resolve_block_cap(self._context.model_name, requested_max)

        # TEMPORARY token-savings experiment (opt-in, disabled by default —
        # see `ChatQuery.enableRecordIdShortening`): an earlier retrieval/
        # search/navigate/lookup_record/list_files call may have handed the
        # model a short "R<n>" label instead of the full Record ID (see
        # `RecordIdShortener` in `utils/chat_helpers.py`). Resolve it back
        # before matching against `virtual_records`. Full ids the model
        # copied verbatim pass through `.resolve()` unchanged. Created here
        # (not just read) so a fetch that happens to be the first knowledge
        # call this request still shortens the ids it prints below. `None`
        # when the flag is off — record_ids pass through untouched.
        from app.utils.chat_helpers import get_record_id_shortener_if_enabled
        record_id_shortener = get_record_id_shortener_if_enabled(self._context.tool_state)
        raw_record_ids = kwargs.get("record_ids")
        if raw_record_ids and record_id_shortener is not None:
            kwargs["record_ids"] = [
                record_id_shortener.resolve(rid) for rid in raw_record_ids
            ]

        structured_tool = create_fetch_full_record_tool(
            self._collector.virtual_records,
            org_id=self._context.org_id,
            graph_provider=self._context.graph_provider,
            # Required for IDs the model got from navigate/lookup_record
            # rather than from retrieval — those are not in the map, so the
            # fetch has to re-check access itself.
            user_id=self._context.user_id,
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
                # Reads start at block 0 unless the caller asked otherwise.
                # Starting at the first block retrieval matched instead drops
                # everything before it — for a match near the end that returns
                # a short tail as if it were the document. Oversized records are
                # bounded by `block_cap`, which appends a continuation hint.
                content_list, ref_mapper = record_to_message_content(
                    record,
                    ref_mapper=ref_mapper,
                    start_block=start_block,
                    max_blocks=block_cap,
                )
                parts.append("".join(
                    item["text"] for item in content_list if item.get("type") == "text"
                ))
            self._context.tool_state["citation_ref_mapper"] = ref_mapper
            text = "\n".join(parts)
            # TEMPORARY token-savings experiment: shorten every "Record ID:"
            # this fetch prints (record headers, FK table rows) back down to
            # the same "R<n>" label the model already saw from retrieval/
            # navigate/lookup_record — see `RecordIdShortener`.
            if record_id_shortener is not None:
                text = record_id_shortener.shorten_record_ids_in_text(text)
            text += (
                "\n\nCite facts from the above using each block's `[refN]` id "
                "as a markdown link, e.g. [source](ref2). Do NOT use external URLs as citations."
            )
            not_available = result.get("not_available_ids", [])
            if not_available:
                if record_id_shortener is not None:
                    not_available = [
                        record_id_shortener.shorten_if_known(rid) for rid in not_available
                    ]
                ids_str = ", ".join(f"'{rid}'" for rid in not_available)
                text += f"\n\nNote: The following record(s) are not available: {ids_str}"
            # Track fetched record IDs for the gate.
            for record in result["records"]:
                rid = record.get("id")
                if rid:
                    self._context.full_records_fetched.add(rid)
                    self._context.tool_state.setdefault("full_records_fetched", set()).add(rid)
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
