"""A connector's address state after an address passed from one source account to another."""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.base.data_processor.source_accounts import SourceAccounts
from tests.support.in_memory_config import InMemoryConfigService


async def handed_over(connector_id: str, email: str, old_id: str, new_id: str) -> SourceAccounts:
    """The real check the processor makes on every user grant (``holds``), once the user listing linked ``email``
    to ``old_id`` and a later listing to ``new_id``."""
    processor = MagicMock(
        get_app_user_links=AsyncMock(return_value=[]),
        get_user_by_email=AsyncMock(return_value=MagicMock(id="pipeshub-user")),
        remove_user_access_to_connector=AsyncMock(return_value=1),
    )
    accounts = SourceAccounts(processor, connector_id, InMemoryConfigService(), logging.getLogger("t"))
    for source_id in (old_id, new_id):
        assert await accounts.before_link([SimpleNamespace(email=email, source_user_id=source_id)])
    await accounts.ensure_loaded()
    return accounts
