"""Wires agent_loop_lib's lazy-toolset progressive disclosure (`list_toolsets`/
`fetch_tools`/`search_tools` meta-tools + `tool_preloading` middleware) into
`PipesHubAgentFactory.create()`, grouping PipesHub's connector tools by their
adapter's `app_name` (see `tool_adapter.py`) into toolsets instead of
shipping every attached connector's full tool schema on every turn.

Entirely gated by `PIPESHUB_ENABLE_LAZY_TOOLS` (default OFF) — same rollout
convention as `PIPESHUB_ENABLE_SKILLS`/`PIPESHUB_USE_COMPOSED_AGENTS`
elsewhere in this adapter layer — AND an automatic size threshold
(`PIPESHUB_LAZY_TOOLS_THRESHOLD`, default 20): a request with few enough
attached tools keeps today's flat, eager path even with the flag on, since
there is no token-budget problem to solve for it and eager disclosure is
strictly simpler to reason about. Below the threshold or with the flag off,
this module's `make_lazy_tools_decider` always hands back its input
unchanged — zero behavior change (see the plan's "no behavior change by
default" design principle).

`PIPESHUB_LAZY_TOOLS_SCOPE` (`top_level` | `domain` | `both`, default
`top_level`) controls WHICH `AgentSpec`(s) actually flip to lazy disclosure:
- `top_level`: the top-level (react/quick/planExecute) agent's own grant —
  the highest-value case, since that's where an org with many attached
  connectors accumulates the most tool schemas.
- `domain`: each domain child (`register_domain_agents()`) goes lazy over
  its OWN claimed tools independently — mainly relevant for
  `internal_exploration_agent`, whose claim grows with every attached
  knowledge connector (`app_names={"retrieval", "knowledgehub"}`).
- `both`: both, independently (each side's threshold check runs against
  its own tool count).

Deliberately out of scope for this pass: `deep` mode's `OrchestratorLoop`.
Its top-level `spec.tool_names` is always just the 4 coordination tools
(never large — see `loops/orchestrator.py::COORDINATION_TOOL_NAMES`), and
its actual large pool (`domain_spec_factory`'s `default_tool_names`) is
already deterministically domain-scoped per `spawn_agent` call by the
orchestrating LLM itself (`spawn_agent`'s `tools` argument), which serves
the same "don't ship every schema to every sub-agent" goal through a
different, pre-existing mechanism. Revisit if that mechanism turns out to
be insufficient for orgs with very many connectors.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.tool_preloading import tool_preloading
from app.agent_loop_lib.tools.builtin.lazy_toolsets import (
    FetchToolsTool,
    ListToolsetsTool,
    SearchToolsTool,
)
from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.tools.global_fallback import GlobalToolHit
    from app.agent_loop_lib.tools.registry import ToolRegistry

__all__ = [
    "lazy_tools_enabled",
    "lazy_tools_threshold",
    "lazy_tools_scope",
    "should_apply_lazy_tools",
    "group_connector_toolsets",
    "PipesHubGlobalCatalogFallback",
    "register_lazy_tool_meta_tools",
    "register_tool_preloading",
    "make_lazy_tools_decider",
    "META_TOOL_NAMES",
    "CONNECTORS_PARENT",
]

logger = logging.getLogger(__name__)

# Public so callers (e.g. `factory.py`'s post-decision log line) can inspect
# which toolsets `group_connector_toolsets` nested under this category
# without duplicating the literal.
CONNECTORS_PARENT = "connectors"

# `app_name` values that are internal implementation details, not a
# user-facing connector — never worth grouping behind a fetch_tools()
# round trip (they're almost always one or two tools, so hiding them would
# only cost a turn for no meaningful token savings).
_NEVER_GROUP_APP_NAMES = frozenset({"internal", "dynamic"})

META_TOOL_NAMES: tuple[str, ...] = ("list_toolsets", "fetch_tools", "search_tools")


def lazy_tools_enabled() -> bool:
    """Kill-switch for the whole subsystem."""
    return os.getenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false").strip().lower() == "true"


def lazy_tools_threshold() -> int:
    raw = os.getenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "20")
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid PIPESHUB_LAZY_TOOLS_THRESHOLD=%r, falling back to 20", raw)
        return 20


def lazy_tools_scope() -> str:
    value = os.getenv("PIPESHUB_LAZY_TOOLS_SCOPE", "top_level").strip().lower()
    if value not in ("top_level", "domain", "both"):
        logger.warning("Invalid PIPESHUB_LAZY_TOOLS_SCOPE=%r, falling back to 'top_level'", value)
        return "top_level"
    return value


def should_apply_lazy_tools(tool_count: int) -> bool:
    """The automatic-threshold half of activation — `tool_count` alone
    crossing the threshold is not sufficient; `lazy_tools_enabled()` (the
    env kill-switch) must also hold. Split out so `make_lazy_tools_decider`
    can short-circuit before doing any registry work."""
    return lazy_tools_enabled() and tool_count > lazy_tools_threshold()


def group_connector_toolsets(tool_registry: "ToolRegistry", tool_names: list[str]) -> bool:
    """Groups every name in `tool_names` that resolves to an adapter with an
    `app_name` (a connector action — see `tool_adapter.py::PipesHubToolAdapter
    .app_name`)     into a per-connector toolset, nested under one
    `"connectors"` category group (see `ToolRegistry.register_toolset`'s
    `parent`). Names with no `app_name` (domain-agent `AgentTool`s,
    coordination tools, ...) are left alone by THIS function — they stay
    "essential" (always visible) as far as grouping goes, which is exactly
    right: they're either already a deliberately-curated handful, or
    (domain agents) the whole point of the entity is to always be a
    visible, callable delegate.

    Skill tools are the one exception worth calling out: they have no
    `app_name` either, so this function never touches them, but
    `register_skill_tools` (`skills_wiring.py`) registers its own
    `"skills"` toolset directly — which makes them grouped (hence hidden
    under lazy disclosure) all the same, since `grouped_tool_names()`
    unions every registered group regardless of who registered it. See
    `factory.py`'s `pinned_toolsets=["skills"]` for why that toolset is
    pinned back to essential instead of left to a `fetch_tools` round trip.

    Idempotent in effect: re-registering the same connector name twice
    just replaces its group with the same membership (`ToolRegistry.
    register_toolset` always replaces by name), so calling this more than
    once per request (e.g. once for the top-level grant, once for a domain
    child's own claim) is safe and cheap.

    Returns whether anything was actually grouped — `False` means every
    name was ungroupable (no connector `app_name` among them), so the
    caller has nothing to gain from flipping `tool_disclosure` to `"lazy"`.
    """
    by_app: dict[str, list[str]] = {}
    for name in tool_names:
        if not tool_registry.has(name):
            continue
        tool = tool_registry.resolve_by_name(name)
        app_name = getattr(tool, "app_name", None)
        if not app_name or app_name in _NEVER_GROUP_APP_NAMES:
            continue
        by_app.setdefault(app_name, []).append(name)

    if not by_app:
        return False

    tool_registry.register_toolset(
        CONNECTORS_PARENT,
        "Connected app integrations (Jira, Slack, Confluence, Google Drive, ...). "
        "Call list_toolsets(toolset) with one of these names for a one-line "
        "description of every tool inside it, or fetch_tools(toolset) to load "
        "the real schemas directly.",
        [],
    )
    for app_name, names in by_app.items():
        tool_registry.register_toolset(
            app_name, _connector_description(app_name), names, parent=CONNECTORS_PARENT,
        )
    logger.info(
        "group_connector_toolsets: grouped %d connector(s) covering %d/%d tool(s): %s",
        len(by_app), sum(len(v) for v in by_app.values()), len(tool_names), sorted(by_app),
    )
    return True


def _connector_description(app_name: str) -> str:
    """Best-effort one-line description sourced from the connector/toolset
    registry's own metadata (`app/connectors/core/registry/tool_builder.py`,
    surfaced via `app/agents/registry/toolset_registry.py`) — falls back to
    a generic label for an `app_name` with no matching registry entry (a
    synthetic bucket, or a connector that predates that registry)."""
    try:
        from app.agents.registry.toolset_registry import get_toolset_registry

        metadata = get_toolset_registry().get_toolset_metadata(app_name)
    except Exception:
        logger.debug("No toolset registry metadata for app_name=%r", app_name, exc_info=True)
        metadata = None
    description = (metadata or {}).get("description")
    return description or f"{app_name.replace('_', ' ').title()} tools."


class PipesHubGlobalCatalogFallback:
    """Adapts PipesHub's process-wide `_global_tools_registry` (every
    connector action ever registered via the `@tool` decorator, across
    every org — see `app.agents.tools.decorator`) as agent_loop_lib's
    `GlobalCatalogFallback`. `SearchToolsTool` only consults this when the
    CURRENT agent's own (per-request, attachment-filtered) registry has
    zero hits, so a hit here always means "exists somewhere, not attached
    to this agent/org" — never a duplicate of an already-visible tool.

    Reuses `_global_tools_registry.search_tools(query=...)` (substring
    match over name/description) rather than reimplementing scoring here —
    good enough for a rare fallback path; the primary ranked search stays
    in `ToolIndex`/`KeywordToolIndex`.
    """

    async def search(self, query: str, limit: int) -> list["GlobalToolHit"]:
        from app.agent_loop_lib.tools.global_fallback import GlobalToolHit
        from app.agents.tools.registry import _global_tools_registry

        hits = _global_tools_registry.search_tools(query=query)[:limit]
        return [
            GlobalToolHit(
                # `__`, not `_` — matches the `{app}__{tool}` convention every
                # OTHER globally-addressable tool name uses in this adapter
                # layer (`PipesHubStructuredToolAdapter.name`, and the split
                # side in `tool_summarizer.py`'s `rsplit("__", 1)`). A `hit.
                # name` that doesn't match the name the tool would actually
                # register under once attached is useless to a caller trying
                # to reference it (e.g. an attach-flow follow-up call).
                name=f"{tool.app_name}__{tool.tool_name}",
                toolset=tool.app_name,
                description=tool.description or f"{tool.app_name} {tool.tool_name}",
            )
            for tool in hits
        ]


def register_lazy_tool_meta_tools(tool_registry: "ToolRegistry") -> None:
    """Registers `list_toolsets`/`fetch_tools`/`search_tools` if not
    already present on `tool_registry` — a no-op for a name that's already
    registered (e.g. a second call within the same request, once for the
    top-level grant and once for a domain child sharing the same registry).

    `search_tools` gets `PipesHubGlobalCatalogFallback` so a zero-hit query
    against THIS agent's tools still tells the model (and, via SSE, the
    user) when a matching tool exists but isn't attached — see
    `SearchToolsTool`'s docstring and `EventType.TOOL_UNAVAILABLE`.
    """
    for tool_cls in (ListToolsetsTool, FetchToolsTool):
        try:
            tool_registry.register_tool(tool_cls(tool_registry))
        except (DuplicateToolNameError, DuplicateToolPathError):
            continue
    try:
        tool_registry.register_tool(
            SearchToolsTool(tool_registry, global_fallback=PipesHubGlobalCatalogFallback())
        )
    except (DuplicateToolNameError, DuplicateToolPathError):
        pass


def register_tool_preloading(hooks: "HookRegistry") -> None:
    """PRE_AGENT — deterministic intent-based toolset preload, mirroring
    `skills_wiring.py::register_skill_preloading`. See `tool_preloading.py`'s
    module docstring for the tools-vs-middleware tradeoff this resolves.
    Safe to call even when no agent in this run ends up with
    `tool_disclosure="lazy"` — the middleware itself no-ops whenever the
    registry has no toolsets or the calling spec's grant is eager."""
    hooks.on(HookEvent.PRE_AGENT).use(tool_preloading(
        preload_threshold=float(os.getenv("PIPESHUB_LAZY_TOOLS_PRELOAD_THRESHOLD", "0.75")),
        mention_threshold=float(os.getenv("PIPESHUB_LAZY_TOOLS_MENTION_THRESHOLD", "0.4")),
    ))


def make_lazy_tools_decider(
    *, apply: bool,
) -> "Callable[[ToolRegistry, list[str]], tuple[list[str], str]]":
    """Builds the `(tool_names, tool_disclosure)` decision function used at
    BOTH the top-level `AgentSpec` construction site (`factory.py`, called
    directly) and per domain child (`domain_agents.py::register_domain_agents`'s
    `lazy_tools` callback) — one implementation, two call sites, so
    `top_level`/`domain`/`both` scope (see this module's docstring) is just
    "which call site(s) pass `apply=True`", never a difference in the
    grouping/threshold logic itself.

    `apply=False` (this scope wasn't selected, or the env flag is off)
    always returns `(tool_names, "eager")` unchanged — zero behavior
    change.
    """

    def _decide(tool_registry: "ToolRegistry", tool_names: list[str]) -> tuple[list[str], str]:
        if not apply or not should_apply_lazy_tools(len(tool_names)):
            return tool_names, "eager"
        if not group_connector_toolsets(tool_registry, tool_names):
            return tool_names, "eager"
        register_lazy_tool_meta_tools(tool_registry)
        augmented = list(tool_names)
        for meta_name in META_TOOL_NAMES:
            if meta_name not in augmented:
                augmented.append(meta_name)
        return augmented, "lazy"

    return _decide
