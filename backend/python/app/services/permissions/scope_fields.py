"""`ScopeFields`: what a vector point carries so a store can filter by permission scope, and the payload keys that
carry it on every store.

Plain data, no I/O. One point (one virtual record) may stand for several records, so the fields of a point are the
`combine` of its records' fields: the combination is exact or the point is `legacy`.

Stamps:
- container: the record's access equals its record group's; the point carries no scope ids (empty lists).
- union / override / intersection: the record departs from its container; `pathIds` say how. `aclScope` holds the record's
  own id (a record its viewer reads by a grant of its own is admitted whatever the path says) and, for union, the seeds on its chain, and for override its nearest
  scope.
- legacy: no exact stamp. The record was refused by `rules.assert_may_stamp`, or its point stands for records that
  cannot share one stamp. It stays on the legacy filter, and its connector is not exact (`rules.connector_is_exact`).
"""

from __future__ import annotations

import os
from collections.abc import Iterable  # noqa: TC003
from dataclasses import dataclass, field
from enum import Enum

#: Bumped when the meaning of a field changes; the backfill rewrites every point stamped with an older version.
SCOPE_FIELDS_VERSION = 6

PATH_IDS_FIELD = "pathIds"
ACL_SCOPE_FIELD = "aclScope"
SCOPE_STAMP_FIELD = "scopeStamp"
SCOPE_VERSION_FIELD = "scopeFieldsVersion"
SCOPE_GENERATION_FIELD = "scopeGeneration"
SCOPE_KEYS = (SCOPE_STAMP_FIELD, PATH_IDS_FIELD, ACL_SCOPE_FIELD, SCOPE_VERSION_FIELD, SCOPE_GENERATION_FIELD)
SCOPE_LIST_KEYS = (PATH_IDS_FIELD, ACL_SCOPE_FIELD)
#: Not a scope field (not in SCOPE_KEYS, read by no filter): the time, in microseconds, taken just before the graph read that a payload write was
#: computed from. A store with ``generation_fence`` refuses a write whose fence is below the one stored; a point with none counts as the oldest.
SCOPE_FENCE_FIELD = "scopeFence"
#: Qdrant's range filter and Redis's script compare a fence as a double: exact only below this.
MAX_FENCE = 2**53


def checked_fence(fence: object) -> int:
    """The fence as given, or ValueError: a non-negative int (not a bool) below ``MAX_FENCE``. A fenced write calls it before anything is
    sent, so a bad fence is an error and never an unconditional write."""
    if isinstance(fence, bool) or not isinstance(fence, int) or not 0 <= fence < MAX_FENCE:
        raise ValueError(f"a fence must be an int from 0 to {MAX_FENCE - 1}, got {fence!r}")
    return fence
ENV_SCOPE_GENERATION = "ACL_SCOPE_GENERATION"


#: Set by `generation.init_scope_generation` once a service has read the shared state: the generation every stamp of this process carries
#: and is judged by. None until then (tests, and a service that never turned the fields on): the optional env value, else 0.
_resolved: dict[str, int | None] = {"generation": None}
UNAVAILABLE_GENERATION = -2  # the shared state could not be read: this process's stamps are never trusted


def set_resolved_generation(value: int | None) -> None:
    _resolved["generation"] = value


def env_scope_generation(env: dict[str, str] | None = None) -> int:
    raw = (env if env is not None else os.environ).get(ENV_SCOPE_GENERATION, "").strip()
    return int(raw) if raw.isdigit() else 0


def current_scope_generation() -> int:
    """The scope generation stamps are written in and trusted by.

    While the fields are off, membership writes leave a point's scope keys as they were, so a point can carry a stamp that a later
    permission change made wrong. Every switch from off to on therefore moves the generation, by itself and exactly once however many
    processes start (`generation.init_scope_generation`), and a stamp of an older generation is stale: the back-fill redoes it and a
    reader must treat it as legacy. `ACL_SCOPE_GENERATION` is an optional floor that forces a bump."""
    resolved = _resolved["generation"]
    return resolved if resolved is not None else env_scope_generation()


def plain_id(value: object) -> bool:
    """An id every store holds and compares as the exact string: non-empty, no comma (the TAG separator of Redis) and no whitespace
    (trimmed at the ends of a TAG, and a tab or newline cannot be escaped in a query) and only printable characters (a NUL or DEL is a
    syntax error in a Redis query). A stamp with another kind of id is not written."""
    text = str(value)
    return bool(text) and text.isprintable() and "," not in text and not any(c.isspace() for c in text)


class Stamp(str, Enum):
    CONTAINER = "container"
    UNION = "union"
    OVERRIDE = "override"
    INTERSECTION = "intersection"
    LEGACY = "legacy"


@dataclass(frozen=True)
class ScopeFields:
    stamp: Stamp
    path_ids: tuple[str, ...] = ()
    acl_scope: tuple[str, ...] = ()
    version: int = SCOPE_FIELDS_VERSION
    generation: int = field(default_factory=current_scope_generation)

    def __post_init__(self) -> None:
        # canonical: sorted and without repeats, so equal fields compare equal whatever order a store gives them back
        for name in ("path_ids", "acl_scope"):
            object.__setattr__(self, name, tuple(sorted(set(getattr(self, name)))))

    @classmethod
    def container(cls) -> ScopeFields:
        return cls(Stamp.CONTAINER)

    @classmethod
    def legacy(cls) -> ScopeFields:
        return cls(Stamp.LEGACY)

    @property
    def exact(self) -> bool:
        return self.stamp is not Stamp.LEGACY

    @property
    def trusted(self) -> bool:
        """What a reader may rely on: an exact stamp of the current meaning, written in the current generation (see
        `current_scope_generation`). Anything else is read through the legacy filter."""
        return self.exact and self.version == SCOPE_FIELDS_VERSION and self.generation >= 0 and self.generation == current_scope_generation()

    @classmethod
    def stale(cls) -> ScopeFields:
        """No exact stamp, and one for the back-fill to redo (version 0)."""
        return cls(Stamp.LEGACY, version=0, generation=0)

    def to_payload(self) -> dict[str, object]:
        """Every key, empty lists included, so a rewrite from an exception stamp to container clears the old ids."""
        return {SCOPE_STAMP_FIELD: self.stamp.value, PATH_IDS_FIELD: list(self.path_ids), ACL_SCOPE_FIELD: list(self.acl_scope),
                SCOPE_VERSION_FIELD: self.version, SCOPE_GENERATION_FIELD: self.generation}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ScopeFields | None:
        """None when the point carries no (or an unreadable) stamp. A stamp written before generations existed has generation -1."""
        try:
            stamp = Stamp(str(payload[SCOPE_STAMP_FIELD]))
            version = int(str(payload[SCOPE_VERSION_FIELD]))
            generation = int(str(payload.get(SCOPE_GENERATION_FIELD, -1)))
        except (KeyError, ValueError):
            return None

        def ids(key: str) -> tuple[str, ...]:
            raw = payload.get(key)
            return tuple(sorted(str(x) for x in raw)) if isinstance(raw, (list, tuple)) else ()

        return cls(stamp, ids(PATH_IDS_FIELD), ids(ACL_SCOPE_FIELD), version, generation)


def combine(fields: Iterable[ScopeFields]) -> ScopeFields:
    """The fields of a point that stands for several records (it is visible to whoever may read any of them).

    Exact only where an OR over the records is: all container, or all override (any readable `aclScope` admits).
    Union and Intersection carry an exclusion over one record's path, which a united path would apply to the other
    record too (it hides what another record admits), and a mix of stamps would let the container clause admit through
    the exception record's groups: those are legacy, as is any legacy record. No records: legacy."""
    items = list(fields)
    if not items or any(not f.exact for f in items):
        return ScopeFields.legacy()
    first = items[0]
    if len(items) == 1:
        return first
    stamps = {f.stamp for f in items}
    if stamps == {Stamp.CONTAINER}:
        return first
    if stamps == {Stamp.OVERRIDE}:
        return ScopeFields(first.stamp, tuple(sorted({i for f in items for i in f.path_ids})),
                           tuple(sorted({i for f in items for i in f.acl_scope})), first.version, first.generation)
    return ScopeFields.legacy()
