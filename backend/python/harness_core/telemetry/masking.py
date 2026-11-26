"""What a tracer exports: attributes and names through the shared token vector (QA-14).

Content capture lets an operator see prompts and tool payloads in traces; it never ships a credential a payload
happened to hold, and a refused call's arguments are the likeliest to hold one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.security.token_patterns import redact_tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.telemetry.ports import AttrValue


def masked_text(text: str) -> str:
    return redact_tokens(text)


def masked_attributes(attributes: Mapping[str, AttrValue | None]) -> dict[str, AttrValue]:
    """``attributes`` without ``None`` values, every string (and string in a tuple) masked."""
    found: dict[str, AttrValue] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if isinstance(value, str):
            found[key] = redact_tokens(value)
        elif isinstance(value, tuple):
            found[key] = tuple(redact_tokens(item) for item in value)
        else:
            found[key] = value
    return found


__all__ = ["masked_attributes", "masked_text"]
