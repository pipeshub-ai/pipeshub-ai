"""For connector tests built on mocks: SourceAccounts reads no saved state and finds nobody linked yet."""

from unittest.mock import AsyncMock, MagicMock


def stub_source_accounts(connector: object) -> object:
    accounts = connector.source_accounts
    accounts.config_service = MagicMock(get_config=AsyncMock(return_value=None), set_config=AsyncMock(return_value=True))
    if not isinstance(getattr(accounts.processor, "get_app_user_links", None), AsyncMock):
        accounts.processor.get_app_user_links = AsyncMock(return_value=[])
    return connector
