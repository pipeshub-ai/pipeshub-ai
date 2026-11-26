"""P4.17L LV-3: both replay drivers put the deployment's output limit on the model call, as the product does.

The live lanes reach the model through ``RecordingTransport`` (and, keeping requests, ``CapturingTransport``) under
both loops' ``TransportModel``. A wrapper that does not pass the capability on sends no limit: live check L3 found
every live model request unbounded."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

import pytest

from app.harness.testing_adapters import ScriptedModelTransport
from harness_core.models.transports.base import LLMTransport
from harness_core.testing import ModelScript, ScriptedModel
from harness_core.testing.model_script import Reply, Turn
from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.legacy_driver import DriveSpec
from tests.harness_replay.wire import CapturingTransport
from tests.harness_replay.world import Doc, WorldFixtures

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from harness_core.kernel.core.responses import ModelResponse, StructuredResponse
    from harness_core.kernel.core.streaming import StreamEvent
    from tests.harness_replay.wire import WireRequest


class LimitTakingModel(LLMTransport):
    """A scripted model behind a transport that puts a limit on its wire, as every product transport does."""

    def __init__(self, script: ModelScript) -> None:
        super().__init__()
        self._inner = ScriptedModelTransport(ScriptedModel(script), model_name="gpt-6-luna")
        self.limits: list[int | None] = []

    @property
    @override
    def provider(self) -> str:
        return self._inner.provider

    @property
    @override
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return True

    @override
    async def complete(self, messages: Any, tools: Any = None, system: Any = None, model: Any = None, thinking_budget: Any = None, effort: Any = None, system_blocks: Any = None, max_output_tokens: int | None = None, *, tool_choice: object = None) -> ModelResponse:
        self.limits.append(max_output_tokens)
        return await self._inner.complete(messages, tools, system, model, thinking_budget, effort, system_blocks)

    @override
    async def complete_structured(self, messages: Any, output_schema: dict[str, Any], system: Any = None, model: Any = None, max_output_tokens: int | None = None) -> StructuredResponse:
        self.limits.append(max_output_tokens)
        return await self._inner.complete_structured(messages, output_schema, system, model)

    @override
    def stream(self, messages: Any, tools: Any = None, system: Any = None, model: Any = None, thinking_budget: Any = None, effort: Any = None, system_blocks: Any = None, max_output_tokens: int | None = None, *, tool_choice: object = None) -> AsyncIterator[StreamEvent]:
        self.limits.append(max_output_tokens)
        return self._inner.stream(messages, tools, system, model, thinking_budget, effort, system_blocks)


def _spec(transport: LLMTransport) -> DriveSpec:
    return DriveSpec(
        scenario="p417l-rig-limit",
        query="What is the travel policy?",
        fixtures=WorldFixtures(knowledge=[Doc(id="pol-travel", title="Travel policy", text="Economy under 6 hours.")]),
        knowledge_search=True,
        fake_toolsets=(),
        transport=transport,
    )


def _script() -> ModelScript:
    return ModelScript(id="answer", turns=[Turn(reply=Reply(text="Economy under 6 hours."))])


DRIVERS: dict[str, Callable[[DriveSpec], Awaitable[Any]]] = {
    "legacy": legacy_driver.drive,
    "durable": durable_driver.drive,
}


@pytest.mark.parametrize("driver", sorted(DRIVERS))
@pytest.mark.parametrize("captured", [False, True], ids=["plain", "dump-requests"])
async def test_lv3_the_replay_drivers_send_the_deployments_limit(
    driver: str, captured: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HARNESS_MODEL_OUTPUT_LIMIT", "5000")
    model = LimitTakingModel(_script())
    seen: list[WireRequest] = []
    transport: LLMTransport = CapturingTransport(model, seen) if captured else model

    _ = await DRIVERS[driver](_spec(transport))

    assert model.limits and set(model.limits) == {5_000}
    if captured:
        assert {r.output_limit for r in seen} == {5_000}
