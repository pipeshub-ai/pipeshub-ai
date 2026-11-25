"""The release keys in force in this process (R41SUPPLY-FU-6, ADR-SEC-06 D1): ``RELEASE_KEYS`` with each key's
not-after, less what the newest signed revocation list this process has seen revokes.

The packaged list (``revoked_keys.json`` and its ``.sig``, when a release ships one) is the floor; a newer list reaches
the process through ``apply_revocations``, which ``app.harness.supply_chain.revocations`` calls with the list it reads
from the deployment's store or fetches. Nothing here reads the environment or a file outside the package, so the keys
stay code (OWD-8): a deployment can narrow them with a list a release key signed, never widen them.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Final, cast

from harness_core.supply_chain.release_keys import (
    NO_REVOCATIONS,
    ReleaseKey,
    RevocationRefused,
    Revocations,
    parse_instant,
    usable_keys,
    verify_revocations,
)
from harness_engines.vendor_rules import RELEASE_KEY_NOT_AFTER, RELEASE_KEYS, packaged

REVOCATIONS_RESOURCE: Final = "revoked_keys.json"
REVOCATIONS_SIGNATURE_RESOURCE: Final = "revoked_keys.json.sig"


class _InForce:
    """The list in force in this process, behind one lock."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state: Revocations | None = None


_IN_FORCE: Final = _InForce()


def release_key_set() -> dict[str, ReleaseKey]:
    keys: dict[str, ReleaseKey] = {}
    for key_id, public in RELEASE_KEYS.items():
        not_after = parse_instant(RELEASE_KEY_NOT_AFTER.get(key_id))
        # A key with no readable not-after is not a release key: the table test refuses it, and so does this.
        if not_after is not None:
            keys[key_id] = ReleaseKey(public, not_after)
    return keys


def _now(now: datetime | None) -> datetime:
    return now if now is not None else datetime.now(UTC)


def revocations() -> Revocations:
    """The list in force: the packaged one until a newer one is applied."""
    with _IN_FORCE.lock:
        if _IN_FORCE.state is None:
            try:
                _IN_FORCE.state = verify_revocations(
                    packaged(REVOCATIONS_RESOURCE), packaged(REVOCATIONS_SIGNATURE_RESOURCE), release_key_set(),
                    NO_REVOCATIONS, _now(None),
                )
            except RevocationRefused:
                _IN_FORCE.state = NO_REVOCATIONS
        return _IN_FORCE.state


def apply_revocations(document: bytes | None, signature: bytes | None, now: datetime | None = None) -> Revocations:
    """Puts ``document`` in force when it is a newer valid list and answers it; raises ``RevocationRefused`` (and keeps
    the list in force) otherwise."""
    proposed = verify_revocations(document, signature, release_key_set(), revocations(), _now(now))
    with _IN_FORCE.lock:
        held = _IN_FORCE.state
        # Another caller may have applied a newer list meanwhile; the newer one stays.
        if held is None or held.issued is None or (proposed.issued is not None and proposed.issued > held.issued):
            _IN_FORCE.state = proposed
        return cast("Revocations", _IN_FORCE.state)


def trusted_release_keys(now: datetime | None = None) -> dict[str, bytes]:
    """Key id -> raw public key: ``RELEASE_KEYS`` neither revoked nor past their not-after at ``now``."""
    return usable_keys(release_key_set(), revocations(), _now(now))


def reset_revocations_for_tests() -> None:
    with _IN_FORCE.lock:
        _IN_FORCE.state = None


__all__ = [
    "REVOCATIONS_RESOURCE",
    "REVOCATIONS_SIGNATURE_RESOURCE",
    "apply_revocations",
    "release_key_set",
    "reset_revocations_for_tests",
    "revocations",
    "trusted_release_keys",
]
