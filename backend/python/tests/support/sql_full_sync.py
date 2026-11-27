"""What a connector's full sync loses, for tests that run one against a fake world.

The end of a full sync unlinks every stored record the sync neither wrote nor
noted kept (sync_runner._unlink_what_full_sync_missed), unless the connector's
full_sync_coverage says it did not read the whole source. SQL connector tests
count what the connector wrote themselves; ``run_full_sync`` runs a connector
through the runner instead, so its processor notes what the run wrote.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from app.config.constants.arangodb import AppStatus
from app.connectors.core.sync import sync_runner

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

    from app.connectors.core.base.connector.connector_service import BaseConnector
    from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage


def full_sync_losses(
    before: Mapping[str, str],
    after: Collection[str],
    seen: Collection[str],
    coverage: FullSyncCoverage,
    at_source: Collection[str],
    kept: Collection[str],
) -> tuple[set[str], set[str]]:
    """Split the records a full sync removed or leaves to be unlinked.

    ``before`` maps each stored record's key to its id before the sync,
    ``after`` holds the keys stored after it, ``seen`` the ids it wrote or
    noted kept, ``at_source`` the keys still at the source and ``kept`` those
    of them whose table the active filter keeps.

    Returns the kept records missing from the store afterwards (deleted,
    even after being written or noted, or unlinked), which the sync should
    not have lost; and the records gone from the source that are removed or
    unlinked.
    """
    unlinks = coverage.complete and coverage.lists_everything
    unlinked = {
        key for key, record_id in before.items()
        if unlinks and key in after and record_id not in seen
    }
    deleted = {key for key in before if key not in after}
    missing = deleted | unlinked
    return missing & set(kept), missing - set(at_source)


async def run_full_sync(connector: BaseConnector, connector_id: str) -> set[str] | None:
    """Run ``connector`` as ``run_sync_task`` runs a full sync, over a faked graph provider.

    The connector's own processor notes what the run writes or keeps. Returns
    the node ids the runner then hands the unlink as seen, or None when it
    unlinks nothing (the connector said it did not read the whole source, or
    the run wrote nothing). A run that raises raises here.
    """
    graph = AsyncMock()
    graph.get_document.return_value = {"id": connector_id, "status": AppStatus.FULL_SYNCING.value}
    graph.unlink_nodes_missed_by_full_sync.return_value = (0, True)
    try:
        await sync_runner.run_sync_task(
            connector, connector_id, graph, logging.getLogger("test.full_sync"),
            start_status=AppStatus.FULL_SYNCING.value,
        )
    finally:
        sync_runner._INCOMPLETE_FULL_SYNC_RETRYING.discard(connector_id)  # each run starts afresh
    if not graph.unlink_nodes_missed_by_full_sync.await_count:
        return None
    seen = graph.unlink_nodes_missed_by_full_sync.await_args.args[1]
    return set().union(*seen.values())
