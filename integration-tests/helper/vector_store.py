"""Ask the vector database what it still holds.

Deletion has to clear four stores — graph, vector, blob and MongoDB — and until
now only the graph was ever checked. A delete that clears the graph and orphans
every embedding passes the existing suite.

Two things about the payload shape drive this module's API, both confirmed
against a running instance rather than read off the write path:

* Points carry no record id. The only record-ish key is
  ``metadata.virtualRecordId``, the id a record keeps across updates that do
  not change its content — the graph explicitly carries it forward so that
  points keyed by it are not orphaned. Blob paths and storage documents are
  filed under the same id, which is what lets one lookup answer for three
  stores, so the counting helpers below are keyed on it.
* ``connectorIds`` is a top-level array, so connector-wide deletion is
  answerable directly.

Every count scans *all* collections rather than the one the product would have
written to. A cleanup bug that leaves points behind in an unexpected collection
is exactly the failure worth catching, and asking the product where it thinks
the points are would hide it.
"""

from __future__ import annotations

import asyncio
import logging
import os
import warnings
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

logger = logging.getLogger("vector-store-probe")

# Deletion is asynchronous: the API returns before the consumer has finished
# clearing the stores, so every assertion polls rather than reading once.
_DEFAULT_TIMEOUT = 120
_POLL_INTERVAL = 2.0

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class VectorProbeUnavailable(RuntimeError):
    """The probe could not inspect the store, so it has no answer to give."""


def _env(key: str, default: str) -> str:
    return os.getenv(key, default).strip() or default


def _is_local(host: str) -> bool:
    return host.strip().lower() in _LOCAL_HOSTS


def _is_missing_collection(exc: Exception) -> bool:
    """Whether the failure is "that collection is not there" and nothing worse.

    Matched on the message because the client raises the same exception type
    for a missing collection and for a transport failure, and only the first is
    a legitimate empty answer.
    """
    text = str(exc).lower()
    return "not found" in text or "doesn't exist" in text or "does not exist" in text


class VectorStoreProbe:
    """Read-only questions about what the vector database still holds."""

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        api_key: str | None = None,
        use_https: bool | None = None,
    ) -> None:
        self._host = host or _env("QDRANT_HOST", "localhost")
        self._port = port or int(_env("QDRANT_PORT", "6333"))
        self._api_key = api_key if api_key is not None else os.getenv("QDRANT_API_KEY")
        if use_https is None:
            use_https = _env("QDRANT_USE_HTTPS", "").lower() in ("1", "true", "yes")
        self._use_https = use_https
        self._client: AsyncQdrantClient | None = None

        # The integration stack publishes Qdrant on loopback with no TLS, which
        # is fine. Sending an api key in the clear to anything else is not, and
        # is far more likely to be a misconfigured host than a deliberate
        # choice — so it has to be asked for explicitly.
        if self._api_key and not self._use_https and not _is_local(self._host):
            raise ValueError(
                f"Refusing to send a Qdrant api key in cleartext to {self._host!r}. "
                "Set QDRANT_USE_HTTPS=true, or clear QDRANT_API_KEY if the host "
                "genuinely needs no authentication."
            )

    async def _conn(self) -> AsyncQdrantClient:
        if self._client is None:
            # Two warnings on every construction, neither actionable here: the
            # api key travels over plain HTTP because this is a local test
            # stack, and the pinned client (1.13.x) trails the shipped server
            # (1.15). Counting and scrolling work across that gap; the warnings
            # would otherwise drown the test output they appear in.
            with warnings.catch_warnings():
                warnings.filterwarnings("ignore", category=UserWarning)
                self._client = AsyncQdrantClient(
                    host=self._host,
                    port=self._port,
                    api_key=self._api_key or None,
                    https=self._use_https,
                )
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def collections(self) -> list[str]:
        client = await self._conn()
        result = await client.get_collections()
        return sorted(c.name for c in result.collections)

    async def _count_matching(self, condition: qmodels.FieldCondition) -> int:
        """Total points matching a condition across every collection."""
        client = await self._conn()
        total = 0
        for name in await self.collections():
            try:
                result = await client.count(
                    collection_name=name,
                    count_filter=qmodels.Filter(must=[condition]),
                    exact=True,
                )
            except Exception as exc:
                # A collection dropped between listing and counting holds
                # nothing, which is the answer the caller wanted. Every other
                # failure means the store was not inspected, and returning zero
                # would report it as clean — the pass condition for
                # assert_embeddings_gone.
                if _is_missing_collection(exc):
                    logger.debug("Collection %s disappeared mid-scan", name)
                    continue
                raise VectorProbeUnavailable(
                    f"Could not count points in collection {name!r}: {exc}"
                ) from exc
            total += result.count
        return total

    async def count_for_virtual_record(self, virtual_record_id: str) -> int:
        return await self._count_matching(
            qmodels.FieldCondition(
                key="metadata.virtualRecordId",
                match=qmodels.MatchValue(value=virtual_record_id),
            )
        )

    async def count_for_connector(self, connector_id: str) -> int:
        return await self._count_matching(
            qmodels.FieldCondition(
                key="connectorIds",
                match=qmodels.MatchValue(value=connector_id),
            )
        )

    async def count_for_org(self, org_id: str) -> int:
        return await self._count_matching(
            qmodels.FieldCondition(
                key="metadata.orgId",
                match=qmodels.MatchValue(value=org_id),
            )
        )

    async def sample_payloads(
        self, virtual_record_id: str, limit: int = 3
    ) -> list[dict[str, Any]]:
        """A few surviving payloads, to make a failure message useful."""
        client = await self._conn()
        found: list[dict[str, Any]] = []
        condition = qmodels.FieldCondition(
            key="metadata.virtualRecordId",
            match=qmodels.MatchValue(value=virtual_record_id),
        )
        for name in await self.collections():
            if len(found) >= limit:
                break
            try:
                points, _ = await client.scroll(
                    collection_name=name,
                    scroll_filter=qmodels.Filter(must=[condition]),
                    limit=limit - len(found),
                    with_payload=True,
                    with_vectors=False,
                )
            except Exception:
                continue
            for point in points:
                payload = dict(point.payload or {})
                payload["_collection"] = name
                found.append(payload)
        return found

    # ------------------------------------------------------------------ #
    # Assertions
    # ------------------------------------------------------------------ #

    async def _wait_for_zero(
        self, count: "Any", describe: str, timeout: int
    ) -> int:
        deadline = asyncio.get_event_loop().time() + timeout
        remaining = await count()
        while remaining > 0 and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(_POLL_INTERVAL)
            remaining = await count()
        if remaining:
            logger.warning("%s still has %d point(s)", describe, remaining)
        return remaining

    async def assert_embeddings_gone(
        self, virtual_record_id: str, timeout: int = _DEFAULT_TIMEOUT
    ) -> None:
        remaining = await self._wait_for_zero(
            lambda: self.count_for_virtual_record(virtual_record_id),
            f"virtual record {virtual_record_id}",
            timeout,
        )
        if remaining:
            sample = await self.sample_payloads(virtual_record_id)
            collections = sorted({str(p.get("_collection")) for p in sample})
            raise AssertionError(
                f"{remaining} embedding(s) for virtual record "
                f"{virtual_record_id} survived deletion after {timeout}s, in "
                f"collection(s) {collections}. The graph may look clean while "
                f"the vector database still holds this record's content."
            )

    async def assert_embeddings_present(self, virtual_record_id: str) -> int:
        count = await self.count_for_virtual_record(virtual_record_id)
        assert count > 0, (
            f"No embeddings found for virtual record {virtual_record_id}. A "
            "deletion test that starts from nothing proves nothing, so this is "
            "checked before the delete rather than after it."
        )
        return count

    async def assert_connector_embeddings_gone(
        self, connector_id: str, timeout: int = _DEFAULT_TIMEOUT
    ) -> None:
        remaining = await self._wait_for_zero(
            lambda: self.count_for_connector(connector_id),
            f"connector {connector_id}",
            timeout,
        )
        if remaining:
            raise AssertionError(
                f"{remaining} embedding(s) still carry connector {connector_id} "
                f"after {timeout}s. Deleting a connector must clear its "
                "embeddings, not only its graph nodes."
            )

    async def assert_embeddings_survive(
        self, virtual_record_id: str, expected_min: int = 1
    ) -> None:
        """A different record's embeddings must outlive this delete.

        Over-deletion is the quieter failure of the two: the surviving record
        stays in the graph and simply stops being findable.
        """
        count = await self.count_for_virtual_record(virtual_record_id)
        assert count >= expected_min, (
            f"Expected at least {expected_min} embedding(s) to survive for "
            f"virtual record {virtual_record_id}, found {count}. Deleting one "
            "record removed another record's embeddings — that record stays in "
            "the graph but silently stops being searchable."
        )
