"""The image policy a request runs under is resolved at construction.

`model_post_init` seeds `tool_state["image_admission"]` from `llm_provider`,
and `factory.create` independently resolves the wire cap from the same field.
They only agree if the provider is known before the context is built — set it
afterwards and every request silently admits at the unknown-provider default
(2) while the wire allows the model's real cap (8 for OpenAI, 12 for Anthropic).
"""

from __future__ import annotations

import pytest

from app.agents.agent_loop.context import AgentContext
from app.utils.image_policy import resolve_image_policy


def _state(**overrides) -> dict:
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "u@example.com",
        "is_multimodal_llm": True,
    }
    state.update(overrides)
    return state


def _admitted_cap(context: AgentContext) -> int:
    return context.tool_state["image_admission"].policy.max_images_per_request


def _wire_cap(context: AgentContext) -> int:
    """What `factory.create` binds onto the transport."""
    return resolve_image_policy(
        provider=context.llm_provider, is_multimodal=context.is_multimodal_llm,
    ).max_images_per_request


class TestProviderReachesTheAdmission:
    @pytest.mark.parametrize(
        ("provider", "expected"),
        [("openai", 8), ("anthropic", 12), ("bedrock", 10), ("ollama", 1)],
    )
    def test_the_admission_uses_the_configured_provider(
        self, provider: str, expected: int,
    ) -> None:
        context = AgentContext.from_chat_state(_state(), llm_provider=provider)

        assert _admitted_cap(context) == expected

    @pytest.mark.parametrize("provider", ["openai", "anthropic", "bedrock", "ollama"])
    def test_the_source_and_the_wire_agree(self, provider: str) -> None:
        """Two independent resolutions of one field. When they disagree the
        stricter wins silently: images are admitted, charged to the
        conversation budget, then dropped before the request."""
        context = AgentContext.from_chat_state(_state(), llm_provider=provider)

        assert _admitted_cap(context) == _wire_cap(context)

    def test_an_unknown_provider_still_falls_back(self) -> None:
        """A caller with no provider to give keeps the conservative default —
        the fallback is intact, it just stopped being what everyone got."""
        context = AgentContext.from_chat_state(_state())

        assert _admitted_cap(context) == _wire_cap(context)
        assert _admitted_cap(context) == 2

    def test_a_non_multimodal_model_admits_nothing(self) -> None:
        context = AgentContext.from_chat_state(
            _state(is_multimodal_llm=False), llm_provider="openai",
        )

        assert _admitted_cap(context) == 0

    def test_the_budget_the_admission_debits_is_the_one_in_tool_state(self) -> None:
        """The conversation-wide ceiling only applies if every source debits
        the same instance."""
        context = AgentContext.from_chat_state(_state(), llm_provider="openai")

        assert context.tool_state["image_admission"].budget is context.tool_state["image_budget"]

    def test_context_length_arrives_with_the_provider(self) -> None:
        """Same two-phase hazard: `resolve_render_budget` sizes a fetch from
        this, and the chat-mode entry point never set it at all."""
        context = AgentContext.from_chat_state(_state(), llm_provider="openai", context_length=1_000_000)

        assert context.context_length == 1_000_000
