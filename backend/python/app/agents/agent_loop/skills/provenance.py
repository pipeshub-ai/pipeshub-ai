"""The confidentiality label a skill's content carries (FOLLOWUPS1-1), on the label's wire form (``PipeshubTurnLabel``).

A skill a person put in the catalog is the agent's own configuration: loading it shows the model no record content.
Content a run's model wrote (``skill_manage``, post-run skill learning, an approved learning candidate) can summarise
what that run read, so it carries that run's label, as a memory does (P7.3), and loading it brings the label back
into the loading run. Labels only rise: a write joins the label the skill had with the writer's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.modules.providers.skills.base import (
    SkillProvenance,
    SkillSource,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agent_loop_lib.modules.providers.skills.base import SkillMetadata
    from harness_core.security.labels import LabelsContext

__all__ = [
    "UNKNOWN_LABEL",
    "RunSkillProvenance",
    "content_label",
    "is_source_free",
    "join_wire",
]

UNKNOWN_LABEL: dict[str, Any] = {"complete": False, "sources": []}


def content_label(metadata: SkillMetadata) -> dict[str, Any] | None:
    """The label ``metadata``'s content carries; None for a person's skill. Agent-written content that recorded
    none (written before labels, or where no label could be read) is unknown."""
    if metadata.label is not None:
        from app.harness.labels import label_from_wire, label_wire

        parsed = label_from_wire(metadata.label)
        return dict(label_wire(parsed)) if parsed is not None else dict(UNKNOWN_LABEL)
    return dict(UNKNOWN_LABEL) if metadata.source == SkillSource.AGENT_CREATED else None


def join_wire(labels: Iterable[dict[str, Any] | None]) -> dict[str, Any] | None:
    """The join of the present labels, in wire form (an unreadable one is unknown); None when none is present."""
    from app.harness.labels import label_from_wire, label_wire, unknown_label
    from harness_core.security.labels import join_labels

    present = [label for label in labels if label is not None]
    if not present:
        return None
    return dict(label_wire(join_labels(label_from_wire(label) or unknown_label() for label in present)))


def is_source_free(label: dict[str, Any] | None) -> bool:
    """Whether a wire label adds nothing to a run that loads its content."""
    return label is None or (label.get("complete") is True and label.get("sources") == [])


class RunSkillProvenance:
    """What a durable run's skill writes are stamped with: the run's label as its source ledger stands at the write,
    and its conversation. Bound to the ledger once the runtime has one; a run without one writes unknown."""

    def __init__(self, run_id: str, session_id: str | None) -> None:
        self._run_id = run_id
        self._session_id = session_id or None
        self._labels: LabelsContext | None = None

    def bind(self, labels: LabelsContext) -> None:
        self._labels = labels

    def __call__(self) -> SkillProvenance:
        label = self._labels.context_label(self._run_id) if self._labels is not None else None
        return SkillProvenance(label=dict(label) if label is not None else None, session_id=self._session_id)
