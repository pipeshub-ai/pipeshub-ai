"""A provider's content filter stopping a response it had already accepted.

A filter that rejects the request outright arrives as an HTTP 400 and is
classified from the error text. One that stops the response instead ends the
stream normally -- `finish_reason="content_filter"` on Chat Completions,
`incomplete_details.reason="content_filter"` on the Responses API, Anthropic's
`stop_reason="refusal"` -- carrying no output and no usage. Treated as an
ordinary end of turn, that empty reply was nudged, retried and finally shown as
a canned refusal. Raising instead routes it to the same `content_filter` error
the 400 gets.
"""

from __future__ import annotations

from app.agent_loop_lib.core.exceptions import TransportError

__all__ = ["CONTENT_FILTER_STOP_REASONS", "content_filter_error"]

CONTENT_FILTER_STOP_REASONS = frozenset({"content_filter", "refusal"})


def content_filter_error(context: str, detail: str) -> TransportError:
    """Non-retryable: the same request is filtered the same way again."""
    return TransportError(
        f"Provider content_filter stopped the response ({context}): {detail}",
        retryable=False,
    )
