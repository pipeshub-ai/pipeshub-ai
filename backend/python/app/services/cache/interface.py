"""Domain-shaped cache interfaces, independent of any Redis command layout.

The Redis-backed implementations own key and serialization details; another
cache backend can implement the same domain operations independently.
"""

from __future__ import annotations

import hashlib
import math
import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

Loader = Callable[[], Awaitable[dict[str, str]]]


@dataclass(frozen=True)
class RetrievalCandidate:
    """A ranked vector-search reference, without document text or user data."""

    collection_name: str
    point_id: str
    virtual_record_id: str
    score: float

    def to_dict(self) -> dict[str, str | float]:
        return {
            "collection_name": self.collection_name,
            "point_id": self.point_id,
            "virtual_record_id": self.virtual_record_id,
            "score": self.score,
        }

    @classmethod
    def from_dict(cls, value: object) -> "RetrievalCandidate":
        if not isinstance(value, dict):
            raise ValueError("Candidate must be an object")

        collection_name = value.get("collection_name")
        point_id = value.get("point_id")
        virtual_record_id = value.get("virtual_record_id")
        score = value.get("score")
        if (
            not isinstance(collection_name, str)
            or not collection_name
            or not isinstance(point_id, str)
            or not point_id
            or not isinstance(virtual_record_id, str)
            or not virtual_record_id
        ):
            raise ValueError("Candidate identifiers must be non-empty strings")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError("Candidate score must be numeric")
        if not math.isfinite(score):
            raise ValueError("Candidate score must be finite")
        return cls(collection_name, point_id, virtual_record_id, float(score))


def validate_semantic_embedding(value: object) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError("Query embedding must be a non-empty sequence")
    if len(value) > 8192:
        raise ValueError("Query embedding exceeds the supported dimension limit")
    if any(
        isinstance(item, bool) or not isinstance(item, (int, float)) for item in value
    ):
        raise ValueError("Query embedding values must be numeric")
    embedding = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in embedding):
        raise ValueError("Query embedding values must be finite")
    if math.hypot(*embedding) == 0:
        raise ValueError("Query embedding must have a non-zero norm")
    return embedding


@dataclass(frozen=True)
class SemanticQueryScope:
    """Compatibility boundary for comparing query embeddings."""

    org_id: str
    collection_name: str
    embedding_config_hash: str
    vector_provider: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or not value
            for value in (
                self.org_id,
                self.collection_name,
                self.embedding_config_hash,
                self.vector_provider,
            )
        ):
            raise ValueError("Semantic query scope fields must be non-empty strings")

    def to_dict(self) -> dict[str, str]:
        return {
            "org_id": self.org_id,
            "collection_name": self.collection_name,
            "embedding_config_hash": self.embedding_config_hash,
            "vector_provider": self.vector_provider,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticQueryScope":
        if not isinstance(value, dict):
            raise ValueError("Semantic query scope must be an object")
        org_id = value.get("org_id")
        collection_name = value.get("collection_name")
        embedding_config_hash = value.get("embedding_config_hash")
        vector_provider = value.get("vector_provider")
        if not all(
            isinstance(item, str)
            for item in (
                org_id,
                collection_name,
                embedding_config_hash,
                vector_provider,
            )
        ):
            raise ValueError("Semantic query scope fields must be strings")
        return cls(
            org_id=org_id,
            collection_name=collection_name,
            embedding_config_hash=embedding_config_hash,
            vector_provider=vector_provider,
        )


@dataclass(frozen=True)
class SemanticQueryEntry:
    """A query embedding and a pointer to its candidate-reference cache entry."""

    scope: SemanticQueryScope
    normalized_query: str
    embedding: tuple[float, ...] | list[float]
    candidate_cache_key: str
    created_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if not isinstance(self.normalized_query, str):
            raise ValueError("Query text must be a string")
        normalized_query = " ".join(self.normalized_query.split())
        if not normalized_query or normalized_query != self.normalized_query:
            raise ValueError("Query text must be non-empty and whitespace-normalized")
        if (
            not isinstance(self.candidate_cache_key, str)
            or not self.candidate_cache_key
        ):
            raise ValueError("Candidate cache key must be a non-empty string")
        if (
            isinstance(self.created_at, bool)
            or not isinstance(self.created_at, (int, float))
            or not math.isfinite(self.created_at)
        ):
            raise ValueError("Creation time must be finite")
        object.__setattr__(
            self, "embedding", validate_semantic_embedding(self.embedding)
        )

    @property
    def entry_id(self) -> str:
        return hashlib.sha256(self.normalized_query.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, object]:
        return {
            "v": 1,
            "scope": self.scope.to_dict(),
            "normalized_query": self.normalized_query,
            "embedding": list(self.embedding),
            "candidate_cache_key": self.candidate_cache_key,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, value: object) -> "SemanticQueryEntry":
        if not isinstance(value, dict) or value.get("v") != 1:
            raise ValueError("Unsupported semantic query entry")
        normalized_query = value.get("normalized_query")
        candidate_cache_key = value.get("candidate_cache_key")
        created_at = value.get("created_at")
        if (
            not isinstance(normalized_query, str)
            or not isinstance(candidate_cache_key, str)
            or isinstance(created_at, bool)
            or not isinstance(created_at, (int, float))
        ):
            raise ValueError("Semantic query entry text fields must be strings")
        return cls(
            scope=SemanticQueryScope.from_dict(value.get("scope")),
            normalized_query=normalized_query,
            embedding=validate_semantic_embedding(value.get("embedding")),
            candidate_cache_key=candidate_cache_key,
            created_at=created_at,
        )


@dataclass(frozen=True)
class SemanticQueryMatch:
    entry: SemanticQueryEntry
    similarity: float


class IAccessibleRecordsCache(ABC):
    """Read-through cache of `virtualRecordId -> recordId` accessible-record maps."""

    @abstractmethod
    async def get_or_compute_kb(
        self, org_id: str, kb_id: str, loader: Loader
    ) -> dict[str, str]: ...

    @abstractmethod
    async def get_or_compute_app_connector(
        self, org_id: str, connector_id: str, loader: Loader
    ) -> dict[str, str]: ...

    @abstractmethod
    async def get_or_compute_user_connector(
        self, org_id: str, connector_id: str, user_id: str, loader: Loader
    ) -> dict[str, str]: ...

    @abstractmethod
    async def invalidate_connector(self, org_id: str, connector_id: str) -> None: ...

    @abstractmethod
    async def invalidate_kb(self, org_id: str, kb_id: str) -> None: ...

    @property
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class NoopAccessibleRecordsCache(IAccessibleRecordsCache):
    """Always-disabled cache: every call falls through to ``loader()``.

    Replaces the ad hoc ``enabled=False`` construction path that used to
    require every caller to know how to build a disabled
    ``AccessibleRecordsCache`` (with ``redis_client=None``); this is the same
    behaviour behind the interface, usable wherever the cache is optional.
    """

    async def get_or_compute_kb(
        self,
        org_id: str,
        kb_id: str,
        loader: Loader,  # noqa: ARG002
    ) -> dict[str, str]:
        return await loader()

    async def get_or_compute_app_connector(
        self,
        org_id: str,
        connector_id: str,
        loader: Loader,  # noqa: ARG002
    ) -> dict[str, str]:
        return await loader()

    async def get_or_compute_user_connector(
        self,
        org_id: str,
        connector_id: str,
        user_id: str,
        loader: Loader,  # noqa: ARG002
    ) -> dict[str, str]:
        return await loader()

    async def invalidate_connector(self, org_id: str, connector_id: str) -> None:  # noqa: ARG002
        return None

    async def invalidate_kb(self, org_id: str, kb_id: str) -> None:  # noqa: ARG002
        return None

    @property
    def enabled(self) -> bool:
        return False

    async def close(self) -> None:
        return None


class ISignedUrlCache(ABC):
    """Replaces ``blob_storage.get_shared_redis()``'s direct Redis access."""

    @abstractmethod
    async def get(self, key: str) -> str | None: ...

    @abstractmethod
    async def set(self, key: str, url: str, ttl_seconds: int) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


class NoopSignedUrlCache(ISignedUrlCache):
    """Used when the cache is disabled or Redis is unreachable; every read is
    a miss and every write is dropped, matching pre-cache behaviour."""

    async def get(self, key: str) -> str | None:  # noqa: ARG002
        return None

    async def set(self, key: str, url: str, ttl_seconds: int) -> None:  # noqa: ARG002
        return None

    async def close(self) -> None:
        return None


class IRetrievalCandidatesCache(ABC):
    """Shared cache of candidate references and semantic query entries."""

    @abstractmethod
    async def get_candidates(self, key: str) -> list[RetrievalCandidate] | None: ...

    @abstractmethod
    async def set_candidates(
        self, key: str, candidates: list[RetrievalCandidate]
    ) -> None: ...

    @abstractmethod
    async def set_semantic_entry(self, entry: SemanticQueryEntry) -> None: ...

    @abstractmethod
    async def get_similar_entries(
        self,
        scope: SemanticQueryScope,
        embedding: list[float],
        limit: int = 1,
    ) -> list[SemanticQueryMatch]: ...

    @property
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    async def close(self) -> None: ...


class NoopRetrievalCandidatesCache(IRetrievalCandidatesCache):
    """Disabled candidate cache: reads miss and writes are ignored."""

    async def get_candidates(
        self,
        key: str,  # noqa: ARG002
    ) -> list[RetrievalCandidate] | None:
        _ = key
        return None

    async def set_candidates(
        self,
        key: str,  # noqa: ARG002
        candidates: list[RetrievalCandidate],  # noqa: ARG002
    ) -> None:
        _ = (key, candidates)

    async def set_semantic_entry(self, entry: SemanticQueryEntry) -> None:
        _ = entry

    async def get_similar_entries(
        self,
        scope: SemanticQueryScope,
        embedding: list[float],
        limit: int = 1,
    ) -> list[SemanticQueryMatch]:
        _ = (scope, embedding, limit)
        return []

    @property
    def enabled(self) -> bool:
        return False

    async def close(self) -> None:
        return None
