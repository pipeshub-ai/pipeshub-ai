"""Filter contracts — how ``search_entities``-style agent tools discover
which structured filters are valid for a given (domain, type) pair.

Reliability tracks the same three-way split used for extraction routing
(KG Clean Rebuild plan, Part E §5.4): ontology fields are declared and
always 100% coverage; schema-free fields are inferred by profiling actual
extracted attributes and may have partial/noisy coverage. A filter field
absent from a type's contract must be rejected by the caller with a helpful
error — never silently dropped.
"""

from enum import Enum

from pydantic import BaseModel, Field

from app.modules.knowledge_graph.contracts.ontology import AttributeDataType
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class FilterFieldOperator(str, Enum):
    EQ = "eq"
    IN = "in"
    RANGE = "range"
    CONTAINS = "contains"


class FilterContractSource(str, Enum):
    """Mirrors ``EntityTypeCategory`` but names the *filter* reliability
    tier rather than the extraction-routing tier, since the two can diverge
    (e.g. a domain_schema_free type profiled with 100% coverage is still
    "inferred", just a confident one)."""
    DECLARED = "declared"
    INFERRED = "inferred"
    INFERRED_LOW_CONFIDENCE = "inferred_low_confidence"


class FilterField(BaseModel):
    field: str = Field(description="Canonical field name after alias normalization")
    raw_aliases: list[str] = Field(default_factory=list)
    data_type: AttributeDataType = AttributeDataType.STRING
    top_values: list[str] = Field(default_factory=list)
    coverage: float = Field(default=1.0, ge=0.0, le=1.0)
    operators: list[FilterFieldOperator] = Field(default_factory=lambda: [FilterFieldOperator.EQ])


class FilterContract(BaseModel):
    """Served by ``get_type_details`` (Phase 5) — the whole point of this
    model is that it never merges field semantics across two different
    types, even within the same tool call."""
    type_name: str
    domain: str = ""
    source: FilterContractSource
    fields: list[FilterField] = Field(default_factory=list)

    def field_names(self) -> set[str]:
        return {f.field for f in self.fields}

    def get_field(self, name: str) -> FilterField | None:
        return next((f for f in self.fields if f.field == name), None)


class AttributeProfile(BaseModel):
    """Background-job output for schema-free types — the source of a
    non-ontology :class:`FilterContract`. Ontology types never need this;
    their filter contract comes straight from the registry definition."""
    org_id: str
    domain: str
    canonical_type: str
    profiled_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")
    sample_size: int = 0
    fields: list[FilterField] = Field(default_factory=list)
    coverage_floor: float = Field(
        default=0.3, description="Fields below this coverage are omitted from the served contract"
    )

    def to_filter_contract(self, *, low_confidence: bool = False) -> FilterContract:
        source = (
            FilterContractSource.INFERRED_LOW_CONFIDENCE
            if low_confidence
            else FilterContractSource.INFERRED
        )
        served_fields = [f for f in self.fields if f.coverage >= self.coverage_floor]
        return FilterContract(
            type_name=self.canonical_type,
            domain=self.domain,
            source=source,
            fields=served_fields,
        )
