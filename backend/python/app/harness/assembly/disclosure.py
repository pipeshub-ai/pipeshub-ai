"""Lazy tool disclosure on the durable path (PA-2), with the legacy decisions.

``make_lazy_tools_decider`` decides eager or lazy for the turn's grant (the
same threshold, scope and essential toolsets as the factory), and
``tool_schemas_for_turn`` decides what the model sees: under lazy disclosure
the essentials, what the run's start hooks preloaded, and the meta-tools. The
kernel fixes that list for the chat session at its first request (R4,
``harness_core.kernel.session_tools``); ``search_tools`` and ``fetch_tools``
answer schemas and never grow it, and a tool they answer is called through
``call_tool``.

``search_tools`` and ``fetch_tools`` run through their own ``resolve`` (the
body of the legacy special route) within the run's grant, as durable routes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.tools.durable_port import RouteResult

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.mcp_lazy import LazyMcpServers
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.logs import HarnessLog
    from harness_core.tools.durable_port import Route

ROUTED = ("search_tools", "fetch_tools")


class _Holder:
    def __init__(self) -> None:
        self.visible_tools: set[str] | None = None


class Disclosure:
    def __init__(
        self,
        env: ToolEnvironment,
        registry: ToolRegistry,
        *,
        essential: list[str],
        log: HarnessLog,
        withheld: frozenset[str] = frozenset(),
        mode: str | None = None,
        lazy: LazyMcpServers | None = None,
    ) -> None:
        self._env = env
        # The run's MCP servers listed at first use (D-LAZY-4): always behind the search, searched to first use.
        self._lazy = lazy
        self._chosen = mode
        self._withheld = withheld
        self._registry = registry
        self._essential = list(essential)
        self._log = log
        # What `tool_schemas_for_turn` reads: the run scope once the run has started (an `Agent`'s in legacy),
        # which the start hooks seed (preloading).
        self._holder: Any = _Holder()
        self._grant: list[str] | None = None
        self._mode = "eager"
        self._decided: dict[tuple[tuple[str, ...], str], tuple[list[str], str]] = {}
        self._pinned: dict[tuple[tuple[str, ...], str], list[str]] = {}

    @property
    def visible_tools(self) -> set[str] | None:
        return self._holder.visible_tools

    @visible_tools.setter
    def visible_tools(self, value: set[str] | None) -> None:
        self._holder.visible_tools = value

    def bind(self, holder: Any) -> None:  # noqa: ANN401 - a RunScope
        """Share the run scope's ``visible_tools`` (what the PRE_AGENT/PRE_TURN
        hooks seed), keeping anything disclosed before."""
        if self._holder.visible_tools is not None:
            holder.visible_tools = (holder.visible_tools or set()) | self._holder.visible_tools
        self._holder = holder

    def decide(self, names: list[str], loop_kind: str) -> tuple[list[str], str, list[str]]:
        """``(tool_names, tool_disclosure, pinned_toolsets)`` as the factory builds the spec."""
        from app.agents.agent_loop.lazy_tools_wiring import (
            LAZY_ONLY_TOOL_NAMES,
            MCP_PARENT,
            META_TOOL_NAMES,
            lazy_tools_enabled,
            lazy_tools_scope,
            make_lazy_tools_planner,
            register_lazy_tool_meta_tools,
        )

        key = (tuple(names), loop_kind)
        if key not in self._decided:
            withheld = sorted(n for n in names if n in self._withheld)
            granted = [n for n in names if n not in self._withheld]
            if loop_kind != "orchestrator":
                granted += [m for m in META_TOOL_NAMES if m not in granted and self._registry.has(m)]
            planner = make_lazy_tools_planner(
                apply=(
                    lazy_tools_enabled()
                    and loop_kind != "orchestrator"
                    and (self._chosen is not None or lazy_tools_scope() in ("top_level", "both"))
                ),
                essential_names=frozenset(self._essential),
                context=self._env.context,
                uncounted_names=_uncounted(),
                mode=self._chosen,
            )
            granted, mode, on_demand_pins = planner(self._registry, granted)
            pinned = list(self._essential)
            # Lazy only for an on-demand toolset (ADR-SCH-03 D8): the grant's other groups stay listed, except the
            # MCP servers listed at first use, which are always behind the search (D-LAZY-4).
            pinned += [g for g in on_demand_pins if not (self._lazy and g == MCP_PARENT)]
            if mode == "eager" and self._lazy and loop_kind != "orchestrator" and self._chosen != "eager":
                # D-LAZY-4: an MCP server's tools reach the model only through search_tools/fetch_tools, whatever
                # the count; every other tool of an eager grant stays listed, as its own pinned group.
                pinned += _listed_groups(self._registry, granted, exclude={MCP_PARENT})
                register_lazy_tool_meta_tools(self._registry, self._env.context)
                granted = [*granted, *(m for m in META_TOOL_NAMES if m not in granted)]
                mode = "lazy"
            if mode != "lazy":
                granted = [n for n in granted if n not in LAZY_ONLY_TOOL_NAMES]
            self._decided[key] = (granted, mode)
            self._pinned[key] = pinned
            self._log.info(
                "tools.disclosure",
                mode=mode,
                chosen=self._chosen or "default",
                granted=len(granted),
                pinned=len(self._essential) if mode == "lazy" else 0,
                **({"withheld": ",".join(withheld), "reason": "code_task"} if withheld else {}),
            )
        granted, mode = self._decided[key]
        self._grant, self._mode = list(granted), mode
        return list(granted), mode, list(self._pinned[key]) if mode == "lazy" else []

    async def _first_use(self, name: str, call: ToolCall) -> None:
        """A search or fetch can answer a server granted whole only once it has been listed (D-LAZY-4)."""
        assert self._lazy is not None
        if name == "search_tools":
            await self._lazy.warm(str(call.arguments.get("query") or ""))
        else:
            for group in _named_groups(call.arguments):
                await self._lazy.warm_group(group)

    def routes(self) -> dict[str, Route]:
        return {name: self._route(name) for name in ROUTED if self._registry.has(name)}

    def _route(self, name: str) -> Route:
        async def route(call: ToolCall) -> RouteResult:
            tool: Any = self._registry.resolve_by_name(name)
            if self._lazy:
                await self._first_use(name, call)
            grant = set(self._grant) if self._grant else None
            if grant is not None and self._lazy:
                # A server granted whole names its tools at its first listing: they are the run's from then on.
                grant |= self._lazy.granted_names()
            if name == "search_tools":
                result, _unavailable = await tool.resolve(call, grant=grant)
            else:
                result = await tool.resolve(call, grant=grant)
            self._log.debug("tools.searched", tool=name, mode=self._mode)
            return RouteResult(result)

        return route


def mcp_at_first_use(chosen: str | None, *, deep: bool) -> bool:
    """Whether the run's MCP servers are listed at first use (D-LAZY-4): where its loop discloses tools lazily, so the
    model can search for a server's tools. An operator who turned lazy disclosure off, an agent that chose to list
    every tool, and a deep run (whose orchestrator lists its sub-agents' tools) keep the claim's listing: their model
    reaches a tool only if it is listed."""
    from app.agents.agent_loop.lazy_tools_wiring import (
        lazy_tools_enabled,
        lazy_tools_scope,
    )

    return (
        lazy_tools_enabled()
        and chosen != "eager"
        and not deep
        and (chosen is not None or lazy_tools_scope() in ("top_level", "both"))
    )


def _named_groups(arguments: dict[str, Any]) -> list[str]:
    raw = arguments.get("toolsets") or arguments.get("toolset") or arguments.get("names") or []
    return [raw] if isinstance(raw, str) else [str(g) for g in raw if isinstance(g, str)]


def _listed_groups(registry: ToolRegistry, granted: list[str], *, exclude: set[str]) -> list[str]:
    """The top-level toolset groups with a tool in ``granted``, other than ``exclude``: what an eager grant lists."""
    names = set(granted)
    return [
        g.name
        for g in registry.toolsets()
        if g.parent is None and g.name not in exclude and not g.on_demand and any(n in names for n in g.tool_names)
    ]


def _uncounted() -> frozenset[str]:
    """Granted but left out of the threshold count, as legacy counts: the tools a deployment switch adds
    (``_SWITCHED_TOOL_NAMES``). The harness's own tools, the durable loop's plan tool among them, are left out by the
    decider itself (``harness_granted``)."""
    from app.agents.agent_loop.factory import (
        _SWITCHED_TOOL_NAMES,  # pyright: ignore[reportPrivateUsage]
    )

    return _SWITCHED_TOOL_NAMES


__all__ = ["Disclosure"]
