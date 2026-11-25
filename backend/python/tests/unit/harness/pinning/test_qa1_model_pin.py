"""QA-1 PIN-16: a ``modelKey`` repointed while a run is suspended does not change the model the run calls.

The run's pin names its model (``ResolvedRunConfig.llm``, 02-lld §5.3): provider, model and the key that served
it. A resumed attempt builds that model from a configured entry that still serves it: the pinned key's, or
another's when the key now points elsewhere. An entry of another provider never lends its endpoint or key to
the pinned one, so a model the org no longer configures ends the run instead (PIN-16b). A run admitted without
a pin reads the configuration behind its key again, as before. The provider client factory is the faked
outermost I/O: it hands out one scripted chat model per provider and model, and records which ones a run built.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from .support import CREATE, DEPLOYMENT_ASKS_FOR_WRITES, LoopRig, child_pin, llm

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

PINNED = ("openai", "gpt-x")
REPOINTED = ("anthropic", "claude-z")
SAME_PROVIDER = ("openai", "gpt-y")


class ScriptedChatModel:
    """A provider's chat model as LangChain exposes it: each call answers the next scripted message, streamed whole."""

    def __init__(self, *replies: AIMessage) -> None:
        self._replies = list(replies)
        self.calls = 0

    def bind_tools(self, tools: list[object]) -> ScriptedChatModel:
        return self

    def with_structured_output(self, schema: object, include_raw: bool = False) -> ScriptedChatModel:
        return self

    async def ainvoke(self, messages: list[object], config: object = None) -> AIMessage:
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1
        return reply

    async def astream(self, messages: list[object], config: object = None) -> AsyncIterator[AIMessageChunk]:
        message = await self.ainvoke(messages, config)
        yield AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": c["name"], "args": json.dumps(c["args"]), "id": c["id"], "index": i}
                for i, c in enumerate(message.tool_calls)
            ],
        )


class TwoModels:
    """The deployment's providers: each (provider, model) is one chat model that asks for a write, then answers."""

    def __init__(self) -> None:
        self.built: list[tuple[str, str]] = []
        self.models: dict[tuple[str, str], ScriptedChatModel] = {}

    async def __call__(self, provider: str, config: dict[str, Any], model_name: str, *_: object, **__: object) -> ScriptedChatModel:
        key = (provider, model_name)
        self.built.append(key)
        return self.models.setdefault(
            key,
            ScriptedChatModel(
                AIMessage(content="", tool_calls=[{"name": CREATE, "args": {"title": "x"}, "id": "c1"}]),
                AIMessage(content=f"answered by {model_name}"),
            ),
        )


@pytest.fixture
def providers(monkeypatch: pytest.MonkeyPatch) -> Iterator[TwoModels]:
    models = TwoModels()
    monkeypatch.setattr("app.utils.llm.get_generator_model_async", models)
    monkeypatch.setattr("app.utils.aimodels.get_generator_model_async", models)
    monkeypatch.setenv("PIPESHUB_AGENT_TRANSPORT", "langchain")
    yield models


async def _suspended_on_the_pinned_model(pins: bool = True) -> LoopRig:
    rig = LoopRig.configured_models(pins=pins)
    rig.control.models(llm("model-1", *PINNED))
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit()
    assert (await rig.attempt()).status == "suspended"
    return rig


@pytest.mark.parametrize(
    "now_configured",
    [
        pytest.param([llm("model-1", *REPOINTED), llm("model-2", *PINNED)], id="another_provider_under_the_key"),
        pytest.param([llm("model-1", *SAME_PROVIDER), llm("model-2", *PINNED)], id="another_model_under_the_key"),
    ],
)
async def test_pin16_a_repointed_model_key_does_not_change_a_suspended_runs_model(
    providers: TwoModels, now_configured: list[dict[str, Any]]
) -> None:
    rig = await _suspended_on_the_pinned_model()

    rig.control.models(*now_configured)
    rig.approve_open()
    _ = await rig.attempt()

    assert set(providers.built) == {PINNED}
    assert providers.models[PINNED].calls == 2, "the pinned model answers the resumed turn"


@pytest.mark.parametrize("repointed", [REPOINTED, SAME_PROVIDER], ids=["another_provider", "another_model"])
async def test_pin16b_a_model_the_org_no_longer_configures_ends_the_run_instead_of_moving_it(
    providers: TwoModels, repointed: tuple[str, str]
) -> None:
    rig = await _suspended_on_the_pinned_model()

    rig.control.models(llm("model-1", *repointed))
    rig.approve_open()
    resumed = await rig.attempt()

    assert set(providers.built) == {PINNED}, "neither the new model nor the pinned one on the new entry's key"
    assert resumed.status == "failed"
    assert [e.data.get("errorType") for e in rig.events() if e.type == "run.failed"] == ["model_no_longer_configured"]


async def test_pin16_a_run_admitted_without_a_pin_follows_its_model_key(providers: TwoModels) -> None:
    rig = await _suspended_on_the_pinned_model(pins=False)

    rig.control.models(llm("model-1", *REPOINTED))
    rig.approve_open()
    _ = await rig.attempt()

    assert set(providers.built) == {PINNED, REPOINTED}


async def test_pin16b_a_child_spawned_after_its_roots_model_was_removed_does_not_run_on_another(
    providers: TwoModels,
) -> None:
    rig = LoopRig.configured_models()
    rig.control.models(llm("model-1", *PINNED))
    root = rig.admit()
    assert root is not None

    rig.control.models(llm("model-1", *REPOINTED))
    _ = rig.admit("run-child", session_id="ses-child", pin=child_pin(root))
    child = await rig.attempt("run-child")

    assert providers.built == []
    assert child.status == "failed"
    failed = [e.data.get("errorType") for e in rig.events("run-child", "ses-child") if e.type == "run.failed"]
    assert failed == ["model_no_longer_configured"]
