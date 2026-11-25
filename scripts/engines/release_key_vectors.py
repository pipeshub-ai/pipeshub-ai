#!/usr/bin/env python3
"""Write the shared test vectors of the release-key checks (R41SUPPLY-FU-6): which keys are usable, and which
revocation lists are applied or refused and why. Python (``harness_core.supply_chain.release_keys``) and Node
(``domain/release-keys.ts``) both run every case, so the two verifiers cannot drift.

    release_key_vectors.py            # write contracts/release-keys/revocation-vectors.json
    release_key_vectors.py --check    # exit 1 when the file is not what this script writes

The keys are throwaway test keys derived from their names (Ed25519 signing is deterministic, so the file is
reproducible); no release key is involved and the private halves are never written.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "contracts/release-keys/revocation-vectors.json"
NOW = "2027-01-01T00:00:00Z"
SCHEMA = "pipeshub.revoked-keys/1"


def _key(name: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(f"pipeshub test vector key {name}".encode()).digest())


def _public(name: str) -> str:
    return base64.b64encode(_key(name).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()


def _sign(name: str, document: str, key_id: str | None = None) -> str:
    sig = base64.b64encode(_key(name).sign(document.encode())).decode()
    return json.dumps({"alg": "Ed25519", "keyId": key_id or f"test-{name}", "sig": sig})


def _doc(issued: str, revoked: list[str], schema: str = SCHEMA) -> str:
    return json.dumps({"schema": schema, "issued": issued, "revoked": revoked})


KEYS = {
    "test-a": {"public": _public("a"), "notAfter": "2028-01-01T00:00:00Z"},
    "test-b": {"public": _public("b"), "notAfter": "2029-01-01T00:00:00Z"},
    "test-x": {"public": _public("x"), "notAfter": "2026-06-01T00:00:00Z"},
}
NONE = {"issued": None, "revoked": []}
T0 = "2026-12-01T00:00:00Z"
T1 = "2026-12-31T00:00:00Z"


def _revocation_cases() -> list[dict[str, object]]:
    cases: list[dict[str, object]] = []

    def case(name: str, document: str | None, signature: str | None, current: dict[str, object], expect: object,
             now: str = NOW) -> None:
        cases.append({"name": name, "document": document, "signature": signature, "current": current, "now": now,
                      "expect": expect})

    doc = _doc(T1, ["test-b"])
    case("a first list, signed by a usable key", doc, _sign("a", doc), NONE, {"issued": T1, "revoked": ["test-b"]})
    doc = _doc(T1, ["test-a"])
    case("a key revokes itself", doc, _sign("a", doc), NONE, {"issued": T1, "revoked": ["test-a"]})
    doc = _doc(T1, ["test-b", "test-x"])
    case("a newer list keeps the earlier revocations and adds one", doc, _sign("a", doc),
         {"issued": T0, "revoked": ["test-b"]}, {"issued": T1, "revoked": ["test-b", "test-x"]})
    doc = _doc(T1, [])
    case("an empty list is a valid list", doc, _sign("b", doc), NONE, {"issued": T1, "revoked": []})
    doc = _doc("2027-01-01T23:59:59Z", ["test-b"])
    case("dated within a day ahead of the clock", doc, _sign("a", doc), NONE,
         {"issued": "2027-01-01T23:59:59Z", "revoked": ["test-b"]})
    doc = _doc(T1, ["test-b"])
    case("no list", None, None, NONE, {"refused": "missing"})
    case("a list with no signature", doc, None, NONE, {"refused": "unsigned"})
    case("a signature that is not one", doc, "not json", NONE, {"refused": "unsigned"})
    case("signed by a key this release does not ship", doc, _sign("c", doc), NONE, {"refused": "unknown_key"})
    case("signed by a key past its not-after", doc, _sign("x", doc), NONE, {"refused": "expired_key"})
    case("signed by a key at its not-after instant", doc, _sign("a", doc), NONE, {"refused": "expired_key"},
         now="2028-01-01T00:00:00Z")
    case("signed by a key the list in force revokes", doc, _sign("a", doc), {"issued": T0, "revoked": ["test-a"]},
         {"refused": "revoked_signer"})
    case("a list changed after signing", _doc(T1, []), _sign("a", doc), NONE, {"refused": "bad_signature"})
    case("signed by another key under a listed id", doc, _sign("b", doc, "test-a"), NONE,
         {"refused": "bad_signature"})
    doc = _doc(T0, ["test-b"])
    case("not newer than the list in force", doc, _sign("a", doc), {"issued": T0, "revoked": ["test-b"]},
         {"refused": "stale"})
    doc = _doc("2026-11-01T00:00:00Z", ["test-b"])
    case("older than the list in force", doc, _sign("a", doc), {"issued": T0, "revoked": []}, {"refused": "stale"})
    doc = _doc("2027-01-02T00:00:01Z", ["test-b"])
    case("dated more than a day ahead", doc, _sign("a", doc), NONE, {"refused": "future"})
    doc = _doc(T1, [])
    case("drops a key the list in force revokes", doc, _sign("a", doc), {"issued": T0, "revoked": ["test-b"]},
         {"refused": "drops_revocation"})
    for label, bad in (
        ("another schema", _doc(T1, ["test-b"], "pipeshub.revoked-keys/2")),
        ("an issued date in another spelling", _doc("2026-12-31", ["test-b"])),
        ("revoked is not a list", json.dumps({"schema": SCHEMA, "issued": T1, "revoked": "test-b"})),
        ("a key id twice", _doc(T1, ["test-b", "test-b"])),
        ("a key id that is not one", _doc(T1, ["Test B"])),
        ("an extra field", json.dumps({"schema": SCHEMA, "issued": T1, "revoked": [], "unrevoked": ["test-b"]})),
        ("not an object", json.dumps([SCHEMA])),
        ("not JSON", "revoked: test-b"),
    ):
        case(f"malformed: {label}", bad, _sign("a", bad), NONE, {"refused": "malformed"})
    return cases


def _usable_cases() -> list[dict[str, object]]:
    return [
        {"name": "nothing revoked: the expired key is out", "revoked": [], "now": NOW, "expect": ["test-a", "test-b"]},
        {"name": "a revoked key is out", "revoked": ["test-b"], "now": NOW, "expect": ["test-a"]},
        {"name": "a key is out at its not-after instant", "revoked": [], "now": "2028-01-01T00:00:00Z",
         "expect": ["test-b"]},
        {"name": "and in a second before", "revoked": [], "now": "2027-12-31T23:59:59Z",
         "expect": ["test-a", "test-b"]},
        {"name": "revoking a key this release does not ship changes nothing", "revoked": ["test-z"], "now": NOW,
         "expect": ["test-a", "test-b"]},
        {"name": "everything revoked or expired", "revoked": ["test-a", "test-b"], "now": NOW, "expect": []},
    ]


def vectors() -> str:
    body = {
        "schema": "pipeshub.release-key-vectors/1",
        "note": "Written by scripts/engines/release_key_vectors.py; throwaway test keys, no release key.",
        "keys": KEYS,
        "usable": _usable_cases(),
        "revocations": _revocation_cases(),
    }
    return json.dumps(body, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = vectors()
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != text:
            sys.stderr.write(f"{OUT.relative_to(REPO)} is stale: run scripts/engines/release_key_vectors.py\n")
            return 1
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
