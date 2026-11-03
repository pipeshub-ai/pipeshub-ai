"""`is_named_entity_extraction_enabled`: on when the deployment sets
`HIGH_ACCURACY_MODE` or the Labs flag `ENABLE_NAMED_ENTITY_EXTRACTION` is on.

Indexing, the duplicate-copy path, the entity tools and the fetch note all ask
this one function, so the wiring tests below go through two of its real
callers rather than patching it."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_discovery
from app.modules.named_entities import stage
from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.featureflag.platform_settings import (
    is_named_entity_extraction_enabled,
)

HAM = "HIGH_ACCURACY_MODE"


def _config_service(flags: dict | None = None) -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={} if flags is None else {"featureFlags": flags})
    return svc


def _failing_config_service() -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=RuntimeError("kv store unavailable"))
    return svc


@pytest.fixture(autouse=True)
def _no_ambient_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HAM, raising=False)


class TestWithoutHighAccuracyMode:
    async def test_off_by_default(self) -> None:
        assert await is_named_entity_extraction_enabled(_config_service()) is False

    async def test_the_labs_flag_turns_it_on(self) -> None:
        svc = _config_service({"ENABLE_NAMED_ENTITY_EXTRACTION": True})
        assert await is_named_entity_extraction_enabled(svc) is True

    async def test_a_read_failure_is_off(self) -> None:
        assert await is_named_entity_extraction_enabled(_failing_config_service()) is False

    async def test_no_config_service_is_off(self) -> None:
        assert await is_named_entity_extraction_enabled(None) is False

    @pytest.mark.parametrize("value", ["false", "", "  ", "1", "yes", "on"])
    async def test_any_value_but_true_leaves_the_labs_flag_in_charge(
        self, monkeypatch: pytest.MonkeyPatch, value: str,
    ) -> None:
        monkeypatch.setenv(HAM, value)
        assert await is_named_entity_extraction_enabled(_config_service()) is False
        on = _config_service({"ENABLE_NAMED_ENTITY_EXTRACTION": True})
        assert await is_named_entity_extraction_enabled(on) is True


class TestWithHighAccuracyMode:
    @pytest.mark.parametrize("value", ["true", "TRUE", " True "])
    async def test_on_without_reading_the_labs_flag(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(HAM, value)
        svc = _config_service()
        assert await is_named_entity_extraction_enabled(svc) is True
        svc.get_config.assert_not_awaited()

    async def test_a_labs_flag_stored_off_does_not_turn_it_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HAM, "true")
        svc = _config_service({"ENABLE_NAMED_ENTITY_EXTRACTION": False})
        assert await is_named_entity_extraction_enabled(svc) is True

    async def test_on_when_the_settings_store_is_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HAM, "true")
        assert await is_named_entity_extraction_enabled(_failing_config_service()) is True

    async def test_on_without_a_config_service(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HAM, "true")
        assert await is_named_entity_extraction_enabled(None) is True

    async def test_the_mode_is_read_on_every_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = _config_service()
        monkeypatch.setenv(HAM, "true")
        assert await is_named_entity_extraction_enabled(svc) is True
        monkeypatch.setenv(HAM, "false")
        assert await is_named_entity_extraction_enabled(svc) is False


def _sink(config_service: AsyncMock) -> SinkOrchestrator:
    sink = SinkOrchestrator.__new__(SinkOrchestrator)
    sink.logger = logging.getLogger("test")
    sink.config_service = config_service
    return sink


class TestCallersFollowTheMode:
    @pytest.mark.parametrize(("mode", "expected"), [("true", True), ("false", False)])
    async def test_indexing_extracts_under_the_mode_with_the_labs_flag_off(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, expected: bool,
    ) -> None:
        monkeypatch.setenv(HAM, mode)
        extract = AsyncMock(return_value=NamedEntityExtraction(status="SKIPPED"))
        monkeypatch.setattr(stage, "extract_named_entities", extract)
        sink = _sink(_config_service({"ENABLE_NAMED_ENTITY_EXTRACTION": False}))
        await sink.extract_named_entities(SimpleNamespace(record=SimpleNamespace(id="rec-1")))
        assert extract.await_args.kwargs["enabled"] is expected

    @pytest.mark.parametrize(("mode", "expected"), [("true", True), ("false", False)])
    async def test_the_entity_tools_follow_the_mode_with_the_labs_flag_off(
        self, monkeypatch: pytest.MonkeyPatch, mode: str, expected: bool,
    ) -> None:
        monkeypatch.setenv(HAM, mode)
        state = {"config_service": _config_service({"ENABLE_NAMED_ENTITY_EXTRACTION": False})}
        assert await entity_discovery._named_entities_enabled(state) is expected

    async def test_indexing_without_the_mode_still_follows_the_labs_flag(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        extract = AsyncMock(return_value=NamedEntityExtraction(status="SKIPPED"))
        monkeypatch.setattr(stage, "extract_named_entities", extract)
        sink = _sink(_config_service({"ENABLE_NAMED_ENTITY_EXTRACTION": True}))
        await sink.extract_named_entities(SimpleNamespace(record=SimpleNamespace(id="rec-1")))
        assert extract.await_args.kwargs["enabled"] is True

