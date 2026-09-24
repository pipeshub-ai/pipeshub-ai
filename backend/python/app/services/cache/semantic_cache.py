import hashlib
import json
import logging
import time
import uuid
import asyncio
from typing import Optional

from pydantic import BaseModel

from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    FilterExpression,
    HybridSearchRequest,
    VectorPoint,
    FieldCondition,
    CollectionConfig
)
from app.services.vector_db.collections import CollectionType


class SemanticCacheScope(BaseModel):
    orgId: str
    userId: str
    permissionsRevision: str
    corpusRevision: str
    filters: dict | None = None
    # All request inputs that influence the generated response.  Two requests
    # that differ in any of these fields must not share a cached answer even
    # when the query and corpus-revision match (e.g. quick vs internal_search
    # mode, different model keys, different project instructions).
    requestProfile: dict | None = None


logger = logging.getLogger(__name__)


class SemanticCacheService:
    def __init__(
        self,
        vector_db_service: IVectorDBService,
    ):
        self.vector_db = vector_db_service
        self.collection_name = CollectionType.SEMANTIC_CACHE.value
        self.threshold = 0.95
        self._initialized = False
        self._background_tasks: set[asyncio.Task] = set()

    async def initialize(self, embedding_dimension: int) -> None:
        if self._initialized:
            return

        exists = await self.vector_db.collection_exists(self.collection_name)
        if not exists:
            config = CollectionConfig(embedding_size=embedding_dimension)
            await self.vector_db.create_collection(self.collection_name, config)

        # Index on the nested metadata field so filters survive OpenSearch
        # document conversion (top-level payload fields are discarded by the
        # vector_point_to_document adapter; only metadata.* survives).
        await self.vector_db.create_index(
            self.collection_name, "metadata.filters_hash", {"type": "keyword"}
        )

        # Migration: apply non-indexed mapping for large stored-text fields.
        # This is an OpenSearch-only operation: put_mapping adds index:false to
        # prevent the 32 766-byte keyword limit from breaking large responses.
        # Qdrant uses payload indices (keyword/float only, no text type) and
        # Redis Vector ignores index:false entirely, so we only call this when
        # the provider is OpenSearch.
        #
        # For OpenSearch, if the field was already mapped as keyword by the
        # dynamic template, put_mapping cannot change the type. In that case we
        # log a warning and continue instead of leaving _initialized=False and
        # retrying on every subsequent request.
        _provider_class = type(self.vector_db).__name__.lower()
        if "opensearch" in _provider_class:
            for field in ("metadata.query_text", "metadata.response_text"):
                try:
                    await self.vector_db.create_index(
                        self.collection_name,
                        field,
                        {"type": "text", "index": False},
                    )
                except Exception as _map_exc:
                    _msg = str(_map_exc).lower()
                    # OpenSearch raises 400 mapper_parsing_exception when the
                    # field is already a keyword and cannot be changed in-place.
                    # Treat this as a no-op: existing docs are still retrievable
                    # and the cache degrades gracefully for large payloads on
                    # pre-migration collections.
                    if "mapper_parsing" in _msg or "illegal_argument" in _msg or "cannot" in _msg:
                        logger.warning(
                            "Semantic cache: cannot update mapping for %s on existing "
                            "OpenSearch index (field already mapped as keyword). "
                            "Large cached responses may be truncated on this collection.",
                            field,
                        )
                    else:
                        raise

        self._initialized = True

    async def get_cached_response(
        self,
        query: str,
        embedding: list[float],
        filters_hash: str,
    ) -> "dict | None":
        """Return ``{"text": str, "citations": list}`` on a cache hit, else ``None``.

        Returning a typed dict (instead of a bare string) lets the caller
        re-emit citations as a STATE_DELTA even on a cache-hit path, so source
        cards are not silently dropped for cached responses.

        A missing or empty ``metadata.response_text`` is treated as a miss so
        we never serve a blank response from cache.
        """
        try:
            req = HybridSearchRequest(
                dense_query=embedding,
                filter=FilterExpression(
                    # Use metadata.filters_hash because OpenSearch stores custom
                    # cache fields inside the metadata sub-document.
                    must=[FieldCondition(key="metadata.filters_hash", value=filters_hash)]
                ),
                limit=1,
                with_payload=True,
                # Must be True so QdrantService uses the dense-only path and
                # preserves the raw cosine score that the threshold check below
                # depends on.  Without this flag the score goes through RRF
                # and is no longer comparable to self.threshold.
                is_semantic_cache_query=True,
            )
            results = await self.vector_db.query_nearest_points(self.collection_name, [req])
            if results and results[0]:
                top_match = results[0][0]
                if top_match.score >= self.threshold:
                    # response_text lives in metadata.response_text after the
                    # OpenSearch round-trip (hit_to_search_result rebuilds payload
                    # as {"metadata": {...}, "page_content": ...}).
                    meta = top_match.payload.get("metadata") or {}
                    text = meta.get("response_text") or ""
                    if not text:
                        # Empty text is not a valid cache hit — treat as miss.
                        return None
                    citations = meta.get("citations") or []
                    logger.info(
                        "Semantic cache hit! Score: %.4f, citations: %d",
                        top_match.score, len(citations),
                    )
                    return {"text": text, "citations": citations}
        except Exception as e:
            logger.error(f"Error reading from semantic cache: {e}", exc_info=True)
        return None

    async def set_cached_response(
        self,
        query: str,
        response_text: str,
        embedding: list[float],
        filters_hash: str,
        org_id: str,
        corpus_revision: str,
        citations: "list | None" = None,
    ) -> None:
        """Persist a query→response pair to the semantic cache.

        ``citations`` should be the list of source-record dicts emitted during
        the live run.  Storing them here lets ``get_cached_response`` re-emit
        them as a STATE_DELTA on cache-hit replays so source cards are preserved.

        Purging stale entries is **not** triggered here; it belongs at the
        revision-bump call-site, not on every write.  Use
        ``bump_corpus_revision`` (below) to atomically increment the revision
        and immediately remove obsolete cache entries in one step.
        """
        try:
            # Store all cache-specific fields inside the `metadata` sub-document
            # so they survive the OpenSearch adapter's vector_point_to_document /
            # hit_to_search_result round-trip.  Top-level payload fields (other
            # than page_content / connectorIds / recordGroupIds) are dropped by
            # the adapter.
            point = VectorPoint(
                id=str(uuid.uuid4()),
                dense_vector=embedding,
                payload={
                    "page_content": query,
                    "metadata": {
                        "query_text": query,
                        "response_text": response_text,
                        "citations": citations or [],
                        "filters_hash": filters_hash,
                        "orgId": org_id,
                        "corpusRevision": corpus_revision,
                        "createdAt": int(time.time()),
                    },
                }
            )
            await self.vector_db.upsert_points(self.collection_name, [point])
            logger.info("Saved response to semantic cache.")
        except Exception as e:
            logger.error(f"Error writing to semantic cache: {e}", exc_info=True)

    async def bump_corpus_revision(
        self,
        org_id: str,
        graph_provider,
    ) -> str:
        """Increment the corpus revision and purge stale cache entries.

        This is the single entry-point for callers that need to advance the
        corpus revision (e.g. after a connector sync, a KB record deletion, or
        an ACL change).  It:

        1. Calls ``graph_provider.increment_corpus_revision(org_id)`` to
           obtain the new revision string.
        2. Immediately calls ``purge_stale_entries`` to delete every cache
           entry that still references an older revision.

        Returns the new revision string so callers can pass it downstream
        if needed.
        """
        new_revision = await graph_provider.increment_corpus_revision(org_id)
        await self.purge_stale_entries(org_id, new_revision)
        return new_revision

    async def purge_stale_entries(self, org_id: str, current_revision: str) -> None:
        """Delete all cache entries for *org_id* whose ``corpusRevision`` does
        not match *current_revision*.

        Call this after a corpus revision bump (connector sync, KB mutation,
        record deletion) rather than automatically on every cache write.
        Prefer ``bump_corpus_revision`` which combines both steps.
        """
        try:
            filter_expr = FilterExpression(
                must=[FieldCondition(key="metadata.orgId", value=org_id)],
                must_not=[FieldCondition(key="metadata.corpusRevision", value=current_revision)]
            )
            await self.vector_db.delete_points(self.collection_name, filter_expr)
            logger.debug(f"Purged stale semantic cache entries for org {org_id}")
        except Exception as e:
            logger.warning(f"Failed to purge stale semantic cache entries: {e}")


def hash_filters(scope: SemanticCacheScope) -> str:
    """Produce a stable SHA-256 hex digest for a SemanticCacheScope.

    Keys are sorted recursively (via json.dumps sort_keys=True) so that
    equivalent filter dicts with different insertion orders hash identically.
    None values are excluded via model_dump's exclude_none so they do not
    contribute to the hash.
    """
    canonical = json.dumps(
        scope.model_dump(exclude_none=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
