"""DUR3-D2 (DUR1-F5, fixed by P4.8): the durable request reaches the provider with legacy's cache split.

``test_driver_symmetry.py::test_dur1_f5_…`` holds the ``[stable, volatile]`` system blocks equal at the transport
boundary. This holds them in the provider payload: the Anthropic transport marks the stable block, and only it, on
the durable request as on legacy's; Bedrock Converse builds the same two system blocks from either (it places no
cache point on either loop's request). The default LangChain transport sends the joined string on both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from harness_core.models.sigv4 import AwsCredentials
from harness_core.models.transports.anthropic import AnthropicTransport
from harness_core.models.transports.bedrock_converse import BedrockConverseTransport
from harness_core.testing import ModelScript
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.knowledge_base import CLIENT_TIME, CLIENT_TIMEZONE
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.wire import WireRequest, _without_plan_tool, drive_captured
from tests.harness_replay.world import Doc, WorldFixtures

if TYPE_CHECKING:
    from collections.abc import Callable

EPHEMERAL = {"type": "ephemeral"}


def _spec() -> DriveSpec:
    return DriveSpec(
        scenario="dur3-cache-markers",
        query="What is the travel policy?",
        fixtures=WorldFixtures(knowledge=[Doc(id="pol-travel", title="Travel policy", text="Economy under 6 hours.")]),
        knowledge_search=True,
        fake_toolsets=(),
        # The client's time is volatile prompt content: it lands in the second, uncached block.
        timezone=CLIENT_TIMEZONE,
        current_time=CLIENT_TIME,
    )


SCRIPT = ModelScript(
    id="cache-markers",
    turns=[
        Turn(reply=Reply(tool_calls=[ToolCallSpec(id="s1", name="knowledgegraph__search", args={"query": "travel"})])),
        Turn(reply=Reply(text="Economy under 6 hours.")),
    ],
)


async def _requests() -> tuple[list[WireRequest], list[WireRequest]]:
    legacy = await drive_captured(legacy_driver.drive, _spec(), SCRIPT)
    durable = await drive_captured(durable_driver.drive, _spec(), SCRIPT)
    # The plan tool's prompt line is the allowed difference (UXR-B1, R49-PLAN); the cache split is what is compared.
    return legacy.requests, [_without_plan_tool(r) for r in durable.requests]


async def test_dur3_d2_anthropic_marks_the_stable_block_of_every_durable_request_as_legacy() -> None:
    anthropic = AnthropicTransport(api_key="test-key")
    legacy, durable = await _requests()
    assert len(durable) == len(legacy) == 2
    for lr, dr in zip(legacy, durable, strict=True):
        build = cast("Callable[[str, list[str]], list[dict[str, Any]] | None]", anthropic._build_system_kwargs)  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
        ours = build(dr.system, list(dr.system_blocks))
        theirs = build(lr.system, list(lr.system_blocks))
        assert ours == theirs
        assert ours is not None and len(ours) == 2, "the stable and the volatile block"
        assert ours[0].get("cache_control") == EPHEMERAL
        assert "cache_control" not in ours[1], "the volatile tail stays uncached"


async def test_dur3_d2_bedrock_builds_the_same_system_blocks_from_either_request() -> None:
    bedrock = BedrockConverseTransport(
        region="us-east-1", credentials=AwsCredentials("AKIDEXAMPLE", "secret"), model="anthropic.claude"
    )
    legacy, durable = await _requests()
    for lr, dr in zip(legacy, durable, strict=True):
        ours = bedrock._body([], None, dr.system, list(dr.system_blocks), None)["system"]  # pyright: ignore[reportPrivateUsage]
        theirs = bedrock._body([], None, lr.system, list(lr.system_blocks), None)["system"]  # pyright: ignore[reportPrivateUsage]
        assert ours == theirs and len(ours) == 2
