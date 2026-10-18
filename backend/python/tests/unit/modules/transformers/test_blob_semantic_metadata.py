"""BlobStorage.save_semantic_metadata: classification is written into the stored record."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.transformers.blob_storage import BlobStorage


def _storage(stored: dict[str, object] | None) -> BlobStorage:
    storage = BlobStorage(MagicMock(), MagicMock(), AsyncMock())
    storage.get_record_from_storage = AsyncMock(return_value=stored)  # type: ignore[method-assign]
    storage._store_record_dict = AsyncMock()  # type: ignore[method-assign]
    return storage


@pytest.mark.asyncio
async def test_classification_is_written_as_the_next_version_of_the_stored_record() -> None:
    storage = _storage({"record_name": "plan.pdf", "block_containers": {"blocks": []}})
    await storage.save_semantic_metadata("org-1", "rec-1", "vr-1", {"summary": "A VPN plan."})
    storage._store_record_dict.assert_awaited_once_with(  # type: ignore[attr-defined]
        "org-1", "rec-1", "vr-1",
        {"record_name": "plan.pdf", "block_containers": {"blocks": []}, "semantic_metadata": {"summary": "A VPN plan."}},
    )


@pytest.mark.asyncio
async def test_a_missing_stored_record_is_an_error() -> None:
    with pytest.raises(LookupError):
        await _storage(None).save_semantic_metadata("org-1", "rec-1", "vr-1", {})
