"""Ontology registry contracts (KG Clean Rebuild plan, Part D / §4.2 of the
original design note).

The compiled, structured form defined here is always the runtime
representation used for prompting and validation — natural-language or
spreadsheet authoring (out of scope for this package) compiles down to this
shape before it is ever used at extraction time.
"""

from enum import Enum

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from app.utils.time_conversion import get_epoch_timestamp_in_ms


class OntologyStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class AttributeDataType(str, Enum):
    STRING = "string"
    NUMBER = "number"
    DATE = "date"
    BOOLEAN = "boolean"
    ENUM = "enum"
    REFERENCE = "reference"


class RelationshipCardinality(str, Enum):
    ONE_TO_ONE = "one-to-one"
    ONE_TO_MANY = "one-to-many"
    MANY_TO_MANY = "many-to-many"


class OntologyAttribute(BaseModel):
    name: str
    data_type: AttributeDataType
    required: bool = False
    allowed_values: list[str] | None = None


class OntologyEntityType(BaseModel):
    name: str
    description: str = ""
    attributes: list[OntologyAttribute] = Field(default_factory=list)


class OntologyRelationshipType(BaseModel):
    name: str
    domain_type: str = Field(description="Allowed subject entity type name")
    range_type: str = Field(description="Allowed object entity type name")
    cardinality: RelationshipCardinality = RelationshipCardinality.MANY_TO_MANY


class OntologyDefinition(BaseModel):
    """A versioned, closed-type schema for one domain.

    Extraction envelopes record the exact ``ontology_id`` + ``version`` used
    so re-extraction/backfill is always reproducible against a known schema
    state (Part F, governance & versioning).
    """
    ontology_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    org_id: str
    domain: str
    status: OntologyStatus = OntologyStatus.DRAFT
    entity_types: list[OntologyEntityType] = Field(default_factory=list)
    relationship_types: list[OntologyRelationshipType] = Field(default_factory=list)
    created_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")
    updated_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")

    def entity_type_names(self) -> set[str]:
        return {t.name for t in self.entity_types}

    def get_entity_type(self, name: str) -> OntologyEntityType | None:
        return next((t for t in self.entity_types if t.name == name), None)

    @field_validator("relationship_types")
    @classmethod
    def _relationship_types_reference_known_entities(
        cls, v: list[OntologyRelationshipType], info: ValidationInfo
    ) -> list[OntologyRelationshipType]:
        entity_types = info.data.get("entity_types") or []
        known = {t.name for t in entity_types}
        if not known:
            return v
        for rel in v:
            if rel.domain_type not in known or rel.range_type not in known:
                raise ValueError(
                    f"Relationship type '{rel.name}' references an entity type "
                    f"not declared in this ontology (domain={rel.domain_type!r}, "
                    f"range={rel.range_type!r})"
                )
        return v
