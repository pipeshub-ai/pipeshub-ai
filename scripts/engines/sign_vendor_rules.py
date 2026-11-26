#!/usr/bin/env python3
"""Sign the vendor rules (A1, D30) and their sign-in hosts (SIGNIN), and write Node's mirror of the rules; sign the
ACP agent profiles (ACP-driver) at a release build or for a test stack (ADR-SEC-01 D7).

    sign_vendor_rules.py --key RELEASE_KEY.pem --key-id ID    # the release ceremony: rules, extensions and hosts,
                                                              # then mirror
    sign_vendor_rules.py --key RELEASE_KEY.pem --key-id ID --acp-out PATH
                                                              # a release build: the ACP profiles' signature only
    sign_vendor_rules.py --test-root DIR                      # a test stack: a fresh test key, its root and signature
    sign_vendor_rules.py --mirror-only                        # re-write the Node mirror from the files

The release key's private half lives in the release secret store, never in the
repository. Its public half (raw 32 bytes, base64) must already be listed under
``ID`` in ``RELEASE_KEYS`` of ``backend/python/harness_engines/vendor_rules.py``
and in ``RELEASE_KEYS`` of the Node mirror, or both loaders refuse the signature
(``unknown_key``); this tool refuses to sign with a key they do not list. The
signature is over the file's exact bytes, so ``openssl pkeyutl -verify -rawin``
checks it too.

The ACP profiles are never signed in the tree: they change often, so a signature there would go stale, and the
release key must not sit on a developer's host. ``--acp-out`` writes the release build's signature (by default next
to the document, ``acp_agents.json.sig``, which git ignores) before the image is built. ``--test-root`` makes a key
in memory, writes its public half (``trust-root.json``) and its signature of the profiles to ``DIR`` and forgets the
private half: a test stack mounts ``DIR`` read-only and names it in ``PIPESHUB_TEST_TRUST_ROOT``
(``harness_engines.profile_trust``).
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = Path(__file__).resolve().parents[2]
RULES = REPO / "backend/python/harness_engines/vendor_rules.json"
SIGNATURE = REPO / "backend/python/harness_engines/vendor_rules.json.sig"
# An edition's signed extension documents beside the rules (harness_engines.vendor_rules.EXTENSION_PATTERN).
EXTENSION_PATTERN = "vendor_rules.*.json"
# Signed in the same ceremony with the same key: where a signed-in engine's sandbox may reach (SIGNIN §2.5).
HOSTS = REPO / "backend/python/harness_engines/vendor_hosts.json"
HOSTS_SIGNATURE = REPO / "backend/python/harness_engines/vendor_hosts.json.sig"
# The long-tail coding agents the generic ACP driver runs: signed by a release build or a test stack, never in the
# tree (harness_engines.acp.registry).
ACP_AGENTS = REPO / "backend/python/harness_engines/acp_agents.json"
ACP_AGENTS_SIGNATURE = REPO / "backend/python/harness_engines/acp_agents.json.sig"
# harness_engines.profile_trust: the test root's file names, schema and key id prefix.
TEST_ROOT_FILE = "trust-root.json"
TEST_ROOT_SIGNATURE = "acp_agents.json.sig"
TEST_ROOT_SCHEMA = "pipeshub.test-trust-root/1"
TEST_KEY_PREFIX = "test-"
PYTHON_KEYS = REPO / "backend/python/harness_engines/vendor_rules.py"
NODE_MIRROR = REPO / "backend/nodejs/apps/src/modules/agent-harness/domain/vendor-rules-release.ts"
_KEY = re.compile(r'"([a-z0-9-]+)":\s*base64\.b64decode\("([A-Za-z0-9+/=]+)"\)')
_NOT_AFTER = re.compile(r'"([a-z0-9-]+)":\s*"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)"')
# The release's revocation list (R41SUPPLY-FU-6), when one ships: Node starts from it as Python does.
REVOCATIONS = REPO / "backend/python/harness_engines/revoked_keys.json"
REVOCATIONS_SIGNATURE = REPO / "backend/python/harness_engines/revoked_keys.json.sig"


def extensions() -> list[Path]:
    return sorted(RULES.parent.glob(EXTENSION_PATTERN))


def _signature_of(document: Path) -> Path:
    return document.with_name(f"{document.name}.sig")


def release_keys() -> dict[str, str]:
    return dict(_KEY.findall(PYTHON_KEYS.read_text(encoding="utf-8")))


def release_key_not_after() -> dict[str, str]:
    return dict(_NOT_AFTER.findall(PYTHON_KEYS.read_text(encoding="utf-8")))


def _text_or_null(path: Path) -> str:
    return json.dumps(path.read_text(encoding="utf-8")) if path.is_file() else "null"


def _extension_entries() -> str:
    rows = "".join(
        f"  Object.freeze({{ text: {json.dumps(path.read_text(encoding='utf-8'))}, "
        f"signature: {_text_or_null(_signature_of(path))} }}),\n"
        for path in extensions()
    )
    return f"Object.freeze([\n{rows}])" if rows else "Object.freeze([])"


def node_mirror(document: str, signature: str, keys: dict[str, str]) -> str:
    entries = "\n".join(f"  '{key_id}': '{public}'," for key_id, public in sorted(keys.items()))
    not_after = "\n".join(f"  '{key_id}': '{when}'," for key_id, when in sorted(release_key_not_after().items()))
    return (
        "// Written by scripts/engines/sign_vendor_rules.py from backend/python/harness_engines/vendor_rules.json(.sig):\n"
        "// do not edit. The Node image ships compiled code only, so the release data travels as text.\n"
        f"export const VENDOR_RULES_TEXT: string = {json.dumps(document)};\n\n"
        f"export const VENDOR_RULES_SIGNATURE_TEXT: string = {json.dumps(signature)};\n\n"
        "/** An edition's signed extension documents (vendor_rules.<name>.json), in name order; none in this repository. */\n"
        "export const VENDOR_RULE_EXTENSIONS: ReadonlyArray<{ readonly text: string; readonly signature: string | null }> =\n"
        f"  {_extension_entries()};\n\n"
        "/** Raw Ed25519 public keys (base64) by id; the same as Python's `RELEASE_KEYS`. */\n"
        "export const RELEASE_KEYS: Readonly<Record<string, string>> = Object.freeze({\n"
        f"{entries}\n"
        "});\n\n"
        "/** Each key's not-after; the same as Python's `RELEASE_KEY_NOT_AFTER` (R41SUPPLY-FU-6). */\n"
        "export const RELEASE_KEY_NOT_AFTER: Readonly<Record<string, string>> = Object.freeze({\n"
        f"{not_after}\n"
        "});\n\n"
        "/** The release's signed revocation list and its signature, when it ships one. */\n"
        f"export const REVOKED_KEYS_TEXT: string | null = {_text_or_null(REVOCATIONS)};\n\n"
        f"export const REVOKED_KEYS_SIGNATURE_TEXT: string | null = {_text_or_null(REVOCATIONS_SIGNATURE)};\n"
    )


def signature(private: Ed25519PrivateKey, document: bytes, key_id: str) -> str:
    """The detached signature document (OWD-8's format) of ``document`` by the Ed25519 key ``private``."""
    sig = base64.b64encode(private.sign(document)).decode()
    return json.dumps({"alg": "Ed25519", "keyId": key_id, "sig": sig}) + "\n"


def public_half(private: Ed25519PrivateKey) -> str:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode()


def release_key(key_path: Path, key_id: str) -> Ed25519PrivateKey:
    """The release key at ``key_path``, refused unless ``RELEASE_KEYS`` lists its public half under ``key_id``."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    private = load_pem_private_key(key_path.read_bytes(), password=None)
    if not isinstance(private, Ed25519PrivateKey):
        sys.exit("the release key is not an Ed25519 key")
    public = public_half(private)
    if release_keys().get(key_id) != public:
        sys.exit(f"add {key_id!r}: {public!r} to RELEASE_KEYS (Python and Node) before signing with it")
    return private


def sign(key_path: Path, key_id: str) -> None:
    private = release_key(key_path, key_id)
    for document, signed in ((RULES, SIGNATURE), (HOSTS, HOSTS_SIGNATURE), *((e, _signature_of(e)) for e in extensions())):
        signed.write_text(signature(private, document.read_bytes(), key_id), encoding="utf-8")


def sign_acp(key_path: Path, key_id: str, out: Path) -> None:
    out.write_text(signature(release_key(key_path, key_id), ACP_AGENTS.read_bytes(), key_id), encoding="utf-8")


def make_test_root(folder: Path, key_id: str | None = None) -> str:
    """A fresh test key's root and its signature of the ACP profiles in ``folder``; the private half is never
    written. Returns the key id."""
    import secrets

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key_id = key_id or f"{TEST_KEY_PREFIX}{secrets.token_hex(6)}"
    if not key_id.startswith(TEST_KEY_PREFIX) or key_id in release_keys():
        sys.exit(f"a test key's id starts with {TEST_KEY_PREFIX!r} and is no release key's, got {key_id!r}")
    private = Ed25519PrivateKey.generate()
    folder.mkdir(parents=True, exist_ok=True)
    root = {"schema": TEST_ROOT_SCHEMA, "keyId": key_id, "publicKey": public_half(private)}
    (folder / TEST_ROOT_FILE).write_text(json.dumps(root) + "\n", encoding="utf-8")
    (folder / TEST_ROOT_SIGNATURE).write_text(signature(private, ACP_AGENTS.read_bytes(), key_id), encoding="utf-8")
    return key_id


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--key-id")
    parser.add_argument("--acp-out", type=Path, nargs="?", const=ACP_AGENTS_SIGNATURE)
    parser.add_argument("--test-root", type=Path)
    parser.add_argument("--mirror-only", action="store_true")
    args = parser.parse_args()
    if args.test_root is not None:
        sys.stdout.write(make_test_root(args.test_root, args.key_id) + "\n")
        return
    if not args.mirror_only:
        if args.key is None or not args.key_id:
            parser.error("--key and --key-id, --test-root DIR, or --mirror-only")
        if args.acp_out is not None:
            sign_acp(args.key, args.key_id, args.acp_out)
            return
        sign(args.key, args.key_id)
    mirror = node_mirror(RULES.read_text(encoding="utf-8"), SIGNATURE.read_text(encoding="utf-8"), release_keys())
    NODE_MIRROR.write_text(mirror, encoding="utf-8")


if __name__ == "__main__":
    main()
