"""Purpose-specific keys derived from the install secret, and how a process reads that secret.

A stopgap with one caller. The key hierarchy, rotation and recovery belong to
the encryption-key work, which will own this module; keep it minimal until then.

HKDF-SHA256 (RFC 5869) of ``SECRET_KEY``, salt ``pipeshub``, info
``pipeshub/derived/<purpose>/v1``. The ``derived`` namespace is reserved for this
helper, so no purpose here can equal another derivation's info string (the
envelope key, or any key-encryption key). Purposes are an allowlist.

``ring_secret`` is the one reader of a secret given by value or by file; the
deployment key ring (``app.utils.encryption.secret_key_ring``) uses it too, so
both always see the same secret.

Nothing is cached or kept at module level: the root secret is also the key of the
configuration store, so neither it nor anything computed from it may sit in a
global, a repr or a log line. Never log a derived key.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

PURPOSES = frozenset({"prompt-cache-key"})
FILE_SUFFIX = "_FILE"

_logger = logging.getLogger(__name__)
_warned_paths: set[str] = set()


class SecretKeyRingError(ValueError):
    """The ring must not be used; the message says why."""


def ring_secret(name: str, env: Mapping[str, str] | None = None) -> str:
    """One ring member: the file ``<name>_FILE`` names (one trailing line break dropped), else ``<name>``, else ``""``."""
    source = os.environ if env is None else env
    file_env = f"{name}{FILE_SUFFIX}"
    path = (source.get(file_env) or "").strip()
    if not path:
        return source.get(name) or ""
    try:
        with open(path, encoding="utf-8") as handle:
            mode = os.fstat(handle.fileno()).st_mode
            secret = handle.read()
    except (OSError, UnicodeDecodeError) as exc:
        raise SecretKeyRingError(f"{file_env} names {path}, which cannot be read ({type(exc).__name__})") from None
    # A file written by `echo` or an editor ends with a line break; any other whitespace is the key's and is refused.
    if secret.endswith("\n"):
        secret = secret[:-1]
        if secret.endswith("\r"):
            secret = secret[:-1]
    if not secret:
        raise SecretKeyRingError(f"{file_env} names {path}, which is empty")
    if mode & stat.S_IROTH and path not in _warned_paths:
        # Not refused: Kubernetes and Docker set a secret's mode themselves. Every account on the host can read it.
        _warned_paths.add(path)
        # The variable's name only: the path comes from the secret's own configuration, and the log needs no part of it.
        _logger.warning("%s names a file every account can read: make it mode 0400", file_env)
    return secret


def derive_purpose_key(purpose: str) -> bytes:
    """32 bytes bound to ``purpose`` and the current ``SECRET_KEY`` (by value or by file).

    Without ``SECRET_KEY`` it raises, as every service does at start.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown key purpose {purpose!r}")
    secret = ring_secret("SECRET_KEY")
    if not secret:
        raise SecretKeyRingError("SECRET_KEY environment variable is required")
    prk = hmac.new(b"pipeshub", secret.encode(), hashlib.sha256).digest()
    return hmac.new(prk, f"pipeshub/derived/{purpose}/v1".encode() + b"\x01", hashlib.sha256).digest()
