"""Unit tests for app.modules.reranker.reranker.RerankerService."""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.models.blocks import BlockType, GroupType


class TestRerankerService:
    """Tests for RerankerService initialization and reranking logic."""

    @pytest.fixture
    def mock_cross_encoder(self):
        """Patch CrossEncoder and torch so no real model loads."""
        with patch("app.modules.reranker.reranker.CrossEncoder") as mock_ce, \
             patch("app.modules.reranker.reranker.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            model_instance = MagicMock()
            mock_ce.return_value = model_instance
            yield mock_ce, model_instance, mock_torch

    @pytest.fixture
    def service(self, mock_cross_encoder):
        """Create a RerankerService with mocked model."""
        from app.modules.reranker.reranker import RerankerService
        mock_ce, model_instance, _ = mock_cross_encoder
        svc = RerankerService(model_name="test-model")
        assert mock_ce.called
        return svc

    # ── Initialization ──────────────────────────────────────────────────

    def test_init_cpu_device(self, mock_cross_encoder):
        from app.modules.reranker.reranker import RerankerService
        _, model_instance, mock_torch = mock_cross_encoder
        mock_torch.cuda.is_available.return_value = False
        svc = RerankerService()
        assert svc.device == "cpu"
        # half() should NOT be called on CPU
        model_instance.model.half.assert_not_called()

    def test_init_cuda_device_applies_half_precision(self):
        with patch("app.modules.reranker.reranker.CrossEncoder") as mock_ce, \
             patch("app.modules.reranker.reranker.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            model_instance = MagicMock()
            mock_ce.return_value = model_instance
            # Capture original .model before it gets overwritten by half()
            original_inner_model = model_instance.model
            from app.modules.reranker.reranker import RerankerService
            svc = RerankerService()
            assert svc.device == "cuda"
            original_inner_model.half.assert_called_once()

    def test_init_stores_model_name(self, mock_cross_encoder):
        from app.modules.reranker.reranker import RerankerService
        svc = RerankerService(model_name="custom/model")
        assert svc.model_name == "custom/model"

    # ── Empty input ─────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_empty_documents_returns_empty(self, service):
        result = await service.rerank("test query", [])
        assert result == []

    # ── Text documents ──────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_text_documents_sorted_by_final_score(self, service):
        service.model.predict.return_value = np.array([0.9, 0.1, 0.5])
        docs = [
            {"content": "doc A about Python", "score": 0.5, "block_type": BlockType.TEXT.value},
            {"content": "doc B about Java", "score": 0.8, "block_type": BlockType.TEXT.value},
            {"content": "doc C about Rust", "score": 0.3, "block_type": BlockType.TEXT.value},
        ]
        result = await service.rerank("programming", docs)
        assert len(result) == 3
        # Check sorted descending by final_score
        assert result[0]["final_score"] >= result[1]["final_score"]
        assert result[1]["final_score"] >= result[2]["final_score"]

    @pytest.mark.asyncio
    async def test_rerank_weighted_score_combination(self, service):
        """final_score = 0.3 * retriever_score + 0.7 * reranker_score."""
        service.model.predict.return_value = np.array([1.0])
        docs = [{"content": "hello", "score": 0.5, "block_type": BlockType.TEXT.value}]
        result = await service.rerank("q", docs)
        expected = 0.3 * 0.5 + 0.7 * 1.0
        assert abs(result[0]["final_score"] - expected) < 1e-6
        assert abs(result[0]["reranker_score"] - 1.0) < 1e-6

    @pytest.mark.asyncio
    async def test_rerank_no_original_score_uses_reranker_only(self, service):
        """If doc has no 'score' key, final_score equals reranker_score."""
        service.model.predict.return_value = np.array([0.75])
        docs = [{"content": "hello", "block_type": BlockType.TEXT.value}]
        result = await service.rerank("q", docs)
        assert abs(result[0]["final_score"] - 0.75) < 1e-6

    # ── IMAGE blocks ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_image_blocks_skipped_with_zero_scores(self, service):
        service.model.predict.return_value = np.array([0.8])
        docs = [
            {"content": "text doc", "score": 0.5, "block_type": BlockType.TEXT.value},
            {"content": "image data", "score": 0.9, "block_type": BlockType.IMAGE.value},
        ]
        result = await service.rerank("q", docs)
        image_doc = [d for d in result if d["block_type"] == BlockType.IMAGE.value][0]
        assert image_doc["reranker_score"] == 0.0
        # IMAGE with original score 0.9 should keep it via doc.get("score", 0.0)
        assert image_doc["final_score"] == 0.9

    @pytest.mark.asyncio
    async def test_rerank_all_images_returns_default_scores(self, service):
        """All IMAGE blocks => no valid pairs => default scores."""
        docs = [
            {"content": "img1", "score": 0.5, "block_type": BlockType.IMAGE.value},
            {"content": "img2", "score": 0.3, "block_type": BlockType.IMAGE.value},
        ]
        result = await service.rerank("q", docs)
        assert len(result) == 2
        for doc in result:
            assert doc["reranker_score"] == 0.0
            assert doc["final_score"] == doc["score"]

    # ── TABLE blocks ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_table_block_uses_first_content_element(self, service):
        service.model.predict.return_value = np.array([0.6])
        docs = [
            {"content": ["table summary", "row1", "row2"], "score": 0.5,
             "block_type": GroupType.TABLE.value},
        ]
        result = await service.rerank("q", docs)
        # Verify model was called with (query, content[0])
        call_args = service.model.predict.call_args[0][0]
        assert call_args[0] == ("q", "table summary")

    # ── Mixed block types ───────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_mixed_blocks_correct_score_index(self, service):
        """Score index must skip IMAGE blocks."""
        # Only 2 pairs: text + table (image is skipped)
        service.model.predict.return_value = np.array([0.9, 0.3])
        docs = [
            {"content": "text content", "score": 0.5, "block_type": BlockType.TEXT.value},
            {"content": "image data", "score": 0.5, "block_type": BlockType.IMAGE.value},
            {"content": ["table data"], "score": 0.5, "block_type": GroupType.TABLE.value},
        ]
        result = await service.rerank("q", docs)
        text_doc = [d for d in result if d["block_type"] == BlockType.TEXT.value][0]
        table_doc = [d for d in result if d["block_type"] == GroupType.TABLE.value][0]
        image_doc = [d for d in result if d["block_type"] == BlockType.IMAGE.value][0]
        assert text_doc["reranker_score"] == 0.9
        assert table_doc["reranker_score"] == 0.3
        assert image_doc["reranker_score"] == 0.0

    # ── Empty content ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_empty_content_skipped(self, service):
        """Documents with empty string content are not scored."""
        docs = [
            {"content": "", "score": 0.5, "block_type": BlockType.TEXT.value},
            {"content": None, "score": 0.3, "block_type": BlockType.TEXT.value},
        ]
        result = await service.rerank("q", docs)
        # No valid pairs => default scores
        for doc in result:
            assert doc["reranker_score"] == 0.0

    # ── top_k ───────────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_top_k_limits_results(self, service):
        service.model.predict.return_value = np.array([0.9, 0.1, 0.5, 0.7])
        docs = [
            {"content": f"doc {i}", "score": 0.5, "block_type": BlockType.TEXT.value}
            for i in range(4)
        ]
        result = await service.rerank("q", docs, top_k=2)
        assert len(result) == 2
        # Should be the top 2 by final_score
        assert result[0]["final_score"] >= result[1]["final_score"]

    @pytest.mark.asyncio
    async def test_rerank_top_k_none_returns_all(self, service):
        service.model.predict.return_value = np.array([0.5, 0.3])
        docs = [
            {"content": f"doc {i}", "score": 0.5, "block_type": BlockType.TEXT.value}
            for i in range(2)
        ]
        result = await service.rerank("q", docs, top_k=None)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_rerank_top_k_larger_than_docs(self, service):
        service.model.predict.return_value = np.array([0.5])
        docs = [{"content": "doc", "score": 0.5, "block_type": BlockType.TEXT.value}]
        result = await service.rerank("q", docs, top_k=10)
        assert len(result) == 1

    # ── Prediction failure ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_prediction_error_returns_default_scores(self, service):
        service.model.predict.side_effect = RuntimeError("model crashed")
        docs = [
            {"content": "doc A", "score": 0.7, "block_type": BlockType.TEXT.value},
            {"content": "doc B", "score": 0.3, "block_type": BlockType.TEXT.value},
        ]
        result = await service.rerank("q", docs)
        assert len(result) == 2
        for doc in result:
            assert doc["reranker_score"] == 0.0
            assert doc["final_score"] == doc["score"]

    # ── Field preservation ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_preserves_original_fields(self, service):
        service.model.predict.return_value = np.array([0.5])
        docs = [
            {"content": "doc", "score": 0.5, "block_type": BlockType.TEXT.value,
             "virtual_record_id": "vr-1", "block_index": 3, "custom_field": "keep me"},
        ]
        result = await service.rerank("q", docs)
        assert result[0]["virtual_record_id"] == "vr-1"
        assert result[0]["block_index"] == 3
        assert result[0]["custom_field"] == "keep me"

    # ── Single document ─────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_single_document(self, service):
        service.model.predict.return_value = np.array([0.85])
        docs = [{"content": "only doc", "score": 0.5, "block_type": BlockType.TEXT.value}]
        result = await service.rerank("q", docs)
        assert len(result) == 1
        assert "reranker_score" in result[0]
        assert "final_score" in result[0]

    # ── Documents without block_type ────────────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_no_block_type_treated_as_text(self, service):
        """If block_type is missing, content should still be scored."""
        service.model.predict.return_value = np.array([0.6])
        docs = [{"content": "some text", "score": 0.4}]
        result = await service.rerank("q", docs)
        assert result[0]["reranker_score"] == 0.6

    # ── IMAGE block with no original score ──────────────────────────────

    @pytest.mark.asyncio
    async def test_rerank_image_no_original_score_defaults_to_zero(self, service):
        service.model.predict.return_value = np.array([0.5])
        docs = [
            {"content": "text", "block_type": BlockType.TEXT.value, "score": 0.5},
            {"content": "img", "block_type": BlockType.IMAGE.value},
        ]
        result = await service.rerank("q", docs)
        img = [d for d in result if d["block_type"] == BlockType.IMAGE.value][0]
        assert img["final_score"] == 0.0
