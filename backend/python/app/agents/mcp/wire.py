"""What happened on the wire during one MCP operation: a connect, a listing or a tool call.

The mcp SDK turns every HTTP error into an `MCPError` without the status, and its error codes
can't be trusted to say what happened: servers send their own JSON-RPC errors, and the code the
SDK uses for its own timeout (-32001) is what many servers send for "session not found" or a
401. So the operation is recorded where it happens instead. The SDK runs each request in a copy
of the caller's context, so the record set here is seen by the URL guard and the client's
response hook. Writers never raise: an exception there would escape the SDK's request task and
take the whole session down.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from app.agents.mcp.www_authenticate import bearer_challenge

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["WireRecord", "current_record", "record_lost", "record_response", "record_unsent", "recording"]


@dataclass
class WireRecord:
    """Every response status an operation's requests got, in order, and whether any of them
    carried a session id. `unsent` is the first failure before a request reached the server,
    `lost` the first after one went out."""

    statuses: list[int] = field(default_factory=list)
    sent_session_id: bool = False
    unsent: Optional[BaseException] = None
    lost: Optional[BaseException] = None
    # The Bearer challenge of the latest 401 and of the latest 403, by status, so an error is
    # never told another response's challenge.
    challenges: dict[int, dict[str, str]] = field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        """Whether the server took any request of the operation. A call's follow-up requests (a
        stream resumed after a drop) go in its record too; once the call itself was taken, their
        failure doesn't mean it didn't run."""
        return any(status < 400 for status in self.statuses)

    @property
    def error_status(self) -> Optional[int]:
        """The status the operation ended with, when it was an error. An earlier error the SDK
        recovered from (a legacy server refusing the version probe) isn't the failure."""
        if not self.statuses or self.statuses[-1] < 400:
            return None
        return self.statuses[-1]


_current: ContextVar[Optional[WireRecord]] = ContextVar("mcp_wire_record", default=None)


@contextmanager
def recording() -> "Iterator[WireRecord]":
    """A fresh record for the operation run inside the block."""
    record = WireRecord()
    token = _current.set(record)
    try:
        yield record
    finally:
        _current.reset(token)


def current_record() -> Optional[WireRecord]:
    return _current.get()


async def record_response(response: Any) -> None:  # noqa: ANN401
    """An httpx2 response hook."""
    record = _current.get()
    if record is None:
        return
    try:
        status = int(response.status_code)
        record.statuses.append(status)
        if response.request.headers.get("mcp-session-id"):
            record.sent_session_id = True
        if status in (401, 403):
            record.challenges[status] = bearer_challenge(response.headers.get_list("www-authenticate"))
    except Exception:  # see the module docstring
        return


def record_unsent(error: BaseException) -> None:
    """A request that never reached the server, and why."""
    record = _current.get()
    if record is not None and record.unsent is None:
        record.unsent = error


def record_lost(error: BaseException) -> None:
    """A request whose connection failed after it went out, and how."""
    record = _current.get()
    if record is not None and record.lost is None:
        record.lost = error
