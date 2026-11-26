"""C-23 (DUR1): every capability of today's agent loop is on the durable loop or listed with a reason.

Three sources of truth are read, never copied: the legacy wiring's source (every middleware it hands a hook
registry, every registration the factory makes), the registries both loops build for one fully granted agent, and
the mode catalogs. A new legacy capability fails here until the durable path has it or ``durable_parity.INVENTORY``
lists it with a reason.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from harness_core.kernel.hooks.events import HookEvent
from tests.cohesion.durable_parity import (
    BY_KEY,
    DURABLE_DISPATCHED_EVENTS,
    INVENTORY,
    post_run_step,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

PYTHON = Path(__file__).resolve().parents[2]
# Where legacy wires its hooks and tools: the factory, and the helpers it calls to register middleware.
LEGACY_WIRING = (
    "app/agents/agent_loop/factory.py",
    "app/agents/agent_loop/sandbox_bridge.py",
    "app/agents/agent_loop/skills_wiring.py",
    "app/agents/agent_loop/lazy_tools_wiring.py",
    "harness_core/kernel/hooks/builtin/turn_guards.py",
)
FACTORY = "app/agents/agent_loop/factory.py"
# The factory registers the PRE_MODEL context layers from one ordered list (main #4535); each layer it builds counts as
# wired, and the loop variable the factory hands ``.use`` names none of them.
CONTEXT_PIPELINE = "app/agent_loop_lib/context/pipeline.py"
_PIPELINE_LOOP_VARIABLE = "layer"
_GENERIC = frozenset({"register", "register_tool"})
VECTOR = PYTHON.parents[1] / "contracts" / "harness" / "vectors" / "durable-chat-modes.json"


def _middleware_used(path: str) -> set[str]:
    """What the module hands to ``.use(...)``: the callee of a factory call, or the expression itself."""
    tree = ast.parse((PYTHON / path).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "use" and node.args:
            arg = node.args[0]
            found.add(ast.unparse(arg.func if isinstance(arg, ast.Call) else arg))
    return found


def _pipeline_layers() -> set[str]:
    """The shapers ``build_context_pipeline`` puts in its list (``layers.append(shape_x(...))`` and the generator
    ``layers.extend``), the layers the factory then registers one by one."""
    tree = ast.parse((PYTHON / CONTEXT_PIPELINE).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id.startswith("shape_"):
            found.add(node.func.id)
    return found


def _registrations(path: str) -> set[str]:
    tree = ast.parse((PYTHON / path).read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else func.id if isinstance(func, ast.Name) else ""
        if ("register" in name or name.startswith("install_") or name == "_build_hooks") and name not in _GENERIC:
            found.add(name)
    return found


def test_c23_every_row_is_ported_or_says_why() -> None:
    keys = [c.key for c in INVENTORY]
    assert len(keys) == len(set(keys)), "one row per capability"
    for row in INVENTORY:
        if row.status == "ported":
            assert row.where, f"{row.key}: ported where?"
        else:
            assert row.reason.strip(), f"{row.key} is {row.status} with no reason"


def test_c23_every_middleware_legacy_wires_is_listed() -> None:
    wired = set().union(*(_middleware_used(p) for p in LEGACY_WIRING)) - {_PIPELINE_LOOP_VARIABLE}
    wired |= _pipeline_layers()
    missing = sorted(wired - set(BY_KEY))
    assert not missing, f"legacy hook middleware not in the DUR1 inventory: {missing}"


def test_c23_every_registration_the_factory_makes_is_listed() -> None:
    hook_factories = {name for name in _registrations(FACTORY) if name.startswith("shape_")}
    missing = sorted((_registrations(FACTORY) - hook_factories) - set(BY_KEY))
    assert not missing, f"legacy factory registrations not in the DUR1 inventory: {missing}"


def test_c23_every_not_ported_hook_is_listed_with_its_reason() -> None:
    from app.harness.assembly.hooks import NOT_PORTED

    rows = [c for c in INVENTORY if c.kind == "hook" and c.status != "ported"]
    for key in NOT_PORTED:
        name = key.split(" ", 1)[0]
        assert any(name.startswith(r.key) or r.key in key or name in r.reason or key in r.reason for r in rows), (
            f"NOT_PORTED {key!r} has no inventory row"
        )


def test_c23_every_mode_is_durable_exactly_when_the_inventory_says_ported() -> None:
    from app.agents.agent_loop.modes import MODE_CATALOG
    from app.agents.chat_modes.policy import _CANONICAL_POLICIES, _LEGACY_MODE_FALLBACK

    vector = json.loads(VECTOR.read_text(encoding="utf-8"))
    durable_agent = {m.lower() for m in vector["durableChatModes"]}
    durable_chat = set(vector["assistantChatModes"])
    modes = {"auto"} | {key for m in MODE_CATALOG for key in (m.name, *m.aliases)}
    for mode in modes:
        row = BY_KEY.get(mode)
        assert row is not None and row.kind == "mode", f"agent mode {mode!r} is not in the inventory"
        assert (row.status != "not_ported") == (mode.lower() in durable_agent), mode
    for mode in set(_CANONICAL_POLICIES) | set(_LEGACY_MODE_FALLBACK):
        row = BY_KEY.get(mode)
        assert row is not None, f"chat mode {mode!r} is not in the inventory"
        if row.kind == "chat_mode":
            assert (row.status == "ported") == (mode in durable_chat), mode


@pytest.fixture
def _world(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from tests.unit.harness.assembly import world as w

    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _with_skills(services: Any) -> Any:  # noqa: ANN401 - world.Services
    """The org runs skills, so both loops build a skill manager (and legacy registers its learning)."""
    from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
    from tests.support.config_service import RIG_PLATFORM_FLAGS

    services.config.put(
        PLATFORM_SETTINGS_KEY, {"featureFlags": {**RIG_PLATFORM_FLAGS, "ENABLE_MCP": True, "ENABLE_SKILLS": True}}
    )
    # An org with no skills of its own: the skill store lists nothing.
    services.graph.get_nodes_by_filters = AsyncMock(return_value=[])
    return services


def _names(hooks: Any) -> dict[str, list[str]]:  # noqa: ANN401 - HookRegistry
    def name(mw: Any) -> str:  # noqa: ANN401
        return (getattr(mw, "__qualname__", None) or type(mw).__qualname__).split(".<locals>")[0]

    out: dict[str, list[str]] = {}
    for event in HookEvent:
        stack = hooks.wrapper(event)._stack if event == HookEvent.PRE_MODEL_CALL else [m for _, m in hooks.on(event)._stack]
        out[event.value] = [name(m) for m in stack]
    return out


def _row(name: str) -> str | None:
    """The inventory key a registered middleware's name belongs to (``ToolErrorTracker.pre_tool_use`` is the
    factory's ``error_tracker.pre_tool_use``)."""
    if name in BY_KEY:
        return name
    owner, _, method = name.rpartition(".")
    prefix = {"ToolErrorTracker": "error_tracker", "RepeatedReadGuard": "repeated_reads"}.get(owner)
    return f"{prefix}.{method}" if prefix and f"{prefix}.{method}" in BY_KEY else None


@pytest.mark.usefixtures("_world")
@pytest.mark.parametrize("chat_mode", ["quick", "react"])
async def test_c23_the_durable_registry_runs_what_legacy_registers_or_lists_why(
    chat_mode: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.harness.assembly import world as w

    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "true")
    from tests.unit.harness.assembly.test_parity import (
        _durable_turn_one,
        _full_grant,
        _legacy,
    )

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    # Hooks with skills on (legacy then registers its learning); the tool comparison below without, since skill tools
    # push the grant over the lazy-disclosure threshold on both loops alike.
    runtime, _, _shaped = await _durable_turn_one(
        _with_skills(w.Services(agent=agent)), spec, chat_mode, "find the bug"
    )
    _agent, legacy_runtime, _goal = await _legacy(_with_skills(w.Services(agent=agent)), spec, chat_mode, "find the bug")
    legacy = _names(legacy_runtime.hooks)
    durable = _names(runtime.shaper._hooks.registry)  # type: ignore[union-attr]
    post_run = {step.key for step in runtime.post_run}
    assert "SkillLearning" in legacy["post_agent"], "the world wires skills, so legacy learns"
    problems: list[str] = []
    for event, names in legacy.items():
        for name in names:
            row = BY_KEY.get(_row(name) or "")
            if row is None:
                problems.append(f"{event}: {name} is not in the inventory")
                continue
            step = post_run_step(row)
            runs = step in post_run if step is not None else name in durable[event] and event in DURABLE_DISPATCHED_EVENTS
            if row.status == "ported" and not runs:
                problems.append(f"{event}: {name} is listed ported but the durable run does not run it")
            if row.status != "ported" and runs:
                problems.append(f"{event}: {name} runs on the durable path but is listed {row.status}")
    assert not problems, problems
    # What the model is offered on turn 1. The sandbox tools need an agent-code pin (P5.11), which this world's
    # spec does not carry; C-22 and C-24 hold run_code to legacy.
    offered_run, _, shaped = await _durable_turn_one(w.Services(agent=agent), spec, chat_mode, "find the bug")
    legacy_agent, _, _ = await _legacy(w.Services(agent=agent), spec, chat_mode, "find the bug")
    offered = {schema.name for schema in shaped.tools or []}
    # ADR-OWD-07 D-LAZY-4: an MCP server's tools wait behind the tool search until first use; they stay granted.
    deferred = {n for n in offered_run.tools.names if n.startswith("mcp_")} - offered  # type: ignore[attr-defined]
    assert not deferred or "search_tools" in offered
    missing = set(legacy_agent.spec.tool_names) - offered - deferred - {"run_code", "install_packages", "read_sandbox_file"}
    assert not missing, f"tools legacy offers and the durable run does not: {sorted(missing)}"


@pytest.mark.parametrize(
    "key", ["transport.system_cache_blocks", "max_turns_final_answer", "entity_tools", "progressive_entity_tools"]
)
def test_dur3_b1_main_sync_and_p48_rows_are_ported(key: str) -> None:
    """Re-derived on the synced head: the rows PR1 and DUR1 left open are ported, none is stale."""
    assert BY_KEY[key].status == "ported", BY_KEY[key]


def test_dur3_a7_the_allowed_differences_are_exactly_the_rows_not_ported() -> None:
    from tests.cohesion.durable_parity import ALLOWED_DIFFERENCES

    assert {c.key for c in ALLOWED_DIFFERENCES} == {c.key for c in INVENTORY if c.status != "ported"}
    assert all(c.reason for c in ALLOWED_DIFFERENCES)
    stale = [c.key for c in ALLOWED_DIFFERENCES if "P4.8's" in c.reason or "PR1 open item" in c.reason]
    assert not stale, f"a reason points at work that has landed: {stale}"

