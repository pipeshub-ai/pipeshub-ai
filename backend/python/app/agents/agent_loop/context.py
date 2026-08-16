"""`AgentContext`: focused per-request context for the agent-loop adapter
layer's tool execution and prompt building — the Phase 3 replacement for
threading a full `ChatState` through `PipesHubToolAdapter`/`PipesHubToolLoader`.

`ChatState` (`app.modules.agents.qna.chat_state`) still exists and is still
built by `build_initial_state()` — now the shared state constructor for
every agent-loop entry point (`stream_bridge.run_agent_loop_stream()`,
`chat_modes.bridge.run_chat_stream()`) after LangGraph's removal.
`AgentContext` doesn't replace it — it's a narrower, validated view
constructed from the same route-handler inputs, carrying only what tool
execution and prompt assembly need.

`ToolInstanceCreator` (`app.agents.agent_loop.instance_creator`) accepts an
`AgentContext` and reads typed fields (``config_service``, ``logger``,
``tool_to_toolset_map``, ``toolset_configs``, ``user_id``) directly from it.
PipesHub tool classes read and mutate `AgentContext.tool_state` — a plain
`ChatState`-shaped dict seeded once from the identity/service fields below,
then shared by reference across every tool call for the life of the request.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr


class AgentContext(BaseModel):
    """Per-request context for tool execution and prompt building."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # Cached once per request — SourceCatalog.from_state walks agent_knowledge
    # and re-renders a few KB of routing text; prompt_builder + domain_agents
    # both need the same catalog in one turn.
    _source_catalog: Any = PrivateAttr(default=None)

    # Identity
    org_id: str
    user_id: str
    user_email: str
    user_info: dict[str, Any] = Field(default_factory=dict)
    org_info: dict[str, Any] = Field(default_factory=dict)
    is_service_account: bool = False

    # Services (injected, not serializable)
    retrieval_service: Any = None
    graph_provider: Any = None
    config_service: Any = None
    blob_store: Any = None
    logger: Any = None
    llm: Any = None

    # Tool config
    agent_toolsets: list[dict[str, Any]] = Field(default_factory=list)
    tool_to_toolset_map: dict[str, str] = Field(default_factory=dict)
    toolset_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    web_search_config: dict[str, Any] | None = None

    # MCP servers — parallel to the toolset fields above. `mcp_servers` is the
    # attached/authenticated instance metadata (from `agent.py`'s chat handler);
    # `mcp_server_configs` is SENSITIVE (contains resolved credentials, keyed by
    # instanceId). Consumed by `MCPAccessResolver`/`MCPToolProvider`
    # (`app/agents/agent_loop/mcp_access.py` / `mcp_tool_loader.py`).
    mcp_servers: list[dict[str, Any]] = Field(default_factory=list)
    mcp_server_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)

    # Knowledge config
    has_knowledge: bool = False
    apps: list[str] | None = None
    kb: list[str] | None = None
    agent_knowledge: list[dict[str, Any]] | None = None
    # Names of skills explicitly assigned to this agent in Agent Builder
    # (`AGENT_HAS_SKILL` edges — see `_parse_skills`/`_create_skill_edges`
    # in `api/routes/agent.py`). Empty/None means no explicit assignment:
    # `skills_wiring.build_skill_manager` then leaves the manager
    # unwrapped, preserving today's full creator+builtin catalog. Non-empty
    # triggers a `ScopedSkillManager` wrap that narrows the catalog/search/
    # activation surface to exactly this allowlist.
    agent_skills: list[str] | None = None
    connector_configs: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    has_sql_connector: bool = False
    has_sql_knowledge: bool = False
    has_slack_connector: bool = False
    has_slack_knowledge: bool = False
    is_multimodal_llm: bool = False

    # TEMPORARY token-savings experiment — see `RecordIdShortener` in
    # `utils/chat_helpers.py`. Opt-in per request (`ChatQuery.
    # enableRecordIdShortening`, default False). Read by every knowledge
    # tool's lazy-creation site via `tool_state["enable_record_id_shortening"]`
    # (mirrored here for typed access) before minting a shortener — never
    # created when this is False, so full record ids pass through unchanged.
    enable_record_id_shortening: bool = False

    # Image attachment blocks (LangChain ``image_url`` dicts) resolved by
    # ``resolve_attachments_for_goal`` for the current turn. Consumed by
    # ``shape_image_injection`` (PRE_MODEL hook) to inject ``ImagePart``
    # objects into the initial ``UserMessage`` on every model call.
    attachment_image_blocks: list[dict[str, Any]] = Field(default_factory=list)

    # Agent config
    system_prompt: str | None = None
    instructions: str | None = None
    # Org-level custom instructions from the workspace "Custom Instructions"
    # settings page (Node.js `customSystemPrompt*` fields). Injected for
    # Chat Assistant `/chat/stream` modes AND Universal Agent Mode
    # (`agentIdPlaceholder` via `agent.py`). Real Agent Builder agents never
    # populate this field — they use their own `system_prompt`/`instructions`.
    custom_instructions: str | None = None
    timezone: str | None = None
    current_time: str | None = None

    # Mutable per-request state
    conversation_id: str | None = None
    has_ui_client: bool = False

    # Conversation history (for multi-turn seeding)
    previous_conversations: list[dict[str, Any]] = Field(default_factory=list)

    # Populated by `PipesHubToolLoader.load()`: toolset registry name ->
    # `"not_authenticated"` (configured, `ToolsetAuthError`/auth-flavored
    # `ValueError` from `ToolInstanceCreator`) | `"error"` (any other load
    # failure). Read by `PipesHubGlobalCatalogFallback` (auth-aware
    # `search_tools` reasons) and `capability_summary.py` (proactive
    # "needs authentication" flagging) — see `tool_loader.py`'s per-toolset
    # try/except for how this gets populated. Also mirrored onto
    # `tool_state["toolset_load_failures"]` (same dict object — see
    # `_seed_tool_state`) so `build_capability_summary(state)` can read it
    # without a hard dependency on `AgentContext`.
    toolset_load_failures: dict[str, str] = Field(default_factory=dict)

    # Populated by `MCPToolProvider.load_into()` (`mcp_tool_loader.py`): one
    # entry per attached MCP instance whose runtime discovery/registration
    # failed (timeout, connection error, ...) — soft-skip, not a hard-block
    # (unlike the chat-route's attach-time authentication hard-block). Mirrored
    # onto `tool_state["mcp_tool_load_failures"]` the same way
    # `toolset_load_failures` is, for the same reason (see comment above).
    mcp_tool_load_failures: list[dict[str, Any]] = Field(default_factory=list)

    # Group names (as registered on the per-request `ToolRegistry`, i.e.
    # `PipesHubToolLoader`'s `group_name`, not the registry's raw toolset
    # key) that loaded with `essential=True` metadata (see `@Toolset`/
    # `ToolsetBuilder.as_essential()`) THIS request — e.g. "retrieval"/
    # "knowledgehub" only when knowledge is attached. `factory.py` derives
    # `AgentSpec.pinned_toolsets` from this list instead of a hardcoded
    # pin list, so these toolsets stay visible from turn 0 under lazy tool
    # disclosure regardless of whatever else got grouped this request.
    essential_toolset_names: list[str] = Field(default_factory=list)

    # `EventSink` (`app.modules.agents.event_sink`) for hooks that must push
    # SSE events mid-run (e.g. Phase 5's `ask_user_question_sse` hook) — the
    # `SSEEventEmitter` implementation is wired in by Phase 7; `None` here
    # just means "no streaming client for this call" (e.g. background/test
    # runs), matching how `has_ui_client=False` is handled today.
    event_sink: Any = None

    # `TranscriptCollector` (`protocol/transcript_collector.py`) for this
    # request — `None` for `protocol == "legacy"` (parts-transcript is an
    # `agui`-only feature) or whenever there's no streaming client. Set by
    # `PipesHubAgentFactory._build_event_emitter` at the same point
    # `event_sink`/`protocol` are known; read by `AnswerFinalizer`
    # (`respond.py`) once the run completes to fill `completion_data["parts"]`.
    transcript_collector: Any = None

    # Negotiated wire protocol for this request — "legacy" (default) or
    # "agui". Set once by `stream_bridge.py` from the route's resolved
    # protocol (see `app/api/routes/agent.py::chat_stream`). Drives both
    # which `EventEmitter` `PipesHubAgentFactory` wires onto the runtime
    # AND which `ProtocolFormatter` the direct `EventSink` writers use
    # (see `formatter` property below) — one flag, no parallel code paths
    # inside any individual producer.
    protocol: str = "legacy"

    # The top-level agent's `run_ctx.run_id`, stashed here by
    # `stream_bridge.py` right after `PipesHubAgentFactory.create()`
    # returns — `AnswerFinalizer`/`clarification`/hooks never hold an
    # `Agent`/`RunContext` reference themselves, but `AGUIFormatter` needs
    # a `runId` to stamp onto the frames it builds directly (STATE_SNAPSHOT,
    # RUN_FINISHED, RUN_ERROR, CUSTOM). `None` until then; irrelevant for
    # `LegacyFormatter`.
    run_id: str | None = None

    # Per-request agent_loop_lib `SandboxManager` (only set when code
    # execution is enabled for this request — see
    # `PipesHubAgentFactory._register_coding_sandbox`). Stashed here rather
    # than threaded through `create()`'s return value so
    # `stream_bridge.py`'s `_produce()` can tear every provisioned sandbox
    # down in its `finally` block without widening the `(Agent, AgentRuntime)`
    # contract `test_factory_wiring.py` asserts throughout.
    sandbox_manager: Any = None

    # Flipped to True by `sandbox_bridge.py::coding_sandbox_artifact_bridge`
    # the moment ANY `run_code` call (top-level or from a spawned
    # `coding_agent` child — both dispatch through this same shared
    # `AgentContext`) produces at least one artifact.
    artifacts_produced_this_run: bool = False
    # Every `ArtifactMetadata.model_dump()` registered via
    # `ArtifactRegistryService` during this run (any agent in the spawn
    # tree — same shared `AgentContext`), appended by
    # `sandbox_bridge.py`/the artifact agent tools. Read by
    # `spawn_scheduler`/`AgentResult` propagation so a parent/sibling agent
    # can see and reuse artifacts a child produced without re-querying the
    # registry. Append-only for the life of the request; never cleared.
    artifacts_registered_this_run: list[dict[str, Any]] = Field(default_factory=list)
    # `"{artifact_id}:{version}"` keys already DELIVERED to the user this
    # request (SSE `artifact` event emitted + `::artifact` marker queued).
    # A model that re-runs the same code re-registers the same content —
    # content-hash dedup keeps the version identical, and this set keeps
    # the delivery pipeline from attaching the same download card N times
    # (see `sandbox_bridge._register_run_code_artifacts`).
    delivered_artifact_versions: set[str] = Field(default_factory=set)
    # Bounds `completion_gate`'s empty-response nudges so a genuinely
    # stuck run terminates rather than looping until `max_turns`.
    completion_gate_nudges: int = 0

    # The top-level `AgentSpec` for this request (same object `factory.py`
    # builds `Agent` from), stashed here so `hooks/citations.py` can grant
    # `dynamic_fetch_full_record` to it too — not just to whichever
    # `RunScope` happened to call retrieval. Under domain-agent composition
    # that's always the `internal_exploration_agent` CHILD, never the
    # top-level agent itself, so without this reference the agent that
    # delegated the search could never fetch a full record it decides it
    # needs after reading the delegate's summary. `Any` (not `AgentSpec`)
    # to avoid a hard dependency from this adapter-layer module onto
    # `agent_loop_lib.agent.spec`.
    root_agent_spec: Any = None

    # Set once in factory.create() from the intent call (or the regex fallback
    # when skip_intent is active). Read by retrieval/search to frame the
    # candidate-list header (imperative vs optional). The list itself is
    # appended whenever any incomplete record exists, regardless of this flag.
    # Tree-wide: top-level agent and every spawned child share this context.
    needs_whole_document: bool = False

    # The model identifier passed to factory.create() — used by the
    # fetch-tool block cap. Stored here (not only on the transport) so
    # ensure_fetch_full_record_available() (called from attachment_resolver,
    # outside _build_hooks) can reach it without the TransportRegistry.
    model_name: str = ""

    # Model profile fields threaded from etcd llm_config at the route layer.
    # Set alongside model_name in factory.create() so PipesHubPromptBuilder
    # can inject tier-appropriate guidance without re-reading etcd.
    # `""` / None are safe defaults for tests and CLI runs that don't
    # go through get_llm_for_chat.
    llm_provider: str = ""
    context_length: int | None = None
    is_reasoning_model: bool = False

    # Record IDs for which dynamic_fetch_full_record was called this request.
    # Written directly by _FetchFullRecordTool.execute() (citations.py /
    # ops/fetch.py) as each fetch completes. Read by retrieval.py/search.py
    # to exclude already-fetched records from their own candidate-list
    # rendering. Pydantic BaseModel requires default_factory for mutable
    # defaults.
    full_records_fetched: set[str] = Field(default_factory=set)

    # The live, mutable ChatState-shaped dict PipesHub tools read/write
    # through unchanged — see module docstring. Populated by
    # `model_post_init`.
    tool_state: dict[str, Any] = Field(default_factory=dict)

    @property
    def model_profile(self) -> Any:
        """Resolved `ModelProfile` for this request.

        Lazily imports ``model_tier`` to avoid import-time overhead on
        routes that don't use tier-aware prompts.  Returns ``UNKNOWN_PROFILE``
        when model info isn't available (tests, CLI).
        """
        from app.agents.agent_loop.model_tier import UNKNOWN_PROFILE, ModelProfile

        if not self.llm_provider and not self.model_name:
            return UNKNOWN_PROFILE
        return ModelProfile.resolve(
            provider=self.llm_provider or "",
            model_name=self.model_name,
            context_length=self.context_length,
            is_reasoning=self.is_reasoning_model,
        )

    @property
    def formatter(self) -> Any:
        """`ProtocolFormatter` for this request's negotiated `protocol` —
        see `protocol/formatter.py`. Both formatters are stateless, so this
        returns the shared module-level singleton rather than allocating a
        new instance on every access (this property is read multiple times
        per streamed chunk). Imported lazily to avoid a hard import-time
        dependency from this narrow adapter-context module onto the
        protocol package."""
        from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER, LEGACY_FORMATTER

        return AGUI_FORMATTER if self.protocol == "agui" else LEGACY_FORMATTER

    @property
    def artifact_registry(self) -> Any:
        """`ArtifactRegistryService` for this request — `None` when either
        `graph_provider` or `blob_store` isn't wired (background/test runs
        with no DB/storage access). Constructed on each access rather than
        cached: no I/O happens in its `__init__`, and this way it always
        reflects the current `graph_provider`/`blob_store` if either is
        ever swapped mid-request. Imported lazily to avoid a hard
        import-time dependency from this narrow adapter-context module onto
        the artifact registry package."""
        if self.graph_provider is None or self.blob_store is None:
            return None
        from app.services.artifact_registry import ArtifactRegistryService

        return ArtifactRegistryService(self.graph_provider, self.blob_store)

    @classmethod
    def from_chat_state(
        cls, state: dict[str, Any], *, event_sink: Any = None, protocol: str = "legacy",
    ) -> "AgentContext":
        """Builds an `AgentContext` from an already-built `ChatState` dict
        (Phase 8, `stream_bridge.py`) rather than re-deriving every field a
        second time. `build_initial_state()` (`chat_state.py`) already does
        100% of the derivation work every agent-loop entry point needs —
        apps/kb extraction from `knowledge`, `tool_to_toolset_map`,
        `has_sql_knowledge`, etc. — so this simply lifts the identity/
        service/config fields out for typed access while passing the
        SAME dict through as `tool_state`. That
        dict-identity is deliberate: `model_post_init`'s `setdefault()` calls
        below are then no-ops (every key already exists with its real,
        already-derived value), and every PipesHub tool mutates the one dict
        both this context and `RespondPipeline` read from.
        """
        return cls(
            org_id=state.get("org_id", ""),
            user_id=state.get("user_id", ""),
            user_email=state.get("user_email", ""),
            user_info=state.get("user_info") or {},
            org_info=state.get("org_info") or {},
            is_service_account=bool(state.get("is_service_account", False)),
            retrieval_service=state.get("retrieval_service"),
            graph_provider=state.get("graph_provider"),
            config_service=state.get("config_service"),
            blob_store=state.get("blob_store"),
            logger=state.get("logger"),
            llm=state.get("llm"),
            agent_toolsets=state.get("agent_toolsets") or [],
            tool_to_toolset_map=state.get("tool_to_toolset_map") or {},
            toolset_configs=state.get("toolset_configs") or {},
            mcp_servers=state.get("mcp_servers") or [],
            mcp_server_configs=state.get("mcp_server_configs") or {},
            web_search_config=state.get("web_search_config"),
            has_knowledge=bool(state.get("has_knowledge", False)),
            apps=state.get("apps"),
            kb=state.get("kb"),
            agent_knowledge=state.get("agent_knowledge"),
            agent_skills=state.get("agent_skills"),
            connector_configs=state.get("connector_configs"),
            filters=state.get("filters"),
            has_sql_connector=bool(state.get("has_sql_connector", False)),
            has_sql_knowledge=bool(state.get("has_sql_knowledge", False)),
            has_slack_connector=bool(state.get("has_slack_connector", False)),
            has_slack_knowledge=bool(state.get("has_slack_knowledge", False)),
            is_multimodal_llm=bool(state.get("is_multimodal_llm", False)),
            enable_record_id_shortening=bool(state.get("enable_record_id_shortening", False)),
            system_prompt=state.get("system_prompt"),
            instructions=state.get("instructions"),
            custom_instructions=state.get("custom_instructions"),
            timezone=state.get("timezone"),
            current_time=state.get("current_time"),
            conversation_id=state.get("conversation_id"),
            has_ui_client=bool(state.get("has_ui_client", False)),
            previous_conversations=state.get("previous_conversations") or [],
            event_sink=event_sink,
            protocol=protocol,
            tool_state=state,
        )

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        for key, value in self._seed_tool_state().items():
            self.tool_state.setdefault(key, value)

    def get_source_catalog(self) -> Any:
        """Return the per-request `SourceCatalog`, building it once on first use."""
        if self._source_catalog is None:
            from app.modules.agents.context.source_catalog import SourceCatalog

            self._source_catalog = SourceCatalog.from_state(self.tool_state)
        return self._source_catalog

    def _seed_tool_state(self) -> dict[str, Any]:
        """Identity/service/config fields every PipesHub tool/action expects
        to find on `ChatState`. Deliberately does not seed the retrieval/
        citation accumulators the first tool call is responsible for
        creating (`final_results`, `tool_records`, ...) beyond their empty
        defaults, so `.setdefault()` never clobbers accumulated state on a
        second call into `_seed_tool_state`."""
        return {
            "logger": self.logger,
            "llm": self.llm,
            "retrieval_service": self.retrieval_service,
            "graph_provider": self.graph_provider,
            "config_service": self.config_service,
            "blob_store": self.blob_store,
            "org_id": self.org_id,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "user_info": self.user_info,
            "org_info": self.org_info,
            "is_service_account": self.is_service_account,
            "conversation_id": self.conversation_id,
            "has_ui_client": self.has_ui_client,
            "agent_toolsets": self.agent_toolsets,
            "tool_to_toolset_map": self.tool_to_toolset_map,
            "toolset_configs": self.toolset_configs,
            "mcp_servers": self.mcp_servers,
            "mcp_server_configs": self.mcp_server_configs,
            "web_search_config": self.web_search_config,
            "has_knowledge": self.has_knowledge,
            "apps": self.apps,
            "kb": self.kb,
            "agent_knowledge": self.agent_knowledge,
            "agent_skills": self.agent_skills,
            "connector_configs": self.connector_configs,
            "filters": self.filters,
            "has_sql_connector": self.has_sql_connector,
            "has_sql_knowledge": self.has_sql_knowledge,
            "has_slack_connector": self.has_slack_connector,
            "has_slack_knowledge": self.has_slack_knowledge,
            "is_multimodal_llm": self.is_multimodal_llm,
            "enable_record_id_shortening": self.enable_record_id_shortening,
            "system_prompt": self.system_prompt,
            "instructions": self.instructions,
            "custom_instructions": self.custom_instructions,
            "timezone": self.timezone,
            "current_time": self.current_time,
            "previous_conversations": self.previous_conversations,
            "final_results": [],
            "virtual_record_id_to_result": {},
            # Written by the knowledge tools that surface Record IDs without a
            # citation payload — see `remember_record_ids()` in chat_state.py.
            "known_record_ids": set(),
            "tool_records": [],
            "citation_ref_mapper": None,
            # TEMPORARY token-savings experiment — see `RecordIdShortener` in
            # `utils/chat_helpers.py`. Lazily created by `retrieval.py` on
            # first search call; resolved back to full ids in
            # `hooks/citations.py`'s `_FetchFullRecordTool`.
            "record_id_shortener": None,
            "all_tool_results": [],
            "web_records": [],
            "toolset_load_failures": self.toolset_load_failures,
            # record_escalation stash — retrieval.py writes these after each
            # search call for observability/debugging.
            "fetch_coverage": {},
            "fetch_plan": None,
            # Convenience read — retrieval.py mirrors context.needs_whole_document
            # here so the tool does not need a direct context reference.
            "needs_whole_document": self.needs_whole_document,
            "mcp_tool_load_failures": self.mcp_tool_load_failures,
        }


__all__ = ["AgentContext"]
