"""Persists :class:`OntologyDefinition` documents and implements the
``RoutingEngine`` ``OntologyLookup`` protocol (KG Clean Rebuild plan, Phase 7
/ Part E governance "promote_type"/"deprecate_type").

``routing/engine.py`` already defines the seam this fills in:

    ``OntologyLookup`` -- "storage of whatever stores compiled ontologies
    (registry service, EE governance store, ...)"

Promotion/deprecation only ever mutate an ontology's ``entity_types`` list
and ``status`` — they never touch already-written graph data. Per Part F
governance & versioning: deprecating a type doesn't delete existing graph
nodes of that type, it only stops *new* extraction from producing it (the
routing engine simply won't see it in the ontology it's handed next time).

Storage: same generic node primitives as ``suggestions.py``
(``batch_upsert_nodes`` / ``get_nodes_by_filters`` / ``update_node``) against
a dedicated ``kgOntologies`` collection — one document per ``ontology_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.modules.knowledge_graph.contracts.ontology import (
    OntologyDefinition,
    OntologyEntityType,
    OntologyStatus,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

_COLLECTION = CollectionNames.KG_ONTOLOGIES.value


class OntologyGovernanceError(Exception):
    """Raised on a promote/deprecate request that can't be satisfied
    (unknown ontology, duplicate type name, etc.)."""


class OntologyRegistryStore:
    """Implements ``app.modules.knowledge_graph.routing.engine.OntologyLookup``
    and adds the admin-facing promote/deprecate operations on top.
    """

    def __init__(self, graph_provider: "IGraphDBProvider", logger: logging.Logger) -> None:
        self.graph_provider = graph_provider
        self.logger = logger

    # ------------------------------------------------------------------
    # OntologyLookup protocol (consumed by RoutingEngine)
    # ------------------------------------------------------------------

    async def get_active_ontology(
        self, org_id: str, domain: str, doc_type: str
    ) -> OntologyDefinition | None:
        """Return the org's ACTIVE ontology for ``domain``, if any.

        ``doc_type`` is unused today (part of the ``OntologyLookup`` protocol
        signature — see the class docstring for why).

        ``doc_type``-level applicability gating (per plan §3.1) is left to a
        future per-ontology "applicability check" — out of scope for Phase 7,
        which only needs a domain-level active/draft/deprecated ontology to
        exist for promote/deprecate to operate on.
        """
        rows = await self.graph_provider.get_nodes_by_filters(
            _COLLECTION, {"orgId": org_id, "domain": domain, "status": OntologyStatus.ACTIVE.value},
        )
        if not rows:
            return None
        return self._to_definition(rows[0])

    # ------------------------------------------------------------------
    # Admin CRUD
    # ------------------------------------------------------------------

    async def list_ontologies(self, org_id: str) -> list[OntologyDefinition]:
        if not org_id:
            return []
        rows = await self.graph_provider.get_nodes_by_filters(_COLLECTION, {"orgId": org_id})
        definitions = []
        for row in rows:
            try:
                definitions.append(self._to_definition(row))
            except Exception as exc:
                self.logger.warning("OntologyRegistryStore: skipping malformed ontology doc: %s", exc)
        return definitions

    async def get_ontology(self, org_id: str, ontology_id: str) -> OntologyDefinition | None:
        doc = await self.graph_provider.get_document(ontology_id, _COLLECTION)
        if not doc or doc.get("orgId") != org_id:
            return None
        return self._to_definition(doc)

    async def promote_type(
        self,
        org_id: str,
        domain: str,
        type_name: str,
        *,
        description: str = "",
        ontology_id: str | None = None,
    ) -> OntologyDefinition:
        """Promote a novel/schema-free type name into an ontology's closed
        type list, creating a new draft ontology for ``domain`` if one
        doesn't exist yet. A newly-created ontology starts in ``draft`` —
        an admin still has to separately activate it (via a follow-up
        ``update_status`` call) before ``RoutingEngine`` will route to it,
        so a single promotion can't silently flip live extraction behavior.
        """
        if not org_id or not domain or not type_name:
            raise OntologyGovernanceError("org_id, domain and type_name are required")

        target_id = ontology_id or f"{domain}-default"
        existing = await self.get_ontology(org_id, target_id)
        if existing is None:
            definition = OntologyDefinition(
                ontology_id=target_id,
                version="0.1.0",
                org_id=org_id,
                domain=domain,
                status=OntologyStatus.DRAFT,
                entity_types=[OntologyEntityType(name=type_name, description=description)],
            )
        else:
            if type_name in existing.entity_type_names():
                raise OntologyGovernanceError(f"Type '{type_name}' is already in ontology '{target_id}'")
            definition = existing.model_copy(
                update={
                    "entity_types": [
                        *existing.entity_types,
                        OntologyEntityType(name=type_name, description=description),
                    ],
                    "updated_at": get_epoch_timestamp_in_ms(),
                }
            )
        await self._save(definition)
        return definition

    async def deprecate_type(self, org_id: str, ontology_id: str, type_name: str) -> OntologyDefinition:
        """Remove ``type_name`` from an ontology's active type list. Existing
        graph nodes of this type are untouched (see module docstring) — this
        only affects what future extraction is told to look for.
        """
        definition = await self.get_ontology(org_id, ontology_id)
        if definition is None:
            raise OntologyGovernanceError(f"Ontology '{ontology_id}' not found for this org")
        remaining = [t for t in definition.entity_types if t.name != type_name]
        if len(remaining) == len(definition.entity_types):
            raise OntologyGovernanceError(f"Type '{type_name}' is not in ontology '{ontology_id}'")
        updated = definition.model_copy(
            update={"entity_types": remaining, "updated_at": get_epoch_timestamp_in_ms()}
        )
        await self._save(updated)
        return updated

    async def update_status(self, org_id: str, ontology_id: str, status: OntologyStatus) -> OntologyDefinition:
        definition = await self.get_ontology(org_id, ontology_id)
        if definition is None:
            raise OntologyGovernanceError(f"Ontology '{ontology_id}' not found for this org")
        updated = definition.model_copy(
            update={"status": status, "updated_at": get_epoch_timestamp_in_ms()}
        )
        await self._save(updated)
        return updated

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _save(self, definition: OntologyDefinition) -> None:
        doc = definition.model_dump(mode="json")
        doc["id"] = doc.pop("ontology_id")
        doc["orgId"] = doc.pop("org_id")
        await self.graph_provider.batch_upsert_nodes([doc], collection=_COLLECTION)

    @staticmethod
    def _to_definition(row: dict[str, Any]) -> OntologyDefinition:
        row = dict(row)
        ontology_id = row.pop("id", None) or row.pop("_key", None)
        org_id = row.pop("orgId", None)
        for meta_field in ("_id", "_rev"):
            row.pop(meta_field, None)
        return OntologyDefinition(ontology_id=ontology_id, org_id=org_id, **row)


__all__ = ["OntologyGovernanceError", "OntologyRegistryStore"]
