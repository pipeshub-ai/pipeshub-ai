"""What a SQL connector's full sync loses, for tests that run one against a fake world.

The end of a full sync unlinks every stored record the sync neither wrote nor
noted kept (sync_runner._unlink_what_full_sync_missed), unless the connector's
full_sync_coverage says it did not read the whole source.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping

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
