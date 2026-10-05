"""``ZammadDataSource.init_knowledge_base``: the knowledge base listing and the answers it reads."""

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


async def test_posts_to_the_init_endpoint_and_returns_the_assets_map() -> None:
    listing = {
        "KnowledgeBase": {"1": {"id": 1}},
        "KnowledgeBaseAnswer": {"5": {"id": 5, "category_id": 2, "updated_at": "2024-01-01T00:00:00.123Z"}},
    }
    ds, execute = _data_source(200, listing)

    response = await ds.init_knowledge_base()

    request = execute.await_args.args[0]
    assert (request.method, request.url) == ("POST", "https://zammad.test/api/v1/knowledge_bases/init")
    assert response.success and response.data == listing


async def test_an_empty_list_means_no_knowledge_base_is_visible() -> None:
    # Zammad's answer to an account with no knowledge-base role when the knowledge base isn't public.
    ds, _ = _data_source(200, [])

    response = await ds.init_knowledge_base()

    assert response.success and response.data == {}


@pytest.mark.parametrize(("status", "body", "error"), [
    (200, {"error": "Not authorized"}, "Not authorized"),
    (403, {"error": "Not authorized"}, "Not authorized"),
    (200, ["unexpected"], "unexpected knowledge base listing response"),
    (200, "unexpected", "unexpected knowledge base listing response"),
])
async def test_an_error_or_unknown_body_is_a_failure(status: int, body: object, error: str) -> None:
    ds, _ = _data_source(status, body)

    response = await ds.init_knowledge_base()

    assert not response.success and response.data is None
    assert response.error == error


async def test_a_server_error_is_a_failure() -> None:
    ds, _ = _data_source(500, None, text="")

    response = await ds.init_knowledge_base()

    assert not response.success and response.data is None
