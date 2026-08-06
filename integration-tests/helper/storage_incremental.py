"""Shared helpers for storage-connector incremental sync integration tests.

Constructor fixtures only wait until ``count_records >= uploaded_files``, so the
graph can still grow (folder records, late file rows) when TC-INCR starts. These
helpers settle a stable baseline, restart sync with enough headroom for Kafka
disable/enable, and require the new file names — not merely a rising count.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Mapping

from helper.graph_provider_utils import (
    async_wait_for_stable_record_count,
    wait_for_sync_completion,
    wait_until_graph_condition,
)

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol
    from pipeshub_client import PipeshubClient

logger = logging.getLogger("storage-incremental")

DEFAULT_SYNC_TIMEOUT_SEC = 240
DEFAULT_MAX_SYNC_ATTEMPTS = 3
DEFAULT_NAME_GRACE_TIMEOUT_SEC = 60


def restart_sync(pipeshub_client: "PipeshubClient", connector_id: str) -> None:
    """Disable then re-enable to trigger a fresh incremental sync."""
    pipeshub_client.toggle_sync(connector_id, enable=False)
    pipeshub_client.wait(5)
    pipeshub_client.toggle_sync(connector_id, enable=True)
    pipeshub_client.wait(8)


def unique_incremental_csv_files(
    *,
    prefix: str = "incremental-test",
    suffix: str | None = None,
) -> dict[str, bytes]:
    """Return two uniquely named CSV blobs under *prefix* for incremental upload."""
    token = suffix if suffix is not None else uuid.uuid4().hex[:8]
    return {
        f"{prefix}/new-file-alpha-{token}.csv": (
            b"id,name,value\n1,alpha,100\n2,bravo,200\n"
        ),
        f"{prefix}/new-file-beta-{token}.csv": (
            b"id,name,value\n1,charlie,300\n2,delta,400\n"
        ),
    }


def record_names_from_keys(object_keys: Mapping[str, bytes] | list[str]) -> list[str]:
    """Basename list for uploaded object keys (dict keys or plain key list)."""
    keys = object_keys.keys() if isinstance(object_keys, Mapping) else object_keys
    return [Path(key).name for key in keys]


async def settle_record_baseline(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    timeout: int = DEFAULT_SYNC_TIMEOUT_SEC,
) -> int:
    """Wait until the connector is IDLE and record count is stable; return that count."""
    await wait_for_sync_completion(
        pipeshub_client,
        graph_provider,
        connector_id,
        timeout=timeout,
        sync_start_timeout=0,
    )
    before_count = await async_wait_for_stable_record_count(
        graph_provider, connector_id
    )
    logger.info(
        "Incremental baseline settled: %d records (connector %s)",
        before_count,
        connector_id,
    )
    return before_count


async def sync_until_names_visible(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    new_names: list[str],
    *,
    max_attempts: int = DEFAULT_MAX_SYNC_ATTEMPTS,
    sync_timeout: int = DEFAULT_SYNC_TIMEOUT_SEC,
    name_grace_timeout: int = DEFAULT_NAME_GRACE_TIMEOUT_SEC,
    restart_fn: Callable[["PipeshubClient", str], None] | None = None,
) -> int:
    """Restart sync until all *new_names* appear in the graph; return final count.

    Retries absorb a late Kafka ``appDisabled`` cancelling the first re-enable.
    """
    do_restart = restart_fn or restart_sync

    async def _new_files_visible() -> bool:
        return await graph_provider.record_paths_or_names_contain(
            connector_id, new_names
        )

    for sync_attempt in range(max_attempts):
        do_restart(pipeshub_client, connector_id)
        await wait_for_sync_completion(
            pipeshub_client,
            graph_provider,
            connector_id,
            timeout=sync_timeout,
        )
        if await _new_files_visible():
            break
        if sync_attempt < max_attempts - 1:
            logger.warning(
                "New files not in graph after sync, re-triggering "
                "(attempt %d/%d) (connector %s)",
                sync_attempt + 2,
                max_attempts,
                connector_id,
            )

    await wait_until_graph_condition(
        connector_id,
        check=_new_files_visible,
        timeout=name_grace_timeout,
        poll_interval=5,
        description="incremental sync new file names",
    )
    return await graph_provider.count_records(connector_id)


async def assert_incremental_new_files(
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    before_count: int,
    after_count: int,
    new_names: list[str],
) -> None:
    """Assert count grew by at least len(new_names) and each name is present."""
    assert after_count >= before_count + len(new_names), (
        f"Expected at least {len(new_names)} new records after incremental sync; "
        f"before={before_count}, after={after_count} (connector {connector_id})"
    )
    await graph_provider.assert_record_paths_or_names_contain(connector_id, new_names)
