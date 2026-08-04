"""ToolSurfaces — single resolved snapshot of what capability surfaces are available.

Collapses `_composed_code_tool`, `_web_reference`, `_internal_knowledge_reference`,
`sandbox_network_enabled()`, and the eight `_has_*_tools(state)` calls in
`prompt_builder.py` into one immutable value computed once per turn and threaded
into every section builder.  This makes "never name an ungranted tool" mechanically
checkable and removes the eight separate boolean re-computations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.modules.agents.context.tool_names import granted, granted_any

# Module-level import so tests can patch `tool_surface.sandbox_network_enabled`.
# The try/except keeps the module importable in test environments that don't
# have the full agent runtime installed.
try:
    from app.agents.agent_loop.sandbox_bridge import sandbox_network_enabled
except ImportError:  # pragma: no cover
    def sandbox_network_enabled() -> bool:  # type: ignore[misc]
        return False


@dataclass(frozen=True)
class ToolSurfaces:
    """What capability surfaces are available for one agent turn.

    All surface names are tool names exactly as they appear in ``spec.tool_names``.
    ``None`` means the surface is not granted this turn.
    """

    retrieval: str | None
    """Flat ``retrieval__search_internal_knowledge`` or composed
    ``internal_exploration_agent``, or ``None`` when no knowledge is configured."""

    web_names: tuple[str, ...]
    """Granted web-tool names in the form they appear in ``spec.tool_names``.

    ``("dynamic__web_search", "dynamic__fetch_url")`` for flat web tools,
    ``("web_agent",)`` for the composed delegate, or ``()`` when no web
    tool is granted this turn (web search may still be *available* via
    ``has_web_search`` when only ``web_search_config`` is set — but no
    callable name exists to print in the prompt).
    """

    code: str | None
    """`"run_code"` or `"coding_agent"`, or `None` when code execution is
    not available."""

    code_networked: bool
    """True iff `code` is not None and the sandbox has network access."""

    graph: bool
    """True iff a knowledgegraph tool is in `tool_names`."""

    live_api_apps: tuple[str, ...]
    """App type keys with live-API service tools (e.g. `("jira", "confluence")`).
    Derived from `agent_toolsets` AND the `_has_*_tools(state)` connector flags —
    whichever non-empty set is available."""

    can_ask_user: bool
    """True iff `internaltools__ask_user_question` is in `tool_names`."""

    can_fetch_full_record: bool
    """True iff `dynamic_fetch_full_record` is in `tool_names`."""

    has_knowledge: bool
    """True iff the state indicates indexed knowledge is available
    (`state["has_knowledge"]` or `agent_knowledge` is non-empty)."""

    has_web_search: bool
    """True iff web search is available — either via granted tool names in
    `web_names` OR via the `web_search_config` state flag (chat/web_search
    mode sets this flag even before tool names are populated)."""

    has_service_tools: bool
    """Convenience — True iff `live_api_apps` is non-empty."""

    @classmethod
    def resolve(
        cls,
        tool_names: Sequence[str],
        state: Mapping[str, Any],
    ) -> "ToolSurfaces":
        """Compute surfaces from the granted tool set and agent state.

        This is the single call site that reads connector flags and sandbox
        state — no other code needs to repeat those checks.

        Every name stored in the returned ``ToolSurfaces`` is the exact
        spelling from ``tool_names`` (or the registry), never a guessed
        separator form — so anything the prompt prints is callable.
        """
        names_set = set(tool_names)

        # ── Retrieval surface ────────────────────────────────────────────────
        # Prefer the composed exploration delegate; fall back to the flat tool
        # (new name first, then legacy for backward-compat with old sessions).
        retrieval = (
            granted("internal_exploration_agent", names_set)
            or granted("knowledgegraph.search", names_set)
            or granted("retrieval.search_internal_knowledge", names_set)
        )

        # ── Web surface ──────────────────────────────────────────────────────
        # Also check state["web_search_config"] — the chat/web_search mode sets
        # this flag even when tool names haven't been populated yet (e.g. the
        # test helper sets the flag without adding tools to spec.tool_names).
        web_search_config = bool(state.get("web_search_config"))

        web_agent = granted("web_agent", names_set)
        if web_agent:
            web_names: tuple[str, ...] = (web_agent,)
        else:
            web_names = granted_any(["web_search", "fetch_url"], names_set)

        has_web_search = bool(web_names) or web_search_config

        # ── Code surface ─────────────────────────────────────────────────────
        code = (
            granted("coding_agent", names_set)
            or granted("run_code", names_set)
        )

        # Uses the module-level `sandbox_network_enabled` import so tests can patch it.
        code_networked = False
        if code is not None:
            code_networked = sandbox_network_enabled()

        # ── Graph surface ────────────────────────────────────────────────────
        graph = bool(granted_any(
            ["knowledgegraph.navigate", "knowledgegraph.lookup_record"],
            names_set,
        ))

        # ── Live-API connector apps ──────────────────────────────────────────
        live_api_apps = _resolve_live_api_apps(state, names_set)

        # ── User-interaction tools ───────────────────────────────────────────
        can_ask_user = granted("internaltools__ask_user_question", names_set) is not None
        can_fetch_full_record = (
            granted("knowledgegraph__fetch_record", names_set) is not None
            or granted("dynamic_fetch_full_record", names_set) is not None
        )

        # ── Knowledge flag ───────────────────────────────────────────────────
        has_knowledge = bool(
            state.get("has_knowledge")
            or (state.get("agent_knowledge") or [])
        )

        has_service_tools = bool(live_api_apps)

        return cls(
            retrieval=retrieval,
            web_names=web_names,
            code=code,
            code_networked=code_networked,
            graph=graph,
            live_api_apps=live_api_apps,
            can_ask_user=can_ask_user,
            can_fetch_full_record=can_fetch_full_record,
            has_knowledge=has_knowledge,
            has_web_search=has_web_search,
            has_service_tools=has_service_tools,
        )

    # ── Convenience formatters ────────────────────────────────────────────────

    def web_md(self) -> str:
        """Backtick-wrapped markdown reference(s) to the granted web tools.

        Returns ``""`` when ``web_names`` is empty (config-only web search
        with no granted tool names this turn).
        """
        if not self.web_names:
            return ""
        return "/".join(f"`{n}`" for n in self.web_names)

    def retrieval_md(self) -> str:
        """Backtick-wrapped markdown reference to the retrieval surface.

        Returns ``""`` when ``retrieval`` is ``None`` — callers must guard
        on ``retrieval is not None`` before calling this.
        """
        if self.retrieval is None:
            return ""
        return f"`{self.retrieval}`"

    def code_md(self) -> str:
        """Backtick-wrapped markdown reference to the code surface."""
        if self.code is None:
            return ""
        return f"`{self.code}`"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

_CONNECTOR_FLAG_APP_MAP: dict[str, str] = {
    "has_jira_connector": "jira",
    "has_confluence_connector": "confluence",
    "has_onedrive_connector": "onedrive",
    "has_outlook_connector": "outlook",
    "has_slack_connector": "slack",
    "has_teams_connector": "teams",
    "has_github_connector": "github",
    "has_clickup_connector": "clickup",
}


def _resolve_live_api_apps(
    state: Mapping[str, Any],
    names_set: set[str],
) -> tuple[str, ...]:
    """Collect app type keys that have live-API tools available this turn.

    Two sources: the `agent_toolsets` list (agent route) and the legacy
    `has_<app>_connector` boolean flags that `connector_detection.py` sets
    on the state dict.  Use whichever is populated.
    """
    apps: set[str] = set()

    # Source 1: agent_toolsets (agent route)
    for ts in state.get("agent_toolsets", []) or []:
        if not isinstance(ts, dict):
            continue
        ts_name = (ts.get("name") or "").strip().lower()
        if not ts_name or ts_name in ("retrieval", "knowledgehub", "knowledgegraph", "calculator"):
            continue
        ts_key = ts_name.split()[0]
        apps.add(ts_key)

    # Source 2: connector-detection flags (chat route / bridge)
    if not apps:
        for flag, app_key in _CONNECTOR_FLAG_APP_MAP.items():
            if state.get(flag):
                apps.add(app_key)

    # Source 3: service-specific tool names in the granted set
    # (covers any case not handled by the two sources above)
    for name in names_set:
        if "__" in name:
            prefix = name.split("__", 1)[0]
            if prefix not in ("retrieval", "database_sandbox", "knowledgehub",
                               "knowledgegraph", "artifacts", "internaltools",
                               "skills", "calculator", "date_calculator",
                               "image_generator", "dynamic"):
                apps.add(prefix)

    return tuple(sorted(apps))
