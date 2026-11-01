"""Which record to answer from when records read give different values for one
part of the question.

The checker describes each value (its record, the scope it applies to, the
kind of document, which other records it says it revises, whether its scope is
the question's); the rules here decide. A record of a different scope is not a
rival version at all, a revision beats what it revises, and the kind of
document the question names beats other kinds. Majority and recency are left
to the answer rules, and only ever between records of the same scope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from app.agents.agent_loop.coverage.facets import tokens

if TYPE_CHECKING:
    from collections.abc import Iterable


class ConflictValue(BaseModel):
    record: str
    value: str = ""
    scope: str = ""
    doc_type: str = ""
    same_scope_as_question: bool = True
    revises: list[str] = Field(default_factory=list)


class Conflict(BaseModel):
    facet: str = ""
    draft_leads_with: list[str] = Field(default_factory=list)
    draft_mentions: list[str] = Field(default_factory=list)
    values: list[ConflictValue] = Field(default_factory=list)


@dataclass
class Ruling:
    facet: str
    preferred: list[str]
    set_aside: list[tuple[str, str]] = field(default_factory=list)
    """``(record label, why)``; the why is one of the ``SET_ASIDE_*`` keys."""

    def reason_for(self, label: str) -> str | None:
        return next((why for record, why in self.set_aside if record == label), None)


SET_ASIDE_SCOPE = "scope"
SET_ASIDE_REVISED = "revised"
SET_ASIDE_DOC_TYPE = "doc_type"


def doc_type_matches(doc_type: str, named: "Iterable[str]") -> bool:
    have = tokens(doc_type)
    return bool(have) and any(have & tokens(name) for name in named)


def adjudicate(conflict: Conflict, named_doc_types: "Iterable[str]" = ()) -> Ruling | None:
    """The records to answer from and the ones to set aside, or None when the
    rules decide nothing (one value, or no record of the question's scope)."""
    values = [value for value in conflict.values if value.record]
    if len({value.record for value in values}) < 2:
        return None
    in_scope = [value for value in values if value.same_scope_as_question]
    if not in_scope:
        return None
    ruling = Ruling(facet=conflict.facet, preferred=[])
    ruling.set_aside.extend(
        (value.record, SET_ASIDE_SCOPE) for value in values if not value.same_scope_as_question
    )
    labels = {value.record for value in in_scope}
    revised = {
        target for value in in_scope for target in value.revises
        if target in labels and target != value.record
    }
    candidates = [value for value in in_scope if value.record not in revised] or in_scope
    ruling.set_aside.extend((label, SET_ASIDE_REVISED) for label in sorted(revised) if len(candidates) < len(in_scope))
    named = [name for name in named_doc_types if name.strip()]
    if named and len(candidates) > 1:
        typed = [value for value in candidates if doc_type_matches(value.doc_type, named)]
        if typed and len(typed) < len(candidates):
            ruling.set_aside.extend(
                (value.record, SET_ASIDE_DOC_TYPE) for value in candidates if value not in typed
            )
            candidates = typed
    ruling.preferred = list(dict.fromkeys(value.record for value in candidates))
    return ruling if ruling.set_aside else None


def draft_disagrees(ruling: Ruling, conflict: Conflict) -> bool:
    """Whether the draft leads with a set-aside record, or presents a record of
    another scope as a version of the answer."""
    leads = set(conflict.draft_leads_with)
    if leads and not leads & set(ruling.preferred):
        return True
    mentioned = set(conflict.draft_mentions) | leads
    return any(why == SET_ASIDE_SCOPE and label in mentioned for label, why in ruling.set_aside)


__all__ = [
    "SET_ASIDE_DOC_TYPE",
    "SET_ASIDE_REVISED",
    "SET_ASIDE_SCOPE",
    "Conflict",
    "ConflictValue",
    "Ruling",
    "adjudicate",
    "doc_type_matches",
    "draft_disagrees",
]
