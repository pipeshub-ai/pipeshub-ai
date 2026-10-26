"""One memory brake per memory domain for parse admission.

In the all-in-one image the indexing, parsing and Docling processes share one
container, and each runs a governor that brakes parse admission on the same
container-wide memory reading. A heavy parse was braked three times in series, each
governor at its own floor and flipping at a different moment (their baselines come
from different start times). The first process that admits a parse under its memory
brake stamps the request with its memory domain; a process in the same domain admits
the request against its pool's fixed ceiling instead of braking it again, and a
process in another domain (its own pod and memory) brakes it and restamps. Where the
domain cannot be read, every process keeps its own brake, as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from app.services.resource_governor.probe import memory_domain_id
from app.utils.request_context import get_admitted_in, set_admitted_in

if TYPE_CHECKING:
    from app.services.resource_governor.models import Pool


class PoolCeilings(Protocol):
    """What the cap needs from a governor: each pool's fixed ceiling."""

    def ceiling(self, pool: Pool) -> int: ...


def parse_admission_cap(governor: PoolCeilings, pool: Pool) -> int | None:
    """*pool*'s ceiling when this parse was already admitted under a memory brake in
    this process's memory domain; None to admit it against the braked limit."""
    domain = memory_domain_id()
    if domain is not None and get_admitted_in() == domain:
        return governor.ceiling(pool)
    return None


def stamp_admitted_here() -> None:
    """Record that this process braked the request's parse, for the services it calls."""
    domain = memory_domain_id()
    if domain is not None:
        set_admitted_in(domain)
