"""A mocked SinkOrchestrator's ``store_blob``."""

from unittest.mock import AsyncMock, MagicMock


def delegate_store_blob(sink: MagicMock) -> AsyncMock:
    """What the real ``store_blob`` does for a non-SQL record (write the blob as
    is), so assertions on ``sink.blob_storage.apply`` keep their meaning."""

    async def _store(ctx: object) -> object:
        return await sink.blob_storage.apply(ctx)

    return AsyncMock(side_effect=_store)
