"""The flip: the embedding server serves other weights than the index a write goes to holds."""

from __future__ import annotations

from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt


def setting_for(precision: str | None) -> str:
    """What to set ``EMBEDDING_SERVER_PRECISION`` to so that the embedding server serves *precision* again."""
    if precision == "int8-w":
        return "EMBEDDING_SERVER_PRECISION=int8"
    if precision == "fp32":
        return "EMBEDDING_SERVER_PRECISION=fp32 (or remove the setting, which means fp32)"
    return "EMBEDDING_SERVER_PRECISION to the value the index was built with"


#: The other way out: keep the new setting and build the index again. Says what the cleanup does and where it is.
CLEANUP_HINT = (
    "or, to keep the new setting, open Workspace > Connectors, run \"Clean up embeddings\" and then \"Reindex embeddings\". "
    "Cleaning up deletes the whole search index, and search finds nothing until the reindex has finished"
)


class PrecisionFlip(RequeueWithoutAttempt):
    """The embedding server serves other weights than the index this record writes to holds.

    Raised before anything is written for the batch. The record goes back to the broker with no attempt
    counted and comes back after the operator reverts the setting or runs the cleanup and reindex. The
    read-time check does the pausing, so this one does not pause the consumer. It is never a final failure.
    """

    counts_toward_backstop = False
    pauses_consumer = False

    def __init__(self, message: str, *, delay_s: float = 60.0) -> None:
        super().__init__(message, delay_s=delay_s)


def precision_flip_in(exc: BaseException | None) -> PrecisionFlip | None:
    """The first ``PrecisionFlip`` in *exc*'s cause and context chain, or None.

    Wrappers between the batch that raised it and the handler turn it into other errors;
    one walk at each handler boundary finds it again, so a wrapper added later needs to know nothing.
    """
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(seen) < 20:
        if isinstance(current, PrecisionFlip):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None
