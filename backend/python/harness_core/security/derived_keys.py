"""Purpose-specific keys derived from the install secret.

A stopgap with one caller. The key hierarchy, rotation and recovery belong to
the encryption-key work, which will own this module; keep it minimal until then.

HKDF-SHA256 (RFC 5869) of ``SECRET_KEY``, salt ``pipeshub``, info
``pipeshub/derived/<purpose>/v1``. The ``derived`` namespace is reserved for this
helper, so no purpose here can equal another derivation's info string (the
envelope key, or any key-encryption key). Purposes are an allowlist.

Nothing is cached or kept at module level: the root secret is also the key of the
configuration store, so neither it nor anything computed from it may sit in a
global, a repr or a log line. Never log a derived key.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os

PURPOSES = frozenset({"prompt-cache-key"})

_logger = logging.getLogger(__name__)


def derive_purpose_key(purpose: str) -> bytes:
    """32 bytes bound to ``purpose`` and the current ``SECRET_KEY``.

    Without ``SECRET_KEY`` (every service requires one to start) the result is
    random per call: private, but stable for no one.
    """
    if purpose not in PURPOSES:
        raise ValueError(f"unknown key purpose {purpose!r}")
    secret = os.getenv("SECRET_KEY")
    if not secret:
        _logger.warning("SECRET_KEY is not set; derived keys are random for this call")
        return os.urandom(32)
    prk = hmac.new(b"pipeshub", secret.encode(), hashlib.sha256).digest()
    return hmac.new(prk, f"pipeshub/derived/{purpose}/v1".encode() + b"\x01", hashlib.sha256).digest()
