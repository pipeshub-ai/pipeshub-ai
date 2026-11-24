"""Call-site helpers for the member budget: reserve once, by the component that runs the work.

Every helper does nothing unless ``PIPESHUB_CAPACITY_PROFILE=auto`` and the container's ledger is
open, so a call site that uses one behaves as before with the flag off.

* a lane or pool that runs a unit someone else admitted *adopts* that caller's permit and reserves
  nothing; it reserves one only when it is given none (:func:`reserve_or_adopt`);
* the Docling service reserves a conversion with the class its caller sent in ``X-Parse-Class``; the
  indexing caller holds no tokens while it waits on that HTTP call;
* an LLM call holds one of the container's member slots (:func:`llm_slot`), whichever cell or
  service makes it.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

from app.capacity.budget_policy import DOCLING_WEIGHT, Demand, Family
from app.capacity.runtime import member_budget

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.capacity.member_budget import CancelToken, Permit

CLASSES = frozenset({"small", "large", "huge"})
DOCLING_UNIT_MEMORY_BYTES = int(1.5 * (1 << 30))  # one conversion's working set (the governor's HEAVY_PARSE default)
LLM_WAIT_S = 3600.0  # a wait is always bounded: nothing here waits for ever


def parse_class(value: object) -> str | None:
    """The cost class a caller sent (``X-Parse-Class``); anything else is unclassified, never trusted."""
    cleaned = value.strip().lower() if isinstance(value, str) else ""  # a route called directly passes its Header() default
    return cleaned if cleaned in CLASSES else None


def docling_demand(cls: str | None) -> Demand:
    return Demand(Family.DOCLING, DOCLING_WEIGHT, DOCLING_UNIT_MEMORY_BYTES, cls)


async def reserve_or_adopt(
    permit: Permit | None,
    demand: Demand,
    *,
    deadline_s: float | None = None,
    cancel: CancelToken | None = None,
) -> Permit | None:
    """The permit a lane releases when its job ends, or ``None`` (flag off: nothing is held).

    A permit passed in is adopted and nothing more is reserved: no unit holds two.
    """
    if permit is not None:
        return permit.adopt()
    budget = member_budget()
    if budget is None:
        return None
    return await budget.reserve(demand, deadline_s=deadline_s, cancel=cancel)


def reserve_or_adopt_blocking(
    permit: Permit | None,
    demand: Demand,
    *,
    deadline_s: float | None = None,
    cancel: CancelToken | None = None,
) -> Permit | None:
    if permit is not None:
        return permit.adopt()
    budget = member_budget()
    if budget is None:
        return None
    return budget.reserve_blocking(demand, deadline_s=deadline_s, cancel=cancel)


@contextlib.asynccontextmanager
async def llm_slot() -> AsyncIterator[None]:
    """One of the container's member LLM slots, held for the call. No-op with the flag off."""
    budget = member_budget()
    if budget is None:
        yield
        return
    permit = await budget.reserve(Demand(Family.LLM, 0), deadline_s=LLM_WAIT_S)
    try:
        yield
    finally:
        permit.release()
