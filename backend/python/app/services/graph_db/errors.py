"""Backend-neutral graph errors, so callers need not import driver exceptions."""

import re


class UniqueConstraintViolation(Exception):
    """A write hit a unique constraint (Neo4j) or unique index (Arango)."""


def violates_unique_constraint(message: str) -> bool:
    lowered = message.lower()
    return (
        "unique constraint violated" in lowered
        or "[1210]" in lowered
        or re.search(r'errornum"?\s*:\s*1210', lowered) is not None
        # Two concurrent Arango transactions inserting the same unique key: the loser gets a
        # 1200 (write-write conflict, or a lock timeout when it waited) instead of 1210 until
        # the winner commits.
        or ("[1200]" in lowered and "in index" in lowered)
        or "already exists with label" in lowered
        or "constraintvalidationfailed" in lowered
    )
