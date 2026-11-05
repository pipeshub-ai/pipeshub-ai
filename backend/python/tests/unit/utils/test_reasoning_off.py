"""Reasoning effort "none" for calls that never bind tools.

Every other call floors "none" to "low", because disabling reasoning made
models garble tool-call names. A tool-free model sends "none" as asked; a
provider that refuses it gets one retry at "low", and the model is remembered
so later builds floor up front.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_openai import AzureChatOpenAI, ChatOpenAI

from app.agent_loop_lib.transport.provider_conflicts import is_reasoning_off_rejection
from app.utils import llm_api_mode_store
from app.utils.aimodels import get_generator_model
from app.utils.llm import (
    ToolFreeChatModel,
    _tool_free_config,
    build_tool_free_llm,
    get_llm_for_role,
    get_tool_free_llm,
    get_tool_free_llm_for_role,
)
from app.utils.llm_api_mode_store import (
    LLMApiMode,
    floor_disabled_reasoning,
    reasoning_off_rejected,
    remember_reasoning_off_rejected,
)


@pytest.fixture(autouse=True)
def _no_learned_facts() -> Any:  # noqa: ANN401
    llm_api_mode_store._reasoning_off_rejected.clear()
    with patch("app.utils.aimodels.get_llm_api_mode_store", return_value=None), \
         patch("app.utils.llm.get_llm_api_mode_store", return_value=None):
        yield
    llm_api_mode_store._reasoning_off_rejected.clear()


def _azure(model: str = "gpt-5.2", key: str = "key-north", *, default: bool = True) -> dict[str, Any]:
    return {
        "provider": "azureOpenAI", "modelKey": key, "isReasoning": True, "isDefault": default,
        "configuration": {
            "model": model, "deploymentName": model, "endpoint": "https://unit-test.openai.azure.com/",
            "apiKey": "placeholder",
        },
    }


def _effort(llm: Any) -> str | None:  # noqa: ANN401
    return llm.reasoning_effort or (llm.reasoning or {}).get("effort")


class TestToolFreeConfig:
    def test_none_is_let_through(self) -> None:
        assert _tool_free_config(_azure(), None, "none")["allowDisabledReasoning"] is True

    @pytest.mark.parametrize("effort", [None, "low", "medium", "high", "max"])
    def test_other_efforts_leave_the_config_alone(self, effort: str | None) -> None:
        config = _azure()
        assert _tool_free_config(config, None, effort) is config

    def test_a_refusal_remembered_in_this_process_floors_it(self) -> None:
        remember_reasoning_off_rejected("key-north", "gpt-5.2")
        config = _azure()
        assert _tool_free_config(config, None, "none") is config
        assert _tool_free_config(_azure(key="key-south"), None, "none")["allowDisabledReasoning"] is True

    def test_a_refusal_learned_by_the_shared_store_floors_it(self) -> None:
        store = MagicMock()
        store.get.return_value = LLMApiMode.REASONING_MANDATORY.value
        config = _azure()
        with patch("app.utils.llm.get_llm_api_mode_store", return_value=store):
            assert _tool_free_config(config, "gpt-5.2", "none") is config
        store.get.assert_called_once_with("key-north", "gpt-5.2")


class TestBuild:
    async def test_none_reaches_the_client(self) -> None:
        model = await build_tool_free_llm(_azure(), reasoning_effort="none")
        assert isinstance(model.llm, AzureChatOpenAI)
        assert model.llm.reasoning_effort == "none"
        assert not hasattr(model, "bind_tools")

    def test_the_factory_still_floors_none_for_everyone_else(self) -> None:
        assert _effort(get_generator_model("azureOpenAI", _azure(), None, "none")) == "low"

    async def test_a_named_model_of_an_entry_is_built(self) -> None:
        config = _azure("gpt-5.2,gpt-5.4")
        with patch("app.utils.llm.get_generator_model", return_value=MagicMock()) as factory:
            model = await build_tool_free_llm(config, model_name="gpt-5.4", reasoning_effort="none")
        provider, built_config, model_name, effort = factory.call_args.args
        assert (provider, model_name, effort) == ("azureOpenAI", "gpt-5.4", "none")
        assert built_config["allowDisabledReasoning"] is True
        assert model.config is config
        assert model._model_name == "gpt-5.4"

    async def test_a_remembered_refusal_floors_the_next_build(self) -> None:
        remember_reasoning_off_rejected("key-north", "gpt-5.2")
        assert _effort((await build_tool_free_llm(_azure(), reasoning_effort="none")).llm) == "low"


class _Rejecting:
    def __init__(self, error: Exception | None, reasoning_effort: str = "none") -> None:
        self.error = error
        self.reasoning_effort = reasoning_effort
        self.reasoning = None
        self.calls = 0

    def model_copy(self, update: dict[str, Any]) -> _Rejecting:
        return _Rejecting(None, update.get("reasoning_effort", self.reasoning_effort))

    def _answer(self) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return f"answered at {self.reasoning_effort}"

    async def ainvoke(self, messages: Any, config: Any = None, **_: Any) -> str:  # noqa: ANN401
        return self._answer()

    def invoke(self, messages: Any, config: Any = None, **_: Any) -> str:  # noqa: ANN401
        return self._answer()


class _Http400(Exception):
    status_code = 400


_REFUSAL = _Http400("Error code: 400 - Unsupported value: 'reasoning_effort' does not support 'none' with this model.")


class TestRuntimeRefusal:
    async def test_retries_once_at_low_and_remembers(self) -> None:
        primary = _Rejecting(_REFUSAL)
        model = ToolFreeChatModel(primary, _azure())
        assert await model.ainvoke(["hello"]) == "answered at low"
        assert primary.calls == 1
        assert reasoning_off_rejected("key-north", "gpt-5.2")
        assert await model.ainvoke(["hello again"]) == "answered at low"
        assert primary.calls == 1

    async def test_the_refusal_is_recorded_in_the_shared_store(self) -> None:
        store = MagicMock(record=AsyncMock())
        with patch("app.utils.llm.get_llm_api_mode_store", return_value=store):
            await ToolFreeChatModel(_Rejecting(_REFUSAL), _azure()).ainvoke(["hello"])
        store.record.assert_awaited_once_with("key-north", "gpt-5.2", LLMApiMode.REASONING_MANDATORY.value)

    async def test_calls_in_flight_when_the_first_refusal_is_handled_retry_on_the_floored_model(self) -> None:
        """Two calls refused together: the second retries on the model the first floored, and is recorded once."""
        both_sent = asyncio.Event()
        sent = 0

        class _RefusedTogether(_Rejecting):
            async def ainvoke(self, messages: Any, config: Any = None, **_: Any) -> str:  # noqa: ANN401
                nonlocal sent
                sent += 1
                if sent == 2:
                    both_sent.set()
                await both_sent.wait()
                return self._answer()

        store = MagicMock(record=AsyncMock())
        model = ToolFreeChatModel(_RefusedTogether(_REFUSAL), _azure())
        with patch("app.utils.llm.get_llm_api_mode_store", return_value=store):
            first, second = await asyncio.gather(model.ainvoke(["one"]), model.ainvoke(["two"]))
        assert (first, second) == ("answered at low", "answered at low")
        store.record.assert_awaited_once()

    def test_a_refusal_from_a_model_already_replaced_is_not_floored_again(self) -> None:
        primary = _Rejecting(_REFUSAL)
        model = ToolFreeChatModel(primary, _azure())
        refused_model = model.llm
        model.llm = primary.model_copy({"reasoning_effort": "low"})
        assert model._floored(_REFUSAL, refused_model) == (model.llm, False)

    def test_sync_invoke_falls_back_too(self) -> None:
        assert ToolFreeChatModel(_Rejecting(_REFUSAL), _azure()).invoke(["hello"]) == "answered at low"
        assert reasoning_off_rejected("key-north", "gpt-5.2")

    @pytest.mark.parametrize("error", [
        _Http400("Error code: 400 - Invalid 'messages[0].content'."),
        RuntimeError("Error code: 500 - server error"),
        TimeoutError("timed out"),
    ])
    async def test_other_errors_are_raised_and_not_remembered(self, error: Exception) -> None:
        with pytest.raises(type(error)):
            await ToolFreeChatModel(_Rejecting(error), _azure()).ainvoke(["hello"])
        assert not reasoning_off_rejected("key-north", "gpt-5.2")

    async def test_a_refusal_with_reasoning_already_on_is_raised(self) -> None:
        with pytest.raises(_Http400):
            await ToolFreeChatModel(_Rejecting(_REFUSAL, reasoning_effort="low"), _azure()).ainvoke(["hello"])
        assert not reasoning_off_rejected("key-north", "gpt-5.2")

    @pytest.mark.parametrize(("error", "expected"), [
        (_REFUSAL, True),
        (Exception("Reasoning is mandatory for this endpoint and cannot be disabled."), True),
        (Exception("Error code: 400 - Unsupported value: 'reasoning.effort' does not support 'none'"), True),
        (_Http400("Unsupported parameter: 'temperature' does not support 'none'"), False),
        (Exception("Error code: 429 - reasoning_effort none rate limited"), False),
    ])
    def test_rejection_predicate(self, error: Exception, expected: bool) -> None:
        assert is_reasoning_off_rejection(error) is expected


class TestFloorDisabledReasoning:
    @pytest.mark.parametrize(("fields", "expected"), [
        ({"reasoning_effort": "none"}, {"reasoning_effort": "low"}),
        ({"reasoning": False}, {"reasoning": "low"}),
        ({"reasoning": {"effort": "none"}}, {"reasoning": {"effort": "low"}}),
    ])
    def test_disabled_reasoning_is_raised(self, fields: dict[str, Any], expected: dict[str, Any]) -> None:
        llm = MagicMock(reasoning_effort=None, reasoning=None)
        for name, value in fields.items():
            setattr(llm, name, value)
        floor_disabled_reasoning(llm)
        llm.model_copy.assert_called_once_with(update=expected)

    def test_reasoning_already_on_is_left_alone(self) -> None:
        llm = MagicMock(reasoning_effort="medium", reasoning={"effort": "medium"})
        assert floor_disabled_reasoning(llm) is None
        llm.model_copy.assert_not_called()


def _models(*, roles: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "llm": [_azure(), _azure("gpt-5.4", "key-south", default=False)],
        "modelRoles": roles if roles is not None else {"query": {"modelType": "llm", "modelKey": "key-south"}},
    }


class TestBuilders:
    async def test_the_role_model_sends_none(self) -> None:
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=_models())):
            model = await get_tool_free_llm_for_role(MagicMock(), "query", reasoning_effort="none")
        assert isinstance(model, ToolFreeChatModel)
        assert model.llm.deployment_name == "gpt-5.4"
        assert model.llm.reasoning_effort == "none"
        assert model.config["modelKey"] == "key-south"
        assert "allowDisabledReasoning" not in model.config

    async def test_an_unassigned_role_uses_the_default_model(self) -> None:
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=_models(roles={}))):
            model = await get_tool_free_llm_for_role(MagicMock(), "query", reasoning_effort="none")
        assert model.llm.deployment_name == "gpt-5.2"
        assert model.llm.reasoning_effort == "none"

    async def test_the_default_model_sends_none(self) -> None:
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=_models())):
            model = await get_tool_free_llm(MagicMock(), reasoning_effort="none")
        assert model.llm.deployment_name == "gpt-5.2"
        assert model.llm.reasoning_effort == "none"

    async def test_existing_callers_asking_none_are_floored_as_before(self) -> None:
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=_models())):
            llm, _config = await get_llm_for_role(MagicMock(), "query", reasoning_effort="none")
        assert _effort(llm) == "low"

    async def test_existing_callers_build_with_the_same_arguments(self) -> None:
        models = _models()
        built = ChatOpenAI(model="gpt-5.4", api_key="placeholder")
        with patch("app.utils.llm.load_ai_models", AsyncMock(return_value=models)), \
             patch("app.utils.llm.get_generator_model", return_value=built) as factory:
            await get_llm_for_role(MagicMock(), "query", reasoning_effort="none")
        assert factory.call_args.args[1] is models["llm"][1]
        assert factory.call_args.args[3] == "none"
        assert factory.call_args.kwargs == {}
