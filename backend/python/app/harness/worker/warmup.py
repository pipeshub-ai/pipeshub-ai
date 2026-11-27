"""Import what a run's assembly needs before the worker claims its first run (P1b C-17).

The assembler imports the agent, retrieval and chat stacks on first use, so a
fresh worker's first run spent seconds importing them (8.9 s in the P1a gate
smokes), and that time was added to every recovery a fresh worker made (a
rolling deploy, a crash). ``harness_main`` runs only where an operator enabled
durable runs, so it pays the imports once at start instead, before it claims.
The memory is the same as after a first run; only when it is paid moves.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from harness_core.logs import HarnessLog

# The roots of what ``PipesHubRuntimeAssembler.build`` and its resources import lazily.
ASSEMBLY_MODULES: tuple[str, ...] = (
    "app.modules.retrieval.retrieval_service",
    "app.agents.agent_loop.factory",
    "app.api.routes.agent",
    "app.containers.utils.utils",
    "app.agents.chat_modes.setup",
    "app.agents.agent_loop.lazy_tools_wiring",
    "app.agents.agent_loop.mcp_lazy",
    "app.agents.agent_loop.skills_wiring",
    "app.agents.agent_loop.skills.bundle_source",
    "app.agents.agent_loop.skills.pinned_manager",
    "app.agents.agent_loop.skills.labelled_manager",
    "app.agent_loop_lib.agent.tool_loop",
    "app.agent_loop_lib.context.base",
    # Deep mode (K-L): its loop, delegation and the citation merge on a join.
    "app.harness.assembly.deep",
    "app.agent_loop_lib.agent.spawn_scheduler",
    "app.agents.actions.retrieval.retrieval",
    # Each model call's output limit (P4.17), read through the durable bindings on the first call.
    "app.utils.output_limits",
    # The OpenAI SDK loads its resources and anyio's asyncio backend on a client's first request (about 500
    # modules): imported on the event loop, they held a fresh worker's lease renewals for seconds under load.
    "openai.resources",
    "anyio._backends._asyncio",
)


# The deployment's graph store provider, which a run's first graph read reaches through ``GraphDBProviderFactory``
# (``DATA_STORE``; ArangoDB's comes with the factory). The worker's module no longer imports one, so the Neo4j driver
# (with the pandas and numpy it loads) is held only where the deployment uses Neo4j (R25OPS-FU-1).
GRAPH_STORE_MODULES: dict[str, tuple[str, ...]] = {
    "neo4j": ("app.services.graph_db.neo4j.neo4j_provider",),
    "postgres": ("app.services.graph_db.postgres.provider",),
}


def graph_store_modules(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """The provider modules of the graph store ``DATA_STORE`` names (the factory's default, ArangoDB, needs none)."""
    store = ((os.environ if env is None else env).get("DATA_STORE") or "arangodb").strip().lower()
    return GRAPH_STORE_MODULES.get(store, ())


def _import_all(modules: Sequence[str]) -> None:
    for name in modules:
        importlib.import_module(name)


def discover_toolsets() -> None:
    """The release's toolsets, which the assembly imports by module name the first time a run lists its tools.

    Left to that first run, the imports (every connector's SDK) ran on the event loop of a worker holding a
    lease: under load the loop missed the renewal and the attempt fenced itself (MCP-FIX, OPS1)."""
    from app.agents.registry.toolset_registry import get_toolset_registry

    registry = get_toolset_registry()
    if not registry.get_all_toolsets():
        registry.auto_discover_toolsets()


async def warm_up(
    log: HarnessLog,
    modules: Sequence[str] = ASSEMBLY_MODULES,
    discover: Callable[[], None] = discover_toolsets,
    stores: Callable[[], Sequence[str]] = graph_store_modules,
) -> bool:
    """Import ``modules`` and the graph store's provider and discover the toolsets off the event loop; False (logged)
    if that fails, whose run's assembly then reports it."""
    started = time.monotonic()
    try:
        await asyncio.to_thread(_import_all, (*modules, *stores()))
        await asyncio.to_thread(discover)
    except Exception as exc:
        log.warning("worker.warm_up_failed", errorType=type(exc).__name__, modules=len(modules))
        return False
    log.info("worker.warmed", modules=len(modules), ms=round((time.monotonic() - started) * 1000))
    return True


__all__ = ["ASSEMBLY_MODULES", "GRAPH_STORE_MODULES", "discover_toolsets", "graph_store_modules", "warm_up"]
