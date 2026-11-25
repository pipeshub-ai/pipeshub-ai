"""A PipesHub run's data classification (OWD-15): an extension point, ``ClassificationPort``, read through
``harness_edition().port``.

The port gives the org's classification as its live layer holds it, the model guard every model call of a run goes
through, the classification layer of the run's approval policy, the conversation's level as the run's ledger stands,
what a run records beside each source it reads, what a call may send out at a level, the record of the level, and
the model gateway's reading of a conversation's classification. This build's default classifies nothing: an org's
classification is ``UNCONFIGURED``, the guard passes every call through, the approval policy is unchanged, nothing is
recorded beside a source, every call may send out what the other rules allow, no level is recorded and the gateway
asks nothing.
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Final, Protocol, override

from app.utils.run_model_scope import tool_call_reads
from harness_core.provenance.types import NO_ATTRIBUTES
from harness_core.security.classification import UNCONFIGURED

if TYPE_CHECKING:
    from collections.abc import (
        Awaitable,
        Callable,
        Iterable,
        Iterator,
        Mapping,
        Sequence,
    )

    from pydantic import JsonValue

    from app.control_plane.runtime.live_layer import LiveLayer
    from app.control_plane.runtime.ports import LiveLayerCache
    from app.harness.assembly.model import ResolvedModel
    from app.harness.run_policy.ports import RunApprovals
    from harness_core.access.ports import ConversationClassification
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.journal.types import NewEvent
    from harness_core.logs import HarnessLog
    from harness_core.provenance.types import SourceAttributes
    from harness_core.security.classification import Classification, EgressRule
    from harness_core.security.labels import SourceLedger

# The level of every conversation when no classification is configured.
UNCLASSIFIED: Final = "unclassified"


class ModelGuard(Protocol):
    """Every model call of a run under the org's classification: the port each route samples through and the
    transport everything else calls are wrapped when the model is resolved; the level is bound once the run's ledger
    exists."""

    def bind(
        self,
        classification: Classification,
        level: Callable[[], str],
        record_level: Callable[[str], str] | None = None,
    ) -> None: ...

    def reads_from(self, records: Callable[[], Iterable[str]]) -> None: ...

    def wrap(self, model: ResolvedModel, model_id: str) -> ResolvedModel: ...

    def level(self) -> str | None: ...

    def refusal(self, model_id: str, provider: str, *, reading: Iterable[str] = ()) -> str | None: ...

    def check(self, model_id: str, provider: str) -> None: ...


class PassthroughGuard:
    """Checks nothing: every model call goes on as it was made."""

    def bind(
        self,
        classification: Classification,
        level: Callable[[], str],
        record_level: Callable[[str], str] | None = None,
    ) -> None:
        return None

    def reads_from(self, records: Callable[[], Iterable[str]]) -> None:
        return None

    def wrap(self, model: ResolvedModel, model_id: str) -> ResolvedModel:
        return model

    def level(self) -> str | None:
        return None

    def refusal(self, model_id: str, provider: str, *, reading: Iterable[str] = ()) -> str | None:
        return None

    def check(self, model_id: str, provider: str) -> None:
        return None


class ClassificationPort(ABC):
    @abstractmethod
    def org_classification(self, document: Mapping[str, JsonValue] | None, log: HarnessLog) -> Classification:
        """The org's classification as its layer holds it."""

    @abstractmethod
    def live_classification(self, carried: Mapping[str, JsonValue], log: HarnessLog) -> Classification:
        """The org's classification from what an edition carries in its live layer (``LiveLayer.carried``)."""

    @abstractmethod
    def model_guard(self, log: HarnessLog) -> ModelGuard:
        """A new guard for one run's model calls."""

    @abstractmethod
    def approval_layer(
        self,
        policy: ApprovalPolicy,
        classification: Classification,
        level: Callable[[], str],
        log: HarnessLog,
    ) -> ApprovalPolicy:
        """``policy`` under ``classification``, the conversation's level read from ``level`` when a call is
        decided."""

    @abstractmethod
    def level_source(
        self,
        classification: Classification,
        ledger: SourceLedger | None,
        run_id: str,
        *,
        record_level: Callable[[str], str] | None = None,
    ) -> Callable[[], str]:
        """The run's conversation level as ``ledger`` stands when it is asked; ``record_level`` ranks each record
        (else the port's own ranking). No ledger: the level of content nobody can name."""

    @abstractmethod
    def record_ranking(
        self, classification: Classification, ledger: object, tool_state: Mapping[str, Any] | None
    ) -> Callable[[str], str]:
        """How a run's records rank: by what ``ledger`` recorded of each when it was read, and for what a tool call
        has read before the ledger has it, by the run's ``tool_state``."""

    @abstractmethod
    def record_attributes(self, record: Mapping[str, object], connector: str | None) -> SourceAttributes:
        """What a run records beside a record or live item it reads (``RecordRef.attributes``), from the retrieved
        record or citation metadata (``record``) and the connector that holds it: what ``record_ranking`` ranks it
        by later. Empty: nothing."""

    @abstractmethod
    def egress(self, classification: Classification, level: str) -> EgressRule:
        """Whether a call may send data out of the organization from a conversation at ``level``."""

    @abstractmethod
    def level_events(
        self, classification: Classification, level: Callable[[], str], accepts: Callable[[str], bool]
    ) -> Callable[[], Sequence[NewEvent]] | None:
        """The events a run records its conversation's level with, for a journal whose claim names their feature
        (``accepts``: the claim's journal features); ``None``: none."""

    @abstractmethod
    def label_fields(self, classification: Classification, level: Callable[[], str]) -> Callable[[], Mapping[str, str]]:
        """What a child a run asks for inherits beside its label (``SessionSourceLedger.bind_label_fields``), read
        when the child is asked for."""

    @abstractmethod
    def gateway(self, live: LiveLayerCache) -> ConversationClassification | None:
        """What the model gateway asks before an engine's call; ``None``: nothing."""

    @abstractmethod
    def declared_regions(self, live: LiveLayerCache) -> Callable[[str, str, str], Awaitable[str | None]] | None:
        """The region an org declared for a model (``org, model, provider``), read where a route's configuration
        names none; ``None``: none declared."""


class NoClassification(ClassificationPort):
    """Classifies nothing."""

    _guard: Final = PassthroughGuard()

    @override
    def org_classification(self, document: Mapping[str, JsonValue] | None, log: HarnessLog) -> Classification:
        return UNCONFIGURED

    @override
    def live_classification(self, carried: Mapping[str, JsonValue], log: HarnessLog) -> Classification:
        return UNCONFIGURED

    @override
    def model_guard(self, log: HarnessLog) -> ModelGuard:
        return self._guard

    @override
    def approval_layer(
        self,
        policy: ApprovalPolicy,
        classification: Classification,
        level: Callable[[], str],
        log: HarnessLog,
    ) -> ApprovalPolicy:
        return policy

    @override
    def level_source(
        self,
        classification: Classification,
        ledger: SourceLedger | None,
        run_id: str,
        *,
        record_level: Callable[[str], str] | None = None,
    ) -> Callable[[], str]:
        return _unclassified

    @override
    def record_ranking(
        self, classification: Classification, ledger: object, tool_state: Mapping[str, Any] | None
    ) -> Callable[[str], str]:
        return _record_unclassified

    @override
    def record_attributes(self, record: Mapping[str, object], connector: str | None) -> SourceAttributes:
        return NO_ATTRIBUTES

    @override
    def egress(self, classification: Classification, level: str) -> EgressRule:
        return "allow"

    @override
    def level_events(
        self, classification: Classification, level: Callable[[], str], accepts: Callable[[str], bool]
    ) -> Callable[[], Sequence[NewEvent]] | None:
        return None

    @override
    def label_fields(self, classification: Classification, level: Callable[[], str]) -> Callable[[], Mapping[str, str]]:
        return _no_fields

    @override
    def gateway(self, live: LiveLayerCache) -> ConversationClassification | None:
        return None

    @override
    def declared_regions(self, live: LiveLayerCache) -> Callable[[str, str, str], Awaitable[str | None]] | None:
        return None


def _no_fields() -> Mapping[str, str]:
    return {}


def _unclassified() -> str:
    return UNCLASSIFIED


def _record_unclassified(_record: str) -> str:
    return UNCLASSIFIED


NO_CLASSIFICATION: Final = NoClassification()


def classification_port() -> ClassificationPort:
    """This process's classification."""
    from app.edition_services import harness_edition

    return harness_edition().port(ClassificationPort, NO_CLASSIFICATION)


def org_classification(document: Mapping[str, JsonValue] | None, log: HarnessLog) -> Classification:
    """The org's classification as its layer holds it."""
    return classification_port().org_classification(document, log)


def with_org_classification(approvals: RunApprovals, layer: LiveLayer | None, log: HarnessLog) -> RunApprovals:
    """``approvals`` with the org's classification from its live layer (the layer the attempt was narrowed by)."""
    if layer is None or not layer.carried:
        return approvals
    found = classification_port().live_classification(layer.carried, log)
    return approvals if not found.configured else dataclasses.replace(approvals, classification=found)


def classification_guarded(
    policy: ApprovalPolicy,
    classification: Classification,
    *,
    ledger: SourceLedger | None,
    run_id: str,
    log: HarnessLog,
) -> ApprovalPolicy:
    """``policy`` under the org's classification; unchanged for an org that has not configured one."""
    if not classification.configured:
        return policy
    return classification_port().approval_layer(policy, classification, level_source(classification, ledger, run_id), log)


def level_source(
    classification: Classification,
    ledger: SourceLedger | None,
    run_id: str,
    *,
    record_level: Callable[[str], str] | None = None,
) -> Callable[[], str]:
    """The run's conversation level as its ledger stands when it is asked."""
    return classification_port().level_source(classification, ledger, run_id, record_level=record_level)


def record_ranking(
    classification: Classification, ledger: object = None, tool_state: Mapping[str, Any] | None = None
) -> Callable[[str], str]:
    """How a run's records rank, for its conversation's level and its model guard alike."""
    return classification_port().record_ranking(classification, ledger, tool_state)


def tool_state_records(state: Mapping[str, Any]) -> Callable[[], Iterable[str]]:
    """The records a run's tools have read, as its tool state keeps them: retrieval's and the record tools' results
    (``virtual_record_id_to_result``) and the ids the record tools surfaced (``known_record_ids``)."""

    def records() -> set[str]:
        found = {str(record) for record in state.get("known_record_ids") or () if record}
        for result in (state.get("virtual_record_id_to_result") or {}).values():
            if isinstance(result, dict) and result.get("id"):
                found.add(str(result["id"]))
        return found

    return records


# Set for the length of one of the run's tool calls (``in_tool_call``): what the call reads is not in the ledger yet.
_IN_TOOL_CALL: ContextVar[bool] = ContextVar("pipeshub_classification_in_tool_call", default=False)


@contextmanager
def in_tool_call() -> Iterator[None]:
    token = _IN_TOOL_CALL.set(True)
    try:
        with tool_call_reads():
            yield
    finally:
        _IN_TOOL_CALL.reset(token)


def inside_tool_call() -> bool:
    """Whether this is one of the run's tool calls (``in_tool_call``): what the call reads is not in the ledger yet."""
    return _IN_TOOL_CALL.get()


__all__ = [
    "NO_CLASSIFICATION",
    "UNCLASSIFIED",
    "ClassificationPort",
    "ModelGuard",
    "NoClassification",
    "PassthroughGuard",
    "classification_guarded",
    "classification_port",
    "in_tool_call",
    "inside_tool_call",
    "level_source",
    "org_classification",
    "record_ranking",
    "tool_state_records",
    "with_org_classification",
]
