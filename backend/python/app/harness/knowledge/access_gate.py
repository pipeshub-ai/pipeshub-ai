"""Whether a run's knowledge reads and exports may go ahead: an extension point, ``KnowledgeAccessGate``.

Every path that reads knowledge (the loop, an engine through the Tool Gateway) hands its approval policy to the gate
before the run's outermost rules wrap it. A gate may narrow the policy (deny or ask before a knowledge read or an
export) and says how many records an export may write now. This build's gate, ``OPEN_KNOWLEDGE_ACCESS``, lets every
read and export through: the policy is unchanged, and one export writes up to ``MAX_EXPORT_RECORDS``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, override

from app import edition_services

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.harness.journal_node.client import AttemptRoute
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.journal.types import Claim
    from harness_core.logs import HarnessLog

# The most records one export writes when no gate says otherwise.
MAX_EXPORT_RECORDS: Final = 10_000


@dataclass(frozen=True, slots=True)
class GatedKnowledge:
    """The run's policy as the gate leaves it, and the most records an export may write now."""

    policy: ApprovalPolicy
    export_allowance: Callable[[], int]


class KnowledgeAccessGate(ABC):
    @abstractmethod
    def gate(
        self, policy: ApprovalPolicy, *, route: AttemptRoute | None, claim: Claim, log: HarnessLog
    ) -> GatedKnowledge:
        """``policy`` for the claim's run (``route``: its way to the control plane, ``None`` without one)."""


class OpenKnowledgeAccess(KnowledgeAccessGate):
    """Every knowledge read and export goes ahead as the run's own rules decide."""

    @override
    def gate(
        self, policy: ApprovalPolicy, *, route: AttemptRoute | None, claim: Claim, log: HarnessLog
    ) -> GatedKnowledge:
        del route, claim, log
        return GatedKnowledge(policy, lambda: MAX_EXPORT_RECORDS)


OPEN_KNOWLEDGE_ACCESS: Final = OpenKnowledgeAccess()


def knowledge_access_gate() -> KnowledgeAccessGate:
    """The edition's gate; ``OPEN_KNOWLEDGE_ACCESS`` in this build."""
    return edition_services.harness_edition().port(KnowledgeAccessGate, OPEN_KNOWLEDGE_ACCESS)


__all__ = [
    "MAX_EXPORT_RECORDS",
    "OPEN_KNOWLEDGE_ACCESS",
    "GatedKnowledge",
    "KnowledgeAccessGate",
    "OpenKnowledgeAccess",
    "knowledge_access_gate",
]
