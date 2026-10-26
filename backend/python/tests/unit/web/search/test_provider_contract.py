"""What every registered search provider promises, checked against each one. A new provider
needs only a canned answer in ``ANSWERS`` to be covered."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from ddgs.exceptions import DDGSException

from app.web.deadline import Deadline
from app.web.errors import TimedOut, WebError
from app.web.fetch.http import FetchedBody
from app.web.models import WebResult
from app.web.search import PROVIDERS
from app.web.search.ddgs_search import DdgsSearch

_DDG_PAGE = (
    '<html><body><div class="web-result"><a class="result__a" href="https://a.example/1">One</a>'
    '<span class="result__snippet">first</span></div></body></html>'
)
# A typical successful answer from each provider, in its own wire format.
ANSWERS: dict[str, object] = {
    "duckduckgo": FetchedBody("https://html.duckduckgo.com/html/", 200, "text/html", _DDG_PAGE.encode()),
    "ddgs": [{"href": "https://a.example/1", "title": "One", "body": "first"}],
    "serper": {"organic": [{"title": "One", "link": "https://a.example/1", "snippet": "first"}]},
    "tavily": {"results": [{"title": "One", "url": "https://a.example/1", "content": "first"}]},
    "exa": {"results": [{"title": "One", "url": "https://a.example/1", "highlights": ["first"]}]},
    "brave": {"web": {"results": [{"title": "One", "url": "https://a.example/1", "description": "first"}]}},
    "searxng": {"results": [{"title": "One", "url": "https://a.example/1", "content": "first"}]},
    "firecrawl": {"success": True, "data": {"web": [{"title": "One", "url": "https://a.example/1", "description": "first"}]}},
}
CONFIGURATION = {"apiKey": "test-key", "baseUrl": "https://provider.example"}


@pytest.fixture(autouse=True)
def network(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every request a provider sends, whether through the shared client or to its own server,
    goes to the handler the test sets in ``network[0]``."""
    handler: list = [lambda request: httpx.Response(500)]
    monkeypatch.setattr(
        "app.web.endpoint.guarded_async_client",
        lambda endpoint, timeout: httpx.AsyncClient(transport=httpx.MockTransport(lambda r: handler[0](r))),
    )
    return handler


def build(type_id: str, handler: object, network: list | None = None) -> object:
    if network is not None:
        network[0] = handler
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
    return PROVIDERS[type_id].build(dict(CONFIGURATION), http)


def test_every_provider_has_a_canned_answer() -> None:
    assert set(ANSWERS) == set(PROVIDERS)


@pytest.mark.parametrize("type_id", sorted(PROVIDERS))
async def test_answers_with_web_results_tagged_with_its_id(type_id: str, network: list) -> None:
    answer = ANSWERS[type_id]
    provider = build(type_id, lambda request: httpx.Response(200, json=answer), network)
    with patch("app.web.search.duckduckgo_html.fetch_public", AsyncMock(return_value=answer)), \
            patch.object(DdgsSearch, "_text", return_value=answer):
        results = await provider.search("query", Deadline.after(5))
    assert results and all(isinstance(r, WebResult) for r in results)
    assert {r.source for r in results} == {provider.id}
    assert results[0].url == "https://a.example/1"
    assert results[0].title == "One"
    assert results[0].snippet == "first"


@pytest.mark.parametrize("type_id", sorted(PROVIDERS))
async def test_an_expired_deadline_fails_fast_without_a_request(type_id: str, network: list) -> None:
    sent = AsyncMock()
    provider = build(type_id, sent, network)
    with patch.object(DdgsSearch, "_text") as ddgs_call, pytest.raises(TimedOut):
        await provider.search("query", Deadline(expires_at=0, clock=lambda: 1))
    sent.assert_not_called()
    ddgs_call.assert_not_called()


@pytest.mark.parametrize("type_id", sorted(PROVIDERS))
def test_has_the_source_attributes(type_id: str) -> None:
    provider = build(type_id, AsyncMock())
    assert provider.id
    assert provider.cooldown_key
    assert "test-key" not in provider.cooldown_key


@pytest.mark.parametrize("type_id", sorted(PROVIDERS))
async def test_failures_are_web_errors(type_id: str, network: list) -> None:
    provider = build(type_id, lambda request: httpx.Response(500), network)
    with patch("app.web.search.duckduckgo_html.fetch_public", AsyncMock(return_value=FetchedBody("u", 500, None, b""))), \
            patch.object(DdgsSearch, "_text", side_effect=DDGSException("boom")), \
            pytest.raises(WebError):
        await provider.search("query", Deadline.after(5))
