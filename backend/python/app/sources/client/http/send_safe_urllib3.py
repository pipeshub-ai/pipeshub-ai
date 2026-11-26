"""urllib3's ``Retry`` (under ``requests`` and PyGithub) minus re-sending a write that may already have landed.

urllib3 retries a read error and a ``status_forcelist`` status for every method in ``allowed_methods`` (``PUT`` and
``DELETE`` by default), and any other error (a TLS error mid-stream) for every method. So a GitHub merge or file
update whose answer was lost was sent again (QA24-O3). Here a write is sent again only when it never left (a failed
connect) or was refused (429); a failure that may have been processed is reported to the running tool call instead.
Reads keep urllib3's retries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self, override

from urllib3.util.retry import Retry
from urllib3.util.util import reraise

from app.sources.client.http.send_ambiguity import (
    classify_status,
    classify_urllib3_failure,
    is_unsafe,
    settle_failed_send,
)

if TYPE_CHECKING:
    from types import TracebackType

    from urllib3.connectionpool import ConnectionPool
    from urllib3.response import BaseHTTPResponse


class SendSafeRetry(Retry):
    @override
    def is_retry(self, method: str, status_code: int, has_retry_after: bool = False) -> bool:
        # urllib3 asks this of every response, so it is where a write's ambiguous answer is seen.
        if is_unsafe(method) and not settle_failed_send(
            classify_status(method, status_code), f"http_{status_code}", unsafe=True
        ):
            return False
        return super().is_retry(method, status_code, has_retry_after)

    @override
    def increment(
        self,
        method: str | None = None,
        url: str | None = None,
        response: BaseHTTPResponse | None = None,
        error: Exception | None = None,
        _pool: ConnectionPool | None = None,
        _stacktrace: TracebackType | None = None,
    ) -> Self:
        if error is not None and method is not None and is_unsafe(method):
            sent = classify_urllib3_failure(error, unsafe=True)
            if not settle_failed_send(sent, type(error).__name__, unsafe=True):
                raise reraise(type(error), error, _stacktrace)
        return super().increment(method, url, response, error, _pool, _stacktrace)


__all__ = ["SendSafeRetry"]
