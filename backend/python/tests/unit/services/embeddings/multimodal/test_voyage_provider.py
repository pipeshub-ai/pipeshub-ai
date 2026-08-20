"""Tests for VoyageMultimodalProvider."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.embeddings.multimodal.voyage_provider import VoyageMultimodalProvider


class TestVoyageMultimodalProvider:
    @pytest.mark.asyncio
    async def test_embed_images_success(self) -> None:
        dense_embeddings = MagicMock()
        dense_embeddings.batch_size = 2
        dense_embeddings.aembed_documents = AsyncMock(return_value=[[0.1, 0.2]])
        provider = VoyageMultimodalProvider(dense_embeddings=dense_embeddings)

        results = await provider.embed_images(["img1"])

        assert len(results) == 1
        assert results[0].embedding == [0.1, 0.2]

    @pytest.mark.asyncio
    async def test_batch_failure_returns_error_results_not_raise(self) -> None:
        """A failing Voyage batch call must not raise — every image in that
        batch should come back as an error result so other batches still run."""
        logger = MagicMock()
        dense_embeddings = MagicMock()
        dense_embeddings.batch_size = 2
        dense_embeddings.aembed_documents = AsyncMock(side_effect=RuntimeError("Voyage API error"))
        provider = VoyageMultimodalProvider(dense_embeddings=dense_embeddings, logger=logger)

        results = await provider.embed_images(["img1"])

        assert len(results) == 1
        assert results[0].embedding is None
        assert "Voyage API error" in results[0].error
        logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_multiple_batches_are_flattened_in_order(self) -> None:
        dense_embeddings = MagicMock()
        dense_embeddings.batch_size = 1
        dense_embeddings.aembed_documents = AsyncMock(
            side_effect=[[[0.1]], [[0.2]], [[0.3]]]
        )
        provider = VoyageMultimodalProvider(dense_embeddings=dense_embeddings)

        results = await provider.embed_images(["img0", "img1", "img2"])

        assert [r.index for r in results] == [0, 1, 2]
        assert [r.embedding for r in results] == [[0.1], [0.2], [0.3]]

    def test_provider_name(self) -> None:
        provider = VoyageMultimodalProvider(dense_embeddings=MagicMock())
        assert provider.provider_name == "voyage"
