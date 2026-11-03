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

from collections.abc import Iterable  # noqa: TC003
from dataclasses import dataclass
from enum import Enum

#: Bumped when the meaning of a field changes; the backfill rewrites every point stamped with an older version.
SCOPE_FIELDS_VERSION = 5

PATH_IDS_FIELD = "pathIds"
ACL_SCOPE_FIELD = "aclScope"
GRANT_TOKENS_FIELD = "grantTokens"
SCOPE_STAMP_FIELD = "scopeStamp"
SCOPE_VERSION_FIELD = "scopeFieldsVersion"
SCOPE_KEYS = (SCOPE_STAMP_FIELD, PATH_IDS_FIELD, ACL_SCOPE_FIELD, GRANT_TOKENS_FIELD, SCOPE_VERSION_FIELD)
#: The id lists, in the order a store maps them to `group_members` kinds 4, 5 and 6.
SCOPE_LIST_KEYS = (PATH_IDS_FIELD, ACL_SCOPE_FIELD, GRANT_TOKENS_FIELD)


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
    grant_tokens: tuple[str, ...] = ()
    version: int = SCOPE_FIELDS_VERSION

    def __post_init__(self) -> None:
        # canonical: sorted and without repeats, so equal fields compare equal whatever order a store gives them back
        for name in ("path_ids", "acl_scope", "grant_tokens"):
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

    def to_payload(self) -> dict[str, object]:
        """Every key, empty lists included, so a rewrite from an exception stamp to container clears the old ids."""
        return {SCOPE_STAMP_FIELD: self.stamp.value, PATH_IDS_FIELD: list(self.path_ids), ACL_SCOPE_FIELD: list(self.acl_scope),
                GRANT_TOKENS_FIELD: list(self.grant_tokens), SCOPE_VERSION_FIELD: self.version}

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> ScopeFields | None:
        """None when the point carries no (or an unreadable) stamp."""
        try:
            stamp = Stamp(str(payload[SCOPE_STAMP_FIELD]))
            version = int(str(payload[SCOPE_VERSION_FIELD]))
        except (KeyError, ValueError):
            return None

        def ids(key: str) -> tuple[str, ...]:
            raw = payload.get(key)
            return tuple(sorted(str(x) for x in raw)) if isinstance(raw, (list, tuple)) else ()

        return cls(stamp, ids(PATH_IDS_FIELD), ids(ACL_SCOPE_FIELD), ids(GRANT_TOKENS_FIELD), version)


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
                           tuple(sorted({i for f in items for i in f.acl_scope})),
                           tuple(sorted({i for f in items for i in f.grant_tokens})), first.version)
    return ScopeFields.legacy()
