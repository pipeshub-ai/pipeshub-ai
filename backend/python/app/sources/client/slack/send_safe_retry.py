"""The Slack client's connection retry, minus re-sending a write that may already have landed (QA24-F1).

``slack_sdk``'s default ``ConnectionErrorRetryHandler`` re-sends any request once after a connection reset or a
server that closed without answering, so a ``chat.postMessage`` whose first send reached Slack posted twice, and
the tool saw success: the effect runner never learned the first send was ambiguous. Here a write that may have been
processed is reported to the running tool call (an unknown effect: reconciled by its hook or put to a person) and
not retried; a request that never left, or a read, is retried as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from slack_sdk.http_retry.builtin_handlers import (
    ConnectionErrorRetryHandler,  # type: ignore
)

from app.sources.client.http.send_ambiguity import (
    classify_status,
    classify_urllib_failure,
    settle_failed_send,
)

if TYPE_CHECKING:
    from slack_sdk.http_retry.request import HttpRequest  # type: ignore
    from slack_sdk.http_retry.response import HttpResponse  # type: ignore
    from slack_sdk.http_retry.state import RetryState  # type: ignore

# Final segments of Web API methods that only read (``conversations.history``, ``users.lookupByEmail``). Every other
# method may change something: Slack takes every method as a POST, so the verb is the only signal (fail closed).
_READ_VERBS = ("list", "history", "info", "replies", "get", "lookup", "test", "members")


def slack_method_writes(url: str) -> bool:
    method = url.split("?", 1)[0].rsplit("/", 1)[-1]
    if method.startswith("search."):
        return False
    return not method.rsplit(".", 1)[-1].startswith(_READ_VERBS)


class SendSafeRetryHandler(ConnectionErrorRetryHandler):
    @override
    def can_retry(
        self,
        *,
        state: RetryState,
        request: HttpRequest,
        response: HttpResponse | None = None,
        error: Exception | None = None,
    ) -> bool:
        # Checked before the attempt budget: the last attempt's failure must be reported too.
        if error is not None and slack_method_writes(request.url):
            sent = (
                classify_status("POST", response.status_code)
                if response is not None
                else classify_urllib_failure(error, unsafe=True)
            )
            if not settle_failed_send(sent, type(error).__name__, unsafe=True):
                return False
        return super().can_retry(state=state, request=request, response=response, error=error)


def send_safe_retry_handlers() -> list[ConnectionErrorRetryHandler]:
    return [SendSafeRetryHandler()]


__all__ = ["SendSafeRetryHandler", "send_safe_retry_handlers", "slack_method_writes"]
