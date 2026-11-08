"""The extraction a record keeps: the entities stage's output first, a legacy stored section only
for a revision the stage never ran on."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.named_entities.domain.models import NamedEntityExtraction
from app.modules.named_entities.kept import keep_extraction, read_kept_extraction

LEGACY = NamedEntityExtraction(status="PARTIAL").model_dump(mode="json")


def _graph(state: dict | None) -> MagicMock:
    graph = MagicMock()
    graph.stage_state_get = AsyncMock(return_value=state)
    graph.stage_state_compare_and_set = AsyncMock(return_value=True)
    return graph


@pytest.mark.asyncio
async def test_the_stage_output_is_the_extraction() -> None:
    graph = _graph({"status": "COMPLETED", "output": NamedEntityExtraction(status="COMPLETED").model_dump_json()})
    kept = await read_kept_extraction(graph, "vr-1", "rev-a", LEGACY)
    assert kept is not None and kept.status == "COMPLETED"
    graph.stage_state_get.assert_awaited_once_with("vr-1:rev-a:entities")


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [None, "", "not json"])
async def test_a_state_without_a_readable_output_keeps_nothing(output: str | None) -> None:
    assert await read_kept_extraction(_graph({"status": "SKIPPED", "output": output}), "vr-1", "rev-a", LEGACY) is None


@pytest.mark.asyncio
async def test_without_a_state_the_legacy_section_is_read() -> None:
    kept = await read_kept_extraction(_graph(None), "vr-1", "rev-a", LEGACY)
    assert kept is not None and kept.status == "PARTIAL"
    assert await read_kept_extraction(_graph(None), "vr-1", "rev-a", None) is None


@pytest.mark.asyncio
async def test_without_a_revision_only_the_legacy_section_is_read() -> None:
    graph = _graph({"status": "COMPLETED", "output": "{}"})
    assert (await read_kept_extraction(graph, "vr-1", None, LEGACY)).status == "PARTIAL"
    graph.stage_state_get.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_graph_error_is_not_read_as_nothing_kept() -> None:
    graph = MagicMock(stage_state_get=AsyncMock(side_effect=ConnectionError("graph down")))
    with pytest.raises(ConnectionError):
        await read_kept_extraction(graph, "vr-1", "rev-a", LEGACY)


@pytest.mark.asyncio
async def test_keeping_replaces_the_output_of_a_completed_state_only() -> None:
    graph = _graph(None)
    extraction = NamedEntityExtraction(status="COMPLETED")

    assert await keep_extraction(graph, "vr-1", "rev-a", extraction) is True

    key, expected, new, fields = graph.stage_state_compare_and_set.await_args.args
    assert (key, expected, new) == ("vr-1:rev-a:entities", "COMPLETED", "COMPLETED")
    assert NamedEntityExtraction.model_validate_json(fields["output"]).status == "COMPLETED"
    assert set(fields) == {"output"}


@pytest.mark.asyncio
async def test_nothing_is_kept_without_a_revision() -> None:
    graph = _graph(None)
    assert await keep_extraction(graph, "vr-1", None, NamedEntityExtraction(status="COMPLETED")) is False
    graph.stage_state_compare_and_set.assert_not_awaited()
