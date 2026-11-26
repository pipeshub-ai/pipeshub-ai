"""Interactions (ARCHITECTURE §4.5): asking a person and holding a call for the answer.

Ports: ``InteractionBridge`` (request + bounded wait) and ``InteractionWakeup``
(a hint that a decision may have landed). Adapters: ``InMemoryInteractionBridge``
and ``JournalInteractionBridge`` (a durable run's journal: ``interaction.requested``
by the worker, ``interaction.resolved`` by Node). ``BridgeApprovalHold`` is the
``EffectRunner``'s ``ApprovalHold`` over any bridge, keyed by effect key.
Contract suite: ``harness_core.testing.contracts.interactions``.
"""

from typing import TYPE_CHECKING

from harness_core.lazy_exports import lazy_exports

if TYPE_CHECKING:
    from harness_core.interactions.hold import (
        APPROVAL_HOLD_S,
        BridgeApprovalHold,
        approval_interaction_id,
    )
    from harness_core.interactions.journal import (
        InteractionView,
        JournalInteractionBridge,
        binding_digest,
        said_in,
    )
    from harness_core.interactions.memory import (
        InMemoryInteractionBridge,
        InMemoryInteractionWakeup,
        NoWakeup,
    )
    from harness_core.interactions.ports import (
        InteractionBridge,
        InteractionDecision,
        InteractionKind,
        InteractionRequest,
        InteractionTicket,
        InteractionWakeup,
        RunBinding,
        SaysWhy,
    )

# Each name's submodule, imported when the name is first read (R25OPS-FU-1).
_EXPORTS = {
    "APPROVAL_HOLD_S": "hold",
    "BridgeApprovalHold": "hold",
    "approval_interaction_id": "hold",
    "InteractionView": "journal",
    "JournalInteractionBridge": "journal",
    "binding_digest": "journal",
    "said_in": "journal",
    "InMemoryInteractionBridge": "memory",
    "InMemoryInteractionWakeup": "memory",
    "NoWakeup": "memory",
    "InteractionBridge": "ports",
    "InteractionDecision": "ports",
    "InteractionKind": "ports",
    "InteractionRequest": "ports",
    "InteractionTicket": "ports",
    "InteractionWakeup": "ports",
    "RunBinding": "ports",
    "SaysWhy": "ports",
}
__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)

__all__ = [
    "APPROVAL_HOLD_S",
    "BridgeApprovalHold",
    "InMemoryInteractionBridge",
    "InMemoryInteractionWakeup",
    "InteractionBridge",
    "InteractionDecision",
    "InteractionKind",
    "InteractionRequest",
    "InteractionTicket",
    "InteractionView",
    "InteractionWakeup",
    "JournalInteractionBridge",
    "NoWakeup",
    "RunBinding",
    "SaysWhy",
    "approval_interaction_id",
    "binding_digest",
    "said_in",
]
