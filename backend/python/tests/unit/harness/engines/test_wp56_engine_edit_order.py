"""WP-56 (AP-06): an engine run's held write names the tool's schema order with its edit offer, as the loop's does.

WP56-ORDER-4 the engine's offer carries the served tool's property order; a tool whose schema lists none carries none.
"""

from __future__ import annotations

from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.testing.engines.durable import CREATE, TOOLS, EngineWorld

RUN = "run-wp56-order"


def _explained(w: EngineWorld) -> dict[str, object]:
    [reason] = [e for e in w.events(RUN) if e.type == "interaction.explained"]
    return dict(reason.data)


async def test_wp56_order_4_the_engine_offer_names_the_served_tools_property_order() -> None:
    schema = ToolSchema(
        name=CREATE.name,
        description="Create a thing",
        input_schema={"type": "object", "properties": {"title": {}, "channel": {}, "text": {}}},
    )
    w = EngineWorld(tools=TOOLS, schemas={CREATE.name: schema})
    w.admit(RUN, 'tool create {"text": "hello", "channel": "sales", "title": "Q3"}\nsay waiting')
    assert (await w.attempt(RUN)).status == "suspended"
    reason = _explained(w)
    assert reason["editable"] is True
    assert reason["fieldOrder"] == ["title", "channel", "text"]


async def test_wp56_order_4_a_tool_whose_schema_lists_no_properties_names_no_order() -> None:
    w = EngineWorld(tools=TOOLS)
    w.admit(RUN, 'tool create {"text": "hello", "channel": "sales"}\nsay waiting')
    assert (await w.attempt(RUN)).status == "suspended"
    reason = _explained(w)
    assert reason["editable"] is True
    assert "fieldOrder" not in reason
