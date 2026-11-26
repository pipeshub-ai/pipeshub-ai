"""WP-22 OWD-7 / C3.29 / MX-05: spec-form model-facing names, with the internal ids kept.

SN-1 a toolset tool ``<group>__<tool>`` is ``pipeshub_<group>_<tool>``; the default surface has fixed names; an MCP
     export keeps its server-prefixed name, so it never takes PipesHub's prefix (MX-05).
SN-2 the mapping is a bijection over one grant, deterministic in any order, and both forms resolve to the grant's name.
SN-3 a name past ``SCOPED_NAME_MAX`` is cut with a digest and stays unique.
SN-4 an engine shown spec names lists them, and ``search_tools``/``call_tool``/``ask_user`` under theirs (TG-03).
SN-5 a call by either name runs the same tool, and the effect log keys it by the internal id, as before (journals and
     approvals unchanged).
SN-6 ``search_tools`` answers spec names; ``call_tool`` takes either form; the activity line names the tool called.
SN-7 an engine not switched to spec names lists today's names exactly (the default, pinned by recorded live runs).
"""

from __future__ import annotations

import json

from harness_core.effects import args_digest
from harness_core.effects.types import EffectKey
from harness_core.gateway.asking import ASK_USER_ALIAS
from harness_core.gateway.disclosure import (
    CALL_TOOL,
    SEARCH_TOOLS,
    LazyDisclosure,
    shown_tool,
)
from harness_core.testing.contracts.tool_endpoint import (
    BINDING,
    CREATE,
    GRANTED,
    SEARCH,
    build_endpoint,
    claims_for,
)
from harness_core.tool_identity.names import SCOPED_NAME_MAX
from harness_core.tool_identity.spec_names import (
    CALL_TOOL_SPEC,
    SEARCH_TOOLS_SPEC,
    ModelNames,
    spec_name,
)

GRANT = ["jira__search_issues", "knowledgegraph__fetch_record", "mcp_github_list_issues", "run_code"]


def test_sn1_spec_names_follow_owd7() -> None:
    assert spec_name("jira__search_issues") == "pipeshub_jira_search_issues"
    assert spec_name("knowledgegraph__fetch_record") == "pipeshub_knowledgegraph_fetch_record"
    assert spec_name("run_code") == "pipeshub_run_code"
    assert (spec_name(SEARCH_TOOLS), spec_name(CALL_TOOL)) == (SEARCH_TOOLS_SPEC, CALL_TOOL_SPEC)
    assert spec_name("mcp_github_list_issues") == "mcp_github_list_issues"
    assert spec_name(ASK_USER_ALIAS) == ASK_USER_ALIAS


def test_sn2_the_mapping_is_a_deterministic_bijection_that_answers_both_forms() -> None:
    names = ModelNames.spec(GRANT)
    assert ModelNames.spec(reversed(GRANT)) == names
    shown = [names.shown(n) for n in GRANT]
    assert len(set(shown)) == len(GRANT)
    for legacy, spec in zip(GRANT, shown, strict=True):
        assert names.internal(spec) == legacy
        assert names.internal(legacy) == legacy
    clash = ModelNames.spec(["a__b_c", "a_b__c"])
    assert clash.shown("a__b_c") != clash.shown("a_b__c")
    assert {clash.internal(clash.shown(n)) for n in ("a__b_c", "a_b__c")} == {"a__b_c", "a_b__c"}


def test_sn3_a_long_name_is_cut_with_a_digest_and_stays_unique() -> None:
    long_a = "salesforce_enterprise__" + "update_opportunity_line_item_schedule_a"
    long_b = "salesforce_enterprise__" + "update_opportunity_line_item_schedule_b"
    names = ModelNames.spec([long_a, long_b])
    a, b = names.shown(long_a), names.shown(long_b)
    assert len(a) <= SCOPED_NAME_MAX and len(b) <= SCOPED_NAME_MAX
    assert a != b and a.startswith("pipeshub_salesforce")


async def test_sn4_an_engine_on_spec_names_lists_them_with_the_surface_under_its_names() -> None:
    world = build_endpoint(
        spec_tool_names=True, asks=True, disclosure=LazyDisclosure(frozenset({"create"}), _rank)
    )
    listed = [t.name for t in await world.endpoint.list_tools(claims_for(BINDING))]
    assert sorted(listed) == sorted(
        [*(f"pipeshub_{t.name}" for t in GRANTED if t.name != "create"), SEARCH_TOOLS_SPEC, CALL_TOOL_SPEC, ASK_USER_ALIAS]
    )
    assert all(name.startswith("pipeshub_") for name in listed)
    assert {t.name for t in world.endpoint.granted_tools()} == {f"pipeshub_{t.name}" for t in GRANTED}


async def _rank(query: str, limit: int) -> list[str]:
    return ["create", "search"][:limit]


async def test_sn5_either_name_runs_the_tool_and_the_log_keeps_the_internal_id() -> None:
    world = build_endpoint(spec_tool_names=True)
    by_spec = await world.endpoint.call_tool(claims_for(BINDING), "pipeshub_create", {"q": "one"})
    by_legacy = await world.endpoint.call_tool(claims_for(BINDING), "create", {"q": "two"})
    assert (by_spec.disposition, by_legacy.disposition) == ("executed", "executed")
    assert world.runs == ["create", "create"]
    for q in ("one", "two"):
        recorded = await world.log.find(EffectKey(BINDING.run_id, CREATE.effect.tool_id, args_digest({"q": q})))
        assert recorded is not None and recorded.state == "completed"
    assert world.endpoint.internal_name("pipeshub_create") == "create"


async def test_sn6_search_answers_spec_names_and_call_tool_takes_either() -> None:
    world = build_endpoint(spec_tool_names=True, disclosure=LazyDisclosure(frozenset({"create", "search"}), _rank))
    found = json.loads((await world.endpoint.call_tool(claims_for(BINDING), SEARCH_TOOLS_SPEC, {"query": "x"})).text)
    assert [f["name"] for f in found] == ["pipeshub_create", "pipeshub_search"]
    for target in ("pipeshub_search", "search"):
        result = await world.endpoint.call_tool(
            claims_for(BINDING), CALL_TOOL_SPEC, {"name": target, "arguments": {"q": "x"}}
        )
        assert result.disposition == "executed"
    assert world.runs == [SEARCH.name, SEARCH.name]
    assert shown_tool("mcp__pipeshub__" + CALL_TOOL_SPEC, {"name": "pipeshub_search"}, prefix="mcp__pipeshub__") == (
        "pipeshub_search"
    )


async def test_sn7_by_default_an_engine_is_listed_todays_names_exactly() -> None:
    world = build_endpoint()
    listed = sorted(t.name for t in await world.endpoint.list_tools(claims_for(BINDING)))
    assert listed == sorted(t.name for t in GRANTED)
    assert world.endpoint.internal_name("pipeshub_create") == "pipeshub_create"
