"""Whether a stage job's connector is being deleted.

A connector delete removes its records and stage states in batches; on ArangoDB a stage job
still writing to those documents makes each batch lose a write-write conflict, and enough of
them abandon the delete with the connector and its records left behind. A job whose connector
is DELETING writes nothing instead.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.entity_index_queries import APP_STATUS_DELETING

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

# A delete's own retries run for seconds; a stage job checks before it claims its state and under
# the lease before each write, so a few seconds of staleness costs at most one more write.
DELETING_TTL_S = 5.0


class ConnectorDeletingCheck:
    """One read of a connector's app per ``ttl_s`` in this process, shared by every job."""

    def __init__(
        self,
        graph: IGraphDBProvider,
        logger: logging.Logger,
        *,
        ttl_s: float = DELETING_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._graph = graph
        self._logger = logger
        self._ttl_s = ttl_s
        self._clock = clock
        self._seen: dict[str, tuple[bool, float]] = {}

    async def __call__(self, connector_id: str) -> bool:
        if not connector_id:
            return False
        now = self._clock()
        cached = self._seen.get(connector_id)
        if cached is not None and cached[1] > now:
            return cached[0]
        try:
            app = await self._graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
        except Exception as exc:
            # Unreadable is not deleting: dropping a live connector's work would lose it, and the
            # delete it would protect retries on its own.
            self._logger.warning("Could not read connector %s to check for a delete: %s", connector_id, exc)
            return False
        deleting = app is not None and app.get("status") == APP_STATUS_DELETING
        self._seen[connector_id] = (deleting, now + self._ttl_s)
        return deleting
