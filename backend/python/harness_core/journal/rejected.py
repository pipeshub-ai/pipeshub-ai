"""A commit the journal refused for good (``CommitRejected``: a 4xx validation refusal), as both kernels settle it.

Nothing was written and the same commit is refused again, so another attempt only repeats the work before it: an
engine child ran its whole turn four times before quarantine, its answer refused each time (CHILDREF). A kernel
first recovers what it can in the same attempt (a child result refused inline is stored sealed and committed
again), and otherwise fails the run once, as ``commit_rejected``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final

from harness_core.journal.inline import inline_bytes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.journal.types import Complete, ContentRef

COMMIT_REJECTED: Final = "commit_rejected"


async def sealed_completion(
    transition: Complete, seal: Callable[[bytes], Awaitable[ContentRef | None]]
) -> Complete | None:
    """``transition`` with its inline child result stored sealed by ``seal``; ``None`` when there is nothing to
    reseal (no child result, or one already out of its ref) or the store cannot seal."""
    result = transition.child_result
    data = inline_bytes(result.result_ref) if result is not None else None
    if result is None or data is None:
        return None
    ref = await seal(data)
    if ref is None:
        return None
    return replace(transition, child_result=replace(result, result_ref=ref))


__all__ = ["COMMIT_REJECTED", "sealed_completion"]
