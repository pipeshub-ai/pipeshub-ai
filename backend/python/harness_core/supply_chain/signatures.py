"""The one Ed25519 detached-signature check (OWD-8's format), for release data and every signed artefact.

A signature is ``{"alg": "Ed25519", "keyId": ..., "sig": <base64>}`` over the signed document's exact bytes, so
``openssl pkeyutl -verify -rawin`` checks it too. ``verify_detached`` answers the key id that signed, or raises
``SignatureRefused`` whose ``detail`` names why: ``missing``, ``unsigned``, ``unknown_key`` or ``bad_signature``.
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import TYPE_CHECKING, Final, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Mapping

SIGNATURE_ALGORITHM: Final = "Ed25519"
RefusalDetail = Literal["missing", "unsigned", "unknown_key", "bad_signature"]


class SignatureRefused(ValueError):
    def __init__(self, detail: RefusalDetail, message: str) -> None:
        super().__init__(message)
        self.detail: RefusalDetail = detail


def parse_signature(signature: bytes) -> tuple[str, bytes]:
    """The key id and raw signature of a detached signature document."""
    try:
        raw: object = json.loads(signature)
    except (ValueError, UnicodeDecodeError):
        raise SignatureRefused("unsigned", "the signature is not JSON") from None
    body = cast("dict[str, object]", raw) if isinstance(raw, dict) else {}
    key_id, value = body.get("keyId"), body.get("sig")
    if (
        body.get("alg") != SIGNATURE_ALGORITHM
        or not isinstance(key_id, str)
        or not isinstance(value, str)
    ):
        raise SignatureRefused("unsigned", "the signature is not an Ed25519 signature")
    try:
        return key_id, base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise SignatureRefused("unsigned", "the signature is not base64") from None


def verify_detached(
    document: bytes | None, signature: bytes | None, keys: Mapping[str, bytes]
) -> str:
    """The id of the key in ``keys`` (key id -> raw 32-byte public key) whose signature ``signature`` is of
    ``document``; ``SignatureRefused`` otherwise."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if document is None:
        raise SignatureRefused("missing", "the signed document is missing")
    if signature is None:
        raise SignatureRefused("unsigned", "the document is not signed")
    key_id, value = parse_signature(signature)
    key = keys.get(key_id)
    if key is None:
        raise SignatureRefused(
            "unknown_key", "the document is signed by a key that is not trusted for it"
        )
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(value, document)
    except (InvalidSignature, ValueError):
        raise SignatureRefused(
            "bad_signature", "the signature does not verify"
        ) from None
    return key_id


def sign_detached(document: bytes, private_key: bytes, key_id: str) -> bytes:
    """A detached signature of ``document`` by the raw 32-byte Ed25519 private key ``private_key``: what a publisher's
    or a CI's tooling writes. PipesHub never holds a signing key at run time; tests sign with generated keys."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    value = Ed25519PrivateKey.from_private_bytes(private_key).sign(document)
    return json.dumps(
        {
            "alg": SIGNATURE_ALGORITHM,
            "keyId": key_id,
            "sig": base64.b64encode(value).decode(),
        }
    ).encode()


__all__ = [
    "SIGNATURE_ALGORITHM",
    "RefusalDetail",
    "SignatureRefused",
    "parse_signature",
    "sign_detached",
    "verify_detached",
]
