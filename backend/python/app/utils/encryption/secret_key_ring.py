"""The deployment secret as a key ring (ADR-KEY-01), read the way Node reads it.

``SECRET_KEY`` is the key every writer uses. ``SECRET_KEY_NEXT`` (before the switch) or ``SECRET_KEY_PREVIOUS``
(after it) is a second key readers also accept, so a fleet restarting one replica at a time never meets data it
cannot open. The rules and messages match ``backend/nodejs/apps/src/libs/encryptor/secret-key-ring.ts``; every
misconfiguration fails closed and names the variables, never a value.

SEC-D-05 (ADR-KEY-02): a key a writer will use (``SECRET_KEY``, ``SECRET_KEY_NEXT``) must look random; the
cases both languages agree on are ``contracts/secret-key/strength-cases.json``. The platform cipher's AES key is
HKDF of the secret; the SHA-256 key it replaces is still read, and ``rotate-secret-key rotate`` moves what it sealed.

R38IDENT-FU-8 (ADR-KEY-02 D8): each ring member may come from a file instead (``SECRET_KEY_FILE``,
``SECRET_KEY_PREVIOUS_FILE``, ``SECRET_KEY_NEXT_FILE``: a Docker secret or a Kubernetes Secret volume), so the root key
is not in the container's environment. A named file wins over the variable; a file that cannot be read or is empty
stops the start with the variable and the path, never the content.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.utils.encryption.secret_strength import (
    _REASON_TEXT,  # pyright: ignore[reportPrivateUsage]
    MIN_SECRET_KEY_LENGTH,
    weak_secret_reason,
)
from app.utils.security_profile import hardened_profile, warn_once
from harness_core.security.derived_keys import (
    FILE_SUFFIX,
    SecretKeyRingError,
    ring_secret,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

SECRET_KEY_ENV = "SECRET_KEY"
SECRET_KEY_PREVIOUS_ENV = "SECRET_KEY_PREVIOUS"
SECRET_KEY_NEXT_ENV = "SECRET_KEY_NEXT"
# ``sha256`` keeps writers on the old cipher key for the first restart of a multi-replica upgrade (ADR-KEY-02 D5);
# ``hkdf`` writes under the HKDF key. Unset follows the security profile (ADR-OPS-07): compatibility writes under the
# SHA-256 key a release without SEC-D-05 reads, so a rollback reads every value written since the upgrade.
SECRET_KEY_KDF_ENV = "SECRET_KEY_KDF"
# The ring's members, writer first; each may also come from the file its ``<name>_FILE`` names.
RING_ENVS = (SECRET_KEY_ENV, SECRET_KEY_PREVIOUS_ENV, SECRET_KEY_NEXT_ENV)
# Every variable that hands a process the platform's root key, by value or by file: what a component refuses.
PLATFORM_KEY_ENVS = tuple(name for env in RING_ENVS for name in (env, f"{env}{FILE_SUFFIX}"))

_CIPHER_SALT = b"pipeshub/platform-cipher"
_CIPHER_INFO = b"aes-256-gcm/v2"



@dataclass(frozen=True, slots=True)
class SecretKeyRing:
    current: str
    other: str | None = None
    other_role: Literal["previous", "next"] | None = None

    @property
    def secrets(self) -> tuple[str, ...]:
        """Every key a reader accepts, the writer's first."""
        return (self.current,) if self.other is None else (self.current, self.other)

    def raw(self) -> tuple[bytes, tuple[bytes, ...]]:
        """The writer's secret and the accepted others, as bytes (the egress sealers' input)."""
        return self.current.encode("utf-8"), tuple(secret.encode("utf-8") for secret in self.secrets[1:])

    def hashed(self) -> tuple[str, ...]:
        """SHA-256 of each secret, hex, the writer's first: the KEK's and run-key digests' input keying material
        (each runs its own HKDF over it). Not the platform cipher's key any more: that is ``cipher_keys``."""
        return tuple(hashed_secret(secret) for secret in self.secrets)

    def cipher_keys(self, env: Mapping[str, str] | None = None) -> tuple[str, ...]:
        """Every AES key the platform cipher reads with, the writer's first: per secret its HKDF key, then the
        SHA-256 key it replaces (SEC-D-05)."""
        keys = tuple(key for secret in self.secrets for key in (platform_cipher_key(secret), hashed_secret(secret)))
        if writes_sha256(env):
            return (keys[1], keys[0], *keys[2:])
        return keys


def writes_sha256(env: Mapping[str, str] | None = None) -> bool:
    """Whether writers seal under the SHA-256 key: ``SECRET_KEY_KDF`` when set, else the compatibility profile (the
    process's: the profile is never read from a caller's mapping)."""
    source = os.environ if env is None else env
    kdf = (source.get(SECRET_KEY_KDF_ENV) or "").strip().lower()
    if kdf == "sha256":
        return True
    if kdf == "hkdf":
        return False
    return not hardened_profile()


def hashed_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def platform_cipher_key(secret: str) -> str:
    """The platform cipher's AES-256 key for one secret, hex: HKDF-SHA256 under the cipher's own salt and label."""
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=_CIPHER_SALT, info=_CIPHER_INFO).derive(
        secret.encode("utf-8")
    ).hex()


def weak_secret_message(name: str, reason: str) -> str:
    return (
        f"{name} {_REASON_TEXT[reason]}. It encrypts every stored credential, so it must be random: generate one "
        "with `openssl rand -base64 32` (install.sh generates one for you). To replace a key already in use, follow "
        '"Rotating SECRET_KEY" in the runbooks: the new key goes in SECRET_KEY, the old one in SECRET_KEY_PREVIOUS.'
    )


def secret_key_configured(env: Mapping[str, str] | None = None) -> bool:
    """Whether this process was given ``SECRET_KEY``, by value or by file (a file that cannot be read still counts:
    reading the ring then says why)."""
    # os.getenv for the process, as this check always read it (callers' tests patch it).
    get = os.getenv if env is None else env.get
    return bool(get(SECRET_KEY_ENV) or (get(f"{SECRET_KEY_ENV}{FILE_SUFFIX}") or "").strip())


def platform_keys_held(env: Mapping[str, str] | None = None) -> list[str]:
    """The variables that hand this process the platform's root key (ADR-KEY-02 D1: a component must hold none)."""
    source = os.environ if env is None else env
    return [name for name in PLATFORM_KEY_ENVS if (source.get(name) or "").strip()]


def secret_key_ring(env: Mapping[str, str] | None = None) -> SecretKeyRing:
    current = ring_secret(SECRET_KEY_ENV, env)
    if not current:
        raise SecretKeyRingError("SECRET_KEY environment variable is required")
    previous = ring_secret(SECRET_KEY_PREVIOUS_ENV, env)
    upcoming = ring_secret(SECRET_KEY_NEXT_ENV, env)
    if previous and upcoming:
        raise SecretKeyRingError(
            "SECRET_KEY_PREVIOUS and SECRET_KEY_NEXT are both set: set SECRET_KEY_NEXT before the switch, "
            "SECRET_KEY_PREVIOUS after it, never both"
        )
    _require_strong(SECRET_KEY_ENV, current)
    if previous:
        # The key being retired may be the weak one this rotation replaces.
        return _checked(current, previous, "previous", SECRET_KEY_PREVIOUS_ENV)
    if upcoming:
        _require_strong(SECRET_KEY_NEXT_ENV, upcoming)
        return _checked(current, upcoming, "next", SECRET_KEY_NEXT_ENV)
    return SecretKeyRing(current)


def _require_strong(name: str, secret: str) -> None:
    reason = weak_secret_reason(secret)
    if reason is None:
        return
    if hardened_profile():
        raise SecretKeyRingError(weak_secret_message(name, reason))
    # The compatibility profile (ADR-OPS-07) keeps the key a release without SEC-D-05 accepted; never the value.
    warn_once(
        f"{weak_secret_message(name, reason)} Accepted because PIPESHUB_HARDENED is not true (the compatibility "
        "profile); the hardened profile refuses it."
    )


def _checked(current: str, other: str, role: Literal["previous", "next"], name: str) -> SecretKeyRing:
    # Compared by digest so the comparison neither leaks length nor depends on content.
    if hmac.compare_digest(hashlib.sha256(other.encode()).digest(), hashlib.sha256(current.encode()).digest()):
        raise SecretKeyRingError(f"{name} is the same as SECRET_KEY: a rotation needs a new key")
    return SecretKeyRing(current, other, role)


def deployment_ring() -> SecretKeyRing | None:
    """The process's ring; ``None`` when ``SECRET_KEY`` is unset. A misconfigured ring raises."""
    return secret_key_ring() if secret_key_configured() else None


def platform_read_keys(hex_key: str) -> tuple[str, ...]:
    """The ring's cipher keys, the writer's first, when ``hex_key`` is the deployment's own (its HKDF key, or the
    SHA-256 key a caller from before SEC-D-05 derives); any other key reads alone."""
    try:
        ring = secret_key_ring()
    except SecretKeyRingError:
        return (hex_key,)
    keys = ring.cipher_keys()
    return keys if hex_key in keys[:2] else (hex_key,)


def deployment_cipher_key() -> str:
    """The platform cipher's writer key for this process's ``SECRET_KEY``; a weak or missing key raises."""
    return secret_key_ring().cipher_keys()[0]


__all__ = [
    "FILE_SUFFIX",
    "PLATFORM_KEY_ENVS",
    "RING_ENVS",
    "SECRET_KEY_ENV",
    "SECRET_KEY_NEXT_ENV",
    "SECRET_KEY_PREVIOUS_ENV",
    "SecretKeyRing",
    "MIN_SECRET_KEY_LENGTH",
    "SECRET_KEY_KDF_ENV",
    "SecretKeyRingError",
    "deployment_cipher_key",
    "deployment_ring",
    "hashed_secret",
    "platform_cipher_key",
    "platform_keys_held",
    "ring_secret",
    "secret_key_configured",
    "platform_read_keys",
    "secret_key_ring",
    "weak_secret_message",
    "weak_secret_reason",
    "writes_sha256",
]
