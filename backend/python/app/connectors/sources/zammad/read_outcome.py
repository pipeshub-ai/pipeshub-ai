"""How far a Zammad read got, which decides what may be written from it."""

from enum import Enum

from app.config.constants.http_status_code import HttpStatusCode


class ReadOutcome(Enum):
    """How far a read of who belongs where got, which decides what may be written from it."""

    COMPLETE = "complete"
    # Stopped for a reason that passes (no answer, a timeout, a rate limit, a server
    # error): replacing stored members with what was read would take access from
    # everyone not read, so what is stored stays until a sync reads it all.
    INTERRUPTED = "interrupted"
    # Zammad refused it (401, 403 and other 4xx), which repeats every sync until the
    # token or Zammad is changed. Keeping stored members would keep access no one can
    # check, so the write goes ahead without what couldn't be read: access fails closed.
    REFUSED = "refused"


# Client errors that pass with time, unlike the rest of 4xx.
RETRYABLE_CLIENT_ERRORS = frozenset({408, HttpStatusCode.TOO_MANY_REQUESTS.value})


def read_failure(status_code: int | None) -> ReadOutcome:
    """The outcome of a read Zammad answered with ``status_code`` instead of the data.

    No status means no answer at all (a network error, or a body that wasn't JSON).
    """
    if (
        isinstance(status_code, int)
        and HttpStatusCode.BAD_REQUEST.value <= status_code < HttpStatusCode.INTERNAL_SERVER_ERROR.value
        and status_code not in RETRYABLE_CLIENT_ERRORS
    ):
        return ReadOutcome.REFUSED
    return ReadOutcome.INTERRUPTED
