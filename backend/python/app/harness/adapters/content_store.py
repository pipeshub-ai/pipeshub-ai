"""``ContentStorePort`` for durable runs: small payloads inline, the rest sealed by Node.

Most kernel payloads (user text, short answers, tool results) are a few KB;
carrying them in the ref keeps a commit to one Node round trip instead of a
content write each (P0.9: ~8 commits per run). Larger payloads are sealed at
rest by Node with the org's data key (DP-1) through the attempt's content
route; the worker holds refs, never keys. Every read verifies hash and size.
"""

from __future__ import annotations

from typing import Protocol

from harness_core.contracts.generated.common import ContentRef
from harness_core.journal.inline import (
    CONTENT_REF_MAX_CHARS,
    content_digest,
    inline_bytes,
    inline_ref,
    inline_ref_chars,
)
from harness_core.journal.types import ContentMissing, JournalError
from harness_core.logs import HarnessLog

INLINE_MAX_BYTES = 16 * 1024
_SEALED = "sealed:"
# What S5..S8 wrote to PipesHub storage unsealed; refused as S8 refuses plaintext events.
_PLAINTEXT_BLOB = "blob:"
LOGGER = "app.harness.content"


class SealedContentPort(Protocol):
    """Node's content route for one attempt; the refs it returns start with ``sealed:``."""

    async def put(self, data: bytes) -> str: ...

    async def get(self, ref: str) -> bytes:
        """Raise ``ContentMissing`` when the content is gone, tampered or not this session's."""
        ...


class PipesHubContentStore:
    def __init__(
        self,
        sealed: SealedContentPort | None,
        *,
        inline_max_bytes: int = INLINE_MAX_BYTES,
        log: HarnessLog | None = None,
    ) -> None:
        self._sealed = sealed
        self._inline_max_bytes = inline_max_bytes
        self._log = log or HarnessLog.named(LOGGER)

    async def put(self, data: bytes) -> ContentRef:
        # The contract bounds the ref every receiver takes; what would not fit is sealed (CHILDREF).
        if len(data) <= self._inline_max_bytes and inline_ref_chars(len(data)) <= CONTENT_REF_MAX_CHARS:
            return inline_ref(data)
        return await self.put_sealed(data)

    async def put_sealed(self, data: bytes) -> ContentRef:
        ref = await self._route("put", len(data)).put(data)
        return ContentRef(ref=ref, hash=content_digest(data), size=len(data))

    async def get(self, ref: ContentRef) -> bytes:
        inline = inline_bytes(ref)
        if inline is not None:
            data = inline
        elif ref.ref.startswith(_SEALED):
            data = await self._route("get", ref.size).get(ref.ref)
        elif ref.ref.startswith(_PLAINTEXT_BLOB):
            self._log.warning("content.refused", reason="plaintext_blob", size=ref.size)
            raise ContentMissing("content stored unsealed before S8b is refused")
        else:
            self._log.warning("content.refused", reason="unknown_ref", size=ref.size)
            raise ContentMissing("not a PipesHub content ref")
        if len(data) != ref.size or content_digest(data) != ref.hash:
            self._log.warning("content.refused", reason="digest_mismatch", size=ref.size)
            raise ContentMissing(f"{ref.ref}: hash or size mismatch")
        return data

    def _route(self, operation: str, size: int) -> SealedContentPort:
        if self._sealed is None:
            self._log.warning(
                "content.refused", reason="no_sealed_route", operation=operation, size=size
            )
            raise JournalError("this attempt has no sealed content route")
        return self._sealed


class InMemorySealedContent:
    """In-memory ``SealedContentPort`` for tests: keeps the bytes, seals nothing."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, data: bytes) -> str:
        ref = f"{_SEALED}memory/{len(self.objects) + 1}"
        self.objects[ref] = bytes(data)
        return ref

    async def get(self, ref: str) -> bytes:
        try:
            return self.objects[ref]
        except KeyError:
            raise ContentMissing(f"{ref}: not stored") from None


__all__ = ["INLINE_MAX_BYTES", "InMemorySealedContent", "PipesHubContentStore", "SealedContentPort"]
