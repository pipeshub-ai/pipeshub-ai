from dataclasses import dataclass, field
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
class VectorPoint:
    id: str
    dense_vector: Optional[List[float]] = None
    sparse_vector: Optional[SparseVector] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridSearchRequest:
    dense_query: Optional[List[float]] = None
    sparse_query: Optional[SparseVector] = None
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


@dataclass
class CollectionConfig:
    embedding_size: int = 1024
    distance_metric: DistanceMetric = DistanceMetric.COSINE
    enable_sparse: bool = True
    sparse_idf: bool = False


@dataclass
class ScrollResult:
    points: List[VectorPoint]
    next_offset: Optional[str] = None
