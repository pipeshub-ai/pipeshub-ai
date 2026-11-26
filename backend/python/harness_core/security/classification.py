"""A conversation's data classification as a run carries it: the shape the run's policy and its ledger share.

What an org's classification decides is an extension point (``app.harness.classification.ClassificationPort``). Here
a classification is opaque: a run asks only whether one is ``configured``, and hands it back to the port that made
it. This build's port configures none (``UNCONFIGURED``), so a classification changes no decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal, Protocol, runtime_checkable

EgressRule = Literal["allow", "ask", "deny"]

# (connector type, the connector's label) of a record, or ``None`` when it carries none.
type LabelLookup = Callable[[str], tuple[str, str] | None]


class Classification(Protocol):
    """An org's classification as its port reads it; only the port that made it reads more than ``configured``."""

    @property
    def configured(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class _Unconfigured:
    configured: bool = False


# An org that has not configured a classification: nothing it decides is stricter than the other rules.
UNCONFIGURED: Final[Classification] = _Unconfigured()


@runtime_checkable
class ClassifiedSources(Protocol):
    """A ``SourceLedger`` facet (OWD-15): what the conversation's level ranks. ``classified_sources``: every record
    and live item that may have reached the run, by the id its label is looked up by (``None``: content nobody can
    name). Unlike ``source_records`` a live item is named: it is traceable. ``inherited_field``: a field an edition
    added to the label a child's admission pinned of its parent (``None``: none)."""

    def classified_sources(self, run_id: str) -> frozenset[str] | None: ...

    def inherited_field(self, name: str) -> str | None: ...


__all__ = [
    "UNCONFIGURED",
    "Classification",
    "ClassifiedSources",
    "EgressRule",
    "LabelLookup",
]
