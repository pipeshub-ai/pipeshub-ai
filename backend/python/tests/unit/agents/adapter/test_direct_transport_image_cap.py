"""The image cap has to hold on every transport, not just the LangChain one.

`PIPESHUB_AGENT_TRANSPORT=direct` routes requests through the provider's own
SDK (`build_direct_transport`). Those transports live in `agent_loop_lib` and
know nothing about PipesHub's per-provider image policy, so without a wrapper
the deployment that opted into the faster path also opted out of the cap —
and the LangChain arm inside `_direct_or_langchain` was built without one too.
"""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.core.messages import (
    ImagePart,
    ImageSource,
    Message,
    TextPart,
    ToolMessage,
    UserMessage,
)
from app.agents.agent_loop.image_guard import count_images, with_image_cap


def _messages(images: int = 5) -> list[Message]:
    return [
        UserMessage(content="what is in these?"),
        *[
            ToolMessage(
                content=[
                    TextPart(text=f"[ref{i}] result {i}"),
                    ImagePart(source=ImageSource(type="url", data=f"https://x/{i}.png")),
                ],
                tool_call_id=f"tc{i}",
            )
            for i in range(images)
        ],
    ]


class _RecordingTransport:
    """Stands in for a direct SDK transport: records what reached the wire."""

    provider = "openai"
    model_name = "gpt-5.6-luna"

    def __init__(self) -> None:
        self.seen: list[Message] | None = None
        self.args: tuple = ()

    async def complete(self, messages, tools=None, system=None, model=None,
                       thinking_budget=None, effort=None, system_blocks=None) -> str:
        self.seen = messages
        self.args = (tools, system, model, thinking_budget, effort, system_blocks)
        return "response"

    async def complete_structured(self, messages, output_schema, system=None, model=None) -> str:
        self.seen = messages
        self.args = (output_schema, system, model)
        return "structured"

    def stream(self, messages, tools=None, system=None, model=None,
               thinking_budget=None, effort=None, system_blocks=None) -> iter:
        self.seen = messages
        self.args = (tools, system, model, thinking_budget, effort, system_blocks)
        return iter(())


class TestEveryCallPathIsCapped:
    @pytest.mark.parametrize("cap", [1, 2, 5, 10])
    def test_complete(self, cap: int) -> None:
        inner = _RecordingTransport()
        asyncio.run(with_image_cap(inner, cap).complete(_messages()))

        assert count_images(inner.seen) == min(cap, 5)

    def test_complete_structured(self) -> None:
        inner = _RecordingTransport()
        asyncio.run(with_image_cap(inner, 2).complete_structured(_messages(), {"type": "object"}))

        assert count_images(inner.seen) == 2

    def test_stream(self) -> None:
        """`stream` returns its iterator rather than awaiting one — wrapping it
        in a coroutine would change the contract."""
        inner = _RecordingTransport()
        with_image_cap(inner, 2).stream(_messages())

        assert count_images(inner.seen) == 2


class TestItOnlyCaps:
    def test_no_cap_configured_is_a_passthrough(self) -> None:
        """Same "unset behaves exactly as before" rule the rest of the image
        path follows."""
        inner = _RecordingTransport()

        assert with_image_cap(inner, None) is inner

    def test_every_other_argument_reaches_the_provider(self) -> None:
        """A knob silently dropped by a decorator is worse than no decorator."""
        inner = _RecordingTransport()
        asyncio.run(with_image_cap(inner, 2).complete(
            _messages(), ["tool"], "sys", "model-x", 1024, "high", ["a", "b"],
        ))

        assert inner.args == (["tool"], "sys", "model-x", 1024, "high", ["a", "b"])

    def test_identity_is_the_wrapped_transports(self) -> None:
        """`ModelSpec` resolution and tracing both read these."""
        capped = with_image_cap(_RecordingTransport(), 2)

        assert capped.provider == "openai"
        assert capped.model_name == "gpt-5.6-luna"

    def test_text_survives_a_dropped_image(self) -> None:
        """Drop pixels, never content — the model still knows a figure existed."""
        inner = _RecordingTransport()
        asyncio.run(with_image_cap(inner, 1).complete(_messages()))

        assert "[ref0] result 0" in str(inner.seen)


class TestFactoryWiring:
    """Both arms of `_direct_or_langchain` build a capped transport."""

    def test_the_langchain_fallback_arm_receives_the_cap(self) -> None:
        """The direct registration's fallback was constructed without
        `max_images_per_request`, so a provider with no direct transport lost
        the cap even on the `direct` arm."""
        import inspect

        from app.agents.agent_loop import factory

        source = inspect.getsource(factory.PipesHubAgentFactory.create)
        arm = source[source.index("def _direct_or_langchain"):]
        arm = arm[: arm.index("transport_registry.register(")]

        assert "with_image_cap(direct, image_cap)" in arm
        assert "max_images_per_request=image_cap" in arm
