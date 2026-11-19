"""``IndexingPipeline.apply`` calls the lifecycle's ``after_parse`` with the record, the graph and its logger; by default it does nothing."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.indexing import extension
from app.modules.indexing.extension import DefaultIndexingLifecycle, IndexingExtension
from tests.unit.modules.transformers.test_pipeline import (  # noqa: F401 - fixtures
    _make_ctx,
    _make_record,
    doc_extraction,
    pipeline,
    sink_orchestrator,
    stage_ingress,
)


class Recording(DefaultIndexingLifecycle):
    def __init__(self) -> None:
        self.after_parse = AsyncMock()  # type: ignore[method-assign]


@pytest.mark.asyncio
async def test_apply_tells_the_lifecycle_the_record_was_parsed(pipeline, doc_extraction) -> None:  # noqa: F811
    lifecycle = Recording()
    extension.install(IndexingExtension(lifecycle=lifecycle))
    record = _make_record(blocks=[], block_groups=[], record_id="rec-1")

    await pipeline.apply(_make_ctx(record))

    lifecycle.after_parse.assert_awaited_once_with(record, doc_extraction.graph_provider, pipeline.logger)


@pytest.mark.asyncio
async def test_the_default_lifecycle_changes_nothing_about_apply(pipeline, doc_extraction) -> None:  # noqa: F811
    record = _make_record(blocks=[], block_groups=[], record_id="rec-1")

    await pipeline.apply(_make_ctx(record))

    fields = doc_extraction.graph_provider.update_node.await_args.args[2]
    assert fields["indexingStatus"] == "EMPTY"
    assert await DefaultIndexingLifecycle().after_parse(record, None, None) is None
