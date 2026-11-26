"""Provider state on assistant messages (P4.3): captured by a transport, journaled with the
response (``rawRef``), sent back only on the wire that produced it.

A provider that reasons before a tool call wants that reasoning back, verbatim, on the
request that answers the call: Anthropic's thinking blocks with their signatures,
Responses ``reasoning`` items with their encrypted content (requests are sent with
``store: false``, so the server keeps nothing), Gemini's thought signatures per call. The
blocks are opaque outside the transport of their ``wire``; another provider never sees them.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from harness_core.kernel.core.messages import AssistantMessage, ProviderState

if TYPE_CHECKING:
    from collections.abc import Sequence

ANTHROPIC_MESSAGES = "anthropic_messages"
OPENAI_RESPONSES = "openai_responses"
BEDROCK_CONVERSE = "bedrock_converse"
GEMINI_GENERATE_CONTENT = "gemini_generate_content"


def state_blocks(message: AssistantMessage, wire: str) -> list[dict[str, Any]]:
    """The message's blocks for ``wire`` (copies), or none when another wire produced them."""
    state = message.provider_state
    if state is None or state.wire != wire:
        return []
    return copy.deepcopy(state.blocks)


def with_state(message: AssistantMessage, wire: str, blocks: Sequence[dict[str, Any]]) -> AssistantMessage:
    """``message`` carrying ``blocks`` for ``wire``; unchanged when there are none."""
    if not blocks:
        return message
    return message.model_copy(update={"provider_state": ProviderState(wire=wire, blocks=list(blocks))})


__all__ = ["ANTHROPIC_MESSAGES", "BEDROCK_CONVERSE", "GEMINI_GENERATE_CONTENT", "OPENAI_RESPONSES", "state_blocks", "with_state"]
