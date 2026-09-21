"""`ArangoHTTPProvider.list_kb_records` must not scan every record's file or
folder once per record (quadratic in KB size, and it ran before LIMIT)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider


@pytest.mark.asyncio
async def test_listing_uses_keyed_lookups_and_fetches_files_after_limit() -> None:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[], [0], [[]]])

    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by="recordName", sort_order="asc",
    )

    main_query = provider.execute_query.await_args_list[0].args[0]
    assert "FOR f IN kbFolders FILTER" not in main_query
    assert "all_files" not in main_query
    assert main_query.index("LIMIT @skip, @limit") < main_query.index("@@is_of_type")


@pytest.mark.asyncio
async def test_page_order_has_a_unique_tie_breaker() -> None:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[], [0], [[]]])
    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by="createdAtTimestamp", sort_order="desc",
    )
    assert "SORT record.createdAtTimestamp DESC, record._key" in provider.execute_query.await_args_list[0].args[0]


async def _captured(folder_id: str | None = None) -> list[tuple[str, dict]]:
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[], [0], [[]]])
    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by="recordName", sort_order="asc", folder_id=folder_id,
    )
    return [(c.args[0], c.kwargs["bind_vars"]) for c in provider.execute_query.await_args_list]


@pytest.mark.asyncio
async def test_whole_kb_listing_includes_root_records_and_folder_listing_does_not() -> None:
    (main, main_bind), (count, _), _ = await _captured()
    assert "APPEND(all_records_data, root_records_data)" in main and main_bind["include_root"] is True
    assert "LENGTH(root_records_data)" in count
    (_, folder_bind), _, _ = await _captured(folder_id="f1")
    assert folder_bind["include_root"] is False


@pytest.mark.asyncio
async def test_queries_only_receive_bind_parameters_they_declare() -> None:
    """ArangoDB rejects undeclared bind parameters; a bare `user_permission`
    parses as a collection name. Either made every listing call fail."""
    for query, bind in (await _captured())[:2]:
        assert "role: user_permission" not in query
        for name in bind:
            token = f"@{name}" if not name.startswith("@") else f"@{name}"
            assert token in query, f"{name} bound but not declared"


@pytest.mark.asyncio
async def test_page_and_count_apply_the_same_record_predicates() -> None:
    """`totalCount` drives the UI's page count. If the count admits records the
    page cannot return, the last page comes back short or empty."""
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[], [0], [[]]])

    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by="recordName", sort_order="asc",
    )

    main_query = provider.execute_query.await_args_list[0].args[0]
    count_query = provider.execute_query.await_args_list[1].args[0]
    for predicate in (
        "FILTER record.mimeType != @folder_mime_type",
        "FILTER record.isDeleted != true",
        "FILTER record.orgId == @org_id",
        "FILTER record.isFile != false",
    ):
        assert main_query.count(predicate) >= 2, f"page query is missing {predicate}"
        assert count_query.count(predicate) >= 2, f"count query is missing {predicate}"


@pytest.mark.asyncio
async def test_sub_folders_are_not_listed_as_records() -> None:
    """A folder record has no `isFile`, so `isFile != false` passes it through.
    Neo4j excludes folders on both branches; Arango has to agree or the same KB
    lists differently depending on which graph backend is installed."""
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[], [0], [[]]])

    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by="recordName", sort_order="asc",
    )

    main_query = provider.execute_query.await_args_list[0].args[0]
    folder_branch = main_query[main_query.index("LET all_records_data"):main_query.index("LET root_records_data")]
    assert "FILTER record.mimeType != @folder_mime_type" in folder_branch


@pytest.mark.asyncio
async def test_count_does_not_materialize_root_record_documents() -> None:
    """Every record sits at the KB root after an upload, so a count that builds
    the full document array reintroduces the pre-LIMIT scan this query removed."""
    provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
    provider.get_user_kb_permission = AsyncMock(return_value="OWNER")
    provider.execute_query = AsyncMock(side_effect=[[], [0], [[]]])

    await provider.list_kb_records(
        "kb1", "u1", "org1", skip=0, limit=10, search=None, record_types=None, origins=None,
        connectors=None, indexing_status=None, date_from=None, date_to=None,
        sort_by="recordName", sort_order="asc",
    )

    count_query = provider.execute_query.await_args_list[1].args[0]
    root_branch = count_query[count_query.index("LET root_records_data"):]
    assert "RETURN 1" in root_branch
    assert "folder_name: null" not in root_branch
