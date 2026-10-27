class GraphDBError(Exception):
    """Base exception for graph database failures."""


class GraphQueryError(GraphDBError):
    """Raised when a graph read could not be completed.

    Exists so callers can tell "the query ran and matched nothing" from "the
    query never ran". Returning an empty list for both makes a database that is
    down, a malformed query, or an expired transaction indistinguishable from a
    normal, expected empty result, and callers act on it: safety gates report
    "nothing in flight", paging loops stop as if complete, and resets report a
    finished cleanup.
    """


class GraphWriteError(GraphDBError):
    """A provider reported a write as failed (returned False) rather than raising.

    Raised inside a transaction block so the block rolls back: a reported
    failure would otherwise commit the writes around it.
    """


class PermissionVerificationUnavailableError(Exception):
    """The graph could not adjudicate which retrieved records a user may read.

    Raised rather than returning an empty map, because an empty map is also the
    honest answer when every candidate is denied — and the two need opposite
    responses: retry later versus "nothing you can read matched".
    """


class GraphLockUnavailableError(GraphDBError):
    """A write could not take the locks it needs, so nothing was attempted.

    Says nothing about the records involved: a caller that counts failures per
    record must not count this one.
    """


class KnowledgeHubListingTooLarge(GraphDBError):
    """The database gave up on a listing for want of memory.

    Raised for the whole-connector walk, so the caller can ask the user to open
    a folder or search instead. No partial page is ever returned for it: a cut-off
    candidate set would drop rows without saying so.
    """

    def __init__(self) -> None:
        super().__init__(
            "This view has too many records to list at once. Open a folder, or narrow by search."
        )


class KnowledgeHubListingBusy(GraphDBError):
    """The listing could not get what it needs right now: a walk waited too long for a turn, or the database ran
    out of transaction memory because other requests held it. Nothing is wrong with the request; retry."""

    def __init__(self, *, out_of_memory: bool = False) -> None:
        self.out_of_memory = out_of_memory
        super().__init__("The listing is busy right now. Please try again in a few seconds.")


def is_out_of_memory(error: BaseException) -> bool:
    """Whether the database gave up on a statement for want of memory, however it was wrapped on the way up
    (an ``ExceptionGroup``, or another error raised ``from`` it)."""
    if isinstance(error, BaseExceptionGroup):
        return any(is_out_of_memory(e) for e in error.exceptions)
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if "OutOfMemoryError" in str(getattr(error, "code", "")) or getattr(error, "out_of_memory", False):
            return True
        error = getattr(error, "__cause__", None)
    return False
