"""One-time move of the S3 and MinIO Created Date sync filter into Modified Date (PP-101).

S3 lists no creation time, so the Created filter was judged on ``LastModified``,
the same as Modified. S3 and MinIO no longer register it, and a saved value would
stop applying and widen the sync. Its bounds move into the Modified filter, so
each connector keeps the scope it synced before.

Writes are idempotent (the result depends only on the saved config), so two
replicas running this at once save the same result.
"""

from typing import Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MIGRATION_FLAG_KEY = "/migrations/s3_created_date_filter_v1"
CONNECTOR_TYPES = ("S3", "MinIO")
_ABSOLUTE = ("is_after", "is_before", "is_between")


def _bounds(date_filter: Any) -> tuple[int | None, int | None] | None:
    """``(start, end)`` of a saved absolute date filter; None for a relative or unreadable one."""
    if not isinstance(date_filter, dict) or date_filter.get("operator") not in _ABSOLUTE:
        return None
    value = date_filter.get("value")
    if not isinstance(value, dict):
        return None
    return value.get("start"), value.get("end")


def _is_set(date_filter: Any) -> bool:
    return isinstance(date_filter, dict) and _bounds(date_filter) != (None, None) and bool(date_filter.get("value"))


def merge_created_into_modified(values: dict[str, Any], connector_id: str, logger: Any) -> bool:
    """Move ``created`` into ``modified`` in ``values``; True when ``values`` changed."""
    if "created" not in values:
        return False
    created = values.pop("created")
    if not _is_set(created):
        return True
    modified = values.get("modified")
    if not _is_set(modified):
        values["modified"] = created
        return True
    created_bounds, modified_bounds = _bounds(created), _bounds(modified)
    if created_bounds is None or modified_bounds is None:
        logger.warning(
            f"S3 created date filter migration: connector {connector_id} keeps its modified filter; "
            f"its relative created filter could not be merged and was dropped: {created}"
        )
        return True
    starts = [s for s in (created_bounds[0], modified_bounds[0]) if s is not None]
    ends = [e for e in (created_bounds[1], modified_bounds[1]) if e is not None]
    start, end = (max(starts) if starts else None), (min(ends) if ends else None)
    operator = "is_between" if start is not None and end is not None else "is_after" if start is not None else "is_before"
    values["modified"] = {**modified, "operator": operator, "value": {"start": start, "end": end}}
    return True


async def _migrate_connector(config_service: ConfigurationService, connector_id: str, logger: Any) -> bool:
    """Migrate one connector's saved filters; True when it changed them."""
    path = f"/services/connectors/{connector_id}/config"
    config = await config_service.get_config(path, use_cache=False, raise_on_error=True)
    filters = config.get("filters") if isinstance(config, dict) else None
    sync = filters.get("sync") if isinstance(filters, dict) else None
    values = sync.get("values") if isinstance(sync, dict) else None
    if not isinstance(values, dict) or not merge_created_into_modified(values, connector_id, logger):
        return False
    if not await config_service.set_config(path, config):
        raise RuntimeError(f"could not save {path}")
    logger.info(f"S3 created date filter migration: moved connector {connector_id}'s created filter into modified")
    return True


async def run_s3_created_date_filter_migration(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    logger: Any,
) -> dict[str, Any]:
    try:
        flag = await config_service.get_config(MIGRATION_FLAG_KEY, use_cache=False)
        if isinstance(flag, dict) and flag.get("done") is True:
            return {"success": True, "skipped": True, "migrated": 0}
    except Exception as e:
        logger.debug(f"Unable to read the S3 created date filter migration flag (assuming not done): {e}")

    migrated = failures = 0
    for connector_type in CONNECTOR_TYPES:
        try:
            apps = await graph_provider.get_nodes_by_filters(
                CollectionNames.APPS.value, {"type": connector_type}, raise_on_error=True
            )
        except Exception as e:
            logger.error(f"S3 created date filter migration: could not list the {connector_type} connectors: {e}")
            failures += 1
            continue
        for app in apps:
            connector_id = app.get("_key") or app.get("id")
            try:
                migrated += await _migrate_connector(config_service, connector_id, logger)
            except Exception as e:
                failures += 1
                logger.error(f"S3 created date filter migration: connector {connector_id} failed: {e}")

    # Left unset after a failure so the next start retries; migrated connectors have no created filter then.
    if not failures and not await config_service.set_config(MIGRATION_FLAG_KEY, {"done": True, "migrated": migrated}):
        logger.warning("S3 created date filter migration finished but its completion flag was not saved")
    return {"success": not failures, "skipped": False, "migrated": migrated, "failures": failures}
