"""Drive (individual): the creator reaches what the Google account they connected reaches, through that link only."""

import asyncio
import logging
import os
import time
from typing import Any

import pytest

from helper.second_user import SecondUser, second_user  # noqa: F401 - fixture
from helper.shadow_access_e2e import verify_shadow_access

logger = logging.getLogger("drive-individual-shadow-access-e2e")

_SYNC_TIMEOUT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_SYNC_TIMEOUT", "900"))
_INDEXING_WAIT_SEC = int(os.getenv("GOOGLE_DRIVE_INDIVIDUAL_INDEXING_WAIT", "900"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.google_drive_individual,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _until_one_indexed(graph_provider, connector_id: str, file_ids: list[str]) -> dict[str, Any]:
    """Docs and Slides need Docling, so waiting for all five could outlast a stack without it."""
    deadline = time.monotonic() + _INDEXING_WAIT_SEC
    while True:
        statuses = {}
        for file_id in file_ids:
            record = await graph_provider.get_record_by_external_id(connector_id, file_id)
            statuses[file_id] = getattr(record, "indexing_status", None) if record else None
        if "COMPLETED" in statuses.values() or time.monotonic() >= deadline:
            return statuses
        await asyncio.sleep(10)


class TestDriveIndividualShadowAccess:
    async def test_creator_reaches_records_only_through_linked_account(
        self,
        drive_individual_entity_connector: dict[str, Any],
        pipeshub_client,
        graph_provider,
        config_service,
        vector_store,
        second_user: SecondUser,  # noqa: F811
    ) -> None:
        report = await verify_shadow_access(
            connector_id=drive_individual_entity_connector["connector_id"],
            pipeshub_client=pipeshub_client,
            graph_provider=graph_provider,
            config_service=config_service,
            vector_store=vector_store,
            second_user=second_user,
            sync_timeout=_SYNC_TIMEOUT_SEC,
        )
        logger.info("Shadow access: %s", report.summary())


class TestDriveIndividualShadowSearch:
    """The entity connector indexes nothing (manual indexing); this one auto-indexes five samples."""

    async def test_creator_finds_records_only_through_linked_account(
        self,
        drive_individual_blocks_connector: dict[str, Any],
        pipeshub_client,
        graph_provider,
        config_service,
        vector_store,
        second_user: SecondUser,  # noqa: F811
    ) -> None:
        connector_id = drive_individual_blocks_connector["connector_id"]
        file_ids = [f["id"] for f in drive_individual_blocks_connector["blocks_files"]]
        statuses = await _until_one_indexed(graph_provider, connector_id, file_ids)
        logger.info("Indexing status of the samples: %s", statuses)
        if "COMPLETED" not in statuses.values():
            pytest.skip(f"no sample finished indexing, so search has nothing to find: {statuses}")

        report = await verify_shadow_access(
            connector_id=connector_id,
            pipeshub_client=pipeshub_client,
            graph_provider=graph_provider,
            config_service=config_service,
            vector_store=vector_store,
            second_user=second_user,
            sync_timeout=_SYNC_TIMEOUT_SEC,
        )
        logger.info("Shadow search: %s", report.summary())
        assert report.search_proven >= 1, f"search proved nothing: {report.summary()}"
