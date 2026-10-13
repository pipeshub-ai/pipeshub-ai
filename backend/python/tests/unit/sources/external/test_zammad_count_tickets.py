"""``ZammadDataSource.count_tickets``: the request it sends and the answers it reads."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.sources.external.zammad.zammad import ZammadDataSource


def _data_source(status: int, body: object) -> tuple[ZammadDataSource, AsyncMock]:
    response = MagicMock(status=status)
    response.text.return_value = "body"
    response.json.return_value = body
    http = MagicMock()
    http.execute = AsyncMock(return_value=response)
    client = MagicMock()
    client.get_client.return_value = http
    client.get_base_url.return_value = "https://zammad.test/"
    return ZammadDataSource(client), http.execute


async def test_counts_among_the_given_ids_in_a_post_body() -> None:
    ds, execute = _data_source(200, {"total_count": 2})

    response = await ds.count_tickets(query="group_id:1", ids=[10, 11])

    assert response.success and response.data == {"total_count": 2}
    request = execute.await_args.args[0]
    assert request.method == "POST"
    assert request.url == "https://zammad.test/api/v1/tickets/search"
    assert request.body == {"query": "group_id:1", "only_total_count": True, "ids": ["10", "11"]}


@pytest.mark.parametrize("body", [
    {"tickets": [10], "tickets_count": 1, "assets": {}},  # Zammad before 6.5: a page, not a count
    {"total_count": True},
])
async def test_an_answer_without_a_count_has_no_data(body: object) -> None:
    ds, _ = _data_source(200, body)

    response = await ds.count_tickets(query="group_id:1", ids=[10])

    assert response.success and response.data is None


async def test_an_error_body_is_a_failure() -> None:
    ds, _ = _data_source(200, {"error": "query is invalid"})

    response = await ds.count_tickets(query="group_id:1")

    assert not response.success and response.data is None
