"""Knowledge Graph rebuild package.

Houses the extraction/indexing/query stack described in the KG Clean Rebuild
plan: typed extraction envelopes and canonical graph contracts
(``contracts/``), the routing engine and extraction workers (``routing/``,
``extraction/``), entity resolution and bi-temporal graph writes
(``indexing/``), query-side filter-contract helpers (``query/``), and
governance (``governance/``).

Agent-facing tools stay under ``app/agents/actions/knowledge_graph/`` — this
package is the deterministic pipeline that feeds them, not a toolset itself.
"""
