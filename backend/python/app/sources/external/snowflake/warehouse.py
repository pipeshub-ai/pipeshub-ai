"""A statement runner for a Snowflake connector, outside the connector's own sync.

Readers outside the sync, such as the agent's SQL tool, read the warehouse
with the connector's stored credentials. They share one limit per connector on
the statements in flight (``statement_slots``) and tag their statements, so
the account owner can see what the warehouse time was spent on.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from app.sources.client.snowflake.snowflake import (
    SnowflakeClient,
    parse_connection_settings,
)
from app.sources.external.snowflake.snowflake_ import SnowflakeDataSource
from app.sources.external.snowflake.statements import SnowflakeStatementRunner
from app.utils.statement_slots import statement_slots

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

@asynccontextmanager
async def connector_runner(
    *,
    logger: Logger,
    config_service: ConfigurationService,
    connector_id: str | None,
    query_tag: str,
    public_only: bool = True,
) -> AsyncIterator[SnowflakeStatementRunner]:
    """A runner on the connector's credentials and warehouse; its HTTP client closes on exit.

    ``public_only`` is the connector's address rule: a private-link account name can resolve
    to a private address, so a personal connector's runner reaches public addresses only.

    Raises ValueError when the connector's config is missing or incomplete.
    """
    config = await SnowflakeClient._get_connector_config(
        logger=logger, config_service=config_service, connector_instance_id=connector_id,
    )
    settings = parse_connection_settings(config)
    data_source = SnowflakeDataSource(settings.create_client())
    if public_only:
        data_source.http.require_public_addresses()
    try:
        yield SnowflakeStatementRunner(
            data_source,
            warehouse=settings.warehouse,
            role=settings.role,
            slots=statement_slots(connector_id) if connector_id else None,
            session_parameters={"QUERY_TAG": query_tag},
        )
    finally:
        await data_source.http.close()
