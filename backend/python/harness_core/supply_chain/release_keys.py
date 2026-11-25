"""Which release keys may vouch for something now (R41SUPPLY-FU-6, ADR-SEC-06 D1): each key's not-after, and the
signed list of revoked keys.

A release key is trusted until its ``not_after`` instant, so a stolen key stops working on its own, even on an install
that never hears of the theft. A revocation list (``pipeshub.revoked-keys/1``) is signed with a release key like other
release data, and only ever narrows trust:

- it is applied only when it is newer (``issued``) than the one in force, at most a day ahead of the clock, and keeps
  every key the one in force revoked, so neither an old list nor a later one can bring a key back;
- its signer must be trusted under the list in force (listed, not expired, not already revoked), but may revoke itself:
  a release with one key can only revoke that key with it.

Pure: Node mirrors it (``domain/release-keys.ts``) and both pass ``contracts/release-keys/revocation-vectors.json``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal, cast

from harness_core.supply_chain.signatures import (
    SignatureRefused,
    parse_signature,
    verify_detached,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

REVOCATIONS_SCHEMA: Final = "pipeshub.revoked-keys/1"
#: How far ahead of this host's clock a list may be dated (clock skew between the signer and the host).
FUTURE_SKEW: Final = timedelta(days=1)
MAX_REVOKED: Final = 256
_INSTANT: Final = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_KEY_ID: Final = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

RevocationReason = Literal[
    "missing", "unsigned", "unknown_key", "bad_signature", "expired_key", "revoked_signer", "malformed", "stale",
    "future", "drops_revocation",
]


class RevocationRefused(ValueError):
    def __init__(self, reason: RevocationReason, message: str) -> None:
        super().__init__(message)
        self.reason: RevocationReason = reason


@dataclass(frozen=True, slots=True)
class ReleaseKey:
    public: bytes
    not_after: datetime


@dataclass(frozen=True, slots=True)
class Revocations:
    """The list in force: when it was issued (``None`` before any) and the key ids it revokes."""

    issued: datetime | None
    revoked: frozenset[str]


NO_REVOCATIONS: Final = Revocations(None, frozenset())


def parse_instant(value: object) -> datetime | None:
    """``YYYY-MM-DDTHH:MM:SSZ`` only, the one spelling both languages read the same."""
    if not isinstance(value, str) or not _INSTANT.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def format_instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def key_usable(key: ReleaseKey, now: datetime) -> bool:
    return now < key.not_after


def usable_keys(keys: Mapping[str, ReleaseKey], revocations: Revocations, now: datetime) -> dict[str, bytes]:
    """Key id -> raw public key, for the keys neither revoked nor past their not-after at ``now``."""
    return {
        key_id: key.public
        for key_id, key in keys.items()
        if key_id not in revocations.revoked and key_usable(key, now)
    }


def _parse(document: bytes) -> Revocations:
    try:
        raw: object = json.loads(document)
    except (ValueError, UnicodeDecodeError):
        raise RevocationRefused("malformed", "the revocation list is not JSON") from None
    if not isinstance(raw, dict) or set(cast("dict[str, object]", raw)) != {"schema", "issued", "revoked"}:
        raise RevocationRefused("malformed", "the revocation list is not a pipeshub.revoked-keys/1 document")
    body = cast("dict[str, object]", raw)
    issued, revoked = parse_instant(body["issued"]), body["revoked"]
    if body["schema"] != REVOCATIONS_SCHEMA or issued is None or not isinstance(revoked, list):
        raise RevocationRefused("malformed", "the revocation list is not a pipeshub.revoked-keys/1 document")
    ids = cast("list[object]", revoked)
    if (
        len(ids) > MAX_REVOKED
        or not all(isinstance(i, str) and _KEY_ID.match(i) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise RevocationRefused("malformed", "the revocation list's key ids are not distinct release key ids")
    return Revocations(issued, frozenset(cast("list[str]", ids)))


def verify_revocations(
    document: bytes | None,
    signature: bytes | None,
    keys: Mapping[str, ReleaseKey],
    current: Revocations,
    now: datetime,
) -> Revocations:
    """The revocations ``document`` puts in force over ``current``, or ``RevocationRefused`` naming why not."""
    if document is None:
        raise RevocationRefused("missing", "there is no revocation list")
    if signature is None:
        raise RevocationRefused("unsigned", "the revocation list is not signed")
    try:
        key_id, _ = parse_signature(signature)
    except SignatureRefused as refused:
        raise RevocationRefused("unsigned", str(refused)) from None
    key = keys.get(key_id)
    if key is None:
        raise RevocationRefused("unknown_key", "the revocation list is signed by a key this release does not ship")
    if not key_usable(key, now):
        raise RevocationRefused("expired_key", "the revocation list is signed by a key past its not-after")
    if key_id in current.revoked:
        raise RevocationRefused("revoked_signer", "the revocation list is signed by a key already revoked")
    try:
        _ = verify_detached(document, signature, {key_id: key.public})
    except SignatureRefused:
        raise RevocationRefused("bad_signature", "the revocation list's signature does not verify") from None
    proposed = _parse(document)
    issued = cast("datetime", proposed.issued)
    if current.issued is not None and issued <= current.issued:
        raise RevocationRefused("stale", "the revocation list is not newer than the one in force")
    if issued > now + FUTURE_SKEW:
        raise RevocationRefused("future", "the revocation list is dated more than a day ahead of this host's clock")
    if not current.revoked <= proposed.revoked:
        raise RevocationRefused("drops_revocation", "the revocation list drops a key the one in force revokes")
    return proposed


__all__ = [
    "FUTURE_SKEW",
    "NO_REVOCATIONS",
    "REVOCATIONS_SCHEMA",
    "ReleaseKey",
    "RevocationReason",
    "RevocationRefused",
    "Revocations",
    "format_instant",
    "key_usable",
    "parse_instant",
    "usable_keys",
    "verify_revocations",
]
