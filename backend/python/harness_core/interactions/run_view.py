"""``ClaimInteractionView``: the ``InteractionView`` of one claimed attempt over a ``JournalPort``.

For holders that are not the durable kernel (an engine run's tool endpoint,
E1): it folds the run itself and commits ``interaction.requested`` with the
attempt's token. The kernel has its own fold and commits, and adapts those.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from harness_core.effects.edits import approved_edit
from harness_core.interactions.approval_request import (
    check_explained,
    explains_every_ask,
)
from harness_core.interactions.journal import said_in
from harness_core.journal.types import CommitRequest, command_id
from harness_core.kernel.fold import RunState, fold_run

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.effects.edits import ApprovedEdit
    from harness_core.interactions.ports import InteractionDecision
    from harness_core.journal.ports import JournalPort
    from harness_core.journal.types import Claim, NewEvent


class ClaimInteractionView:
    def __init__(self, journal: JournalPort, claim: Claim, *, kind: str = "interaction") -> None:
        self._journal = journal
        self._claim = claim
        self._kind = kind
        self._commits = 0
        self._run = RunState(run_id=claim.run_id)

    async def refresh(self) -> None:
        events = await self._journal.read(self._claim.stream_id, after_seq=self._run.seq)
        self._run = fold_run(events, run_id=self._claim.run_id, state=self._run)

    def requested(self, interaction_id: str) -> bool:
        return interaction_id in self._run.interactions

    def decision(self, interaction_id: str) -> InteractionDecision | None:
        record = self._run.interactions.get(interaction_id)
        if record is None or record.decision is None:
            return None
        return cast("InteractionDecision", record.decision)

    def said(self, interaction_id: str) -> str | None:
        record = self._run.interactions.get(interaction_id)
        return said_in(record.answer_ref) if record is not None else None

    def edit(self, interaction_id: str) -> ApprovedEdit | None:
        record = self._run.interactions.get(interaction_id)
        if record is None or record.decision != "approved":
            return None
        return approved_edit(record.answer_ref)

    async def append(self, events: Sequence[NewEvent]) -> None:
        # Run 10 (WP-97): an approval request never reaches the person without its reason.
        check_explained(events, explains=explains_every_ask(self._claim.journal_accepts))
        self._commits += 1
        _ = await self._journal.commit(
            CommitRequest(
                command_id(self._claim.attempt_id, self._kind, self._commits),
                self._claim.token,
                tuple(events),
            )
        )
        await self.refresh()


__all__ = ["ClaimInteractionView"]
