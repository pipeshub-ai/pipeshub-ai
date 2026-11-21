"""For connector tests built on mocks: a real SourceAccounts on the connector, loaded with no saved state."""

import logging
from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.base.data_processor.source_accounts import SourceAccounts


def stub_source_accounts(connector: object) -> object:
    config = MagicMock(get_config=AsyncMock(return_value=None), set_config=AsyncMock(return_value=True))
    connector.source_accounts = SourceAccounts(
        connector.data_entities_processor, connector.connector_id, config, logging.getLogger("t")
    )
    connector.source_accounts.loaded = True
    return connector
