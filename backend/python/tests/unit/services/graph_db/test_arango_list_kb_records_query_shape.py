"""Query shape of `ArangoHTTPProvider.list_kb_records`."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


async def _captured(folder_id: str | None = None, **kwargs) -> list[tuple[str, dict]]:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[{"records": [], "total": 0}], [[]]])
    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by=kwargs.get("sort_by", "recordName"), sort_order=kwargs.get("sort_order", "asc"),
        folder_id=folder_id,
    )
    return [(c.args[0], c.kwargs["bind_vars"]) for c in provider.execute_query.await_args_list]


@pytest.mark.asyncio
async def test_page_order_has_a_unique_tie_breaker() -> None:
    (main_query, _), _ = await _captured(sort_by="createdAtTimestamp", sort_order="desc")
    assert "SORT record.createdAtTimestamp DESC, record._key" in main_query


@pytest.mark.asyncio
async def test_listing_uses_keyed_lookups_and_fetches_files_after_limit() -> None:
    """A folder scan per record and every record's file matched before LIMIT
    made each page cost O(records x folders) and O(records) file reads."""
    (main_query, _), _ = await _captured()
    assert "FOR f IN kbFolders FILTER" not in main_query
    assert "all_files" not in main_query
    assert main_query.index("LIMIT @skip, @limit") < main_query.index("@@is_of_type")
