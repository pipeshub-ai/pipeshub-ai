"""C-1 (PA-16): images a tool returns reach every later model request, durably, as they do in legacy.

A tool that returns an image (as the search and fetch tools do for a
multimodal model) returns multipart content. A provider that accepts images
in tool results gets them there; for one that does not, the transport sends
them in a user message after the run of results they came from (main #4535
retired the PRE_MODEL hook that moved them into the first user message), so the
request both loops build keeps them in the tool result. A durable run journals the
multipart result as parts and rebuilds both from the journal, so the requests
equal legacy's call by call, on every attempt.
"""

from __future__ import annotations

import base64
import json
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from tests.contracts import golden_support
from tests.contracts.golden_support import Scenario
from tests.contracts.test_durable_goldens import _requests, run_durably

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

# Scripted runs that write after reading, compared with today's loop or with recordings made before the flow
# guard (on by default since UX1), which would hold those writes for a person. The guard is tested on its own.
pytestmark = pytest.mark.usefixtures("flow_guard_off")

# A 1x1 PNG.
PNG = base64.b64encode(
    bytes.fromhex(
        "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
        "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
    )
).decode()
IMAGE = {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{PNG}"}}


class GoldenViz:
    """Returns an image with its text, stashing it like `retrieval.py` does."""

    def __init__(self, state: dict[str, Any] | None = None) -> None:
        self._state = state if state is not None else {}

    @tool(
        path="/tools/goldenviz/show_chart",
        short_description="Show a chart",
        description="Render the quarter's chart.",
        parameters=[ToolParameter(name="quarter", type=ParameterType.STRING, description="Quarter", required=True)],
        tags=[Tag(key="effect", value="read")],
    )
    async def show_chart(self, quarter: str) -> Any:  # noqa: ANN401 - multipart tool output
        from app.agent_loop_lib.core.messages import TextPart
        from app.utils.chat_helpers import image_dict_to_part

        if not self._state.get("supports_multipart_tool_result", True):
            self._state.setdefault("pending_tool_images", []).append(dict(IMAGE))
        return [TextPart(text=f"The {quarter} chart."), image_dict_to_part(IMAGE)]


def _script(t: ScriptedTransport) -> None:
    t.add_tool_call(ToolCall(id="call-chart", name="goldenviz__show_chart", arguments={"quarter": "Q3"}))
    t.add_tool_call(ToolCall(id="call-search", name="goldendocs__search_docs", arguments={"query": "q3 revenue"}))
    t.add_text("Q3 revenue grew, as the chart shows.")


SCENARIO = Scenario("agent_tool_image", "agent", "quick", "Show me the Q3 chart", _script)


@pytest.fixture(autouse=True)
def _viz(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    catalog = golden_support._GoldenToolsetRegistry.get_all_toolsets

    def with_viz(self: object) -> dict[str, dict[str, Any]]:
        return {
            **catalog(self),  # type: ignore[arg-type]
            "goldenviz": {"class": GoldenViz, "isInternal": True, "provider": "platform", "essential": False, "description": "Charts"},
        }

    monkeypatch.setattr(golden_support._GoldenToolsetRegistry, "get_all_toolsets", with_viz)
    yield


def _images(request: dict[str, Any]) -> list[tuple[str, int]]:
    """Where each image of a request sits: (role, message index)."""
    return [
        (message["role"], index)
        for index, message in enumerate(request["messages"])
        if isinstance(message.get("content"), list)
        for part in message["content"]
        if part.get("type") == "image"
    ]


@pytest.mark.parametrize("multipart", [True, False], ids=["tool_result_images", "user_message_images"])
async def test_c1_tool_images_reach_every_later_request_as_in_legacy(multipart: bool) -> None:
    with ExitStack() as stack:
        # Only the transport decides now: main #4535 removed the factory's image re-injection hook (L0.1).
        for target in ("app.agents.agent_loop.langchain_transport._supports_multipart_tool_result",):
            stack.enter_context(patch(target, new=lambda _llm: multipart))
        legacy: list[ScriptedTransport] = []
        _ = await golden_support.record_stream(SCENARIO, legacy)
        durable: list[ScriptedTransport] = []
        _ = await run_durably(SCENARIO, transports=durable)
        crashed: list[ScriptedTransport] = []
        _ = await run_durably(
            SCENARIO, transports=crashed, crash_at="worker.commit.after_ack", crash_on=3
        )

    expected = _requests(legacy[0])
    assert len(expected) == 3
    assert _requests(durable[0]) == expected
    # A re-claimed attempt rebuilds the images from the journal, not from process memory.
    assert _requests(crashed[0])[-1] == expected[-1]
    placed = [_images(request) for request in expected]
    assert placed[0] == []
    # Either way the request keeps the image in its tool result: a provider that takes none there gets it in a user
    # message the transport adds on the wire (main #4535 retired the factory's re-injection into the first message).
    assert placed[1] == placed[2] == [("tool", 2)]
    assert json.dumps(expected).count(PNG) == 2
