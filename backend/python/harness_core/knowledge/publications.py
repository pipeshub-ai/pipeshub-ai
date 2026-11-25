"""The publication rule (D21, REPORT §6.13): pure, table-tested.

A reader's knowledge scope is their own access, narrowed by the grant, plus what an owner published to an audience
containing them. An agent that retrieves as its creator (a service account) never discloses the creator's
clearance: its caller gets ``creator ∩ (caller ∪ published(caller))``. Publications are read at every retrieval,
so revocation and expiry act at the next one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Literal, TypeVar

from harness_core.knowledge.errors import PublicationAnswerOnly, PublicationInvalid

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

PublicationMode = Literal["answer_only", "full"]
ReadPurpose = Literal["answer", "raw"]
ReadDecision = Literal["own", "published_full", "published_answer_only", "denied"]
MODES: tuple[PublicationMode, ...] = ("answer_only", "full")
MAX_AUDIENCE_ENTRIES = 500

K = TypeVar("K")
V = TypeVar("V")


@dataclass(frozen=True, slots=True)
class Principal:
    """Whom a scope is computed for: a user of an org and the groups they are a member of."""

    org_id: str
    user_id: str
    group_ids: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class Audience:
    user_ids: frozenset[str] = frozenset()
    group_ids: frozenset[str] = frozenset()
    org: bool = False

    @property
    def is_empty(self) -> bool:
        return not (self.user_ids or self.group_ids or self.org)

    @property
    def size(self) -> int:
        return len(self.user_ids) + len(self.group_ids)

    def contains(self, principal: Principal) -> bool:
        return self.org or principal.user_id in self.user_ids or not self.group_ids.isdisjoint(principal.group_ids)


@dataclass(frozen=True, slots=True)
class PublicationDraft:
    """What an owner asks for; ``org_id`` and ``created_by`` come from the caller's credential, never a body."""

    org_id: str
    container_id: str
    audience: Audience
    mode: PublicationMode
    created_by: str
    expires_at_ms: int | None = None


@dataclass(frozen=True, slots=True)
class Publication:
    id: str
    org_id: str
    container_id: str
    audience: Audience
    mode: PublicationMode
    created_by: str
    created_at_ms: int
    expires_at_ms: int | None = None
    revoked_at_ms: int | None = None
    revoked_by: str | None = None

    def is_live(self, now_ms: int) -> bool:
        if self.revoked_at_ms is not None:
            return False
        return self.expires_at_ms is None or now_ms < self.expires_at_ms

    def admits(self, principal: Principal, now_ms: int) -> bool:
        return principal.org_id == self.org_id and self.is_live(now_ms) and self.audience.contains(principal)


def validate_draft(draft: PublicationDraft, now_ms: int) -> None:
    """Raises :class:`PublicationInvalid` naming the first problem."""
    if not draft.org_id or not draft.container_id or not draft.created_by:
        raise PublicationInvalid("org, container and creator are required")
    if draft.mode not in MODES:
        raise PublicationInvalid(f"mode must be one of {', '.join(MODES)}")
    if draft.audience.is_empty:
        raise PublicationInvalid("the audience is empty")
    if draft.audience.size > MAX_AUDIENCE_ENTRIES:
        raise PublicationInvalid(f"at most {MAX_AUDIENCE_ENTRIES} users and teams")
    if any(not entry for entry in (*draft.audience.user_ids, *draft.audience.group_ids)):
        raise PublicationInvalid("an audience entry is empty")
    if draft.expires_at_ms is not None and draft.expires_at_ms <= now_ms:
        raise PublicationInvalid("the expiry is not in the future")


def admitting(principal: Principal, publications: Iterable[Publication], now_ms: int) -> tuple[Publication, ...]:
    """The publications that disclose to ``principal`` now, in a stable order."""
    return tuple(sorted((p for p in publications if p.admits(principal, now_ms)), key=lambda p: p.id))


@dataclass(frozen=True, slots=True)
class ReadGrant:
    """A read decision and, when a publication decided it, which one (labels and shared views name it)."""

    decision: ReadDecision
    publication_id: str | None = None


_PUBLISHED: tuple[tuple[PublicationMode, ReadDecision], ...] = (
    ("full", "published_full"),
    ("answer_only", "published_answer_only"),
)


def grant_read(*, own: bool, covering: Iterable[Publication], principal: Principal, now_ms: int) -> ReadGrant:
    """How ``principal`` may read a record: its own access first, then the widest publication covering it; of
    several of that mode, the lowest id (the one ``published_map`` names for the same record)."""
    if own:
        return ReadGrant("own")
    live = admitting(principal, covering, now_ms)
    for mode, decision in _PUBLISHED:
        chosen = next((p for p in live if p.mode == mode), None)
        if chosen is not None:
            return ReadGrant(decision, chosen.id)
    return ReadGrant("denied")


def decide_read(*, own: bool, covering: Iterable[Publication], principal: Principal, now_ms: int) -> ReadDecision:
    return grant_read(own=own, covering=covering, principal=principal, now_ms=now_ms).decision


def permits(decision: ReadDecision, purpose: ReadPurpose) -> bool:
    if decision == "denied":
        return False
    return purpose == "answer" or decision != "published_answer_only"


def require_raw(decision: ReadDecision) -> bool:
    """True when a raw read may go ahead; raises for ``answer_only``; False when simply denied."""
    if decision == "published_answer_only":
        raise PublicationAnswerOnly("this record was published for answers only")
    return decision != "denied"


@dataclass(frozen=True, slots=True)
class Admitted(Generic[V]):
    """A record in scope; ``publication`` is set when only a publication put it there."""

    value: V
    publication: Publication | None = None


@dataclass(frozen=True, slots=True)
class ScopeInputs(Generic[K, V]):
    """Each map is already narrowed by the grant (the request's filters)."""

    principal_own: Mapping[K, V]
    published: Mapping[K, tuple[V, Publication]]
    caller_own: Mapping[K, V] | None = None


def effective_scope(inputs: ScopeInputs[K, V]) -> dict[K, Admitted[V]]:
    """``own ∪ published`` for a principal reading as itself; with ``caller_own`` (an agent retrieving as its
    creator) ``creator ∩ (caller ∪ published(caller))``. Own access wins: such a record names no publication."""
    if inputs.caller_own is None:
        scope: dict[K, Admitted[V]] = {key: Admitted(value, publication) for key, (value, publication) in inputs.published.items()}
        scope.update({key: Admitted(value) for key, value in inputs.principal_own.items()})
        return scope
    delegated: dict[K, Admitted[V]] = {}
    for key, value in inputs.principal_own.items():
        if key in inputs.caller_own:
            delegated[key] = Admitted(value)
        elif key in inputs.published:
            delegated[key] = Admitted(value, inputs.published[key][1])
    return delegated


__all__ = [
    "MAX_AUDIENCE_ENTRIES",
    "MODES",
    "Admitted",
    "Audience",
    "Principal",
    "Publication",
    "PublicationDraft",
    "PublicationMode",
    "ReadDecision",
    "ReadGrant",
    "ReadPurpose",
    "ScopeInputs",
    "admitting",
    "decide_read",
    "grant_read",
    "effective_scope",
    "permits",
    "require_raw",
    "validate_draft",
]
