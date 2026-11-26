"""How far a BookStack read got: the outcome of a listing or a get, shared by the content sync and the
permission reading (``app.ee.permission_sync.bookstack``)."""

from enum import Enum

from app.config.constants.http_status_code import HttpStatusCode


class ReadOutcome(Enum):
    """How far a read of users or roles got, which decides what may be written from it."""

    COMPLETE = "complete"
    # Stopped for a reason that passes (no answer, a timeout, a rate limit, a server
    # error): role members and grants written from it would leave out whoever wasn't
    # read, and writing replaces what is stored, so what is stored stays until a
    # sync reads it all.
    INTERRUPTED = "interrupted"
    # BookStack refused it (401, 403 and other 4xx), which repeats every sync until
    # the token or its role is changed. Keeping stored access would keep access no one
    # can check, so the write goes ahead without what couldn't be read: it fails closed.
    REFUSED = "refused"


# Client errors that pass with time, unlike the rest of 4xx.
RETRYABLE_CLIENT_ERRORS = frozenset({408, HttpStatusCode.TOO_MANY_REQUESTS.value})


def read_failure(response: object) -> ReadOutcome:
    """The outcome of a BookStack answer that wasn't the data asked for.

    BookStack answers an error with a JSON body, which the data source can hand
    back as a success; its status tells them apart. No status means no answer at
    all: a network error, a body that wasn't JSON, or an exception.
    """
    status = getattr(response, "status_code", None)
    if (
        isinstance(status, int)
        and HttpStatusCode.BAD_REQUEST.value <= status < HttpStatusCode.INTERNAL_SERVER_ERROR.value
        and status not in RETRYABLE_CLIENT_ERRORS
    ):
        return ReadOutcome.REFUSED
    return ReadOutcome.INTERRUPTED


def _combined(first: ReadOutcome, second: ReadOutcome) -> ReadOutcome:
    """A read made of parts: any part that may still be read keeps what is stored."""
    for outcome in (ReadOutcome.INTERRUPTED, ReadOutcome.REFUSED):
        if outcome in (first, second):
            return outcome
    return ReadOutcome.COMPLETE


def _answered(response: object) -> bool:
    """Whether a get call (user, role, book or chapter) came back with the item, not an error body."""
    if isinstance(response, BaseException) or not getattr(response, "success", False):
        return False
    status = getattr(response, "status_code", None)
    data = getattr(response, "data", None)
    return (
        (not isinstance(status, int) or status < HttpStatusCode.BAD_REQUEST.value)
        and isinstance(data, dict)
        and "error" not in data
    )


def _gone(response: object) -> bool:
    """BookStack says the item no longer exists: a definite answer, not a failed read."""
    return getattr(response, "status_code", None) == HttpStatusCode.NOT_FOUND.value
