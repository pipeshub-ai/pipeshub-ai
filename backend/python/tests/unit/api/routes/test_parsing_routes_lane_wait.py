"""A local conversion that finds no CPU lane in time is answered as backpressure (T70)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from app.services.parsing.registry import ParserRegistry
from app.services.resource_governor.lanes import LaneWaitTimeout
from tests.unit.api.routes.test_parsing_routes import _build_app


def _post(client: TestClient) -> object:
    return client.post(
        "/api/v1/parse",
        files={"file": ("a.docx", b"PK", "application/octet-stream")},
        data={"mime_type": "application/octet-stream", "extension": "docx", "provider": "default"},
    )


def test_lane_wait_timeout_is_a_retryable_429_not_a_500() -> None:
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()
    parser.parse = AsyncMock(side_effect=LaneWaitTimeout("no lane"))
    registry.resolve = MagicMock(return_value=parser)
    with TestClient(_build_app(registry)) as client:
        response = _post(client)
        assert response.status_code == 429  # type: ignore[attr-defined]
        assert response.headers["retry-after"] == "5"  # type: ignore[attr-defined]
        body = response.json()  # type: ignore[attr-defined]
        assert body["error"]["code"] == "PARSE_BACKPRESSURE"
        # The permit went back: the next request is admitted.
        parser.parse = AsyncMock(side_effect=RuntimeError("x"))
        assert _post(client).status_code == 500  # type: ignore[attr-defined]
