"""Put every extension point back to its default, so a test never sees what another one installed."""
from __future__ import annotations


def reset_extensions() -> None:
    from app.config.constants import neo4j as neo4j_constants
    from app.connectors import record_views
    from app.modules.indexing import extension
    from app.modules.retrieval import content_locator
    from app.services.vector_db import write_scope
    from app.services.vector_db.const import const

    extension.reset()
    content_locator.reset_locator_factory()
    write_scope.reset_scope_resolver()
    const._reset_payload_registry()
    record_views.reset_decorator()
    reset_labels = getattr(neo4j_constants, "_reset_registered_labels", None)
    if reset_labels is not None:
        reset_labels()
