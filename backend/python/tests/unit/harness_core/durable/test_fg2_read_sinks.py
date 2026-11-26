"""FG2 XS-1..XS-3: a read's sink says where its request goes, and the flow rule holds any call that sends data
outside the organization once the run has read private data, read or write."""

from __future__ import annotations

from typing import get_args

import pytest

from harness_core.effects.flow import FlowRule, Sensitivity, sends_out
from harness_core.kernel.durable.effects import classify_tool_effect, with_call_effect
from harness_core.tools.base import Tag
from harness_core.tools.tags import (
    TAG_EFFECT_READ,
    TAG_EFFECT_WRITE,
    TAG_SINK_EXTERNAL,
    TAG_SINK_NONE,
    TAG_SINK_SELF,
)

FETCH = classify_tool_effect("fetch_url", [TAG_EFFECT_READ, TAG_SINK_EXTERNAL])
SEARCH = classify_tool_effect("jira_search", [TAG_EFFECT_READ])
POST = classify_tool_effect("slack_send", [TAG_EFFECT_WRITE])
DRAFT = classify_tool_effect("gmail_draft", [TAG_EFFECT_WRITE, TAG_SINK_SELF])


@pytest.mark.parametrize(
    ("tags", "effect", "egress"),
    [
        ([TAG_EFFECT_READ], "read", "self"),
        ([Tag("type", "search")], "read", "self"),
        ([TAG_EFFECT_READ, TAG_SINK_EXTERNAL], "read", "external"),
        ([TAG_EFFECT_READ, TAG_SINK_NONE], "read", "none"),
        ([TAG_EFFECT_READ, Tag("sink", "elsewhere")], "read", "external"),
        ([TAG_EFFECT_READ, TAG_SINK_NONE, TAG_SINK_SELF], "read", "external"),
        ([TAG_EFFECT_WRITE], "write", "external"),
        ([], "write", "external"),
    ],
)
def test_xs1_a_read_is_its_own_systems_unless_it_declares_otherwise(tags: list[Tag], effect: str, egress: str) -> None:
    classified = classify_tool_effect("t", tags)
    assert (classified.effect, classified.sink.egress) == (effect, egress)
    assert classified.sink.args_leave_boundary == (egress == "external")


@pytest.mark.parametrize("sensitivity", get_args(Sensitivity))
def test_xs2_the_rule_holds_an_external_sink_call_whatever_its_effect(sensitivity: Sensitivity) -> None:
    rule = FlowRule("ask")
    held = "allow" if sensitivity == "public" else "ask"
    assert rule.decide(sensitivity, FETCH) == held
    assert rule.decide(sensitivity, POST) == held
    assert rule.decide(sensitivity, SEARCH) == "allow"
    assert rule.decide(sensitivity, DRAFT) == "allow"


def test_xs2_a_threshold_and_a_level_apply_to_reads_as_to_writes() -> None:
    assert FlowRule("deny", "private").decide("private", FETCH) == "allow"
    assert FlowRule("deny", "private").decide("unknown", FETCH) == "deny"
    assert [sends_out(e) for e in (FETCH, SEARCH, POST, DRAFT)] == [True, False, True, False]


def test_xs3_a_call_takes_the_sink_of_its_own_class() -> None:
    soql = classify_tool_effect("salesforce_soql_query", [TAG_EFFECT_WRITE])
    assert with_call_effect(soql, "read").sink.egress == "self"
    assert with_call_effect(SEARCH, "write").sink.egress == "external"
    assert with_call_effect(FETCH, "workspace").sink.egress == "none"
    assert with_call_effect(DRAFT, "execute").sink.egress == "self"
