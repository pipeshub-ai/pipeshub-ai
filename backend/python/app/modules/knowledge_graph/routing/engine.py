"""RoutingEngine — decides which extraction mode handles a document.

Decision table (plan Part B §3.1), evaluated top-down:

    1. An ACTIVE ontology is registered for (org, domain) and applicable
       to this doc_type                                -> ontology_guided
    2. A soft vocabulary is registered for this domain  -> domain_schema_free
    3. Neither exists                                   -> generic_schema_free

The routing decision itself is a persisted record (``RoutingDecision``), not
just a transient return value, so re-extraction/audits can see exactly why a
document went down a given path.
"""

import uuid
from typing import Protocol

from pydantic import BaseModel, Field

from app.modules.knowledge_graph.contracts.envelope import ExtractionMode
from app.modules.knowledge_graph.contracts.ontology import (
    OntologyDefinition,
    OntologyStatus,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class OntologyLookup(Protocol):
    """Seam to whatever stores compiled ontologies (registry service, EE
    governance store, ...). Kept as a narrow protocol so the routing engine
    has no storage dependency of its own.
    """

    async def get_active_ontology(
        self, org_id: str, domain: str, doc_type: str
    ) -> OntologyDefinition | None:
        """Return the applicable ACTIVE ontology for this (org, domain,
        doc_type), or ``None`` if none is registered/applicable."""
        ...


class RoutingDecision(BaseModel):
    routing_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str
    org_id: str
    domain: str
    doc_type: str
    mode: ExtractionMode
    ontology_id: str | None = None
    rule_matched: str
    decided_at: int = Field(default_factory=get_epoch_timestamp_in_ms, description="Epoch ms")


class RoutingEngine:
    """Evaluates the routing decision table for one document.

    ``domain_vocabularies`` maps domain -> a non-empty soft vocabulary (list
    of type names); presence of a key (regardless of list contents) is what
    routes to ``domain_schema_free`` — an empty vocabulary is treated the
    same as "not yet authored" and falls back to generic.
    """

    def __init__(
        self,
        ontology_lookup: OntologyLookup | None = None,
        domain_vocabularies: dict[str, list[str]] | None = None,
    ) -> None:
        self._ontology_lookup = ontology_lookup
        self._domain_vocabularies = domain_vocabularies or {}

    async def decide(
        self,
        *,
        document_id: str,
        org_id: str,
        domain: str,
        doc_type: str,
    ) -> RoutingDecision:
        if self._ontology_lookup is not None:
            ontology = await self._ontology_lookup.get_active_ontology(
                org_id, domain, doc_type
            )
            if ontology is not None and ontology.status == OntologyStatus.ACTIVE:
                return RoutingDecision(
                    document_id=document_id,
                    org_id=org_id,
                    domain=domain,
                    doc_type=doc_type,
                    mode=ExtractionMode.ONTOLOGY_GUIDED,
                    ontology_id=ontology.ontology_id,
                    rule_matched="rule_1_active_ontology",
                )

        if self._domain_vocabularies.get(domain):
            return RoutingDecision(
                document_id=document_id,
                org_id=org_id,
                domain=domain,
                doc_type=doc_type,
                mode=ExtractionMode.DOMAIN_SCHEMA_FREE,
                rule_matched="rule_2_domain_vocabulary",
            )

        return RoutingDecision(
            document_id=document_id,
            org_id=org_id,
            domain=domain,
            doc_type=doc_type,
            mode=ExtractionMode.GENERIC_SCHEMA_FREE,
            rule_matched="rule_3_fallback_generic",
        )
