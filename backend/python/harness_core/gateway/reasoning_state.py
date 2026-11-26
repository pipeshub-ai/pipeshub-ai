"""A provider's encrypted reasoning, handed to an engine and taken back (WP-62).

Under ``store: false`` the provider keeps nothing, so the encrypted reasoning it returns is the only way a reasoning
model sees its own thinking on the next request of a tool turn; Codex asks for it and replays it. The gateway holds no
state either: it seals each item's content to the run's org and the route that produced it, and opens it only on a
call of the same org and route. Content sealed for another route (an engine that switched model mid-thread) or
another org, or that the gateway never issued, does not reach the provider: another deployment cannot decrypt it, and
the provider would refuse the whole request. The seal is a tag, not a cipher; the content is the provider's own
ciphertext.

Anthropic's thinking (WP56-WIRE) travels the same way on the Messages wire: the provider signs each thinking block,
and a tool turn's assistant message must carry its thinking back verbatim. The gateway hands the engine each block
with the provider's whole block sealed in ``signature`` (``data`` for redacted thinking), the thinking text shown
only when the engine asked to see it, and opens it on the next request of the same org and route.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from typing import TYPE_CHECKING, Any, Final, cast

from harness_core.kernel.core.messages import AssistantMessage, ProviderState
from harness_core.models.provider_state import ANTHROPIC_MESSAGES, OPENAI_RESPONSES

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.core.messages import Message

_PREFIX: Final = "pxh1."


def reasoning_tag(tenant: str, route_id: str) -> str:
    return hashlib.sha256(f"{tenant}\x00{route_id}".encode()).hexdigest()[:16]


def seal(content: str, tag: str) -> str:
    return f"{_PREFIX}{tag}.{content}"


def unseal(content: str, tag: str) -> str | None:
    head = f"{_PREFIX}{tag}."
    return content[len(head):] if tag and content.startswith(head) else None


def is_sealed(content: object) -> bool:
    return isinstance(content, str) and content.startswith(_PREFIX)


def seal_anthropic_block(block: dict[str, Any], tag: str, *, shown: bool) -> dict[str, Any]:
    """The engine's copy of one of Anthropic's thinking blocks: the provider's block sealed whole, the thinking text
    beside it only when ``shown``."""
    payload = base64.urlsafe_b64encode(json.dumps(block, separators=(",", ":")).encode()).decode()
    sealed = seal(payload, tag)
    if block.get("type") == "redacted_thinking":
        return {"type": "redacted_thinking", "data": sealed}
    return {"type": "thinking", "thinking": str(block.get("thinking") or "") if shown else "", "signature": sealed}


def _open_anthropic_block(block: dict[str, Any], tag: str) -> dict[str, Any] | None:
    content = block.get("data") if block.get("type") == "redacted_thinking" else block.get("signature")
    opened = unseal(content, tag) if isinstance(content, str) else None
    if opened is None:
        return None
    try:
        original = json.loads(base64.urlsafe_b64decode(opened.encode()))
    except (binascii.Error, ValueError):
        return None
    if not isinstance(original, dict):
        return None
    original = cast("dict[str, Any]", original)
    return original if original.get("type") == block.get("type") else None


def _open_block(block: dict[str, Any], wire: str, tag: str) -> dict[str, Any] | None:
    if wire == ANTHROPIC_MESSAGES:
        return _open_anthropic_block(block, tag)
    content = block.get("encrypted_content")
    opened = unseal(content, tag) if isinstance(content, str) else None
    return None if opened is None else {**block, "encrypted_content": opened}


def open_reasoning(
    messages: Sequence[Message], tag: str, wire: str = OPENAI_RESPONSES
) -> tuple[tuple[Message, ...], int, int]:
    """``messages`` with each reasoning block of ``wire`` (Responses reasoning, Anthropic thinking) opened for
    ``tag``, and the blocks kept and dropped. A block sealed for anything else, or another wire's, is dropped; a turn
    left with none carries no state."""
    kept = dropped = 0
    out: list[Message] = []
    for message in messages:
        state = message.provider_state if isinstance(message, AssistantMessage) else None
        if state is None or state.wire not in _WIRES:
            out.append(message)
            continue
        blocks: list[dict[str, Any]] = []
        for block in state.blocks:
            opened = _open_block(block, wire, tag) if state.wire == wire else None
            if opened is None:
                dropped += 1
                continue
            blocks.append(opened)
        kept += len(blocks)
        assert isinstance(message, AssistantMessage)
        out.append(
            message.model_copy(
                update={"provider_state": ProviderState(wire=wire, blocks=blocks) if blocks else None}
            )
        )
    return tuple(out), kept, dropped


_WIRES: Final = (OPENAI_RESPONSES, ANTHROPIC_MESSAGES)


def reasoning_blocks(messages: Sequence[Message]) -> int:
    return sum(
        len(m.provider_state.blocks)
        for m in messages
        if isinstance(m, AssistantMessage) and m.provider_state is not None and m.provider_state.wire in _WIRES
    )


__all__ = [
    "is_sealed",
    "open_reasoning",
    "reasoning_blocks",
    "reasoning_tag",
    "seal",
    "seal_anthropic_block",
    "unseal",
]
