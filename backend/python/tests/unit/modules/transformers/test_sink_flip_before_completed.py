"""A record is marked indexed only after the writes that can be handed back for the weight precision.

The entity points of a record are written by ``index`` after its vectors. A flip there must leave the record
unfinished, so its redelivery runs again, instead of ``COMPLETED``, which a redelivery treats as already indexed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.exceptions.indexing_exceptions import RecordDeletedError
from app.modules.transformers import sink_orchestrator as sink_module
from app.modules.transformers.stored_write import COW
from app.modules.transformers.transformer import TransformContext
from app.services.featureflag.platform_settings import ArtifactSettings
from app.services.messaging.precision_flip import PrecisionFlip
from tests.unit.modules.transformers.test_sink_orchestrator_projection import (
    _record,
    _sink,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _settings():  # noqa: ANN202
    with patch.object(sink_module, "artifact_settings", AsyncMock(return_value=ArtifactSettings(COW, "g"))), \
            patch.object(sink_module, "is_artifact_write_enabled", AsyncMock(return_value=True)), \
            patch.object(sink_module, "projection_enabled", return_value=False):
        yield


async def test_the_entity_points_are_written_before_the_record_is_marked_indexed() -> None:
    orch = _sink("NOT_STARTED")
    order: list[str] = []
    orch._sync_record_identity_entities = AsyncMock(side_effect=lambda ctx: order.append("entities"))
    orch._update_indexing_status = AsyncMock(side_effect=lambda ctx: order.append("status") or True)
    await orch.index(TransformContext(record=_record()))
    assert order == ["entities", "status"]


async def test_a_flip_in_the_entity_sync_leaves_the_record_unfinished() -> None:
    orch = _sink("NOT_STARTED")
    orch._sync_record_identity_entities = AsyncMock(side_effect=PrecisionFlip("the index holds fp32"))
    with pytest.raises(PrecisionFlip):
        await orch.index(TransformContext(record=_record()))
    orch._update_indexing_status.assert_not_awaited()  # nothing wrote COMPLETED, so the redelivery indexes again
    orch._save_reconciliation_metadata.assert_not_awaited()


async def test_a_record_deleted_while_it_was_indexed_loses_the_entity_point_it_just_got() -> None:
    orch = _sink("NOT_STARTED")
    orch.entity_vector_store = MagicMock(delete_entity=AsyncMock())
    orch._update_indexing_status = AsyncMock(return_value=False)
    with pytest.raises(RecordDeletedError):
        await orch.index(TransformContext(record=_record()))
    orch.entity_vector_store.delete_entity.assert_awaited_once_with("org-1", "record", "rec-1")
