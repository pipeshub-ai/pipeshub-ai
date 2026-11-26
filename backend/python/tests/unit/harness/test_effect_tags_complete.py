# pyright: reportPrivateUsage=false
# The enumerators call the loaders' own helpers, so they scan what the loaders scan.
"""P2.1: every tool a durable run can reach states its effect class (ET-1..ET-7).

Each enumerator builds tools the way its ``ToolSource`` does, through the
same registration functions, with stand-ins for the services those tools would
call (nothing is executed). RO-1 (``test_tool_effects.py``) scans every
``@tool`` in the source; this test covers what the sources register at run
time, including tools that are not ``@tool`` methods. Delegates are not tagged:
the assembler derives their class from the tools they may call (ET-6).
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING

import pytest

from app.harness.assembly.assembler import Catalog
from app.harness.assembly.loop import LATE_TOOLS
from app.harness.assembly.tool_sources import PLATFORM, SourcedTool, default_sources
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.provenance.rules import NO_CONTENT_RULE
from harness_core.tools.tags import EFFECT_TAG_KEY

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from harness_core.tools.base import Tag, Tool

EFFECT_CLASSES = ("read", "workspace", "write", "execute")


def _registered(register: Callable[[object], None]) -> list[Tool]:
    from app.agent_loop_lib.tools.registry import ToolRegistry

    registry = ToolRegistry()
    register(registry)
    return [registry.resolve_by_name(name) for name in registry.names()]


def toolsets() -> list[Tool]:
    """Connector and internal toolsets (``PipesHubToolLoader``'s scan) and the dynamic tools."""
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder
    from app.agents.agent_loop.tool_loader import (
        _build_dynamic_tools,
        _has_tool_decorated_methods,
        _infer_path_prefix,
    )
    from tests.support.toolset_registry import discovered_toolsets
    from tests.support.web_tools import request_context

    discovered = discovered_toolsets()
    tools: list[Tool] = []
    for ts_name, meta in sorted(discovered.items()):
        cls = meta.get("class")
        if cls is None or not _has_tool_decorated_methods(cls):
            continue
        prefix = _infer_path_prefix(cls, fallback_name=ts_name)
        # The scan reads the @tool metadata only; the toolset is never constructed.
        builder = ToolsetBuilder(
            cls.__new__(cls), name=prefix.rsplit("/", 1)[-1], description="", path_prefix=prefix
        )
        tools.extend(builder.tools)
    every_dynamic_tool = {
        "config_service": object(),
        "has_sql_connector": True,
        "has_sql_knowledge": True,
        "has_slack_connector": True,
        "has_slack_knowledge": True,
        "web_search_config": {"provider": "duckduckgo"},
        "org_id": "org-1",
    }
    tools.extend(_build_dynamic_tools(request_context(every_dynamic_tool)))  # type: ignore[arg-type]
    return tools


def mcp() -> list[Tool]:
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
    from app.agents.mcp.models import MCPToolInfo

    server = types.SimpleNamespace(instance_id="inst-1", display_name="Docs", name="docs")
    info = MCPToolInfo(name="delete_page", namespaced_name="mcp_docs_delete_page")
    return [MCPToolAdapter(server, info, object())]  # type: ignore[arg-type]


def meta() -> list[Tool]:
    from app.agents.agent_loop.lazy_tools_wiring import register_lazy_tool_meta_tools

    return _registered(lambda registry: register_lazy_tool_meta_tools(registry, None))  # type: ignore[arg-type]


def sandbox() -> list[Tool]:
    from app.agents.agent_loop.sandbox_bridge import register_coding_sandbox_tools

    return _registered(
        lambda registry: register_coding_sandbox_tools(registry, object(), allow_network=False)  # type: ignore[arg-type]
    )


def skills() -> list[Tool]:
    from app.agents.agent_loop.skills_wiring import register_skill_tools

    return _registered(lambda registry: register_skill_tools(registry, object()))  # type: ignore[arg-type]


def artifacts() -> list[Tool]:
    from app.agent_loop_lib.tools.builtin.data.retrieve_artifact import (
        RetrieveArtifactContentTool,
    )

    return [RetrieveArtifactContentTool(store=object())]  # type: ignore[arg-type]


def final_answer() -> list[Tool]:
    from app.agent_loop_lib.tools.builtin.planning.final_answer import FinalAnswerTool

    # ``FinalAnswerSource`` registers this when PIPESHUB_ENABLE_FINAL_ANSWER is set.
    return [FinalAnswerTool()]


def late() -> list[Tool]:
    """Tools a hook registers mid-run and the durable port describes on arrival."""
    from app.agents.agent_loop.hooks.citations import (
        CitationCollector,
        _FetchFullRecordTool,
    )

    context = types.SimpleNamespace(tool_state={})
    return [_FetchFullRecordTool(CitationCollector(context), context)]  # type: ignore[arg-type]


ENUMERATORS: dict[str, Callable[[], list[Tool]]] = {
    "toolsets": toolsets,
    "mcp": mcp,
    "meta": meta,
    "sandbox": sandbox,
    "skills": skills,
    "artifacts": artifacts,
    "final_answer": final_answer,
    "late": late,
}

# Measured on 91a43c821: a source that
# loads fewer tools has lost some to an import failure, which must not pass silently.
FLOORS = {
    "toolsets": 371,
    "mcp": 1,
    "meta": 3,
    "sandbox": 3,
    "skills": 5,
    "artifacts": 1,
    "final_answer": 1,
    "late": 1,
}


def effect_tags(tags: Iterable[Tag]) -> list[str]:
    return [tag.value for tag in tags if tag.key == EFFECT_TAG_KEY]


def untagged(tools: Mapping[str, Iterable[Tool]]) -> list[str]:
    """``source: name [effect tags]`` for every tool without exactly one valid effect tag."""
    return [
        f"{source}: {tool.name} {effect_tags(tool.tags)}"
        for source, listed in tools.items()
        for tool in listed
        if len(found := effect_tags(tool.tags)) != 1 or found[0] not in EFFECT_CLASSES
    ]


@pytest.fixture(scope="module")
def enumerated() -> dict[str, list[Tool]]:
    return {source: enumerate_() for source, enumerate_ in ENUMERATORS.items()}


def test_et1_every_reachable_tool_states_its_effect(enumerated: dict[str, list[Tool]]) -> None:
    offenders = untagged(enumerated)
    assert offenders == [], (
        "every tool a durable run can reach carries exactly one effect tag "
        "(harness_core.tools.tags TAG_EFFECT_*; when unsure, write):\n" + "\n".join(offenders)
    )


def test_et2_the_check_lists_an_untagged_tool() -> None:
    from app.agent_loop_lib.tools.builtin.coordination.clarify import ClarifyTool

    planted = ClarifyTool.__new__(ClarifyTool)
    tagged = mcp()[0]
    assert untagged({"planted": [planted, tagged]}) == [f"planted: {planted.name} []"]


def test_et3_every_source_is_enumerated_and_none_lost_tools(enumerated: dict[str, list[Tool]]) -> None:
    assert {source.name for source in default_sources()} | {"late"} == set(ENUMERATORS)
    assert set(LATE_TOOLS) <= {tool.name for tool in enumerated["late"]}
    counts = {source: len(tools) for source, tools in enumerated.items()}
    assert {s: n for s, n in counts.items() if n < FLOORS[s]} == {}, counts


def test_et4_mcp_tools_are_writes_whatever_the_server_claims() -> None:
    effect = classify_tool_effect("mcp_docs_delete_page", mcp()[0].tags)
    assert (effect.effect, effect.idempotent) == ("write", False)
    assert (effect.sink.egress, effect.sink.args_leave_boundary) == ("external", True)


@pytest.mark.parametrize(
    ("name", "effect"),
    [("run_code", "execute"), ("install_packages", "execute"), ("read_sandbox_file", "read")],
)
def test_et5_sandbox_tools_keep_their_class_now_stated(name: str, effect: str) -> None:
    tool = next(t for t in sandbox() if t.name == name)
    stated = classify_tool_effect(name, tool.tags)
    before = classify_tool_effect(name, [t for t in tool.tags if t.key != EFFECT_TAG_KEY])
    assert effect_tags(tool.tags) == [effect]
    assert stated == before and stated.effect == effect


@pytest.mark.parametrize(
    ("delegate", "effect"),
    [("reader", "read"), ("writer", "write"), ("nested_reader", "read"), ("nested_writer", "write"), ("stray", "write")],
)
def test_et6_a_delegate_is_read_only_if_everything_it_may_call_is(delegate: str, effect: str) -> None:
    from app.agent_loop_lib.tools.registry import ToolRegistry

    registry = ToolRegistry()
    for tool in skills():
        registry.register_tool(tool)
    tools = (
        SourcedTool("skills_list", f"{PLATFORM}@skills/skills_list", None, provenance=NO_CONTENT_RULE),
        SourcedTool("skill_manage", f"{PLATFORM}@skills/skill_manage", None, provenance=NO_CONTENT_RULE),
    )
    catalog = Catalog(registry, tools).with_delegates(
        {
            "reader": ("skills_list",),
            "writer": ("skills_list", "skill_manage"),
            "nested_reader": ("reader",),
            "nested_writer": ("writer",),
            "stray": ("not_in_the_catalog",),
        }
    )
    effects = catalog.effects()
    assert effects[delegate].effect == effect
    assert effects[delegate].tool_id == f"{PLATFORM}@agents/{delegate}"


def test_et7_legacy_search_ranking_ignores_the_new_tags() -> None:
    from dataclasses import replace

    from app.agent_loop_lib.tools.index import _tool_haystack

    for tool in [*sandbox(), *mcp(), *skills()]:
        summary = tool.to_summary()
        plain = replace(summary, tags=tuple(t for t in summary.tags if t.key != EFFECT_TAG_KEY))
        assert _tool_haystack(summary) == _tool_haystack(plain), tool.name
