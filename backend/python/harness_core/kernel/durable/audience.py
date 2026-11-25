"""Whose turns a run's model may see in a shared session (SS2, ARCHITECTURE 4.9).

A run acts as its sender. Its own turns (this run and the earlier runs its
actor started) reach the model as they were. Another participant's turn whose
confidentiality label passed for the actor (P2.5a, decided by the host with the
actor's own credential) reaches it as its question and final answer text only;
every other one is replaced by one stub, never its text, tool calls, tool
results, steering parts or attachments.

The host pins the audience with the run (Node, at admission); the worker never
derives it. History imported from the conversation names its sender in
``sender`` (a principal ref) when it is not the session owner's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

FOREIGN_TURN_STUB = (
    "[A turn by another participant in this shared conversation is not shown to you.]"
)


@dataclass(frozen=True, slots=True)
class HistoryAudience:
    """``principal``: the run's actor; ``owner``: the session's owner; ``own_runs``: the
    actor's earlier root runs of the session (this run is always its own); ``visible_runs``:
    other participants' runs whose label passed for the actor."""

    principal: str
    owner: str
    own_runs: frozenset[str]
    visible_runs: frozenset[str] = frozenset()

    def owns_run(self, run_id: str | None, current_run_id: str | None) -> bool:
        return run_id is not None and (run_id == current_run_id or run_id in self.own_runs)

    def passed_run(self, run_id: str | None) -> bool:
        return run_id is not None and run_id in self.visible_runs

    def owns_turn(self, turn: Mapping[str, object]) -> bool:
        sender = turn.get("sender")
        return (sender if isinstance(sender, str) else self.owner) == self.principal


def project_turns(
    turns: Iterable[Mapping[str, object]], audience: HistoryAudience
) -> list[dict[str, object]]:
    """History entries (``role``/``content`` dicts) as ``audience`` may see them: own entries
    without ``sender``; each run of consecutive foreign entries one stub question and answer."""
    projected: list[dict[str, object]] = []
    in_foreign = False
    for turn in turns:
        if audience.owns_turn(turn):
            projected.append({k: v for k, v in turn.items() if k != "sender"})
            in_foreign = False
        elif not in_foreign:
            projected.extend(stub_turns())
            in_foreign = True
    return projected


def stub_turns() -> Sequence[dict[str, object]]:
    return (
        {"role": "user_query", "content": FOREIGN_TURN_STUB},
        {"role": "bot_response", "content": FOREIGN_TURN_STUB},
    )


__all__ = ["FOREIGN_TURN_STUB", "HistoryAudience", "project_turns", "stub_turns"]
