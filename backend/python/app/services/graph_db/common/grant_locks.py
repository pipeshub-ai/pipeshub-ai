"""Which grants a replace takes as kept, and which (holder, app) pairs a transaction has locked: one contract for the three graph stores.

A grant writer locks the flagged app memberships its grants keep (``lock_external_app_memberships``), so the external-collaborator reaper either
waits for the grant or decided before it existed. A grant the reaper already reads, committed into a live node, needs no lock; anything else does.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# the targets whose grants keep an app's membership, and whose liveness the reaper reads
GRANTED = ("records", "recordGroups")
PEOPLE = ("users", "person")

Grant = tuple[str, str, str, str]      # (holder collection, holder, target collection, target)


def kept_holders(removed: set[tuple[str, str]], to_collection: str, *, dead: bool, target_written: bool) -> set[tuple[str, str]]:
    """The holders whose grant into a replace's node needs no lock: those with an edge into it already (``removed``: deleted and written
    again), each committed before this transaction or locked when it created it. None for a record or record group the reaper reads as
    deleted (``dead``) or that this transaction wrote (``target_written``: its committed liveness is not known here)."""
    if to_collection in GRANTED and (dead or target_written):
        return set()
    return removed


def grants_to_lock(grants: Sequence[Grant], kept: set[tuple[str, str]], app: str | None,
                   lock_done: set[tuple[str, str, str]] | None) -> list[Grant]:
    """The grants a replace locks: not kept, and not a pair of ``app`` the transaction has already locked."""
    done = lock_done if lock_done is not None and app else set()
    return [g for g in grants if (g[0], g[1]) not in kept and (g[0], g[1], app) not in done]


def record_locked(lock_done: set[tuple[str, str, str]] | None, locked: Iterable[Grant], app: str | None) -> None:
    """Adds the pairs of ``app`` whose membership a write locked, for users and people only: a group's members are read again by its next
    grant (one may be added after this read), and a kept grant is no lock (its node can be trashed before this transaction commits)."""
    if lock_done is not None and app:
        lock_done.update((hc, hi, app) for hc, hi, _, _ in locked if hc in PEOPLE)
