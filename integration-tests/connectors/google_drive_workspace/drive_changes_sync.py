"""Drive Changes API settle + re-sync helpers for Google Drive ITs.

A single incremental sync right after a Drive write often misses the change.
These helpers settle briefly, sync, and re-sync via ``sync_until_condition``
until a graph check passes. Connector suites supply their own ``sync_and_wait``
and sync timeout; settle/retry defaults come from shared env vars.

Shared by workspace and personal Drive ITs (personal imports from this module).
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING

from helper.graph_provider_utils import sync_until_condition

if TYPE_CHECKING:
    from helper.graph_provider import GraphProviderProtocol
    from pipeshub_client import PipeshubClient

logger = logging.getLogger("drive-changes-sync")

DRIVE_CHANGES_SETTLE_SEC = int(os.getenv("GOOGLE_DRIVE_CHANGES_SETTLE", "15"))
DRIVE_CHANGES_RETRY_GAP_SEC = int(os.getenv("GOOGLE_DRIVE_CHANGES_RETRY_GAP", "15"))

SyncAndWait = Callable[
    ["PipeshubClient", "GraphProviderProtocol", str],
    Awaitable[None],
]


async def settle_drive_changes(
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
) -> None:
    """Pause so Drive Changes API can publish recent writes before the next sync."""
    if settle_sec <= 0:
        return
    logger.info("Waiting %ds for Drive Changes API settle...", settle_sec)
    await asyncio.sleep(settle_sec)


async def sync_until(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    *,
    check: Callable[[], Awaitable[bool]],
    description: str,
    sync_and_wait: SyncAndWait,
    timeout: int,
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
    retry_gap_sec: int = DRIVE_CHANGES_RETRY_GAP_SEC,
) -> None:
    """Settle, sync, and re-sync until *check* passes (Drive Changes lag)."""

    async def _sync() -> None:
        await sync_and_wait(pipeshub_client, graph_provider, connector_id)

    await sync_until_condition(
        connector_id,
        sync_fn=_sync,
        check=check,
        timeout=timeout,
        settle_sec=settle_sec,
        retry_gap_sec=retry_gap_sec,
        description=description,
    )


async def sync_until_record_present(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    external_id: str,
    *,
    description: str,
    sync_and_wait: SyncAndWait,
    timeout: int,
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
    retry_gap_sec: int = DRIVE_CHANGES_RETRY_GAP_SEC,
) -> None:
    async def _present() -> bool:
        return (
            await graph_provider.get_record_by_external_id(connector_id, external_id)
            is not None
        )

    await sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_present,
        description=description,
        sync_and_wait=sync_and_wait,
        timeout=timeout,
        settle_sec=settle_sec,
        retry_gap_sec=retry_gap_sec,
    )


async def sync_until_record_absent(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    external_id: str,
    *,
    description: str,
    sync_and_wait: SyncAndWait,
    timeout: int,
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
    retry_gap_sec: int = DRIVE_CHANGES_RETRY_GAP_SEC,
) -> None:
    async def _absent() -> bool:
        return (
            await graph_provider.get_record_by_external_id(connector_id, external_id)
            is None
        )

    await sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_absent,
        description=description,
        sync_and_wait=sync_and_wait,
        timeout=timeout,
        settle_sec=settle_sec,
        retry_gap_sec=retry_gap_sec,
    )


async def sync_until_records_present(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    external_ids: list[str],
    *,
    description: str,
    sync_and_wait: SyncAndWait,
    timeout: int,
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
    retry_gap_sec: int = DRIVE_CHANGES_RETRY_GAP_SEC,
) -> None:
    async def _present() -> bool:
        for external_id in external_ids:
            if (
                await graph_provider.get_record_by_external_id(
                    connector_id, external_id
                )
                is None
            ):
                return False
        return True

    await sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_present,
        description=description,
        sync_and_wait=sync_and_wait,
        timeout=timeout,
        settle_sec=settle_sec,
        retry_gap_sec=retry_gap_sec,
    )


async def sync_until_records_absent(
    pipeshub_client: "PipeshubClient",
    graph_provider: "GraphProviderProtocol",
    connector_id: str,
    external_ids: list[str],
    *,
    description: str,
    sync_and_wait: SyncAndWait,
    timeout: int,
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
    retry_gap_sec: int = DRIVE_CHANGES_RETRY_GAP_SEC,
) -> None:
    async def _absent() -> bool:
        for external_id in external_ids:
            if (
                await graph_provider.get_record_by_external_id(
                    connector_id, external_id
                )
                is not None
            ):
                return False
        return True

    await sync_until(
        pipeshub_client,
        graph_provider,
        connector_id,
        check=_absent,
        description=description,
        sync_and_wait=sync_and_wait,
        timeout=timeout,
        settle_sec=settle_sec,
        retry_gap_sec=retry_gap_sec,
    )


def bind_drive_changes_helpers(
    *,
    sync_and_wait: SyncAndWait,
    timeout: int,
    settle_sec: int = DRIVE_CHANGES_SETTLE_SEC,
    retry_gap_sec: int = DRIVE_CHANGES_RETRY_GAP_SEC,
) -> SimpleNamespace:
    """Bind connector-specific sync/timeout into the shared Drive Changes helpers.

    Returns a namespace with the same callable shapes the Drive ITs used locally
    (``settle_drive_changes``, ``sync_until``, ``sync_until_record_present``, …)
    so call sites stay unchanged.
    """

    async def _settle_drive_changes() -> None:
        await settle_drive_changes(settle_sec)

    async def _sync_until(
        pipeshub_client: "PipeshubClient",
        graph_provider: "GraphProviderProtocol",
        connector_id: str,
        *,
        check: Callable[[], Awaitable[bool]],
        description: str,
    ) -> None:
        await sync_until(
            pipeshub_client,
            graph_provider,
            connector_id,
            check=check,
            description=description,
            sync_and_wait=sync_and_wait,
            timeout=timeout,
            settle_sec=settle_sec,
            retry_gap_sec=retry_gap_sec,
        )

    async def _sync_until_record_present(
        pipeshub_client: "PipeshubClient",
        graph_provider: "GraphProviderProtocol",
        connector_id: str,
        external_id: str,
        *,
        description: str,
    ) -> None:
        await sync_until_record_present(
            pipeshub_client,
            graph_provider,
            connector_id,
            external_id,
            description=description,
            sync_and_wait=sync_and_wait,
            timeout=timeout,
            settle_sec=settle_sec,
            retry_gap_sec=retry_gap_sec,
        )

    async def _sync_until_record_absent(
        pipeshub_client: "PipeshubClient",
        graph_provider: "GraphProviderProtocol",
        connector_id: str,
        external_id: str,
        *,
        description: str,
    ) -> None:
        await sync_until_record_absent(
            pipeshub_client,
            graph_provider,
            connector_id,
            external_id,
            description=description,
            sync_and_wait=sync_and_wait,
            timeout=timeout,
            settle_sec=settle_sec,
            retry_gap_sec=retry_gap_sec,
        )

    async def _sync_until_records_present(
        pipeshub_client: "PipeshubClient",
        graph_provider: "GraphProviderProtocol",
        connector_id: str,
        external_ids: list[str],
        *,
        description: str,
    ) -> None:
        await sync_until_records_present(
            pipeshub_client,
            graph_provider,
            connector_id,
            external_ids,
            description=description,
            sync_and_wait=sync_and_wait,
            timeout=timeout,
            settle_sec=settle_sec,
            retry_gap_sec=retry_gap_sec,
        )

    async def _sync_until_records_absent(
        pipeshub_client: "PipeshubClient",
        graph_provider: "GraphProviderProtocol",
        connector_id: str,
        external_ids: list[str],
        *,
        description: str,
    ) -> None:
        await sync_until_records_absent(
            pipeshub_client,
            graph_provider,
            connector_id,
            external_ids,
            description=description,
            sync_and_wait=sync_and_wait,
            timeout=timeout,
            settle_sec=settle_sec,
            retry_gap_sec=retry_gap_sec,
        )

    return SimpleNamespace(
        settle_drive_changes=_settle_drive_changes,
        sync_until=_sync_until,
        sync_until_record_present=_sync_until_record_present,
        sync_until_record_absent=_sync_until_record_absent,
        sync_until_records_present=_sync_until_records_present,
        sync_until_records_absent=_sync_until_records_absent,
    )
