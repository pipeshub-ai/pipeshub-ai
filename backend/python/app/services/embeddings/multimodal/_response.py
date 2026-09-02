"""Shared parsing for the OpenAI-style ``{"data": [{"index", "embedding"}]}``
embedding envelope, which Jina and every OpenAI-compatible server return.

Lives here rather than in each provider because getting it wrong is silent:
zipping ``data`` to the request list by position attaches an embedding to the
wrong image whenever a server reorders or omits entries.
"""

from collections.abc import Sequence

import httpx

from app.services.embeddings.multimodal.interface import ImageEmbeddingResult

_MISSING_EMBEDDING_ERROR = "no embedding returned for this image"
_MAX_ERROR_BODY_CHARS = 300


def describe_request_error(exc: Exception) -> str:
    """``str(HTTPStatusError)`` names the status but never the body, so an
    operator sees "422 Unprocessable Entity" with no hint that (say) the
    configured model has no image schema. Append what the server said.
    """
    if not isinstance(exc, httpx.HTTPStatusError):
        return str(exc)
    try:
        body = exc.response.text
    except Exception:
        return str(exc)
    return f"{exc}: {body[:_MAX_ERROR_BODY_CHARS]}" if body else str(exc)


def map_embedding_response(
    data: object,
    request_indices: Sequence[int],
) -> list[ImageEmbeddingResult]:
    """Map an embedding response onto the caller's original indices.

    ``request_indices[p]`` is the caller-side index of the image sent at
    position ``p``. Entries are matched on the response's own ``index``
    field, falling back to arrival order for servers that omit it. Every
    requested position gets exactly one result — a position the response
    never covered becomes an error rather than a missing entry, since
    ``IMultimodalEmbeddingProvider`` forbids silently dropping inputs.
    """
    items = data if isinstance(data, list) else []
    by_position: dict[int, object] = {}
    for arrival, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        position = item.get("index")
        if not isinstance(position, int) or isinstance(position, bool):
            position = arrival
        by_position.setdefault(position, item)

    results: list[ImageEmbeddingResult] = []
    for position, original_index in enumerate(request_indices):
        embedding = _embedding_of(by_position.get(position))
        if embedding is None:
            results.append(
                ImageEmbeddingResult(index=original_index, error=_MISSING_EMBEDDING_ERROR)
            )
        else:
            results.append(ImageEmbeddingResult(index=original_index, embedding=embedding))
    return results


def _embedding_of(item: object) -> list[float] | None:
    if not isinstance(item, dict):
        return None
    embedding = item.get("embedding")
    if not isinstance(embedding, list) or not embedding:
        return None
    return list(embedding)
