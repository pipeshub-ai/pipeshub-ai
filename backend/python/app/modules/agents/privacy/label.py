"""The answer label (version 5): everything an answer's model calls were given.

Python builds it and sends it as `provenance` on the final answer payload and
on error answers. Node fills `ver` and `contentHash`, stores it, and never
returns it to a client. A reader shows an answer only to a viewer who can open
everything named here; a missing, incomplete or unreadable label means only
the asker sees it.

`direct.records` holds record ids, the unit access is checked on. Virtual
record ids (`vrids`, `saVrids`) are kept for audit only.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

LABEL_VERSION: Final = 5
# The set of writers this build tags with. Raised when a writer is added or a
# rule changes, so labels written by an older set can be told apart.
TAGSET: Final = 1
# One answer's direct ids. More is never stored cut short: it makes the label
# incomplete instead.
LABEL_DIRECT_MAX: Final = 2_000

DIRECT_KEYS: Final = ("records", "vrids", "nodes", "saVrids", "personal", "agents", "chat", "projects", "marks")
LABEL_KEYS: Final = frozenset(
    {"v", "ver", "contentHash", "direct", "parents", "complete", "tagset", "pub", "incomplete"},
)
PARENT_MODES: Final = frozenset({"text", "full", "grant", "preswitch"})
AGENT_KINDS: Final = frozenset({"service", "config"})

ParentMode = Literal["text", "full", "grant", "preswitch"]
AgentKind = Literal["service", "config"]

# Reasons a label is incomplete. Node adds its own (`no_final_event`,
# `unsealed_ref`, `citation_outside_label`, ...); these are the ones Python writes.
OVER_CAP: Final = "ledger_over_cap"
NO_FINAL_EVENT: Final = "no_final_event"
UNKNOWN_VERSION: Final = "label_unknown_version"
LABEL_ERROR: Final = "label_error"
UNREFERENCED_ROW: Final = "unreferenced_row"
UNTAGGED_MODEL_CALL: Final = "untagged_model_call"
UNTAGGED_PART: Final = "untagged_prompt_part"


class LabelError(ValueError):
    """A label this build cannot read: an unknown version, key or value."""


@dataclass(frozen=True, order=True)
class AgentRef:
    key: str
    kind: AgentKind

    def to_wire(self) -> dict[str, str]:
        return {"key": self.key, "kind": self.kind}


@dataclass(frozen=True, order=True)
class ChatUpload:
    """A file uploaded into a chat: readable by that chat's members."""

    conversation_id: str
    record_id: str

    def to_wire(self) -> dict[str, str]:
        return {"conversationId": self.conversation_id, "recordId": self.record_id}


@dataclass(frozen=True, order=True)
class Mark:
    """A reversible admin attestation, such as a self-hosted fetcher that reaches only the internet."""

    source: str
    ver: str

    def to_wire(self) -> dict[str, str]:
        return {"source": self.source, "ver": self.ver}


@dataclass(frozen=True, order=True)
class ParentRef:
    """An earlier row version the run was given. An artifact read from an
    earlier run is a parent with `artifact_id` set and `id`/`ver` empty, and
    `run_id` the run that last wrote it: Node judges the row version that run
    wrote. With no `run_id` (not known), Node can't, and the answer is its
    asker's alone."""

    id: str
    ver: str
    mode: ParentMode
    artifact_id: str | None = None
    # Empty when not known; a string either way, so parents always sort.
    run_id: str = ""

    def to_wire(self) -> dict[str, str]:
        wire = {"id": self.id, "ver": self.ver, "mode": self.mode}
        if self.artifact_id:
            wire["artifactId"] = self.artifact_id
            if self.run_id:
                wire["runId"] = self.run_id
        return wire


def _ids(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(sorted({v for v in values if isinstance(v, str) and v}))


@dataclass(frozen=True)
class LabelDirect:
    records: tuple[str, ...] = ()
    vrids: tuple[str, ...] = ()
    nodes: tuple[str, ...] = ()
    sa_vrids: tuple[str, ...] = ()
    personal: tuple[str, ...] = ()
    agents: tuple[AgentRef, ...] = ()
    chat: tuple[ChatUpload, ...] = ()
    projects: tuple[str, ...] = ()
    marks: tuple[Mark, ...] = ()

    def restricts(self) -> bool:
        """Whether anything here narrows who may see the answer."""
        return any((
            self.records, self.vrids, self.nodes, self.sa_vrids, self.personal,
            self.agents, self.chat, self.projects,
        ))

    def size(self) -> int:
        return sum(len(getattr(self, name)) for name in _FIELD_OF.values())

    def union(self, other: LabelDirect) -> LabelDirect:
        return LabelDirect(**{
            name: tuple(sorted({*getattr(self, name), *getattr(other, name)}))
            for name in _FIELD_OF.values()
        })

    def to_wire(self) -> dict[str, list[Any]]:
        return {
            "records": list(_ids(self.records)),
            "vrids": list(_ids(self.vrids)),
            "nodes": list(_ids(self.nodes)),
            "saVrids": list(_ids(self.sa_vrids)),
            "personal": list(_ids(self.personal)),
            "agents": [a.to_wire() for a in sorted(set(self.agents))],
            "chat": [c.to_wire() for c in sorted(set(self.chat))],
            "projects": list(_ids(self.projects)),
            "marks": [m.to_wire() for m in sorted(set(self.marks))],
        }

    @classmethod
    def from_wire(cls, wire: object) -> LabelDirect:
        if not isinstance(wire, Mapping):
            raise LabelError("direct is not an object")
        unknown = set(wire) - set(DIRECT_KEYS)
        if unknown:
            raise LabelError(f"unknown direct keys: {sorted(unknown)}")
        try:
            agents = tuple(AgentRef(str(a["key"]), a["kind"]) for a in wire.get("agents") or ())
            if any(a.kind not in AGENT_KINDS for a in agents):
                raise LabelError("unknown agent kind")
            return cls(
                records=_ids(wire.get("records") or ()),
                vrids=_ids(wire.get("vrids") or ()),
                nodes=_ids(wire.get("nodes") or ()),
                sa_vrids=_ids(wire.get("saVrids") or ()),
                personal=_ids(wire.get("personal") or ()),
                agents=agents,
                chat=tuple(ChatUpload(str(c["conversationId"]), str(c["recordId"])) for c in wire.get("chat") or ()),
                projects=_ids(wire.get("projects") or ()),
                marks=tuple(Mark(str(m["source"]), str(m["ver"])) for m in wire.get("marks") or ()),
            )
        except (KeyError, TypeError) as exc:
            raise LabelError(f"malformed direct entry: {exc}") from exc


_FIELD_OF: Final = {
    "records": "records", "vrids": "vrids", "nodes": "nodes", "saVrids": "sa_vrids",
    "personal": "personal", "agents": "agents", "chat": "chat", "projects": "projects", "marks": "marks",
}


@dataclass(frozen=True)
class AnswerProvenance:
    direct: LabelDirect
    complete: bool
    parents: tuple[ParentRef, ...] = ()
    incomplete: tuple[str, ...] = ()
    tagset: int = TAGSET
    ver: str = ""
    content_hash: str = ""
    v: int = field(default=LABEL_VERSION)

    @property
    def is_complete(self) -> bool:
        return self.complete and not self.incomplete and self.direct.size() <= LABEL_DIRECT_MAX

    @property
    def pub(self) -> bool:
        """Every id common to the org: complete, naming nothing and built on nothing."""
        return self.is_complete and not self.direct.restricts() and not self.parents

    def to_wire(self) -> dict[str, Any]:
        reasons = set(self.incomplete)
        if self.direct.size() > LABEL_DIRECT_MAX:
            reasons.add(OVER_CAP)
        wire: dict[str, Any] = {
            "v": self.v,
            "ver": self.ver,
            "contentHash": self.content_hash,
            "direct": self.direct.to_wire(),
            "parents": [p.to_wire() for p in sorted(set(self.parents))],
            "complete": self.complete and not reasons,
            "tagset": self.tagset,
            "pub": self.pub,
        }
        if reasons:
            wire["incomplete"] = sorted(reasons)
        return wire

    @classmethod
    def from_wire(cls, wire: object) -> AnswerProvenance:
        if not isinstance(wire, Mapping):
            raise LabelError("label is not an object")
        if wire.get("v") != LABEL_VERSION:
            raise LabelError(f"unknown label version: {wire.get('v')!r}")
        unknown = set(wire) - LABEL_KEYS
        if unknown:
            raise LabelError(f"unknown label keys: {sorted(unknown)}")
        parents: list[ParentRef] = []
        for p in wire.get("parents") or ():
            if not isinstance(p, Mapping) or p.get("mode") not in PARENT_MODES:
                raise LabelError("unknown parent mode")
            parents.append(ParentRef(
                str(p.get("id") or ""), str(p.get("ver") or ""), p["mode"], p.get("artifactId"),
                run_id=str(p.get("runId") or ""),
            ))
        incomplete = wire.get("incomplete") or ()
        if not isinstance(incomplete, (list, tuple)):
            raise LabelError("incomplete is not a list")
        return cls(
            direct=LabelDirect.from_wire(wire.get("direct") or {}),
            complete=wire.get("complete") is True,
            parents=tuple(parents),
            incomplete=tuple(str(r) for r in incomplete),
            tagset=int(wire.get("tagset") or 0),
            ver=str(wire.get("ver") or ""),
            content_hash=str(wire.get("contentHash") or ""),
        )


def merge_wire(labels: Iterable[Mapping[str, Any] | None]) -> dict[str, Any]:
    """One label for an answer made by several passes: the union of each.

    A pass that left no label, or one this build cannot read, makes the union
    incomplete: what that pass's model calls were given is unknown.
    """
    direct = LabelDirect()
    parents: set[ParentRef] = set()
    reasons: set[str] = set()
    complete = True
    for wire in labels:
        if wire is None:
            reasons.add(NO_FINAL_EVENT)
            continue
        try:
            label = AnswerProvenance.from_wire(wire)
        except LabelError:
            reasons.add(UNKNOWN_VERSION)
            continue
        direct = direct.union(label.direct)
        parents.update(label.parents)
        reasons.update(label.incomplete)
        complete = complete and label.complete
    return AnswerProvenance(
        direct=direct, parents=tuple(parents), complete=complete and not reasons, incomplete=tuple(reasons),
    ).to_wire()


__all__ = [
    "AGENT_KINDS",
    "DIRECT_KEYS",
    "LABEL_DIRECT_MAX",
    "LABEL_ERROR",
    "LABEL_VERSION",
    "NO_FINAL_EVENT",
    "OVER_CAP",
    "PARENT_MODES",
    "TAGSET",
    "UNKNOWN_VERSION",
    "UNREFERENCED_ROW",
    "UNTAGGED_MODEL_CALL",
    "UNTAGGED_PART",
    "AgentRef",
    "AnswerProvenance",
    "ChatUpload",
    "LabelDirect",
    "LabelError",
    "Mark",
    "ParentRef",
    "merge_wire",
]
