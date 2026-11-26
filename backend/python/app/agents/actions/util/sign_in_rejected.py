"""A toolset's vendor rejected the saved sign-in (HTTP 401): said once, where the toolset reads the status.

A toolset answers a rejected sign-in in words (``(False, "GitHub did not accept the saved sign-in ...")``),
which the code around the call must not parse. So the toolset also calls ``note_sign_in_rejected`` where it
reads the 401, and the call's scope (``ToolInstanceCreator.call_scope``) collects it: the credential is then
known to fail, and is withdrawn like one the person never connected (WP62-TS).
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# Where a request's ``tool_state`` keeps the toolsets whose saved sign-in was rejected and withdrawn: instance id ->
# the connect text. The call scope writes it; the harness's connect gate reads it to wait for a reconnect (WP-64).
SIGN_IN_REJECTED_STATE = "_sign_in_rejected"

# A mutable holder, so a rejection noted in a task the call spawned (a gather) still reaches the scope.
_NOTED: ContextVar[list[bool] | None] = ContextVar("toolset_sign_in_rejected", default=None)


def note_sign_in_rejected() -> None:
    """The vendor rejected the credential this call used. Outside a watched call it does nothing."""
    holder = _NOTED.get()
    if holder is not None:
        holder.append(True)


@contextmanager
def watching_sign_in() -> Iterator[list[bool]]:
    """Collects ``note_sign_in_rejected`` for one call: the holder is non-empty once a rejection was noted."""
    holder: list[bool] = []
    token = _NOTED.set(holder)
    try:
        yield holder
    finally:
        _NOTED.reset(token)


__all__ = ["SIGN_IN_REJECTED_STATE", "note_sign_in_rejected", "watching_sign_in"]
