"""`ChatModePolicy`: the three `/chat/stream` modes (`internal_search`,
`web_search`, `agent`) as data, not branches.

Design note (post-implementation-audit): the original migration plan
sketched a `ToolPolicy` dataclass threaded through `PipesHubAgentFactory.
create()`/`PipesHubToolLoader.load()` to gate which tools register. Reading
`tool_loader.py` closely shows every gate this migration needs already
exists on `ChatState`/`AgentContext`:

- `context.has_knowledge` (`False` disables the "retrieval"/"knowledgehub"
  internal toolsets entirely — `tool_loader.py`'s `_KNOWLEDGE_TOOLSETS`
  gate).
- `state["web_search_config"]` (`None` skips building the `web_search`/
  `fetch_url` dynamic tools — `tool_loader.py::_build_dynamic_tools`).
- `state["has_sql_knowledge"]` / `state["has_slack_knowledge"]` (gate the
  SQL/Slack dynamic tools the same way).
- External connector toolsets never load for chat modes regardless,
  because chat modes never populate `agent_toolsets`.

So `ChatModePolicy` only needs to say WHAT state to force, not carry a
parallel `ToolPolicy` object the loader would have to learn a second
gating language for. `chat_modes/bridge.py` applies these flags directly
to the `ChatState` dict before wrapping it in an `AgentContext` — no
changes to `factory.py`/`tool_loader.py` were needed at all.
"""

from __future__ import annotations

from dataclasses import dataclass


# Every chat mode runs the existing "quick" `ModeDefinition` from
# `app.agents.agent_loop.modes.MODE_CATALOG` (flat ReAct, `skip_intent=True`,
# no domain-agent composition) -- passed as the literal `chat_mode` value to
# `PipesHubAgentFactory.create()`/`select_loop_and_goal()`. Modes differ only
# in tool availability (via the flags below), prefetch, and system prompt
# addendum -- never in loop shape. `modes.py` is intentionally unmodified.
_LOOP_CHAT_MODE = "quick"


@dataclass(frozen=True)
class ChatModePolicy:
    """Resolved behavior for one `/chat/stream` request.

    Attributes:
        name: canonical wire value (`"internal_search"` | `"web_search"` |
            `"agent"`).
        has_knowledge: forced onto `ChatState["has_knowledge"]`. Also drives
            `has_sql_knowledge`/`has_slack_knowledge` in the bridge (chat
            modes have no per-KB "agent knowledge" scoping the way agents
            do -- SQL/Slack tools are available whenever the connector
            exists org-wide, exactly like today's chatbot).
        include_web_search: whether the bridge resolves and attaches the
            org's default web-search provider config.
        prefetch_retrieval: whether `prefetch.prefetch_retrieval()` runs
            before the agent's first turn (internal_search only -- web
            search has no internal context to prefetch; agent mode lets the
            model decide whether/when to search rather than paying upfront
            retrieval latency on every turn regardless of query shape).
        system_prompt_key: looked up by the bridge to append a short,
            mode-specific addendum to the agent's system prompt (mirrors
            today's `get_model_config_for_mode()["system_prompt"]` framing,
            e.g. "answer ONLY from internal knowledge" for internal_search).
    """

    name: str
    has_knowledge: bool
    include_web_search: bool
    prefetch_retrieval: bool
    system_prompt_key: str

    @property
    def loop_chat_mode(self) -> str:
        return _LOOP_CHAT_MODE


@dataclass(frozen=True)
class AgentCapabilities:
    """Runtime capability overrides for agent mode.

    Sent by the frontend in ``agentCapabilities`` and parsed in the route
    layer.  ``True`` = capability enabled (default); ``False`` = user has
    toggled it off for this conversation.  Capabilities only *narrow* —
    setting ``internal_search=True`` on a scoped agent that lacks knowledge
    does not grant new access; the Python gate in ``agent.py`` enforces this.
    """

    internal_search: bool = True
    web_search: bool = True
    deep_search: bool = False


def _resolve_agent_prompt_key(caps: AgentCapabilities) -> str:
    """Pick a system-prompt addendum based on which capabilities are active."""
    if caps.internal_search and not caps.web_search:
        return "internal_search"
    if caps.web_search and not caps.internal_search:
        return "web_search"
    return "agent"  # both or neither — use the combined agent prompt


def resolve_agent_policy(capabilities: AgentCapabilities | None = None) -> ChatModePolicy:
    """Build a capabilities-aware `ChatModePolicy` for agent mode.

    Capability toggles narrow which tools are available (knowledge, web search)
    but do NOT change the prompt key — the user is always in agent mode, so the
    custom instructions always read from ``customSystemPromptAgent``.
    """
    caps = capabilities or AgentCapabilities()
    return ChatModePolicy(
        name="agent",
        has_knowledge=caps.internal_search,
        include_web_search=caps.web_search,
        prefetch_retrieval=False,
        system_prompt_key="agent",
    )


INTERNAL_SEARCH_POLICY = ChatModePolicy(
    name="internal_search",
    has_knowledge=True,
    include_web_search=False,
    prefetch_retrieval=True,
    system_prompt_key="internal_search",
)

WEB_SEARCH_POLICY = ChatModePolicy(
    name="web_search",
    has_knowledge=False,
    include_web_search=True,
    prefetch_retrieval=False,
    system_prompt_key="web_search",
)

AGENT_POLICY = ChatModePolicy(
    name="agent",
    has_knowledge=True,
    include_web_search=True,
    prefetch_retrieval=False,
    system_prompt_key="agent",
)

_CANONICAL_POLICIES: dict[str, ChatModePolicy] = {
    p.name: p for p in (INTERNAL_SEARCH_POLICY, WEB_SEARCH_POLICY, AGENT_POLICY)
}

# Persisted conversations and older clients still send these `chatMode`
# values (see chatbot.py's pre-migration `get_model_config_for_mode()`).
# They all mapped to the same upfront-retrieval, knowledge-base-only
# pipeline chatbot.py ran for anything that wasn't `web_search` -- so they
# all resolve to `INTERNAL_SEARCH_POLICY` here too. Their temperature/
# max_tokens tuning is unaffected by this migration: `get_model_config_for_
# mode()` in chatbot.py still keys off the RAW wire value, unchanged, at LLM
# construction time -- this map only decides agent-loop tool/prefetch
# behavior, never model params.
_LEGACY_MODE_FALLBACK = frozenset({
    "analysis", "deep_research", "creative", "precise", "standard", "quick",
})


def resolve_chat_mode_policy(chat_mode: str | None) -> ChatModePolicy:
    """Maps a wire `chatMode` value to its `ChatModePolicy`.

    Canonical values resolve directly; legacy values and any unrecognized/
    missing value fall back to `INTERNAL_SEARCH_POLICY` (today's default
    behavior for "anything that isn't explicitly web_search").
    """
    if not chat_mode:
        return INTERNAL_SEARCH_POLICY
    normalized = chat_mode.strip().lower()
    if normalized in _CANONICAL_POLICIES:
        return _CANONICAL_POLICIES[normalized]
    return INTERNAL_SEARCH_POLICY


__all__ = [
    "AGENT_POLICY",
    "AgentCapabilities",
    "INTERNAL_SEARCH_POLICY",
    "WEB_SEARCH_POLICY",
    "ChatModePolicy",
    "resolve_agent_policy",
    "resolve_chat_mode_policy",
]
