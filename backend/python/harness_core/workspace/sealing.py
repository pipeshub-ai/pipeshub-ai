"""``BinaryObjectStore`` that seals objects with the partition's data key (D33).

One key per partition (a session workspace); the store that holds the bytes
never sees plaintext. Within a partition, sealing is deterministic so equal
files are stored once; across partitions the keys differ, so neither the
stored bytes nor the content hashes in manifests can be correlated.
Destroying the partition's key makes every object of it unreadable.

Construction: the nonce is a keyed hash of the plaintext (a synthetic IV), so
the same (key, plaintext) gives the same ciphertext and AES-GCM never reuses a
nonce for two different plaintexts unless a 96-bit keyed hash collides
(about n^2 / 2^97 for n objects in one partition). AES-GCM rather than
GCM-SIV because Node's OpenSSL must be able to open these objects too.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from harness_core.logs import HarnessLog
from harness_core.workspace.types import ObjectRef, ObjectUnreadable

if TYPE_CHECKING:
    from harness_core.workspace.ports import BinaryObjectStore, DataKey, KeyProvider
    from harness_core.workspace.types import ObjectScope

SEALED_MAGIC = b"HWS1"
NONCE_BYTES = 12
TAG_BYTES = 16
CONTENT_HASH_PREFIX = "hmac-sha256:"
_REF_PREFIX = "sealed"
LOGGER = "harness_core.workspace.sealing"


def _subkey(key: DataKey, label: bytes) -> bytes:
    """HKDF-Expand (RFC 5869) of one block; the data key is already uniform."""
    return hmac.new(key.material, label + b"\x01", hashlib.sha256).digest()


def _content_hash(key: DataKey, plaintext: bytes) -> str:
    return CONTENT_HASH_PREFIX + hmac.new(_subkey(key, b"content-hash"), plaintext, hashlib.sha256).hexdigest()


def _require_partition(scope: ObjectScope) -> str:
    if scope.partition is None:
        raise ValueError("A sealed object store needs a partitioned scope")
    return scope.partition


def _aad(scope: ObjectScope, key: DataKey, content_hash: str) -> bytes:
    return b"\x00".join(
        part.encode() for part in (scope.tenant_id, scope.partition or "", key.key_id, content_hash)
    )


def _encode_id(inner: ObjectRef) -> str:
    # The inner ref is needed to fetch and verify the sealed bytes; its hash
    # has no "/", so the opaque inner id goes last and may contain any.
    return f"{_REF_PREFIX}/{inner.size_bytes}/{inner.content_hash}/{inner.object_id}"


def _decode_id(object_id: str) -> ObjectRef:
    parts = object_id.split("/", 3)
    if len(parts) != 4 or parts[0] != _REF_PREFIX or not parts[1].isdigit():
        raise ObjectUnreadable("Not a sealed object reference")
    return ObjectRef(object_id=parts[3], content_hash=parts[2], size_bytes=int(parts[1]))


class SealedObjectStore:
    """Seals with ``keys.data_key(scope.tenant_id, scope.partition)`` around ``inner``."""

    def __init__(
        self,
        inner: BinaryObjectStore,
        keys: KeyProvider,
        *,
        log: HarnessLog | None = None,
    ) -> None:
        self._inner = inner
        self._keys = keys
        self._log = log or HarnessLog(logging.getLogger(LOGGER))

    async def content_hash(self, scope: ObjectScope, content: bytes) -> str:
        key = await self._keys.data_key(scope.tenant_id, _require_partition(scope))
        return _content_hash(key, content)

    async def put(self, scope: ObjectScope, content: bytes) -> ObjectRef:
        key = await self._keys.data_key(scope.tenant_id, _require_partition(scope))
        plaintext = bytes(content)
        content_hash = _content_hash(key, plaintext)
        nonce = hmac.new(_subkey(key, b"nonce"), plaintext, hashlib.sha256).digest()[:NONCE_BYTES]
        sealed = AESGCM(_subkey(key, b"encrypt")).encrypt(
            nonce, plaintext, _aad(scope, key, content_hash)
        )
        kid = key.key_id.encode()
        stored = SEALED_MAGIC + bytes([len(kid)]) + kid + nonce + sealed
        inner = await self._inner.put(scope, stored)
        return ObjectRef(_encode_id(inner), content_hash, len(plaintext))

    async def get(self, scope: ObjectScope, ref: ObjectRef) -> bytes:
        partition = _require_partition(scope)
        inner_ref = _decode_id(ref.object_id)
        key = await self._keys.data_key(scope.tenant_id, partition)
        stored = await self._inner.get(scope, inner_ref)
        header = len(SEALED_MAGIC) + 1
        if len(stored) < header or not stored.startswith(SEALED_MAGIC):
            raise self._unreadable(scope, "malformed")
        kid_end = header + stored[header - 1]
        if len(stored) < kid_end + NONCE_BYTES + TAG_BYTES:
            raise self._unreadable(scope, "malformed")
        if stored[header:kid_end] != key.key_id.encode():
            raise self._unreadable(scope, "other_key")
        nonce = stored[kid_end : kid_end + NONCE_BYTES]
        try:
            plaintext = AESGCM(_subkey(key, b"encrypt")).decrypt(
                nonce, stored[kid_end + NONCE_BYTES :], _aad(scope, key, ref.content_hash)
            )
        except InvalidTag:
            raise self._unreadable(scope, "authentication") from None
        if len(plaintext) != ref.size_bytes:
            raise self._unreadable(scope, "size")
        return plaintext

    def _unreadable(self, scope: ObjectScope, reason: str) -> ObjectUnreadable:
        self._log.warning(
            "workspace.object_unreadable",
            reason=reason,
            tenantId=scope.tenant_id,
            partition=scope.partition,
        )
        return ObjectUnreadable(f"Sealed object is unreadable ({reason})")
