"""An approval request and its explanation, made together (WP-97).

A long research run journaled ``interaction.requested`` for an approval with no ``interaction.explained``: a wrapper in the
run's policy chain asked without saying why, so every reason under it was dropped and the person got a card with
no reason line. Three rules make that impossible to repeat:

- ``harness_core.effects.ask_reason.card_reason``: the reason a held call shows is never ``None``. A policy that
  asks without naming its rule gets the plain reason, logged at warning so the gap is found.
- ``approval_request``: the one constructor of an approval request; its explanation comes first, in the same
  journal command, whenever the journal accepts reasons.
- ``check_explained``: the commit seam an engine run's holds journal through refuses an approval request whose
  explanation is not in its command, so a holder that skips the rules above fails loudly instead of showing a card
  without a reason.

A journal that predates ``approval_reasons`` gets the request alone, as before UX1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from harness_core.effects.flow import edition_rule_features
from harness_core.journal.types import (
    APPROVAL_REASONS,
    APPROVAL_RULE_LAYERS,
    EXTERNAL_POLICY_ASKS,
    NewEvent,
)
from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

EXPLAINED: Final = "interaction.explained"
REQUESTED: Final = "interaction.requested"
APPROVAL_ID_PREFIX: Final = "ix.approve."
# The journal features that let every reason through ``reason_for_journal`` whole.
_EVERY_REASON: Final = (APPROVAL_REASONS, APPROVAL_RULE_LAYERS, EXTERNAL_POLICY_ASKS)


class UnexplainedApproval(HarnessRefusal):
    """An approval request reached the journal without its explanation in the same command."""

    reason_code = "approval_unexplained"


def approval_request(
    interaction_id: str, binding_digest: str, reason: Mapping[str, JsonValue] | None
) -> tuple[NewEvent, ...]:
    """The events of one approval request: its explanation, then the request. ``reason`` is ``None`` only for a
    journal that does not accept reasons (``reason_for_journal`` said so); then the request goes alone."""
    requested = NewEvent(
        REQUESTED, {"interactionId": interaction_id, "kind": "approval", "bindingDigest": binding_digest}
    )
    if reason is None:
        return (requested,)
    return (NewEvent(EXPLAINED, {**reason, "interactionId": interaction_id}, critical=False), requested)


def unexplained(events: Sequence[NewEvent]) -> list[str]:
    """The approval requests in ``events`` whose explanation does not come before them in the same list."""
    explained: set[str] = set()
    missing: list[str] = []
    for event in events:
        ix = str(event.data.get("interactionId", ""))
        if event.type == EXPLAINED:
            explained.add(ix)
        elif (
            event.type == REQUESTED
            and event.data.get("kind") == "approval"
            and ix.startswith(APPROVAL_ID_PREFIX)
            and ix not in explained
        ):
            missing.append(ix)
    return missing


def explains_every_ask(accepts: Callable[[str], bool]) -> bool:
    """Whether a journal takes the reason of every ask this build makes (``reason_for_journal`` never withholds one
    there): only then is an approval request without its explanation a reason that was dropped. An older journal
    legitimately gets some requests alone (QA6-F6, UX2)."""
    # An edition's rules' features too (``register_edition_rule``): a journal without one gets those asks alone.
    return all(accepts(feature) for feature in (*_EVERY_REASON, *edition_rule_features()))


def check_explained(events: Sequence[NewEvent], *, explains: bool) -> None:
    """Refuses a command with an approval request but not its explanation, for a journal that takes every reason."""
    if not explains:
        return
    missing = unexplained(events)
    if missing:
        raise UnexplainedApproval(f"approval requested without its explanation: {', '.join(missing)}")


__all__ = [
    "APPROVAL_ID_PREFIX",
    "UnexplainedApproval",
    "approval_request",
    "check_explained",
    "explains_every_ask",
    "unexplained",
]
