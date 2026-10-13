"""Which written extractions are queued to be extracted again, and how."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.named_entities import stage
from app.modules.named_entities.domain.config import NamedEntityConfig
from app.modules.named_entities.domain.models import (
    ExtractionStats,
    NamedEntityExtraction,
)
from app.modules.named_entities.stage import REEXTRACT_MAX_WINDOWS, needs_reextract


def _extraction(status: str, strategy: str = "single_call", reason: str = "single_call", **stats: int) -> NamedEntityExtraction:
    return NamedEntityExtraction(status=status, strategy=strategy, termination_reason=reason, stats=ExtractionStats(**stats))


@pytest.mark.parametrize(("extraction", "expected"), [
    (_extraction("PARTIAL", windows_total=7, windows_run=5), True),
    (_extraction("PARTIAL", strategy="agent", reason="budget_turns"), True),
    (_extraction("COMPLETED", windows_total=2, windows_run=2), False),
    # Failed calls are not unread text: a re-extraction would fail the same way.
    (_extraction("PARTIAL", windows_total=3, windows_run=3, windows_failed=1), False),
    (_extraction("PARTIAL", strategy="agent", reason="llm_error"), False),
    # A re-extraction that used its whole budget settles instead of looping.
    (_extraction("PARTIAL", windows_total=80, windows_run=REEXTRACT_MAX_WINDOWS), False),
])
def test_only_unread_text_queues_a_reextraction(extraction: NamedEntityExtraction, expected: bool) -> None:
    assert needs_reextract(extraction) is expected


def _ctx() -> SimpleNamespace:
    record = SimpleNamespace(
        block_containers=SimpleNamespace(blocks=[SimpleNamespace(index=0, id="b0", type="text", data="Acme")]),
        org_id="o1", record_name="r", record_type="FILE", source_updated_at=None, source_created_at=None,
    )
    return SimpleNamespace(record=record)


async def test_a_reextraction_reads_in_single_calls_with_the_larger_budget() -> None:
    extract = AsyncMock(return_value=_extraction("COMPLETED"))
    client = MagicMock()
    with patch.object(stage, "load_named_entity_config", AsyncMock(return_value=NamedEntityConfig())), \
            patch.object(stage, "indexing_llm", AsyncMock(return_value=(None, None, "p", "m"))), \
            patch.object(stage.NamedEntityExtractor, "extract", extract):
        await stage.extract_named_entities(_ctx(), config_service=None, client=client, enabled=True, reextract=True)

    request = extract.await_args.args[0]
    assert request.budgets.max_llm_windows == REEXTRACT_MAX_WINDOWS
    assert request.supports_tools is False
    client.extract_entities.assert_not_called()


async def test_indexing_keeps_the_configured_budget_and_the_agent() -> None:
    extract = AsyncMock(return_value=_extraction("COMPLETED"))
    with patch.object(stage, "load_named_entity_config", AsyncMock(return_value=NamedEntityConfig())), \
            patch.object(stage, "indexing_llm", AsyncMock(return_value=(None, None, "p", "m"))), \
            patch.object(stage.NamedEntityExtractor, "extract", extract):
        await stage.extract_named_entities(_ctx(), config_service=None, enabled=True)

    request = extract.await_args.args[0]
    assert request.budgets.max_llm_windows == NamedEntityConfig().budgets.max_llm_windows
    assert request.supports_tools is None
