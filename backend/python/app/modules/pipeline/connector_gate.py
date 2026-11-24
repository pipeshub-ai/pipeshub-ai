"""Whether a stage job's connector is being deleted.

A connector delete removes its records and stage states in batches; on ArangoDB a stage job
still writing to those documents makes each batch lose a write-write conflict, and enough of
them abandon the delete with the connector and its records left behind. A job whose connector
is DELETING writes nothing and is handed back for later instead.

DELETING is not final: a delete whose events cannot be sent, or whose graph delete fails while
the connector is still there, puts the connector's status back. So a held job is never settled:
once the delete sticks, its records are gone and it is dropped as stale; after a revert it runs.
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

# Only "not deleting" is remembered, for this long: a delete that starts meanwhile costs at most
# one more write before a job sees it. "Deleting" is read afresh each time, so a revert is seen
# at once.
NOT_DELETING_TTL_S = 5.0
# A failed read counts as not deleting and is asked again after this, so a failing graph is not
# read before every write.
FAILED_READ_TTL_S = 1.0


class ConnectorBeingDeleted(Exception):
    """Raised by a stage IO, before a read or under the lease before a write, when the job's
    connector is being deleted: the run hands its job back without writing anything."""


class ConnectorDeletingCheck:
    """Whether a connector is being deleted, shared by every job in this process."""

    def __init__(
        self,
        graph: IGraphDBProvider,
        logger: logging.Logger,
        *,
        ttl_s: float = NOT_DELETING_TTL_S,
        failed_read_ttl_s: float = FAILED_READ_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__()
        self._graph = graph
        self._logger = logger
        self._ttl_s = ttl_s
        self._failed_read_ttl_s = failed_read_ttl_s
        self._clock = clock
        self._not_deleting_until: dict[str, float] = {}

    async def __call__(self, connector_id: str) -> bool:
        if not connector_id:
            return False
        now = self._clock()
        until = self._not_deleting_until.get(connector_id)
        if until is not None and until > now:
            return False
        try:
            app = await self._graph.get_document(connector_id, CollectionNames.APPS.value, raise_on_error=True)
        except Exception as exc:
            # Unreadable is not deleting: holding a live connector's work back would stall it, and
            # the delete it would protect retries on its own.
            self._logger.warning("Could not read connector %s to check for a delete: %s", connector_id, exc)
            self._not_deleting_until[connector_id] = now + self._failed_read_ttl_s
            return False
        if app is not None and app.get("status") == APP_STATUS_DELETING:
            self._not_deleting_until.pop(connector_id, None)
            return True
        self._not_deleting_until[connector_id] = now + self._ttl_s
        return False
