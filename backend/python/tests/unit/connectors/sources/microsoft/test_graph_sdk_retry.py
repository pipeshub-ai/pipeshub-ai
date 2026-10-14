"""The Microsoft Graph SDK retries throttled and unavailable requests on its own.

OneDrive and SharePoint rely on the SDK's retry middleware for 429 and 503
answers. The client is built the way ``OneDriveConnector.init`` builds it, with
the real SDK and middleware; only the HTTP transport and the token are fakes.
If these tests fail, check the installed microsoft-kiota-http version against
the pin in pyproject.toml.
"""

import json
import logging
import time

import httpx
import pytest
from azure.core.credentials import AccessToken
from kiota_http.kiota_client_factory import KiotaClientFactory
from msgraph import GraphServiceClient

from app.connectors.sources.microsoft.common.msgraph_client import MSGraphClient

DELTA_URL = "https://graph.microsoft.com/v1.0/users/u-ana/drive/root/delta"


class _StaticToken:
    async def get_token(self, *_scopes: str, **_kwargs: object) -> AccessToken:
        return AccessToken("fake-graph-token", int(time.time()) + 3600)

    async def close(self) -> None:
        return None


def _graph(status: int, body: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers={"content-type": "application/json", **(headers or {})})


@pytest.fixture
def slept(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    delays: list[float] = []

    async def _sleep(delay: float, *_: object) -> None:
        delays.append(delay)

    monkeypatch.setattr("kiota_http.middleware.retry_handler.asyncio.sleep", _sleep)
    return delays


def onedrive_graph_client(monkeypatch: pytest.MonkeyPatch, answers: list[httpx.Response]) -> tuple[MSGraphClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _answer(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answers.pop(0)

    monkeypatch.setattr(
        KiotaClientFactory, "get_default_client", staticmethod(lambda: httpx.AsyncClient(transport=httpx.MockTransport(_answer)))
    )
    client = GraphServiceClient(_StaticToken(), scopes=["https://graph.microsoft.com/.default"])
    return MSGraphClient("OneDrive", "onedrive-1", client, logging.getLogger("test.graph_retry")), seen


class TestGraphSdkRetries:
    async def test_a_throttled_request_is_retried_after_the_wait_microsoft_asks_for(self, monkeypatch, slept) -> None:
        graph, seen = onedrive_graph_client(monkeypatch, [
            _graph(429, {"error": {"code": "activityLimitReached", "message": "throttled"}}, {"Retry-After": "7"}),
            _graph(200, {"value": [{"id": "f1", "name": "one.pdf"}], "@odata.deltaLink": f"{DELTA_URL}?token=D1"}),
        ])

        result = await graph.get_delta_response(DELTA_URL)

        assert [item.name for item in result["drive_items"]] == ["one.pdf"]
        assert result["delta_link"] == f"{DELTA_URL}?token=D1"
        assert len(seen) == 2
        assert slept == [7]

    async def test_a_briefly_unavailable_service_is_retried(self, monkeypatch, slept) -> None:
        graph, seen = onedrive_graph_client(monkeypatch, [
            _graph(503, {"error": {"code": "serviceNotAvailable", "message": "try later"}}),
            _graph(200, {"value": [], "@odata.deltaLink": f"{DELTA_URL}?token=D1"}),
        ])

        result = await graph.get_delta_response(DELTA_URL)

        assert result["delta_link"] == f"{DELTA_URL}?token=D1"
        assert len(seen) == 2
        assert len(slept) == 1
