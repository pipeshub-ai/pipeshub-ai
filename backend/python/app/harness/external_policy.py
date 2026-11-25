"""Whether an outside policy is asked before a run's calls are decided: an extension point, ``ExternalPolicyGate``.

Every path that decides a run's tool calls (the loop, an engine through the Tool Gateway) prepares the gate when the
attempt is assembled, then hands it the run's approval policy as the outermost rule. This build's gate,
``NO_EXTERNAL_POLICY``, reads nothing and leaves the policy as the run's own rules make it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Final, override

from app import edition_services

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.logs import HarnessLog


class ExternalPolicyGate(ABC):
    @abstractmethod
    async def prepare(self, config_service: ConfigurationService, org_id: str, log: HarnessLog) -> object | None:
        """What ``guard`` needs for the org's runs, read when an attempt is assembled; ``None``: nothing to ask."""

    @abstractmethod
    def guard(
        self, policy: ApprovalPolicy, prepared: object | None, *, org_id: str, run_id: str, log: HarnessLog, dry_run: bool
    ) -> ApprovalPolicy:
        """``policy`` with the outside policy outermost; ``dry_run``: the run's dry-run gate."""


class NoExternalPolicy(ExternalPolicyGate):
    """No outside policy: the run's own rules decide every call."""

    @override
    async def prepare(self, config_service: ConfigurationService, org_id: str, log: HarnessLog) -> object | None:
        del config_service, org_id, log
        return None

    @override
    def guard(
        self, policy: ApprovalPolicy, prepared: object | None, *, org_id: str, run_id: str, log: HarnessLog, dry_run: bool
    ) -> ApprovalPolicy:
        del prepared, org_id, run_id, log, dry_run
        return policy


NO_EXTERNAL_POLICY: Final = NoExternalPolicy()


def external_policy_gate() -> ExternalPolicyGate:
    """The edition's gate; ``NO_EXTERNAL_POLICY`` in this build."""
    return edition_services.harness_edition().port(ExternalPolicyGate, NO_EXTERNAL_POLICY)


__all__ = [
    "NO_EXTERNAL_POLICY",
    "ExternalPolicyGate",
    "NoExternalPolicy",
    "external_policy_gate",
]
