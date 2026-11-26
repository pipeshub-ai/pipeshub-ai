"""Which ACP agent profiles this install trusts (ADR-SEC-01 D7). Three operator settings, each its own decision:

- ``PIPESHUB_ACP_PROFILES_UNSIGNED`` (dev only, exactly ``true``): read the packaged profiles while they carry no
  signature. It no longer follows the gVisor dev override: running engines below gVisor and running profiles nobody
  signed are unrelated trust decisions.
- ``PIPESHUB_TEST_TRUST_ROOT``: a folder a test stack writes at build (``scripts/engines/sign_vendor_rules.py
  test-root``) with its own public key (``trust-root.json``) and that key's signature of the packaged profiles
  (``acp_agents.json.sig``). The key vouches for the ACP profiles only, never for the vendor rules, their hosts or an
  image, and only under a ``test-`` key id, which no release key carries.
- ``PIPESHUB_ALLOW_TEST_TRUST_ROOT`` (dev only, exactly ``true``): lets a release build start with a test root.

Only the process environment carries them: no org setting, API or sandbox reaches a service's environment or its
read-only mounts. A release build (its packaged profiles carry the release signature, which the release build writes
and the tree never holds) refuses to read its profiles with a test root unless the last setting is on, and every
harness process reads its profiles at start, so it does not start.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from harness_core.engines.isolation import override_on

if TYPE_CHECKING:
    from collections.abc import Mapping

UNSIGNED_ENV: Final = "PIPESHUB_ACP_PROFILES_UNSIGNED"
TEST_ROOT_ENV: Final = "PIPESHUB_TEST_TRUST_ROOT"
ALLOW_TEST_ROOT_ENV: Final = "PIPESHUB_ALLOW_TEST_TRUST_ROOT"
TEST_KEY_PREFIX: Final = "test-"
ROOT_FILE: Final = "trust-root.json"
SIGNATURE_FILE: Final = "acp_agents.json.sig"
ROOT_SCHEMA: Final = "pipeshub.test-trust-root/1"


class ProfileTestRootRefused(ValueError):
    """A test trust root this process must not use: unreadable, not a test key, or on a release build without
    ``PIPESHUB_ALLOW_TEST_TRUST_ROOT``. Raised where the profiles are read, which every harness process does at
    start."""

    reason_code = "test_trust_root_refused"


@dataclass(frozen=True, slots=True)
class ProfileTestRoot:
    """A test stack's key (``key_id`` -> raw Ed25519 public key) and its signature of the packaged profiles."""

    key_id: str
    public_key: bytes
    signature: bytes | None


def env_setting(name: str, env: Mapping[str, str] | None = None) -> str | None:
    """``name`` in ``env``, else the process environment (C-16 reads its callers' names)."""
    return (os.environ if env is None else env).get(name)


def unsigned_profiles_allowed(env: Mapping[str, str] | None = None) -> bool:
    """Whether this install reads the packaged ACP profiles while they are unsigned (dev only)."""
    return override_on(env_setting(UNSIGNED_ENV, env))


@cache
def read_test_root(folder: str, release_keys: frozenset[str]) -> ProfileTestRoot:
    """The root in ``folder``; ``release_keys``: the release's key ids, which a test key may never take."""
    path = Path(folder)
    try:
        raw: object = json.loads((path / ROOT_FILE).read_bytes())
    except (OSError, ValueError):
        raise ProfileTestRootRefused(f"{TEST_ROOT_ENV}: {ROOT_FILE} is missing or not JSON") from None
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    key_id, public = body.get("keyId"), body.get("publicKey")
    if body.get("schema") != ROOT_SCHEMA or not isinstance(key_id, str) or not isinstance(public, str):
        raise ProfileTestRootRefused(f"{TEST_ROOT_ENV}: {ROOT_FILE} is not a test trust root")
    if not key_id.startswith(TEST_KEY_PREFIX) or key_id in release_keys:
        raise ProfileTestRootRefused(f"{TEST_ROOT_ENV}: a test key's id starts with {TEST_KEY_PREFIX!r}, got {key_id!r}")
    try:
        key = base64.b64decode(public, validate=True)
    except (binascii.Error, ValueError):
        raise ProfileTestRootRefused(f"{TEST_ROOT_ENV}: the public key is not base64") from None
    if len(key) != 32:
        raise ProfileTestRootRefused(f"{TEST_ROOT_ENV}: the public key is not a raw Ed25519 key")
    signature = path / SIGNATURE_FILE
    return ProfileTestRoot(key_id, key, signature.read_bytes() if signature.is_file() else None)


def profile_test_root(
    env: Mapping[str, str] | None = None, *, release_build: bool, release_keys: frozenset[str]
) -> ProfileTestRoot | None:
    """The deployment's test trust root, or None when it sets none; raises ``ProfileTestRootRefused``."""
    folder = (env_setting(TEST_ROOT_ENV, env) or "").strip()
    if not folder:
        return None
    if release_build and not override_on(env_setting(ALLOW_TEST_ROOT_ENV, env)):
        raise ProfileTestRootRefused(
            f"{TEST_ROOT_ENV} is set on a release build: remove it, or set {ALLOW_TEST_ROOT_ENV}=true on a test install"
        )
    return read_test_root(folder, release_keys)


__all__ = [
    "ALLOW_TEST_ROOT_ENV",
    "ROOT_FILE",
    "ROOT_SCHEMA",
    "SIGNATURE_FILE",
    "TEST_KEY_PREFIX",
    "TEST_ROOT_ENV",
    "UNSIGNED_ENV",
    "env_setting",
    "ProfileTestRoot",
    "ProfileTestRootRefused",
    "read_test_root",
    "profile_test_root",
    "unsigned_profiles_allowed",
]
