"""The entity store behind the knowledge-graph entity tools, as one request (legacy) or one durable run sees it.

Shared by the query service's routes and the durable worker's assembly (DUR3-B2), so both loops offer
``search_entities`` and ``find_records_by_entity`` under the same rule.
"""

from __future__ import annotations

import inspect
from typing import Any


async def load_entity_vector_store(
    container: Any, logger_: Any, org_id: str | None,  # noqa: ANN401
) -> Any | None:  # noqa: ANN401
    """The entity store backing the knowledge-graph entity tools, or None.

    None hides the tools and their prompt paragraph: when the provider is
    missing or fails, and when *org_id*'s entities collection does not
    exist yet, so a deployment with nothing indexed does not pay for tools
    that can only come back empty. Never raises; chat must not fail over an
    optional dependency.
    """
    provider = getattr(container, "entity_vector_store", None)
    if provider is None:
        return None
    try:
        store = provider()
        if inspect.isawaitable(store):
            store = await store
        if store is None or not await store.collection_exists(org_id):
            return None
        return store
    except Exception as exc:
        logger_.warning("entity_vector_store unavailable: %s", exc)
        return None


async def warm_up_entity_vector_store(container: Any, logger_: Any) -> None:  # noqa: ANN401
    """Load the entity store's embedding models at startup. Loaded lazily,
    they used up the first entity search's deadline, which then dropped every
    match unchecked. Skipped without an entities collection: initialising
    would create one, and chat hides the tools until there is one. Never
    raises; a failed load is retried after the store's back-off, as a failed
    first search would be."""
    store = await load_entity_vector_store(container, logger_, None)
    if store is None:
        return
    try:
        logger_.info("Warming up entity vector store in background")
        await store.embedding_fingerprint()
        logger_.info("Entity vector store warmup complete")
    except Exception as exc:
        logger_.warning("Entity vector store warmup failed (a later search retries): %s", exc)


__all__ = ["load_entity_vector_store", "warm_up_entity_vector_store"]
