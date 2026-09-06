from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Union


FilterValue = Union[str, int, float, bool, List[Union[str, int, float, bool]]]


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


@dataclass
class SparseVector:
    indices: List[int]
    values: List[float]


@dataclass
class FieldCondition:
    key: str
    value: Optional[FilterValue] = None
    values: Optional[List[Union[str, int, float, bool]]] = None


@dataclass
class FilterExpression:
    must: List[FieldCondition] = field(default_factory=list)
    should: List[FieldCondition] = field(default_factory=list)
    must_not: List[FieldCondition] = field(default_factory=list)
    min_should_match: Optional[int] = None

    def is_empty(self) -> bool:
        return not self.must and not self.should and not self.must_not


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
    """
    orgId: Optional[str] = None
    virtualRecordId: Optional[str] = None
    blockId: Optional[str] = None
    blockIndex: Optional[int] = None
    blockType: Optional[str] = None
    isImage: Optional[bool] = None


@dataclass
class VectorChunkPayload:
    """Typed payload for vector points crossing provider boundaries.

    ``connectorIds`` and ``recordGroupIds`` are VRID-level arrays stored as
    siblings of ``metadata`` / ``page_content`` so filter-based ``set_payload``
    can rewrite them without wiping chunk metadata.
    """
    page_content: str = ""
    metadata: VectorChunkMetadata = field(default_factory=VectorChunkMetadata)
    connectorIds: List[str] = field(default_factory=list)
    recordGroupIds: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
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
    def from_dict(cls, data: Dict[str, Any]) -> "VectorChunkPayload":
        meta_raw = data.get("metadata") or {}
        metadata = VectorChunkMetadata(
            orgId=meta_raw.get("orgId"),
            virtualRecordId=meta_raw.get("virtualRecordId"),
            blockId=meta_raw.get("blockId"),
            blockIndex=meta_raw.get("blockIndex"),
            blockType=meta_raw.get("blockType"),
            isImage=meta_raw.get("isImage"),
        )
        return cls(
            page_content=data.get("page_content", ""),
            metadata=metadata,
            connectorIds=_as_str_list(data.get("connectorIds")),
            recordGroupIds=_as_str_list(data.get("recordGroupIds")),
        )


def _as_str_list(value: Any) -> List[str]:
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
    dense_vector: Optional[List[float]] = None
    sparse_vector: Optional[SparseVector] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridSearchRequest:
    dense_query: Optional[List[float]] = None
    sparse_query: Optional[SparseVector] = None
    # Always populated by retrieval; used by Redis/OpenSearch as the lexical leg
    text_query: Optional[str] = None
    filter: Optional[FilterExpression] = None
    limit: int = 10
    fusion_method: FusionMethod = FusionMethod.RRF
    with_payload: bool = True


@dataclass
class SearchResult:
    id: str
    score: float
    payload: Dict[str, Any] = field(default_factory=dict)


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
    m: Optional[int] = None               # Number of bi-directional links per node
    ef_construct: Optional[int] = None    # Construction-time ef (higher = better recall, slower build)
    ef: Optional[int] = None              # Query-time ef (higher = better recall, slower query)
    full_scan_threshold: Optional[int] = None  # Qdrant: fallback to full-scan below this count


@dataclass
class MRLConfig:
    """Matryoshka Representation Learning (MRL) dimension-reduction config.

    Set ``dimensions`` to an integer < ``CollectionConfig.embedding_size`` to
    instruct the provider to store and search only the leading N dimensions.
    Providers that do not support MRL should raise ``NotImplementedError``.
    """
    dimensions: Optional[int] = None     # Target reduced dimensionality (None = full size)


@dataclass
class CollectionConfig:
    embedding_size: int = 1024
    distance_metric: DistanceMetric = DistanceMetric.COSINE
    enable_sparse: bool = True
    sparse_idf: bool = False
    # Performance / accuracy tuning knobs.
    # quantization defaults to SCALAR to preserve the original INT8 scalar behaviour.
    # Set to NONE to explicitly disable quantization.
    quantization: QuantizationType = QuantizationType.SCALAR
    hnsw: Optional[HNSWConfig] = None
    mrl: Optional[MRLConfig] = None
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


@dataclass
class ScrollResult:
    points: List[VectorPoint]
    next_offset: Optional[str] = None


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
    supported_fusion_methods: List[FusionMethod] = field(
        default_factory=lambda: [FusionMethod.RRF]
    )
    supports_multi_collection: bool = True
    max_recommended_collections: int | None = None
    score_semantics: ScoreSemantics = ScoreSemantics.RANK_FUSED


@dataclass
class VectorDBHealth:
    status: HealthStatus = HealthStatus.HEALTHY
    latency_ms: float = 0.0
    server_version: Optional[str] = None
    message: Optional[str] = None


@dataclass
class VectorCollectionInfo:
    """Provider-neutral collection metadata."""
    name: str
    exists: bool = False
    dense_dimension: Optional[int] = None
    points_count: int = 0


def to_generic_sparse_vector(sparse: Any) -> SparseVector:
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
