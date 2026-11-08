"""The parsing route reserves the memory its kind holds in the parent, once, beside its permit (PX-2e)."""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.services.parsing.registry import ParserRegistry
from app.services.resource_governor import policy
from app.services.resource_governor.memory_domain import permit_covers_here
from app.services.resource_governor.models import Pool
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

if TYPE_CHECKING:
    from fastapi import FastAPI

GIB = 2**30


def _post(client: TestClient, name: str, extension: str, size: int = 1000) -> object:
    return client.post(
        "/api/v1/parse",
        files={"file": (name, b"x" * size, "application/octet-stream")},
        data={"mime_type": "application/octet-stream", "extension": extension, "provider": "default"},
    )


def _app(measured: bool, seen: dict) -> tuple[FastAPI, ParserRegistry]:
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()

    async def parse(*a: object, **k: object) -> object:
        governor = app.state.governor
        seen["reserved"] = governor.memory_budget.stats()["reserved_bytes"]
        seen["in_use"] = governor.gate(Pool.HEAVY_PARSE).in_use
        seen["covers"] = permit_covers_here()
        return _ok_result()

    parser.parse = parse
    registry.resolve = MagicMock(return_value=parser)
    app = _build_app(registry)
    governor = app.state.governor
    governor.memory_budget.limit = 8 * GIB
    governor._heavy_ws = policy.HeavyWorkingSet(0.25, "measured") if measured else policy.HeavyWorkingSet(1.5, "fallback")
    return app, registry


@pytest.mark.parametrize(("extension", "expected"), [("docx", int(1.25 * GIB)), ("pdf", int(0.25 * GIB)), ("xlsx", None)])
def test_a_heavy_parse_reserves_its_excess_while_it_holds_the_permit(extension: str, expected: int | None) -> None:
    seen: dict = {}
    app, _ = _app(True, seen)
    with TestClient(app) as client:
        assert _post(client, f"a.{extension}", extension).status_code == 200  # type: ignore[attr-defined]
        if expected is None:
            from app.modules.parsers.excel.limits import excel_document_memory_bytes

            expected = excel_document_memory_bytes() - int(0.25 * GIB)
        assert seen["reserved"] == expected
        assert seen["in_use"] == 1 and seen["covers"] is True
        assert app.state.governor.memory_budget.stats()["reserved_bytes"] == 0, "released with the permit"
        assert not permit_covers_here()


def test_a_light_parse_reserves_nothing() -> None:
    seen: dict = {}
    app, _ = _app(True, seen)
    with TestClient(app) as client:
        assert _post(client, "a.md", "md").status_code == 200  # type: ignore[attr-defined]
    assert seen["reserved"] == 0 and seen["covers"] is False


def test_a_large_light_text_parse_reserves_its_parent_growth() -> None:
    """Text parses on the light tier, holds no heavy permit, and still grows the parent with its size."""
    from app.services.resource_governor.kinds import ParseKind, permit_excess_bytes
    from app.services.resource_governor.policy import INDEX_LIGHT_WORKING_SET_GB

    size = 5 * 2**20
    seen: dict = {}
    app, _ = _app(True, seen)
    with TestClient(app) as client:
        assert _post(client, "a.md", "md", size).status_code == 200  # type: ignore[attr-defined]
    expected = permit_excess_bytes(ParseKind.TEXT, INDEX_LIGHT_WORKING_SET_GB, "measured", size)
    assert expected > 200 * 2**20
    assert seen["reserved"] == expected
    assert app.state.governor.memory_budget.stats()["reserved_bytes"] == 0, "released after the parse"


def test_off_makes_no_budget_call_on_the_route() -> None:
    seen: dict = {}
    app, _ = _app(False, seen)
    budget = app.state.governor.memory_budget
    budget.reserve = AsyncMock(side_effect=AssertionError("the memory budget was used in off"))  # type: ignore[method-assign]
    with TestClient(app) as client:
        for extension in ("docx", "pdf", "xlsx", "zzz"):
            assert _post(client, f"a.{extension}", extension).status_code == 200  # type: ignore[attr-defined]
    budget.reserve.assert_not_called()


def test_no_memory_is_a_retryable_429_and_the_permit_goes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.routes.parsing as route

    monkeypatch.setattr(route, "EXCESS_WAIT_SECONDS", 0.2)
    seen: dict = {}
    app, _ = _app(True, seen)
    governor = app.state.governor
    governor.memory_budget.limit = 2 * GIB
    governor.memory_budget._reserved = int(1.5 * GIB)  # spoken for by other records
    with TestClient(app) as client:
        response = _post(client, "a.docx", "docx")
        assert response.status_code == 429  # type: ignore[attr-defined]
        assert response.headers["retry-after"] == "5"  # type: ignore[attr-defined]
        assert response.json()["error"]["details"]["reason"] == "memory"  # type: ignore[attr-defined]
    assert governor.gate(Pool.HEAVY_PARSE).in_use == 0
    assert governor.memory_budget.stats()["reserved_bytes"] == int(1.5 * GIB)
