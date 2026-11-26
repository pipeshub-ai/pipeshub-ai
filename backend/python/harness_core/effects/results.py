"""How a tool outcome is stored as a blob: the bytes behind ``tool.outcome.resultRef``."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from harness_core.contracts import canonical_json

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.journal.ports import ContentStorePort


def encode_result(
    content: JsonValue,
    *,
    is_error: bool,
    meta: dict[str, JsonValue] | None = None,
    parts: bool = False,
    error_code: str | None = None,
) -> bytes:
    """``meta``: what the context shapers read off the tool message (its
    ``artifact_meta``: a large result the loop stored and may compact).
    ``parts``: ``content`` is the message's parts (text and images).
    ``error_code``: a failure's ``ToolErrorCode``, kept beside the detail so the presenter shows the code (ERR-UX)."""
    doc: dict[str, JsonValue] = {"content": content, "isError": is_error}
    if meta:
        doc["meta"] = meta
    if parts:
        doc["parts"] = True
    if is_error and error_code is not None:
        doc["errorCode"] = error_code
    return canonical_json(doc)


def decode_result(data: bytes) -> tuple[JsonValue, bool]:
    doc: dict[str, Any] = json.loads(data)
    return cast("JsonValue", doc["content"]), bool(doc["isError"])


async def put_ref(content: ContentStorePort, data: bytes) -> dict[str, JsonValue]:
    """Store ``data`` and return its ref as journal event data."""
    ref = await content.put(data)
    return cast("dict[str, JsonValue]", ref.model_dump(mode="json", by_alias=True))


__all__ = ["decode_result", "encode_result", "put_ref"]
