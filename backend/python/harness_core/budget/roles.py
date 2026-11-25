"""GA-15: the model role a call is charged as, when its caller knows better than the transport.

A run's metered transport charges its calls as the role it was built for (the loop's: primary). A caller that
makes another kind of call on it, such as a summary for compaction, says so for the call's duration; the
context travels with the call's task, so concurrent calls of the run keep their own role.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_ROLE: ContextVar[str | None] = ContextVar("pipeshub_charge_role", default=None)


def current_charge_role() -> str | None:
    return _ROLE.get()


@contextlib.contextmanager
def charged_as(role: str) -> Iterator[None]:
    token = _ROLE.set(role)
    try:
        yield
    finally:
        _ROLE.reset(token)


__all__ = ["charged_as", "current_charge_role"]
