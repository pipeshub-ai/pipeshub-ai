"""The parsing service's ``parse`` writer lists the paid calls of a parse in its revision's manifest.

With ``USE_PARSING_SERVICE`` on, this manifest is the only thing that protects the page OCR and table results a
parse stores from the sweep the ``index`` run starts (QA-S7a-7).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.routes import parsing
from app.modules.pipeline.fingerprint import content_revision
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.run import clear_run
from app.services.parsing.registry import ParserRegistry
from tests.support.paid_cache import MemoryResultStore, record_in_flight
from tests.unit.api.routes.test_parsing_routes import _build_app, _ok_result

ORG, REC = "org-123", "rec-1"
BODY = b"%PDF-1.4 a scan"
KEY = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    monkeypatch.setattr(parsing, "_config_service", lambda _request: MagicMock())

    @asynccontextmanager
    async def scope(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        with record_in_flight(ORG, REC):
            yield

    monkeypatch.setattr(parsing, "scope_from_claims", scope)
    yield memory
    clear_run()


def _post(parser_parse: Any, **extra_form: str) -> Any:  # noqa: ANN401
    registry = MagicMock(spec=ParserRegistry)
    parser = MagicMock()
    parser.parse = parser_parse
    registry.resolve = MagicMock(return_value=parser)
    client = TestClient(_build_app(registry), raise_server_exceptions=False)
    return client.post(
        "/api/v1/parse",
        files={"file": ("s.pdf", BODY, "application/pdf")},
        data={"record_name": "s.pdf", "mime_type": "application/pdf", "extension": "pdf", "provider": "default", **extra_form},
    )


def test_a_parse_lists_the_paid_calls_it_made_in_the_manifest_of_its_revision(store: MemoryResultStore) -> None:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await access.paid_cache_for(MagicMock()).get_or_compute(KEY, AsyncMock(return_value="page text"))
        return _ok_result()

    response = _post(parse)

    assert response.status_code == 200
    manifest = json.loads(store.files[(ORG, REC, f"manifests/{content_revision(BODY)}/parse.json")][0])
    assert manifest["writer"] == "parse" and manifest["keys"] == [KEY.relative_path()]
    assert (ORG, REC, f"manifests/{content_revision(BODY)}/parse.open.json") not in store.files


def test_a_parse_that_fails_keeps_its_open_manifest(store: MemoryResultStore) -> None:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await access.paid_cache_for(MagicMock()).get_or_compute(KEY, AsyncMock(return_value="page text"))
        raise RuntimeError("renderer crashed")

    assert _post(parse).status_code >= 500
    rev = content_revision(BODY)
    assert (ORG, REC, f"manifests/{rev}/parse.open.json") in store.files
    assert (ORG, REC, f"manifests/{rev}/parse.json") not in store.files


def test_a_parse_that_outlives_the_stage_deadline_is_stopped_and_pays_for_nothing_more(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client giving up does not stop a parse, so the server does: nothing is paid for after the deadline."""
    from app.services.paid_cache import locks

    monkeypatch.setattr(locks, "STAGE_DEADLINE_S", 0.1)
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.1")
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "0.1")
    paid_after: list[str] = []

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        import asyncio

        await asyncio.sleep(10)
        paid_after.append("a call made after the deadline")
        return _ok_result()

    response = _post(parse)

    assert response.status_code == 504
    assert response.json()["error"]["code"] == "PARSE_FAILED"
    assert paid_after == []


def test_a_timeout_raised_inside_the_parser_is_not_answered_as_the_stage_deadline(store: MemoryResultStore) -> None:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        raise TimeoutError("the provider timed out")

    response = _post(parse)

    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Unexpected error while parsing the document"


def test_a_parse_the_indexing_side_marked_as_a_refresh_opens_a_bypassing_run(store: MemoryResultStore) -> None:
    from app.services.paid_cache.run import current_run

    seen: list[bool] = []

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        run = current_run()
        seen.append(run is not None and run.refresh)
        return _ok_result()

    assert _post(parse, refresh_paid_outputs="true").status_code == 200
    assert _post(parse).status_code == 200
    assert seen == [True, False]


def test_the_route_uses_the_record_budget_the_client_sent_and_falls_back_to_its_own(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """A 9000 s client budget (scaled) is honoured even when the service's own settings are smaller."""
    import asyncio

    from app.api.routes import parsing
    from app.services.paid_cache import locks

    monkeypatch.setattr(locks, "STAGE_DEADLINE_S", 0.1)
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.1")
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "0.1")
    monkeypatch.setattr(parsing, "_budget_mismatch_logged", set())

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(0.4)
        return _ok_result()

    with caplog.at_level("WARNING"):
        assert _post(parse, record_budget_s="2.0").status_code == 200       # the client's budget covers the parse
    assert any("differs from this service" in r.getMessage() for r in caplog.records)
    assert _post(parse).status_code == 504                                  # no budget sent: the service's own applies
    assert _post(parse, record_budget_s="nan").status_code == 504           # an unusable figure is ignored
    assert _post(parse, record_budget_s="-5").status_code == 504


def test_the_stage_start_write_bound_uses_the_budget_the_client_sent(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.paid_cache import locks

    monkeypatch.setattr(locks, "STAGE_DEADLINE_S", 0.1)
    monkeypatch.setenv("RECORD_PROCESSING_TIMEOUT", "0.1")
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "0.1")
    assert locks.stage_deadline_for(9000.0) == 9000.0
    assert locks.stage_deadline_for(None) == locks.stage_deadline_s()
    assert locks.stage_deadline_for(float("inf")) == locks.stage_deadline_s()
    assert locks.stage_deadline_for(locks.MAX_RECORD_BUDGET_S * 2) == locks.stage_deadline_s()


def test_a_parse_that_fails_after_noting_keys_keeps_every_key_listed(store: MemoryResultStore) -> None:
    """Through the real failure path: no flush() called by the test, the route flushes before it answers."""
    import json

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        cache = access.paid_cache_for(MagicMock())
        for i in range(3):
            await cache.get_or_compute(CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"page{i}"), AsyncMock(return_value="t"))
        raise RuntimeError("renderer crashed")

    assert _post(parse).status_code >= 500
    listed = json.loads(store.files[(ORG, REC, f"manifests/{content_revision(BODY)}/parse.open.json")][0])["keys"]
    assert len(listed) == 3


def test_a_retry_of_a_refresh_request_is_still_a_refresh_that_reuses_only_its_own_earlier_pages(store: MemoryResultStore) -> None:
    from app.services.paid_cache.run import current_run

    seen: list[tuple[bool, bool]] = []

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        run = current_run()
        seen.append((run is not None and run.refresh, run is not None and run.reuse_prior_attempt))
        return _ok_result()

    first = _post(parse, refresh_paid_outputs="true", refresh_token="run-abc")
    retry = _post(parse, refresh_paid_outputs="true", refresh_token="run-abc")     # the client's own retry
    other = _post(parse, refresh_paid_outputs="true", refresh_token="run-xyz")     # a new refresh
    assert (first.status_code, retry.status_code, other.status_code) == (200, 200, 200)
    assert seen == [(True, False), (True, True), (True, False)]


@pytest.mark.parametrize("sent", ["inf", "-inf", "nan", "0", "-5", "1e400"])
def test_an_unusable_record_budget_falls_back_cleanly_and_is_not_reported_as_used(
    store: MemoryResultStore, caplog: pytest.LogCaptureFixture, sent: str,
) -> None:
    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        return _ok_result()

    with caplog.at_level("WARNING"):
        assert _post(parse, record_budget_s=sent).status_code == 200            # not a 500
    assert not [r for r in caplog.records if "differs from this service" in r.getMessage()]


def test_the_runs_stage_bound_is_the_budget_the_client_sent_with_the_2400_s_floor(store: MemoryResultStore) -> None:
    """The write bound of a conversion thread that outlives its 504 is the run's own: it carries the sent budget."""
    from app.services.paid_cache import locks
    from app.services.paid_cache.run import current_run

    bounds: list[float | None] = []

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        run = current_run()
        bounds.append(run.stage_deadline_s if run is not None else None)
        return _ok_result()

    _post(parse, record_budget_s="9000")
    _post(parse, record_budget_s="100")                                          # below the floor
    _post(parse)                                                                  # absent: the process setting
    assert bounds[0] == 9000.0
    assert bounds[1] == float(locks.STAGE_DEADLINE_S)
    assert bounds[2] == locks.stage_deadline_s()


def test_a_budget_longer_than_the_open_manifest_protection_falls_back_and_one_inside_it_is_honoured(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.paid_cache import locks
    from app.services.paid_cache.run import OPEN_PROTECTS_MS

    assert locks.MAX_RECORD_BUDGET_S * 1000 < OPEN_PROTECTS_MS                  # derived from the window, below it
    assert locks.stage_deadline_for(20 * 3600.0) == 20 * 3600.0                 # 20 h is honoured
    assert locks.stage_deadline_for(23 * 3600.0) == locks.stage_deadline_s()    # 23 h falls back
    assert locks.stage_deadline_for(locks.MAX_RECORD_BUDGET_S + 1) == locks.stage_deadline_s()

    monkeypatch.setattr("app.api.routes.parsing._budget_mismatch_logged", set())

    async def parse(*_a: Any, **_k: Any) -> Any:  # noqa: ANN401
        return _ok_result()

    with caplog.at_level("WARNING"):
        assert _post(parse, record_budget_s=str(23 * 3600)).status_code == 200
    assert any("above" in r.getMessage() and "accepts" in r.getMessage() for r in caplog.records)
