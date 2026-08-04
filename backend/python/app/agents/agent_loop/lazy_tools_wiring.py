"""Wires agent_loop_lib's lazy-toolset progressive disclosure (`list_toolsets`/
`fetch_tools`/`search_tools` meta-tools + `tool_preloading` middleware) into
`PipesHubAgentFactory.create()`.

Grouping reuses the per-toolset `ToolsetGroup`s `PipesHubToolLoader.load()`
already registers for every connector it loads (`tool_loader.py`) — it does
NOT try to re-derive grouping from an `app_name` attribute on individual
`Tool` objects, because the connector tools loaded via `AgentLoopToolsetBuilder`
(`BoundMethodTool` instances) never had one; only the small number of
per-request dynamic tools (`PipesHubStructuredToolAdapter` — web_search,
execute_sql_query, ...) do. `group_connector_toolsets` below just re-parents
those EXISTING groups under one `"connectors"` category (see
`CONNECTORS_PARENT`), excluding whichever toolset names the caller has
already decided should stay essential (retrieval/knowledgehub/artifacts when
attached, skills — see `factory.py`'s `essential_toolset_names`).

Lazy disclosure itself is gated by `PIPESHUB_ENABLE_LAZY_TOOLS` (default ON —
this is the "search instead of ship every schema" progressive-disclosure
pattern, not an experimental feature) AND an automatic size threshold
(`PIPESHUB_LAZY_TOOLS_THRESHOLD`, default 20): a request with few enough
attached tools keeps the flat, eager path regardless, since there is no
token-budget problem to solve for it and eager disclosure is strictly
simpler to reason about. Below the threshold, or with the env kill-switch
off, this module's `make_lazy_tools_decider` always hands back its input
unchanged.

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
    from app.agents.agent_loop.context import AgentContext

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

META_TOOL_NAMES: tuple[str, ...] = ("list_toolsets", "fetch_tools", "search_tools")


def lazy_tools_enabled() -> bool:
    """Kill-switch for the whole subsystem — defaults ON. Progressive tool
    disclosure only actually changes anything once `should_apply_lazy_tools`
    ALSO clears the size threshold below, so this default doesn't affect any
    agent with a modest number of attached toolsets."""
    return os.getenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true").strip().lower() == "true"


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


def group_connector_toolsets(
    tool_registry: "ToolRegistry",
    tool_names: list[str],
    *,
    exclude: frozenset[str] = frozenset(),
) -> bool:
    """Re-parents every top-level `ToolsetGroup` already registered on
    `tool_registry` (by `PipesHubToolLoader.load()` for every connector it
    loads, or `register_skill_tools` for `"skills"`) under one
    `"connectors"` category group (see `ToolRegistry.register_toolset`'s
    `parent`) — EXCEPT groups named in `exclude` (the caller's essential
    set: retrieval/knowledgehub/artifacts when attached, skills — see
    `factory.py`'s `essential_toolset_names`) and `CONNECTORS_PARENT`
    itself (idempotency guard, see below).

    Only a group with at least one member in `tool_names` (the current
    grant) is considered — a group entirely outside this turn's grant
    (e.g. a domain child's own narrower claim) has nothing to hide behind
    `fetch_tools` for THIS spec. A group already nested under some other
    parent (i.e. this function already ran once against the same shared
    registry) is left alone rather than re-parented again.

    Idempotent in effect: re-registering the same name twice just replaces
    its group with the same membership (`ToolRegistry.register_toolset`
    always replaces by name), so calling this more than once per request
    (e.g. once for the top-level grant, once for a domain child's own
    claim, both against the SAME shared `tool_registry`) is safe and cheap.

    Returns whether anything was actually grouped — `False` means nothing
    in `tool_names` belonged to a groupable, non-excluded toolset, so the
    caller has nothing to gain from flipping `tool_disclosure` to `"lazy"`.
    """
    tool_name_set = set(tool_names)
    candidates = [
        group
        for group in tool_registry.toolsets()
        if group.parent is None
        and group.name != CONNECTORS_PARENT
        and group.name not in exclude
        and any(name in tool_name_set for name in group.tool_names)
    ]

    if not candidates:
        return False

    tool_registry.register_toolset(
        CONNECTORS_PARENT,
        "Connected app integrations (Jira, Slack, Confluence, Google Drive, ...). "
        "Call list_toolsets(toolset) with one of these names for a one-line "
        "description of every tool inside it, or fetch_tools(toolset) to load "
        "the real schemas directly.",
        [],
    )
    for group in candidates:
        tool_registry.register_toolset(
            group.name, group.description, group.tool_names, parent=CONNECTORS_PARENT,
        )
    logger.info(
        "group_connector_toolsets: grouped %d toolset(s) covering %d/%d tool(s): %s",
        len(candidates), sum(len(g.tool_names) for g in candidates), len(tool_names),
        sorted(g.name for g in candidates),
    )
    return True


class PipesHubGlobalCatalogFallback:
    """Adapts PipesHub's process-wide toolset catalog (`ToolsetRegistry` —
    every toolset the app knows how to build, across every org, whether or
    not it's attached to THIS agent) as agent_loop_lib's
    `GlobalCatalogFallback`. `SearchToolsTool` only consults this when the
    CURRENT agent's own (per-request, attachment-filtered) registry has
    zero hits, so a hit here always means "exists somewhere, not usable by
    this agent right now" — never a duplicate of an already-visible tool.

    Auth-aware: a hit whose toolset is in `context.toolset_load_failures`
    with reason `"not_authenticated"` (configured on this agent, but
    `ToolInstanceCreator` failed OAuth/API-key auth when `PipesHubToolLoader`
    tried to load it — see `tool_loader.py`) is reported with that same
    reason instead of the generic `"not_attached"` default, so the model
    (and, via `EventType.TOOL_UNAVAILABLE`, the user) gets "this exists and
    is configured, you just need to authenticate it" instead of a flat
    "this doesn't exist" when a query names an unauthenticated toolset.

    Simple keyword/substring match over each toolset's discovered tool
    name + description — good enough for a rare fallback path; the primary
    ranked search stays in `ToolIndex`/`KeywordToolIndex`.
    """

    def __init__(self, context: "AgentContext | None" = None) -> None:
        self._context = context

    async def search(self, query: str, limit: int) -> list["GlobalToolHit"]:
        from app.agent_loop_lib.tools.global_fallback import GlobalToolHit
        from app.agents.agent_loop.tool_loader import _has_tool_decorated_methods, _infer_path_prefix
        from app.agents.registry.toolset_registry import get_toolset_registry

        query_terms = [t for t in query.lower().split() if t]
        failures = self._context.toolset_load_failures if self._context is not None else {}

        hits: list[GlobalToolHit] = []
        for ts_name, ts_meta in get_toolset_registry().get_all_toolsets().items():
            if ts_meta.get("isInternal", False):
                # Never a user-facing "go attach/authenticate this" suggestion —
                # there's nothing for the user to configure.
                continue

            ts_class = ts_meta.get("class")
            group_name = (
                _infer_path_prefix(ts_class, fallback_name=ts_name).rsplit("/", 1)[-1]
                if ts_class is not None and _has_tool_decorated_methods(ts_class)
                else ts_name
            )
            reason = "not_authenticated" if failures.get(ts_name) == "not_authenticated" else "not_attached"

            for tool in ts_meta.get("tools", []):
                tool_name = tool.get("name", "")
                if not tool_name:
                    continue
                description = tool.get("description") or f"{group_name} {tool_name}"
                haystack = f"{tool_name} {description}".lower()
                if query_terms and not any(term in haystack for term in query_terms):
                    continue
                hits.append(GlobalToolHit(
                    # `__`, not `_` — matches the `{app}__{tool}` convention
                    # every OTHER globally-addressable tool name uses in this
                    # adapter layer (`BoundMethodTool.name`,
                    # `PipesHubStructuredToolAdapter.name`). A `hit.name` that
                    # doesn't match the name the tool would actually register
                    # under once attached is useless to a caller trying to
                    # reference it (e.g. an attach-flow follow-up call).
                    name=f"{group_name}__{tool_name}",
                    toolset=group_name,
                    description=(
                        f"{description} — configured but not authenticated; "
                        "tell the user to authenticate this toolset in "
                        "Settings > Toolsets."
                        if reason == "not_authenticated" else description
                    ),
                    reason=reason,
                ))
                if len(hits) >= limit:
                    return hits
        return hits


def register_lazy_tool_meta_tools(
    tool_registry: "ToolRegistry", context: "AgentContext | None" = None,
) -> None:
    """Registers `list_toolsets`/`fetch_tools`/`search_tools` if not
    already present on `tool_registry` — a no-op for a name that's already
    registered (e.g. a second call within the same request, once for the
    top-level grant and once for a domain child sharing the same registry).

    Called unconditionally by `factory.py` regardless of `tool_disclosure`
    — `fetch_tools`'/`list_toolsets`' visibility side effects are no-ops
    under eager disclosure (nothing is hidden to reveal), but `search_tools`
    still provides global discovery + auth-aware flagging (via
    `PipesHubGlobalCatalogFallback(context)`) either way: a zero-hit query
    against THIS agent's tools tells the model (and, via SSE, the user)
    when a matching tool exists but isn't attached/authenticated — see
    `SearchToolsTool`'s docstring and `EventType.TOOL_UNAVAILABLE`.
    """
    for tool_cls in (ListToolsetsTool, FetchToolsTool):
        try:
            tool_registry.register_tool(tool_cls(tool_registry))
        except (DuplicateToolNameError, DuplicateToolPathError):
            continue
    try:
        tool_registry.register_tool(
            SearchToolsTool(tool_registry, global_fallback=PipesHubGlobalCatalogFallback(context))
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
    *,
    apply: bool,
    essential_names: frozenset[str] = frozenset(),
    context: "AgentContext | None" = None,
) -> "Callable[[ToolRegistry, list[str]], tuple[list[str], str]]":
    """Builds the `(tool_names, tool_disclosure)` decision function used at
    BOTH the top-level `AgentSpec` construction site (`factory.py`, called
    directly) and per domain child (`domain_agents.py::register_domain_agents`'s
    `lazy_tools` callback) — one implementation, two call sites, so
    `top_level`/`domain`/`both` scope (see this module's docstring) is just
    "which call site(s) pass `apply=True`", never a difference in the
    grouping/threshold logic itself.

    `essential_names` — toolset group names to exclude from grouping (see
    `group_connector_toolsets`'s `exclude` param) — is the caller's
    "essential" set, e.g. `factory.py`'s `essential_toolset_names`
    (retrieval/knowledgehub/artifacts when attached, plus `"skills"`).
    `context` is threaded into `PipesHubGlobalCatalogFallback` for
    auth-aware `search_tools` reasons.

    `apply=False` (this scope wasn't selected, or the env flag is off)
    always returns `(tool_names, "eager")` unchanged — zero behavior
    change.
    """

    def _decide(tool_registry: "ToolRegistry", tool_names: list[str]) -> tuple[list[str], str]:
        if not apply or not should_apply_lazy_tools(len(tool_names)):
            return tool_names, "eager"
        if not group_connector_toolsets(tool_registry, tool_names, exclude=essential_names):
            return tool_names, "eager"
        register_lazy_tool_meta_tools(tool_registry, context)
        augmented = list(tool_names)
        for meta_name in META_TOOL_NAMES:
            if meta_name not in augmented:
                augmented.append(meta_name)
        return augmented, "lazy"

    return _decide
