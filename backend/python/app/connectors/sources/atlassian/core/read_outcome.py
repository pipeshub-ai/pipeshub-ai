"""How a Jira read that did not answer 200 is classed.

A sync that saves memberships or grants from a read replaces what is stored, so it must
know whether a failed read means "there is nothing" or "we don't know yet".
"""

from app.config.constants.http_status_code import HttpStatusCode

# Answers that repeat on every sync: what was asked for is not there.
_CONFIRMED_ABSENT = frozenset({HttpStatusCode.BAD_REQUEST.value, HttpStatusCode.NOT_FOUND.value})


def is_inconclusive_status(status: int) -> bool:
    """Whether a response other than 200 leaves the answer unknown rather than empty.

    400 and 404 answer the same way on every sync, so they count as a confirmed empty
    answer. Everything else (401, 408, 429, 5xx, a redirect, any status not listed) may
    pass, so the answer is unknown. A 403 is a lasting refusal that each caller handles
    before asking this.
    """
    return status not in _CONFIRMED_ABSENT


class StoredAccessReadError(Exception):
    """The stored access a sync would keep could not be read. Raised so the sync stops
    before a write that might replace that access with a list it could not read in full."""
