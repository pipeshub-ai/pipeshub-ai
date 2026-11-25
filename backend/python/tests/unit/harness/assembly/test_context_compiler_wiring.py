"""P4 FL-1, FL-3: ``HARNESS_CONTEXT_COMPILER`` gives the durable kernel the compiler, or nothing."""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.assembly.context_compiler import build_context_compiler
from app.harness.assembly.model import ResolvedModel
from app.utils.env_flags import InvalidEnvFlag
from harness_core.context import ModelCompaction, TruncationCompaction
from harness_core.kernel.budget.pricing import get_context_window
from harness_core.testing.model_gateway import RecordedTransport


class _Host:
    async def bands(self, call: Any, turn: int, state: Any) -> Any:  # noqa: ANN401
        raise NotImplementedError


def _model(*, context_length: int | None = 64_000, transport: bool = True) -> ResolvedModel:
    return ResolvedModel(
        raw_llm=None,
        provider="openai",
        is_multimodal=False,
        context_length=context_length,
        is_reasoning=False,
        port=None,  # type: ignore[arg-type]
        transport=RecordedTransport() if transport else None,
    )


def test_fl1_off_by_default_the_kernel_gets_no_compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HARNESS_CONTEXT_COMPILER", raising=False)
    assert build_context_compiler(_model(), "gpt-5.4", _Host()) is None
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "false")
    assert build_context_compiler(_model(), "gpt-5.4", _Host()) is None


def test_fl1_a_malformed_value_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "maybe")
    with pytest.raises(InvalidEnvFlag):
        _ = build_context_compiler(_model(), "gpt-5.4", _Host())


def test_fl3_on_the_window_is_the_model_s_and_compaction_runs_on_its_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    host = _Host()
    model = _model()
    compiler = build_context_compiler(model, "gpt-5.4", host)
    assert compiler is not None
    assert compiler.window_tokens == 64_000
    assert compiler.host is host
    assert isinstance(compiler.strategy, ModelCompaction)
    assert compiler.strategy._model.transport is model.transport  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]


def test_fl3_without_a_declared_window_or_a_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "1")
    compiler = build_context_compiler(_model(context_length=None, transport=False), "gpt-5.4", _Host())
    assert compiler is not None
    assert compiler.window_tokens == get_context_window("gpt-5.4")
    assert isinstance(compiler.strategy, TruncationCompaction)


async def _bands_turn_one(images: list[dict[str, Any]], *, multipart: bool) -> tuple[Any, Any]:
    """The PipesHub host's bands for turn 1 of a fresh quick run, with the compiler on."""
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
    from harness_core.kernel.fold import RunState
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    from . import world as w

    query = "what is in the picture?"
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(w.pinned_spec(tool_ids=[], kb=[]), query=query, chat_mode="quick")
    with w.deployment():
        assembler = PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(port))
        runtime = await assembler.build(claim)
        slots: dict[str, Any] = {}
        for step in runtime.pre_run:
            slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(query)
        shaper: Any = runtime.shaper
        context = shaper._env.context  # pyright: ignore[reportPrivateUsage]
        context.attachment_image_blocks = images
        context.tool_state["supports_multipart_tool_result"] = multipart
        bands = await shaper.bands(ModelCall([], None, None, slots), 1, RunState(run_id="run-1", slots=slots))
    return bands, slots


async def test_p45_4_attachment_images_and_the_goal_reach_the_compiler_as_the_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from harness_core.kernel.core.messages import ImagePart

    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    bands, slots = await _bands_turn_one([{"image_url": {"url": "data:image/png;base64,AAAA"}}], multipart=True)
    assert bands.input_text == slots["pre_run.goal"]["description"]
    assert len(bands.input_parts) == 1 and isinstance(bands.input_parts[0], ImagePart)
    assert bands.tool_images_after_results is False


async def test_p45_5_a_provider_without_multipart_results_gets_images_after_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "true")
    bands, _ = await _bands_turn_one([], multipart=False)
    assert bands.input_parts == ()
    assert bands.tool_images_after_results is True


def test_lc2fix_2_the_compact_rung_is_on_by_default_and_the_operator_can_turn_it_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.harness.assembly.context_compiler import auto_compact_enabled

    monkeypatch.delenv("HARNESS_AUTO_COMPACT", raising=False)
    monkeypatch.delenv("HARNESS_CONTEXT_COMPILER", raising=False)
    assert auto_compact_enabled() is True
    monkeypatch.setenv("HARNESS_AUTO_COMPACT", "false")
    assert auto_compact_enabled() is False


async def test_lc2fix_2_an_assembled_run_compacts_on_its_own_without_the_compiler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    from . import world as w

    monkeypatch.delenv("HARNESS_CONTEXT_COMPILER", raising=False)
    for value, on in ((None, True), ("false", False)):
        if value is None:
            monkeypatch.delenv("HARNESS_AUTO_COMPACT", raising=False)
        else:
            monkeypatch.setenv("HARNESS_AUTO_COMPACT", value)
        port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
        claim = w.claim(w.pinned_spec(tool_ids=[], kb=[]), query="hi", chat_mode="quick")
        with w.deployment():
            assembler = PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(port))
            runtime = await assembler.build(claim)
        assert runtime.context is None
        assert runtime.auto_compact is on
        assert (runtime.compactor is not None) is on
