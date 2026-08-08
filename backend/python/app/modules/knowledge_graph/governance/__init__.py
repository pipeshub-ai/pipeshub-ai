"""EE governance for the knowledge graph (KG Clean Rebuild plan, Phase 7).

Everything here is a pure service — no FastAPI, no DI container — following
the same seam pattern as ``indexing/temporal.py`` and
``indexing/cross_app_linking.py``. Storage is entirely via
``IGraphDBProvider``'s existing *generic* node primitives
(``batch_upsert_nodes`` / ``get_nodes_by_filters`` / ``update_node``) plus the
Phase 6 bi-temporal edge methods — no new abstract provider methods were
needed for this phase.

Modules:
    merge.py        -- EntityMergeService: redirect bi-temporal edges from a
                        duplicate node onto a survivor and mark it merged.
    suggestions.py  -- MergeSuggestionStore: durable, per-org queue of
                        ambiguous LLM-adjudicated merge decisions awaiting
                        human review (feeds a ``list_suggestions`` API).
    ontology_store.py -- OntologyRegistryStore: persists OntologyDefinition
                        documents; implements RoutingEngine's OntologyLookup
                        protocol; exposes promote_type/deprecate_type.
"""
