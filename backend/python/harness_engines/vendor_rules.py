"""Release data for model access: the signed vendor rules (A1, D30).

``vendor_rules.json`` is the ARCHITECTURE §4.6.3 table as data and
``vendor_rules.json.sig`` its detached Ed25519 signature. Both ship in the
package; the public key is this module's code, never configuration, so a
deployment can neither swap the rules nor the key that vouches for them.
Node verifies the same bytes against the same key
(``domain/vendor-rules-release.ts``).

An edition that adds an offering ships, beside them, a signed extension
document ``vendor_rules.<name>.json`` (and its ``.sig``) naming the rows that
offering may use (``extend_vendor_rules``). This repository ships none, and an
extension refused for any reason refuses the whole release, as the rules do.
"""

from __future__ import annotations

import base64
import fnmatch
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Final

from harness_core.access.vendor_rules import (
    extend_vendor_rules,
    verify_release_document,
    verify_vendor_rules,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.access.vendor_rules import VendorRules

RULES_RESOURCE: Final = "vendor_rules.json"
SIGNATURE_RESOURCE: Final = "vendor_rules.json.sig"
EXTENSION_PATTERN: Final = "vendor_rules.*.json"
# Raw Ed25519 public keys by id. A release adds its key here (and in Node) before signing with it.
# A retired key is removed, not kept: an older signed document must not verify again (a downgrade).
RELEASE_KEYS: Final[Mapping[str, bytes]] = {
    # The detached signature beside the rules (vendor_rules.json.sig) verifies against this public key.
    "pipeshub-release-2026-11-10": base64.b64decode("ZL1FtQx5zyxF/DGUE1MnCx5jESL9+bjlW8uwfxfy4FU="),
}
# Each key's not-after (R41SUPPLY-FU-6): from then nothing it signed verifies, so a stolen key expires on its own on an
# install that never hears of the theft. At most three years after the key was made; a release ships the next key well
# before. Node's mirror carries the same table (sign_vendor_rules.py writes it).
RELEASE_KEY_NOT_AFTER: Final[Mapping[str, str]] = {
    "pipeshub-release-2026-11-10": "2029-11-10T00:00:00Z",
}


def packaged(name: str) -> bytes | None:
    resource = resources.files("harness_engines").joinpath(name)
    return resource.read_bytes() if resource.is_file() else None


def load_vendor_rules(document: bytes | None, signature: bytes | None) -> VendorRules:
    """Verified against the release keys in force (not revoked, not past their not-after) only; raises
    ``VendorRulesInvalid``."""
    from harness_engines.release_keys import trusted_release_keys

    return verify_vendor_rules(document, signature, trusted_release_keys())


def extension_resources() -> tuple[str, ...]:
    """The packaged extension documents' names, in name order."""
    folder = resources.files("harness_engines")
    return tuple(sorted(e.name for e in folder.iterdir() if e.is_file() and fnmatch.fnmatchcase(e.name, EXTENSION_PATTERN)))


@cache
def _release(trusted: frozenset[str]) -> VendorRules:
    del trusted
    from harness_engines.release_keys import trusted_release_keys

    rules = load_vendor_rules(packaged(RULES_RESOURCE), packaged(SIGNATURE_RESOURCE))
    keys = trusted_release_keys()
    for name in extension_resources():
        rules = extend_vendor_rules(rules, verify_release_document(packaged(name), packaged(f"{name}.sig"), keys))
    return rules


def release_vendor_rules() -> VendorRules:
    """The packaged rules, verified once per set of keys in force; a refusal is raised again at every call, and a key
    revoked or expired since is checked again."""
    from harness_engines.release_keys import trusted_release_keys

    return _release(frozenset(trusted_release_keys()))


__all__ = [
    "RELEASE_KEYS",
    "RELEASE_KEY_NOT_AFTER",
    "EXTENSION_PATTERN",
    "RULES_RESOURCE",
    "SIGNATURE_RESOURCE",
    "extension_resources",
    "load_vendor_rules",
    "packaged",
    "release_vendor_rules",
]
