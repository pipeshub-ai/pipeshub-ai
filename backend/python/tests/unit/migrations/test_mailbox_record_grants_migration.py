"""The mailbox record grants removal names exactly the mailbox connectors whose
sync writes no record grant today, runs once, and is retried after a failure."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.migrations.mailbox_record_grants_migration import MailboxRecordGrantsMigrationService


def _service(provider, done=False) -> tuple[MailboxRecordGrantsMigrationService, MagicMock]:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={"done": True} if done else None)
    config.set_config = AsyncMock()
    return MailboxRecordGrantsMigrationService(provider, config, MagicMock()), config


@pytest.mark.asyncio
async def test_mail_and_attachments_of_the_mailbox_connectors_lose_their_grants() -> None:
    provider = MagicMock()
    provider.remove_inherited_record_grants = AsyncMock(return_value={"removed": 7, "records": 3})
    service, config = _service(provider)

    assert await service.migrate() == {"success": True, "removed": 7, "records": 3}

    connectors, record_types = provider.remove_inherited_record_grants.await_args.args
    assert sorted(connectors) == ["GMAIL", "GMAIL WORKSPACE", "OUTLOOK", "OUTLOOK PERSONAL"]
    assert sorted(record_types) == ["FILE", "GROUP_MAIL", "MAIL"]
    key, flag = config.set_config.await_args.args
    assert key == "/migrations/mailbox_record_grants_v1"
    assert flag["done"] is True and flag["removed"] == 7 and flag["records"] == 3


@pytest.mark.asyncio
async def test_a_failure_is_retried_next_startup() -> None:
    provider = MagicMock()
    provider.remove_inherited_record_grants = AsyncMock(side_effect=RuntimeError("2 left"))
    service, config = _service(provider)
    assert (await service.migrate())["success"] is False
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_backend_without_it_is_skipped_and_not_flagged() -> None:
    provider = MagicMock()
    provider.remove_inherited_record_grants = AsyncMock(side_effect=NotImplementedError)
    service, config = _service(provider)
    assert (await service.migrate())["unsupported"] is True
    config.set_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_finished_migration_does_not_run_again() -> None:
    provider = MagicMock()
    provider.remove_inherited_record_grants = AsyncMock()
    service, _ = _service(provider, done=True)
    assert (await service.migrate())["skipped"] is True
    provider.remove_inherited_record_grants.assert_not_called()
