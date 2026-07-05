"""`AgentContext`: focused per-request context for the agent-loop adapter
layer's tool execution and prompt building — the Phase 3 replacement for
threading a full `ChatState` through `PipesHubToolAdapter`/`PipesHubToolLoader`.

`ChatState` (`app.modules.agents.qna.chat_state`) still exists and is still
built by `build_initial_state()` for the legacy LangGraph path and for
Phase 6's `RespondPipeline`. `AgentContext` doesn't replace it — it's a
narrower, validated view constructed from the same route-handler inputs,
carrying only what tool execution and prompt assembly need.

Unmodified PipesHub tools (via `RegistryToolWrapper` / `ToolInstanceCreator` /
the dynamic tool factories in `tool_system.py`) read and mutate a plain
`ChatState`-shaped dict — `retrieval.search_internal_knowledge` appends to
`final_results`, `image_generator` reads `blob_store/conversation_id`, etc.
`AgentContext.tool_state` is that dict: seeded once from the identity/service
fields below, then shared by reference across every tool call for the life
of the request so those mutations accumulate exactly as they do today.
Phase 5's POST_TOOL_USE hooks read the deltas back off of it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentContext(BaseModel):
    """Per-request context for tool execution and prompt building."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

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

    # Knowledge config
    has_knowledge: bool = False
    apps: list[str] | None = None
    kb: list[str] | None = None
    agent_knowledge: list[dict[str, Any]] | None = None
    connector_configs: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    has_sql_connector: bool = False
    has_sql_knowledge: bool = False
    has_slack_connector: bool = False
    has_slack_knowledge: bool = False
    is_multimodal_llm: bool = False

    # Agent config
    system_prompt: str | None = None
    instructions: str | None = None
    timezone: str | None = None
    current_time: str | None = None

    # Mutable per-request state
    conversation_id: str | None = None
    has_ui_client: bool = False

    # Conversation history (for multi-turn seeding)
    previous_conversations: list[dict[str, Any]] = Field(default_factory=list)

    # `EventSink` (`app.modules.agents.event_sink`) for hooks that must push
    # SSE events mid-run (e.g. Phase 5's `ask_user_question_sse` hook) — the
    # `SSEEventEmitter` implementation is wired in by Phase 7; `None` here
    # just means "no streaming client for this call" (e.g. background/test
    # runs), matching how `has_ui_client=False` is handled today.
    event_sink: Any = None

    # Per-request agent_loop_lib `SandboxManager` (only set when code
    # execution is enabled for this request — see
    # `PipesHubAgentFactory._register_coding_sandbox`). Stashed here rather
    # than threaded through `create()`'s return value so
    # `stream_bridge.py`'s `_produce()` can tear every provisioned sandbox
    # down in its `finally` block without widening the `(Agent, AgentRuntime)`
    # contract `test_factory_wiring.py` asserts throughout.
    sandbox_manager: Any = None

    # The live, mutable ChatState-shaped dict PipesHub tools read/write
    # through unchanged — see module docstring. Populated by
    # `model_post_init`; safe to pass directly to `RegistryToolWrapper`,
    # `ToolInstanceCreator`, or `tool_system.py`'s loader functions.
    tool_state: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_chat_state(cls, state: dict[str, Any], *, event_sink: Any = None) -> "AgentContext":
        """Builds an `AgentContext` from an already-built `ChatState` dict
        (Phase 8, `stream_bridge.py`) rather than re-deriving every field a
        second time. `build_initial_state()` (`chat_state.py`) already does
        100% of the derivation work the legacy LangGraph path needs — apps/kb
        extraction from `knowledge`, `tool_to_toolset_map`, `has_sql_knowledge`,
        etc. — and the agent-loop path needs exactly the same derived values,
        so this simply lifts the identity/service/config fields out for typed
        access while passing the SAME dict through as `tool_state`. That
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
            web_search_config=state.get("web_search_config"),
            has_knowledge=bool(state.get("has_knowledge", False)),
            apps=state.get("apps"),
            kb=state.get("kb"),
            agent_knowledge=state.get("agent_knowledge"),
            connector_configs=state.get("connector_configs"),
            filters=state.get("filters"),
            has_sql_connector=bool(state.get("has_sql_connector", False)),
            has_sql_knowledge=bool(state.get("has_sql_knowledge", False)),
            has_slack_connector=bool(state.get("has_slack_connector", False)),
            has_slack_knowledge=bool(state.get("has_slack_knowledge", False)),
            is_multimodal_llm=bool(state.get("is_multimodal_llm", False)),
            system_prompt=state.get("system_prompt"),
            instructions=state.get("instructions"),
            timezone=state.get("timezone"),
            current_time=state.get("current_time"),
            conversation_id=state.get("conversation_id"),
            has_ui_client=bool(state.get("has_ui_client", False)),
            previous_conversations=state.get("previous_conversations") or [],
            event_sink=event_sink,
            tool_state=state,
        )

    def model_post_init(self, __context: Any) -> None:  # noqa: ANN401
        for key, value in self._seed_tool_state().items():
            self.tool_state.setdefault(key, value)

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
            "web_search_config": self.web_search_config,
            "has_knowledge": self.has_knowledge,
            "apps": self.apps,
            "kb": self.kb,
            "agent_knowledge": self.agent_knowledge,
            "connector_configs": self.connector_configs,
            "filters": self.filters,
            "has_sql_connector": self.has_sql_connector,
            "has_sql_knowledge": self.has_sql_knowledge,
            "has_slack_connector": self.has_slack_connector,
            "has_slack_knowledge": self.has_slack_knowledge,
            "is_multimodal_llm": self.is_multimodal_llm,
            "system_prompt": self.system_prompt,
            "instructions": self.instructions,
            "timezone": self.timezone,
            "current_time": self.current_time,
            "previous_conversations": self.previous_conversations,
            "final_results": [],
            "virtual_record_id_to_result": {},
            "tool_records": [],
            "citation_ref_mapper": None,
            "all_tool_results": [],
            "web_search_results": [],
        }


__all__ = ["AgentContext"]
