"""UX1 CL-1, CL-2, CL-9: a tool's ``sink`` tag says where its writes go; without one, or with one nobody can read,
a write is external (fail closed)."""

from __future__ import annotations

import pytest

from harness_core.kernel.durable.effects import classify_tool_effect, with_call_effect
from harness_core.tools.base import Tag
from harness_core.tools.tags import (
    TAG_EFFECT_READ,
    TAG_EFFECT_WRITE,
    TAG_SINK_NONE,
    TAG_SINK_SELF,
)


@pytest.mark.parametrize(
    ("tags", "egress", "leaves"),
    [
        ([TAG_EFFECT_WRITE], "external", True),
        ([TAG_EFFECT_WRITE, TAG_SINK_NONE], "none", False),
        ([TAG_EFFECT_WRITE, TAG_SINK_SELF], "self", False),
        ([TAG_EFFECT_WRITE, Tag("sink", "internal_audience")], "internal_audience", False),
        ([TAG_EFFECT_WRITE, Tag("sink", "external")], "external", True),
        ([Tag("effect", "execute"), TAG_SINK_NONE], "none", False),
        ([Tag("effect", "workspace"), Tag("sink", "external")], "none", False),
    ],
)
def test_cl1_the_sink_tag_sets_the_sink(tags: list[Tag], egress: str, leaves: bool) -> None:
    sink = classify_tool_effect("t", tags).sink
    assert (sink.egress, sink.args_leave_boundary) == (egress, leaves)


@pytest.mark.parametrize(
    "tags",
    [
        [TAG_EFFECT_WRITE, Tag("sink", "nowhere")],
        [TAG_EFFECT_WRITE, TAG_SINK_NONE, TAG_SINK_SELF],
        [Tag("type", "mystery"), TAG_SINK_NONE, Tag("sink", "bogus")],
    ],
)
def test_cl2_an_unreadable_or_ambiguous_sink_is_external(tags: list[Tag]) -> None:
    assert classify_tool_effect("t", tags).sink.egress == "external"


def test_cl9_a_call_of_a_write_tool_keeps_its_declared_sink() -> None:
    own = classify_tool_effect("create_doc", [TAG_EFFECT_WRITE, TAG_SINK_SELF])
    assert with_call_effect(own, "execute").sink.egress == "self"
    assert with_call_effect(own, "workspace").sink.egress == "none"
    reader = classify_tool_effect("search", [TAG_EFFECT_READ])
    assert with_call_effect(reader, "write").sink.egress == "external"
    assert with_call_effect(reader, "workspace").sink.egress == "none"
