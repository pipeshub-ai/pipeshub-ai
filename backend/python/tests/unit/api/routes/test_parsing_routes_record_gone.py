"""The parsing service refuses a parse whose record's delete marker changed since the caller's run started (410)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import parsing
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import DELETED_TTL_MS, InProcessLocks, deleted_name
from app.services.paid_cache.run import clear_run
from app.services.parsing.registry import ParserRegistry
from tests.support.paid_cache import MemoryResultStore, record_in_flight
from tests.support.parsing_app import build_app, ok_result

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG, REC = "org-123", "rec-1"


def _key(i: int) -> CacheKey:
    return CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"p{i}")


class World:
    def __init__(self) -> None:
        self.store, self.locks = MemoryResultStore(), InProcessLocks()
        self.paid = 0

    def post(self, pages: int = 3, delete_after: int | None = None, **form: str) -> Any:  # noqa: ANN401
        cache = PaidResultCache(self.store, self.locks)

        async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
            for i in range(pages):
                if delete_after is not None and i == delete_after:
                    await self.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000")

                async def paid() -> str:
                    self.paid += 1
                    return "page"
                await cache.get_or_compute(_key(i), paid)
            return ok_result()

        registry = MagicMock(spec=ParserRegistry)
        parser = MagicMock()
        parser.parse = parse
        registry.resolve = MagicMock(return_value=parser)
        client = TestClient(build_app(registry), raise_server_exceptions=False)
        return client.post(
            "/api/v1/parse", files={"file": ("s.pdf", b"%PDF-1.4 x", "application/pdf")},
            data={"record_name": "s.pdf", "mime_type": "application/pdf", "extension": "pdf", "provider": "default", **form},
        )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    w = World()
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: PaidResultCache(w.store, w.locks))
    monkeypatch.setattr(parsing, "_config_service", lambda _request: MagicMock())

    @asynccontextmanager
    async def scope(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        with record_in_flight(ORG, REC):
            yield

    monkeypatch.setattr(parsing, "scope_from_claims", scope)
    yield w
    clear_run()


def test_a_marker_that_changed_mid_parse_stops_the_parse_with_a_410_and_pays_for_nothing_more(world: World) -> None:
    response = world.post(pages=5, delete_after=2, orphan_guard="true", record_marker="", org_marker="")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "RECORD_GONE" and response.json()["success"] is False
    assert world.paid == 2                                        # the pages before the delete; none after


def test_a_marker_the_run_started_under_does_not_stop_it(world: World) -> None:
    import asyncio

    asyncio.run(world.locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, "2000"))
    response = world.post(pages=3, orphan_guard="true", record_marker="2000", org_marker="")
    assert response.status_code == 200 and world.paid == 3


def test_an_older_caller_that_sends_no_markers_is_never_stopped_and_so_is_a_request_with_the_stop_disabled(world: World) -> None:
    assert world.post(pages=4, delete_after=1).status_code == 200                       # no orphan_guard field: fail open
    world.paid = 0
    world.store.files.clear()
    assert world.post(pages=4, delete_after=1, orphan_guard="true", stop_disabled="true").status_code == 200
    assert world.paid == 4


def test_the_check_off_is_todays_behaviour(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_ORPHAN_CHECK", "0")
    assert world.post(pages=4, delete_after=1, orphan_guard="true").status_code == 200


_ = AsyncMock
