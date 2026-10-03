"""``ZammadDataSource.search_tickets``: the request it sends and the answers it reads."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.zammad.zammad import ZammadDataSource


def _data_source(status: int, body: object, text: str = "body") -> tuple[ZammadDataSource, AsyncMock]:
    response = MagicMock(status=status)
    response.text.return_value = text
    response.json.return_value = body
    http = MagicMock()
    http.execute = AsyncMock(return_value=response)
    client = MagicMock()
    client.get_client.return_value = http
    client.get_base_url.return_value = "https://zammad.test/"
    return ZammadDataSource(client), http.execute


@pytest.mark.parametrize(("limit", "offset", "page"), [(50, 0, "1"), (50, 100, "3"), (1, 9999, "10000"), (200, 400, "3")])
async def test_pages_through_tickets_search_by_page_and_per_page(limit: int, offset: int, page: str) -> None:
    ds, execute = _data_source(200, [])

    response = await ds.search_tickets(query="group_id:1 AND updated_at:[2024-01-01T00:00:00Z TO *]", limit=limit, offset=offset)

    assert response.success
    request = execute.await_args.args[0]
    assert request.method == "GET"
    # /api/v1/search ignores offset before Zammad 6.5 and would answer every page with the first.
    assert request.url == "https://zammad.test/api/v1/tickets/search"
    assert request.query_params == {
        "query": "group_id:1 AND updated_at:[2024-01-01T00:00:00Z TO *]",
        "page": page,
        "per_page": str(limit),
        "sort_by": "updated_at,id",
        "order_by": "desc,desc",
        "expand": "true",
    }


async def test_without_limit_or_offset_it_asks_for_the_first_page_of_fifty() -> None:
    ds, execute = _data_source(200, [])

    await ds.search_tickets(query="group_id:1")

    params = execute.await_args.args[0].query_params
    assert (params["page"], params["per_page"]) == ("1", "50")


async def test_returns_the_expanded_tickets_in_the_order_zammad_sent_them() -> None:
    tickets = [
        {"id": 7, "group_id": 1, "state_id": 2, "state": "open", "updated_at": "2024-01-02T00:00:00Z"},
        {"id": 3, "group_id": 1, "state_id": 1, "state": "new", "updated_at": "2024-01-01T00:00:00Z"},
    ]
    ds, _ = _data_source(200, tickets)

    response = await ds.search_tickets(query="group_id:1", limit=50, offset=0)

    assert response.success and response.data == tickets


async def test_an_empty_body_is_an_empty_page() -> None:
    ds, _ = _data_source(200, None, text="")

    response = await ds.search_tickets(query="group_id:1", limit=50, offset=50)

    assert response.success and response.data == []


@pytest.mark.parametrize(("status", "body", "error"), [
    (422, {"error": "Found invalid column 'nope' for sorting."}, "Found invalid column 'nope' for sorting."),
    (200, {"error": "query is invalid"}, "query is invalid"),
    # The unexpanded pre-6.5 shape: never read as "no tickets", which would end a listing early.
    (200, {"tickets": [1], "tickets_count": 1, "assets": {"Ticket": {"1": {"id": 1}}}}, "unexpected ticket search response"),
])
async def test_an_error_or_unknown_body_is_a_failure(status: int, body: object, error: str) -> None:
    ds, _ = _data_source(status, body)

    response = await ds.search_tickets(query="group_id:1", limit=50, offset=0)

    assert not response.success and response.data is None
    assert response.error == error


@pytest.mark.parametrize(("limit", "offset"), [(201, 0), (0, 0), (50, 25), (50, -50)])
async def test_a_page_zammad_cannot_serve_exactly_is_refused_without_a_request(limit: int, offset: int) -> None:
    ds, execute = _data_source(200, [])

    response = await ds.search_tickets(query="group_id:1", limit=limit, offset=offset)

    assert not response.success and response.error
    execute.assert_not_awaited()
