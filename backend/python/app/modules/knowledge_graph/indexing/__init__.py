"""Indexing pipeline — turns validated :class:`ExtractionEnvelope`s into
canonical graph writes (KG Clean Rebuild plan, Part C / Part E Layer 4).

``resolution`` is the only place that mints canonical entity identity;
extraction (``modules.knowledge_graph.extraction``, Phase 4) never does.

``temporal`` (Phase 6) is the bi-temporal graph-edge write/read seam built on
top of ``IGraphDBProvider``'s ``*_bitemporal_edge*`` methods; ``cross_app_linking``
(also Phase 6) uses it to bridge entities across connectors/apps that share
an exact hard-key value (e.g. ``email``) with a ``SAME_AS`` edge.
"""
