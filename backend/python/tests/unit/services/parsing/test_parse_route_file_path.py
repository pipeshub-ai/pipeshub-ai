"""The parse route hands ``file_path`` to the parser through its config."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.parsing import router as parsing_router
from app.models.blocks import BlocksContainer
from app.services.parsing.interface import ParseResult, ParserProvider
from app.services.parsing.registry import ParserRegistry


def _client_and_parser() -> tuple[TestClient, MagicMock]:
    parser = MagicMock()
    parser.parse = AsyncMock(
        return_value=ParseResult(
            block_container=BlocksContainer(blocks=[], block_groups=[]),
            provider_used=ParserProvider.DEFAULT,
            metadata={},
        )
    )
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=parser)

    app = FastAPI()
    app.state.parser_registry = registry
    app.state.governor = MagicMock()
    app.state.governor.gate.return_value = MagicMock(limit=5, in_use=0)
    app.include_router(parsing_router)
    return TestClient(app), parser


@pytest.mark.parametrize(
    ("form_extra", "expected"),
    [({"file_path": "src/pkg/a.py"}, "src/pkg/a.py"), ({}, None)],
    ids=["given", "absent"],
)
def test_file_path_form_field_reaches_parser_config(form_extra, expected) -> None:
    client, parser = _client_and_parser()

    with patch(
        "app.api.routes.parsing.acquire_gate_with_backpressure",
        AsyncMock(return_value=True),
    ):
        response = client.post(
            "/api/v1/parse",
            files={"file": ("a.py", b"x = 1\n", "text/x-python")},
            data={"record_name": "a.py", "extension": "py", "provider": "default", **form_extra},
        )

    assert response.status_code == 200
    config = parser.parse.await_args.args[2]
    assert config.get("file_path") == expected
