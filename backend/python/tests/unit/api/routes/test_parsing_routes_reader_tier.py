"""The parsing route admits a request under the tier of the parser its registry resolves when that parser is heavy (F23g)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services.parsing.registry import ParserRegistry
from app.services.resource_governor.models import Pool
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result


def _pools_in_use(name: str, extension: str, mime_type: str) -> dict[Pool, int]:
    """The parse gates' use while the parser runs, for one request with these labels."""
    seen: dict[Pool, int] = {}
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()

    async def parse(*_a: object, **_k: object) -> object:
        governor = app.state.governor
        seen.update({pool: governor.gate(pool).in_use for pool in (Pool.HEAVY_PARSE, Pool.LIGHT_PARSE)})
        return _ok_result()

    parser.parse = parse
    registry.resolve = MagicMock(return_value=parser)
    app = _build_app(registry)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/parse",
            files={"file": (name, b"%PDF-1.7 bytes", mime_type)},
            data={"mime_type": mime_type, "extension": extension, "provider": "default"},
        )
    assert response.status_code == 200, response.text
    return seen


@pytest.mark.parametrize(("extension", "mime_type"), [
    ("txt", "application/pdf"),  # the extension says light; the registry resolves the PDF parser
    ("md", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ("pdf", "application/octet-stream"),  # the extension decides over a generic mime, as before
])
def test_a_heavy_parser_is_admitted_on_the_heavy_pool(extension: str, mime_type: str) -> None:
    assert _pools_in_use(f"Report.{extension}", extension, mime_type) == {Pool.HEAVY_PARSE: 1, Pool.LIGHT_PARSE: 0}


@pytest.mark.parametrize(("extension", "mime_type"), [
    ("txt", "text/plain"),
    ("md", "text/markdown"),
    ("txt", "application/octet-stream"),
])
def test_a_light_parser_stays_on_the_light_pool(extension: str, mime_type: str) -> None:
    assert _pools_in_use(f"Notes.{extension}", extension, mime_type) == {Pool.HEAVY_PARSE: 0, Pool.LIGHT_PARSE: 1}
