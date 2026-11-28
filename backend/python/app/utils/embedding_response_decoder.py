"""Decode the body of an OpenAI-protocol ``/embeddings`` response.

The OpenAI SDK builds every float of a response through a pure-Python model constructor, and, when a server answers
with float lists although base64 was requested, spends hundreds of milliseconds on one request holding the event loop.
This module is the one place that turns the raw body into vectors: ``orjson`` parses the JSON, ``numpy`` decodes base64
items and checks the values. It is pure (bytes in, vectors out), so a caller can run it on a thread.

Limit: ``orjson.loads`` holds the GIL for the whole parse, so running it on a thread keeps the loop free of the
decode's own bookkeeping but not of the parse: the loop cannot run Python while the parse runs. That stall grows with
the body, about 3 ms per MB of float-list JSON (about 7 ms for the 50-chunk, 2 MB batch the indexing service sends;
372 ms for a 124 MB body of 2,048 x 3,072 floats). It is far below the SDK's per-float parse it replaces and is
bounded by the batch size, so keep embedding batches bounded rather than raising them. No process pool is used.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from typing import Any, Final, cast

import numpy as np
import orjson

# Bodies at or above this size are decoded on a thread by the async adapter; below it the hop costs more than it saves.
OFF_LOOP_BODY_BYTES: Final = 64 * 1024

_FLOAT32_BYTES: Final = 4


class EmbeddingResponseError(ValueError):
    """The body is not a well-formed embeddings response. Nothing partial is ever returned."""


@dataclass(frozen=True, slots=True)
class DecodedEmbeddings:
    """The vectors of one response, in request order, with the fields the SDK's model dump carries."""

    embeddings: list[list[float]]
    model: str | None
    usage: dict[str, Any] | None

    def as_response(self) -> dict[str, Any]:
        """The dict form LangChain's OpenAI embeddings read (``response["data"][i]["embedding"]``)."""
        response: dict[str, Any] = {
            "object": "list",
            "data": [
                {"object": "embedding", "index": index, "embedding": vector}
                for index, vector in enumerate(self.embeddings)
            ],
            "model": self.model,
        }
        if self.usage is not None:
            response["usage"] = self.usage
        return response


def decode_embedding_response(body: bytes, *, expected_count: int | None = None) -> DecodedEmbeddings:
    """Vectors and usage of a response body; base64 strings and float lists may both appear.

    Raises ``EmbeddingResponseError`` for truncated or non-JSON bodies, a missing or empty ``data``, a count other than
    ``expected_count``, ragged dimensions, non-numeric or non-finite values, and base64 that is not whole float32s.
    """
    try:
        parsed: object = orjson.loads(body)
    except orjson.JSONDecodeError as exc:  # NaN and infinity literals are refused by the parser too
        raise EmbeddingResponseError(f"embeddings response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise EmbeddingResponseError("embeddings response is not a JSON object")
    payload = cast("dict[str, Any]", parsed)
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        raise EmbeddingResponseError("embeddings response has no data")
    items = cast("list[Any]", data)
    if expected_count is not None and len(items) != expected_count:
        raise EmbeddingResponseError(f"embeddings response has {len(items)} items for {expected_count} inputs")

    vectors: list[list[float]] = []
    for position, item in enumerate(items):
        raw = cast("dict[str, Any]", item).get("embedding") if isinstance(item, dict) else None
        vectors.append(_vector(raw, position))
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or 0 in dimensions:
        raise EmbeddingResponseError(f"embeddings response has inconsistent or empty dimensions {sorted(dimensions)}")

    usage = payload.get("usage")
    model = payload.get("model")
    return DecodedEmbeddings(
        embeddings=vectors,
        model=model if isinstance(model, str) else None,
        usage=cast("dict[str, Any]", usage) if isinstance(usage, dict) else None,
    )


def _vector(raw: object, position: int) -> list[float]:
    if isinstance(raw, str):
        return _from_base64(raw, position)
    if isinstance(raw, list):
        return _from_floats(cast("list[Any]", raw), position)
    raise EmbeddingResponseError(f"embedding {position} is neither a base64 string nor a list")


def _from_base64(text: str, position: int) -> list[float]:
    try:
        packed = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise EmbeddingResponseError(f"embedding {position} is not valid base64") from exc
    if not packed or len(packed) % _FLOAT32_BYTES:
        raise EmbeddingResponseError(f"embedding {position} is not a whole number of float32 values")
    values = cast("np.ndarray[Any, np.dtype[np.float32]]", np.frombuffer(packed, dtype="<f4"))
    if not bool(np.isfinite(values).all()):
        raise EmbeddingResponseError(f"embedding {position} has non-finite values")
    # The SDK's own decode (``np.frombuffer(..., "float32").tolist()``): float32 values widened to Python floats.
    return cast("list[float]", values.tolist())


def _from_floats(values: list[Any], position: int) -> list[float]:
    # orjson refuses NaN, infinity and overflowing numbers, so every number here is finite; what is left to check is
    # that each element is a JSON number (a string, null, bool or nested list is none).
    kinds = set(map(type, values))
    if kinds == {float}:
        return values  # already the parsed floats, element-exact
    if kinds <= {float, int}:
        return [float(x) for x in values]
    raise EmbeddingResponseError(f"embedding {position} has non-numeric values")
