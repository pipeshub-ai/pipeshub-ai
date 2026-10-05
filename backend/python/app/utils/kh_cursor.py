"""The opaque paging cursor for the knowledge hub listing.

**Keyset, not offset.** The cursor stores the boundary row's own sort key. An
offset per partition cannot give an exact previous page once rows are merged
from several partitions (the earlier page is not the previous ``limit`` rows of
any one of them), and it drifts when rows are inserted or deleted mid-walk.

The stored key is **the comparator's own output** (``sortKey`` and
``nullRank``), as the query computed it, plus the row id as the tiebreak. That
is what keeps the query and the in-process merge using one comparator rather
than two implementations that happen to agree today.

**One boundary for every partition.** Every partition sorts on the same
comparator, and a node found in two partitions sits at the same position in
both, so a page's position is a single boundary — its last row going forward,
its first row going back — and each partition resumes from that same point.
A boundary per partition would need a partition that contributed nothing to a
page to carry its old boundary forward, and a previous page from it to include
the boundary row itself: two edges where rows are silently skipped or repeated.

The cursor also says which partitions may still have rows, so a finished one is
not queried again, as a **bitset over the partition order discovery returns**
plus a fingerprint of that list. Naming them by id does not fit: a Drive tenant
has a top-level group per user, so the id list runs to tens of kilobytes and a
cursor that travels in a URL stops working at exactly the scale where skipping
finished partitions matters. If the fingerprint does not match (the user gained
or lost access mid-walk) the bits are meaningless and every current partition
is queried instead, which the shared boundary keeps exact. The service
re-checks partitions against the user's current access on every page either
way.

**Every failure is a 400.** A cursor that does not parse, does not verify, or
does not belong to this user is never repaired, clamped, or silently treated as
"first page" — each of those turns a bug or an attack into a plausible-looking
result. Callers translate `CursorError` into an error response and nothing else.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

# Version 1 carried a boundary per partition; such a cursor is refused rather
# than reinterpreted.
_VERSION = 2

# Wire keys are short because the cursor travels in a query string, and long
# ones cost more than they explain. The mapping lives here and nowhere else.
_B_SORT_KEY, _B_NULL_RANK, _B_LAST_ID = "sk", "nr", "id"

_CURSOR_KEY_LABEL = b"kh-cursor-v1"


class CursorError(ValueError):
    """The cursor is unusable. The caller must answer 400, never fall back."""


def derive_cursor_secret(scoped_jwt_secret: str | bytes) -> bytes:
    """The cursor-signing key, derived from the shared ``scopedJwtSecret``.

    Deriving under a fixed label keeps cursors from ever sharing a key with the
    JWTs and signed URLs that secret already signs, without adding a config
    value every install would need. A missing secret is a server fault, not a
    bad cursor, so it raises ``ValueError`` rather than ``CursorError``.
    """
    if not scoped_jwt_secret:
        raise ValueError("no scopedJwtSecret configured to derive the cursor key from")
    key = scoped_jwt_secret.encode("utf-8") if isinstance(scoped_jwt_secret, str) else scoped_jwt_secret
    return hmac.new(key, _CURSOR_KEY_LABEL, sha256).digest()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError) as exc:
        raise CursorError(f"cursor is not valid base64url: {exc}") from exc


@dataclass(frozen=True)
class Boundary:
    """Where a page stopped, as the query's own comparator output for its edge row.

    ``last_id`` breaks ties and is what makes the keyset predicate total —
    without it two rows with an equal sort key could be skipped or repeated.
    """

    null_rank: int
    sort_key: Any
    last_id: str

    @classmethod
    def of(cls, row: dict) -> "Boundary":
        return cls(null_rank=row["nullRank"], sort_key=row.get("sortKey"), last_id=row["id"])

    def as_after(self) -> dict:
        """The keyset boundary in the shape the v2 provider methods take."""
        return {"nullRank": self.null_rank, "sortKey": self.sort_key, "id": self.last_id}

    def to_wire(self) -> dict:
        return {_B_SORT_KEY: self.sort_key, _B_NULL_RANK: self.null_rank, _B_LAST_ID: self.last_id}

    @classmethod
    def from_wire(cls, raw: Any) -> "Boundary":
        if not isinstance(raw, dict):
            raise CursorError("boundary is not an object")
        try:
            null_rank = raw[_B_NULL_RANK]
            last_id = raw[_B_LAST_ID]
        except KeyError as exc:
            raise CursorError(f"boundary is missing {exc.args[0]!r}") from exc
        if not isinstance(last_id, str):
            raise CursorError("boundary id must be a string")
        # bool is an int subclass, and a bool here would sort in a way the
        # query's integer rank never produces.
        if isinstance(null_rank, bool) or not isinstance(null_rank, int):
            raise CursorError("null rank must be an integer")
        return cls(null_rank=null_rank, sort_key=raw.get(_B_SORT_KEY), last_id=last_id)


def _fingerprint(partition_ids: Sequence[str]) -> str:
    return sha256("\n".join(partition_ids).encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class LivePartitions:
    """Which partitions may still have rows, positionally against the search's list.

    A position is only meaningful against the same list it was recorded from,
    so the fingerprint is not an optimisation — without it, a partition the user
    gained access to would shift every later bit by one and silently skip a
    partition's worth of results.
    """

    fingerprint: str
    bits: bytes

    @classmethod
    def of(cls, partition_ids: Sequence[str], live: Collection[str]) -> "LivePartitions":
        raw = bytearray((len(partition_ids) + 7) // 8)
        for index, partition_id in enumerate(partition_ids):
            if partition_id in live:
                raw[index // 8] |= 1 << (index % 8)
        return cls(fingerprint=_fingerprint(partition_ids), bits=bytes(raw))

    def select(self, partition_ids: Sequence[str]) -> list[str] | None:
        """The live partitions, or `None` when the bits no longer apply.

        `None` means "query them all": correct, just less selective.
        """
        if _fingerprint(partition_ids) != self.fingerprint:
            return None
        return [
            partition_id
            for index, partition_id in enumerate(partition_ids)
            if self.bits[index // 8] & (1 << (index % 8))
        ]

    def to_wire(self) -> dict:
        return {"h": self.fingerprint, "b": _b64e(self.bits)}

    @classmethod
    def from_wire(cls, raw: Any) -> "LivePartitions":
        if not isinstance(raw, dict):
            raise CursorError("live partitions are not an object")
        fingerprint, bits = raw.get("h"), raw.get("b")
        if not isinstance(fingerprint, str) or not isinstance(bits, str):
            raise CursorError("live partitions are malformed")
        return cls(fingerprint=fingerprint, bits=_b64d(bits))


@dataclass(frozen=True)
class KnowledgeHubCursor:
    """One page boundary, plus the partitions still in play in its direction.

    ``direction`` says which side of the boundary the requested page lies on, so
    one format serves both ``nextCursor`` and ``prevCursor``: the boundary is
    the page's last row for "next" and its first row for "prev".

    ``live`` is absent on a previous-page cursor: a partition that ran out going
    forward can still hold rows *behind* the boundary, so going back queries
    every partition the search covers.
    """

    boundary: Boundary
    live: LivePartitions | None = None
    direction: str = "next"
    items_seen: int = 0
    total: int | None = None
    counts_by_type: dict[str, int] | None = None
    filters: dict[str, Any] | None = None
    sort_by: str | None = None
    sort_order: str | None = None
    parent_id: str | None = None
    parent_type: str | None = None
    via_parent_id: str | None = None
    # Which listing issued it; with the parent, the place it may be replayed at.
    mode: str | None = None
    user_id: str | None = None
    org_id: str | None = None
    issued_at_ms: int = 0

    def to_wire(self) -> dict:
        wire: dict[str, Any] = {
            "v": _VERSION,
            "d": self.direction,
            "b": self.boundary.to_wire(),
            "sn": self.items_seen,
            "ts": self.issued_at_ms or int(time.time() * 1000),
        }
        # Absent rather than null: the cursor is repeated on every page and the
        # unset fields are the common case.
        for key, value in (
            ("p", self.live.to_wire() if self.live else None),
            ("tc", self.total), ("cb", self.counts_by_type), ("f", self.filters),
            ("sb", self.sort_by), ("so", self.sort_order),
            ("pi", self.parent_id), ("pt", self.parent_type),
            ("vp", self.via_parent_id), ("m", self.mode),
            ("u", self.user_id), ("o", self.org_id),
        ):
            if value is not None:
                wire[key] = value
        return wire

    @classmethod
    def from_wire(cls, wire: Any) -> "KnowledgeHubCursor":
        if not isinstance(wire, dict):
            raise CursorError("cursor payload is not an object")
        if wire.get("v") != _VERSION:
            raise CursorError(f"unsupported cursor version {wire.get('v')!r}")
        direction = wire.get("d", "next")
        if direction not in ("next", "prev"):
            raise CursorError(f"unknown cursor direction {direction!r}")
        if "b" not in wire:
            raise CursorError("cursor carries no boundary")
        items_seen = wire.get("sn", 0)
        if isinstance(items_seen, bool) or not isinstance(items_seen, int) or items_seen < 0:
            raise CursorError("items seen must be a non-negative integer")
        return cls(
            boundary=Boundary.from_wire(wire["b"]),
            live=LivePartitions.from_wire(wire["p"]) if wire.get("p") is not None else None,
            direction=direction,
            items_seen=items_seen,
            total=wire.get("tc"),
            counts_by_type=wire.get("cb"),
            filters=wire.get("f"),
            sort_by=wire.get("sb"),
            sort_order=wire.get("so"),
            parent_id=wire.get("pi"),
            parent_type=wire.get("pt"),
            via_parent_id=wire.get("vp"),
            mode=wire.get("m"),
            user_id=wire.get("u"),
            org_id=wire.get("o"),
            issued_at_ms=wire.get("ts", 0),
        )


def encode(cursor: KnowledgeHubCursor, secret: str | bytes | None = None) -> str:
    """Serialise to the wire form ``payload`` or ``payload.signature``."""
    raw = json.dumps(
        cursor.to_wire(), separators=(",", ":"), sort_keys=True, default=str
    ).encode("utf-8")
    payload = _b64e(raw)
    if secret is None:
        return payload
    return f"{payload}.{_b64e(_mac(payload, secret))}"


def decode(
    token: str,
    secret: str | bytes | None = None,
    *,
    expected_user_id: str | None = None,
    expected_org_id: str | None = None,
    max_age_seconds: int | None = None,
) -> KnowledgeHubCursor:
    """Parse and validate, or raise `CursorError`.

    A signature and a secret must agree about existing — accepting an unsigned
    token while holding a secret would let a caller strip the signature and edit
    the payload. The binding checks below are what stop one user replaying
    another's cursor.
    """
    if not isinstance(token, str) or not token:
        raise CursorError("cursor is empty")
    if not token.isascii():
        raise CursorError("cursor is not valid base64url")

    payload, _, signature = token.partition(".")
    if secret is None:
        if signature:
            raise CursorError("cursor is signed but no signing secret is configured")
    else:
        if not signature:
            raise CursorError("cursor is unsigned")
        if not hmac.compare_digest(_b64d(signature), _mac(payload, secret)):
            raise CursorError("cursor signature does not verify")

    try:
        wire = json.loads(_b64d(payload))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CursorError(f"cursor payload is not valid JSON: {exc}") from exc

    cursor = KnowledgeHubCursor.from_wire(wire)

    # Binding: a cursor names partitions the issuing user could reach, so
    # replaying it as someone else would page through their partition list.
    if expected_user_id is not None and cursor.user_id != expected_user_id:
        raise CursorError("cursor was issued for a different user")
    if expected_org_id is not None and cursor.org_id != expected_org_id:
        raise CursorError("cursor was issued for a different organization")
    if max_age_seconds is not None:
        age_ms = int(time.time() * 1000) - cursor.issued_at_ms
        if cursor.issued_at_ms <= 0 or age_ms > max_age_seconds * 1000:
            raise CursorError("cursor has expired")
    return cursor


def _mac(payload: str, secret: str | bytes) -> bytes:
    key = secret.encode("utf-8") if isinstance(secret, str) else secret
    return hmac.new(key, payload.encode("ascii"), sha256).digest()
