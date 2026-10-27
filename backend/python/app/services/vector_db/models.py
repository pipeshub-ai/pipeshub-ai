from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

FilterValue = str | int | float | bool | list[str | int | float | bool]


class FilterMode(Enum):
    MUST = "must"
    SHOULD = "should"
    MUST_NOT = "must_not"


class DistanceMetric(Enum):
    COSINE = "cosine"
    L2 = "l2"
    DOT_PRODUCT = "dot_product"


class FusionMethod(Enum):
    RRF = "rrf"
    ARITHMETIC_MEAN = "arithmetic_mean"
    HARMONIC_MEAN = "harmonic_mean"


class ScoreSemantics(Enum):
    """What a provider's ``SearchResult.score`` actually measures.

    Only matters once one search spans more than one collection. Within a
    single result list either kind ranks correctly, so nothing depended on the
    distinction until a multi-collection strategy made merging possible.

    SIMILARITY: a distance/similarity in the embedding space. Two collections'
        scores are directly comparable, because the same model and metric
        produced both, so merging is an ordinary sort.

    RANK_FUSED: the output of a rank fusion (RRF and friends), i.e. a function
        of a point's *position within its own result list* rather than of how
        well it matched. Every provider here fuses internally — Qdrant with a
        FusionQuery, OpenSearch with a score-ranker pipeline, Redis inside
        FT.HYBRID — even when only a dense leg is present. Comparing these
        across collections compares ranks: the best hit in a tiny, irrelevant
        collection scores the same as the best hit in a huge, relevant one.
        Merging must re-fuse the rank lists instead of sorting the numbers.
    """

    SIMILARITY = "similarity"
    RANK_FUSED = "rank_fused"


class HealthStatus(Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class SearchFailureKind(Enum):
    """Why a vector store could not answer a search, when trying again may help."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


@dataclass
class SparseVector:
    indices: list[int]
    values: list[float]


@dataclass
class FieldCondition:
    """One filter predicate on a payload field.

    ``values_count_lte`` bounds how many elements an array field holds. Only
    meaningful for the membership arrays (``connectorIds``,
    ``recordGroupIds``), and only ever valid in a MUST clause — callers apply
    the bound conjunctively, so under ``must_not`` it would come back with its
    meaning inverted.
    """

    key: str
    value: FilterValue | None = None
    values: list[str | int | float | bool] | None = None
    values_count_lte: int | None = None


@dataclass
class FilterExpression:
    must: list[FieldCondition] = field(default_factory=list)
    should: list[FieldCondition] = field(default_factory=list)
    must_not: list[FieldCondition] = field(default_factory=list)
    min_should_match: int | None = None

    def is_empty(self) -> bool:
        return not self.must and not self.should and not self.must_not

    def has_positive_match(self) -> bool:
        """Whether anything here matches on a *value* rather than only a count.

        A point whose array field is absent satisfies an array-length bound
        too, so a count-only expression matches most of a collection. Delete
        paths refuse one on that basis.

        ``must_not`` never counts: it only ever *subtracts*, so a term there is
        not the positive match a bound needs. Counting it would let
        ``filter_collection(MUST_NOT, max_values=..., virtualRecordId=x)``, a
        single public call, pass every provider's delete guard and wipe the
        collection bar one record.

        ``should`` counts only when nothing in ``must`` bounds an array length.
        A SHOULD clause on its own is a real constraint — every provider
        requires one of them to match when there is no MUST beside it — and the
        legacy id-shipping delete relies on exactly that. Put a length bound in
        MUST next to it, though, and the picture inverts: OpenSearch makes SHOULD
        optional as soon as a MUST exists (``min_should_match=0`` says so
        outright), which leaves the bound standing alone as the whole filter —
        and a point whose field is absent satisfies a bound.
        """
        def _matches_a_value(conds: list[FieldCondition]) -> bool:
            return any(
                cond.value is not None or cond.values is not None
                for cond in conds
            )

        if _matches_a_value(self.must):
            return True
        if any(cond.values_count_lte is not None for cond in self.must):
            return False
        return _matches_a_value(self.should)


@dataclass
class VectorChunkMetadata:
    """Known metadata fields stored on indexed vector points.

    blockType: mirrors ``app.models.blocks.BlockType`` (e.g. "text", "image",
        "table_row", "record_summary", "sql_table", "sql_view") — set on every
        point at indexing time so retrieval can filter/branch on it without
        sniffing ``page_content`` (e.g. base64 detection). Points written
        before this field existed simply omit it (``None``); callers must
        treat ``None`` as "unknown" and fall back to legacy heuristics.
    isImage: True when the point's dense vector is a native image embedding
        (i.e. produced by a multimodal embedding provider from raw image
        bytes) rather than a text embedding of a description. Absent/None on
        VLM-description-fallback image points, which are text embeddings and
        should be treated like any other text point for search purposes.
    isBlock / isBlockGroup: whether the point embeds one block or a block group.
    isRecordSummary: True on the point holding the record's summary.
    blockGroupIndex: the group's index, on table and SQL group points.
    """
    orgId: str | None = None
    virtualRecordId: str | None = None
    blockId: str | None = None
    blockIndex: int | None = None
    blockType: str | None = None
    isImage: bool | None = None
    isBlock: bool | None = None
    isBlockGroup: bool | None = None
    isRecordSummary: bool | None = None
    blockGroupIndex: int | None = None


@dataclass
class VectorChunkPayload:
    """Typed payload for vector points crossing provider boundaries.

    ``connectorIds`` and ``recordGroupIds`` are VRID-level arrays stored as
    siblings of ``metadata`` / ``page_content`` so filter-based ``set_payload``
    can rewrite them without wiping chunk metadata.
    """
    page_content: str = ""
    metadata: VectorChunkMetadata = field(default_factory=VectorChunkMetadata)
    connectorIds: list[str] = field(default_factory=list)
    recordGroupIds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        meta = {
            k: v for k, v in asdict(self.metadata).items() if v is not None
        }
        return {
            "page_content": self.page_content,
            "metadata": meta,
            "connectorIds": list(self.connectorIds),
            "recordGroupIds": list(self.recordGroupIds),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VectorChunkPayload":
        meta_raw = data.get("metadata") or {}
        metadata = VectorChunkMetadata(
            orgId=meta_raw.get("orgId"),
            virtualRecordId=meta_raw.get("virtualRecordId"),
            blockId=meta_raw.get("blockId"),
            blockIndex=meta_raw.get("blockIndex"),
            blockType=meta_raw.get("blockType"),
            isImage=meta_raw.get("isImage"),
            isBlock=meta_raw.get("isBlock"),
            isBlockGroup=meta_raw.get("isBlockGroup"),
            isRecordSummary=meta_raw.get("isRecordSummary"),
            blockGroupIndex=meta_raw.get("blockGroupIndex"),
        )
        return cls(
            page_content=data.get("page_content", ""),
            metadata=metadata,
            connectorIds=_as_str_list(data.get("connectorIds")),
            recordGroupIds=_as_str_list(data.get("recordGroupIds")),
        )


def _as_str_list(value: Any) -> list[str]:  # noqa: ANN401
    if not value:
        return []
    if isinstance(value, str):
        return [p.strip() for p in value.split(",") if p.strip()]
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if v is not None and str(v).strip()]
    return []


@dataclass
class VectorPoint:
    id: str
    dense_vector: list[float] | None = None
    sparse_vector: SparseVector | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridSearchRequest:
    dense_query: list[float] | None = None
    sparse_query: SparseVector | None = None
    # Always populated by retrieval; used by Redis/OpenSearch as the lexical leg
    text_query: str | None = None
    filter: FilterExpression | None = None
    limit: int = 10
    fusion_method: FusionMethod = FusionMethod.RRF
    with_payload: bool = True


@dataclass
class SearchResult:
    id: str
    score: float
    payload: dict[str, Any] = field(default_factory=dict)


class QuantizationType(Enum):
    """Quantization strategy for dense vectors.

    - NONE  — no quantization, full float32 stored (default, backward-compatible).
    - SCALAR — INT8 scalar quantization (Qdrant scalar quantization).
    - PRODUCT — Product quantization (Qdrant PQ; higher compression, some recall loss).
    - BINARY — Binary / 1-bit quantization (Qdrant binary quantization).
    """
    NONE = "none"
    SCALAR = "scalar"
    PRODUCT = "product"
    BINARY = "binary"


@dataclass
class HNSWConfig:
    """HNSW index tuning knobs.

    All fields default to ``None`` which means "use the provider's own defaults".
    Providers should only forward non-None values to the underlying index.
    """
    m: int | None = None               # Number of bi-directional links per node
    ef_construct: int | None = None    # Construction-time ef (higher = better recall, slower build)
    ef: int | None = None              # Query-time ef (higher = better recall, slower query)
    full_scan_threshold: int | None = None  # Qdrant: fallback to full-scan below this count


@dataclass
class MRLConfig:
    """Matryoshka Representation Learning (MRL) dimension-reduction config.

    Set ``dimensions`` to an integer < ``CollectionConfig.embedding_size`` to
    instruct the provider to store and search only the leading N dimensions.
    Providers that do not support MRL should raise ``NotImplementedError``.
    """
    dimensions: int | None = None     # Target reduced dimensionality (None = full size)


@dataclass
class CollectionConfig:
    embedding_size: int = 1024
    distance_metric: DistanceMetric = DistanceMetric.COSINE
    enable_sparse: bool = True
    # The sparse leg is fastembed's Qdrant/bm25, which emits term-frequency
    # weights only and relies on the store for IDF. Without it a common word
    # scores like a rare name, and hybrid search ranks below dense-only.
    sparse_idf: bool = True
    # Performance / accuracy tuning knobs.
    # quantization defaults to SCALAR to preserve the original INT8 scalar behaviour.
    # Set to NONE to explicitly disable quantization.
    quantization: QuantizationType = QuantizationType.SCALAR
    hnsw: HNSWConfig | None = None
    mrl: MRLConfig | None = None
    # Keep bulk structures memory-mapped rather than RAM-resident. Page cache is
    # reclaimable under a cgroup limit; anonymous memory is not, so pinning these
    # is what turns a large collection into an OOM kill instead of a slow query.
    on_disk_vectors: bool = True
    on_disk_sparse: bool = True
    on_disk_hnsw: bool = True
    # The quantized copy is ~1/4 the size of the originals but still scales linearly
    # with point count (1 byte per dimension per point), so pinning it only fits
    # small collections. Operators with RAM to spare can set this True for speed.
    quantization_always_ram: bool = False


class ScrollWindowExceededError(RuntimeError):
    """A scroll offset lies past what the provider can page to (RediSearch's
    MAXSEARCHRESULTS). Every page before it was returned; the rest cannot be."""


@dataclass
class ScrollResult:
    points: list[VectorPoint]
    next_offset: str | None = None


# ---------------------------------------------------------------------------
# Capability and health types (new)
# ---------------------------------------------------------------------------

@dataclass
class VectorDBCapabilities:
    """Describes what a provider natively supports.

    supports_sparse_vectors: provider stores/queries client-computed sparse
        vectors (e.g. FastEmbed BM25 indices/values).  If False, the lexical
        leg is handled server-side via text search (Redis BM25 / OpenSearch BM25).
    supports_server_side_text_search: provider performs lexical search
        internally when text_query is supplied (Redis FT.HYBRID / OpenSearch match).
    supported_fusion_methods: fusion algorithms the provider supports.
    supports_multi_collection: provider can hold many independent collections
        without a shared-infrastructure penalty (all current providers do;
        exists so a future provider without this capability can opt out and
        CollectionRegistry can fail fast rather than silently degrading).
    max_recommended_collections: soft ceiling used only for a startup warning
        when a multi-collection strategy's projected collection count exceeds
        it (Qdrant ~1000, OpenSearch ~200, Redis ~500). None means "no known
        practical limit for typical deployments".
    score_semantics: what ``SearchResult.score`` measures, which decides how a
        search spanning several collections may merge them. Defaults to
        RANK_FUSED because every provider here fuses internally; a provider
        returning raw similarity should say so and get a cheaper merge.
    """
    supports_sparse_vectors: bool = False
    supports_server_side_text_search: bool = False
    supported_fusion_methods: list[FusionMethod] = field(
        default_factory=lambda: [FusionMethod.RRF]
    )
    supports_multi_collection: bool = True
    max_recommended_collections: int | None = None
    score_semantics: ScoreSemantics = ScoreSemantics.RANK_FUSED


@dataclass
class VectorDBHealth:
    status: HealthStatus = HealthStatus.HEALTHY
    latency_ms: float = 0.0
    server_version: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class CollectionMemoryFootprint:
    """The part of one collection that every search touches, in bytes."""

    name: str
    points: int
    quantization: str
    always_ram: bool
    quantized_vectors_bytes: int
    hnsw_bytes: int
    sparse_index_bytes: int
    payload_index_bytes: int

    @property
    def index_bytes(self) -> int:
        return (
            self.quantized_vectors_bytes
            + self.hnsw_bytes
            + self.sparse_index_bytes
            + self.payload_index_bytes
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "points": self.points,
            "quantization": self.quantization,
            "alwaysRam": self.always_ram,
            "indexBytes": self.index_bytes,
            "quantizedVectorsBytes": self.quantized_vectors_bytes,
            "hnswBytes": self.hnsw_bytes,
            "sparseIndexBytes": self.sparse_index_bytes,
            "payloadIndexBytes": self.payload_index_bytes,
        }


@dataclass
class VectorDBMemoryReport:
    """How much memory the vector database needs for fast search, against what it has."""

    status: str
    needed_bytes: int
    index_bytes: int
    base_bytes: int
    available_bytes: int | None
    available_source: str | None
    points: int
    nodes: int
    collections: list[CollectionMemoryFootprint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # The HTTP body: the verdict and totals an admin needs to size Qdrant.
        # Per-collection names and counts stay server-side.
        return {
            "status": self.status,
            "neededBytes": self.needed_bytes,
            "indexBytes": self.index_bytes,
            "baseBytes": self.base_bytes,
            "availableBytes": self.available_bytes,
            "availableSource": self.available_source,
            "points": self.points,
            "nodes": self.nodes,
        }


@dataclass
class VectorCollectionInfo:
    """Provider-neutral collection metadata."""
    name: str
    exists: bool = False
    dense_dimension: int | None = None
    points_count: int = 0


def to_generic_sparse_vector(sparse: Any) -> SparseVector:  # noqa: ANN401
    """Convert various sparse embedding formats to a generic SparseVector.

    Works for:
    - SparseVector instances (pass-through)
    - Objects with .indices / .values attributes (FastEmbed output)
    - Dicts with 'indices' / 'values' keys
    """
    if isinstance(sparse, SparseVector):
        return sparse
    if hasattr(sparse, "indices") and hasattr(sparse, "values"):
        return SparseVector(indices=list(sparse.indices), values=list(sparse.values))
    if isinstance(sparse, dict) and "indices" in sparse and "values" in sparse:
        return SparseVector(indices=sparse["indices"], values=sparse["values"])
    raise ValueError(f"Cannot convert {type(sparse)} to SparseVector")
