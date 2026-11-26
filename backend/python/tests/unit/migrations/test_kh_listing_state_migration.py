"""The knowledge hub stamp migrations: their flag is what the listing reads, so a flag that could not be written is a failed step, retried."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.migrations.kh_listing_state_migration import (
    KhChildrenOrderMigrationService,
    KhListingStateMigrationService,
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("service", "stamp"), [(KhListingStateMigrationService, "stamp_kh_listing_state"),
                                                (KhChildrenOrderMigrationService, "stamp_kh_children_order")])
async def test_a_flag_that_could_not_be_written_fails_the_step_so_it_is_retried(service, stamp) -> None:
    provider = AsyncMock()
    getattr(provider, stamp).return_value = {"stamped": 3}
    config = AsyncMock()
    config.get_config.return_value = None
    config.set_config.side_effect = ConnectionError("store unavailable")
    migration = service(provider, config, logging.getLogger("test"))

    assert (await migration.migrate())["success"] is False

    config.set_config.side_effect = None
    assert await migration.migrate() == {"success": True, "stamped": 3}
    assert getattr(provider, stamp).await_count == 2
