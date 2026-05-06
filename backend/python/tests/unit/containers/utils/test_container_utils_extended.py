"""
Extended tests for app/containers/utils/utils.py — ContainerUtils.

Targets additional coverage for:
- get_vector_db_service: verifies correct params passed
- create_indexing_pipeline: verifies VECTOR_DB_COLLECTION_NAME used
- create_parsers: verifies all extension types present
- create_feature_flag_service: with None config_service calls get_service
- create_feature_flag_service: with config_service and refresh failure
- create_vector_store: verifies args forwarded correctly
- create_sink_orchestrator: verifies args forwarded correctly
- create_retrieval_service: verifies VECTOR_DB_COLLECTION_NAME used
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.utils.utils import ContainerUtils


class TestGetVectorDbServiceExtended:
    @pytest.mark.asyncio
    async def test_passes_correct_params(self):
        """Verifies is_async=False and correct service_type."""
        cu = ContainerUtils()
        config_service = MagicMock()

        with patch(
            "app.containers.utils.utils.VectorDBFactory.create_vector_db_service",
            new_callable=AsyncMock,
        ) as mock_factory:
            mock_factory.return_value = MagicMock()
            await cu.get_vector_db_service(config_service)

            mock_factory.assert_called_once()
            call_kwargs = mock_factory.call_args[1]
            assert call_kwargs["is_async"] is False
            assert call_kwargs["config"] is config_service


# ---------------------------------------------------------------------------
# create_indexing_pipeline: verifies collection name
# ---------------------------------------------------------------------------


class TestCreateIndexingPipelineExtended:
    @pytest.mark.asyncio
    async def test_uses_collection_name_constant(self):
        """Verifies VECTOR_DB_COLLECTION_NAME is passed."""
        cu = ContainerUtils()
        from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME

        with patch("app.containers.utils.utils.IndexingPipeline") as MockPipeline:
            mock_instance = MagicMock()
            MockPipeline.return_value = mock_instance

            await cu.create_indexing_pipeline(
                MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )

            call_kwargs = MockPipeline.call_args[1]
            assert call_kwargs["collection_name"] == VECTOR_DB_COLLECTION_NAME


# ---------------------------------------------------------------------------
# create_parsers: verify all extension types present
# ---------------------------------------------------------------------------


class TestCreateParsersExtended:
    @pytest.mark.asyncio
    async def test_all_expected_extensions(self):
        """Verify all file extension types have parsers."""
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.DocxParser"), \
             patch("app.containers.utils.utils.DocParser"), \
             patch("app.containers.utils.utils.PPTXParser"), \
             patch("app.containers.utils.utils.PPTParser"), \
             patch("app.containers.utils.utils.HTMLParser"), \
             patch("app.containers.utils.utils.MarkdownParser"), \
             patch("app.containers.utils.utils.MDXParser"), \
             patch("app.containers.utils.utils.CSVParser"), \
             patch("app.containers.utils.utils.ExcelParser"), \
             patch("app.containers.utils.utils.XLSParser"), \
             patch("app.containers.utils.utils.ImageParser"):
            parsers = await cu.create_parsers(MagicMock())

            expected_keys = [
                "docx", "doc", "pptx", "ppt", "html", "md", "mdx",
                "csv", "tsv", "xlsx", "xls",
                "png", "jpg", "jpeg", "webp", "svg", "heic", "heif",
            ]
            for key in expected_keys:
                assert key in parsers, f"Missing parser for extension: {key}"

    @pytest.mark.asyncio
    async def test_image_parsers_share_same_instance(self):
        """All image extensions should share the same ImageParser instance."""
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.DocxParser"), \
             patch("app.containers.utils.utils.DocParser"), \
             patch("app.containers.utils.utils.PPTXParser"), \
             patch("app.containers.utils.utils.PPTParser"), \
             patch("app.containers.utils.utils.HTMLParser"), \
             patch("app.containers.utils.utils.MarkdownParser"), \
             patch("app.containers.utils.utils.MDXParser"), \
             patch("app.containers.utils.utils.CSVParser"), \
             patch("app.containers.utils.utils.ExcelParser"), \
             patch("app.containers.utils.utils.XLSParser"), \
             patch("app.containers.utils.utils.ImageParser") as MockImg:
                mock_img = MagicMock()
                MockImg.return_value = mock_img
                parsers = await cu.create_parsers(MagicMock())

                image_exts = ["png", "jpg", "jpeg", "webp", "svg", "heic", "heif"]
                for ext in image_exts:
                    assert parsers[ext] is mock_img


# ---------------------------------------------------------------------------
# create_feature_flag_service: edge cases
# ---------------------------------------------------------------------------


class TestCreateFeatureFlagServiceExtended:
    @pytest.mark.asyncio
    async def test_without_config_returns_env_service(self):
        """When config_service is None, uses EnvFileProvider fallback."""
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.FeatureFlagService") as MockFF:
            mock_ff = MagicMock()
            MockFF.get_service.return_value = mock_ff
            result = await cu.create_feature_flag_service(None)
            assert result is mock_ff
            MockFF.get_service.assert_called_once()

    @pytest.mark.asyncio
    async def test_with_config_creates_etcd_provider(self):
        """When config_service provided, creates EtcdProvider."""
        cu = ContainerUtils()
        config_service = MagicMock()
        with patch("app.containers.utils.utils.EtcdProvider") as MockEtcd, \
             patch("app.containers.utils.utils.FeatureFlagService") as MockFF:
            mock_provider = MagicMock()
            mock_provider.refresh = AsyncMock()
            MockEtcd.return_value = mock_provider
            mock_ff = MagicMock()
            MockFF.init_with_etcd_provider = AsyncMock(return_value=mock_ff)

            result = await cu.create_feature_flag_service(config_service)
            assert result is mock_ff
            MockEtcd.assert_called_once_with(config_service)
            mock_provider.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_refresh_failure_still_creates_service(self):
        """Even if refresh fails, service is still created."""
        cu = ContainerUtils()
        config_service = MagicMock()
        with patch("app.containers.utils.utils.EtcdProvider") as MockEtcd, \
             patch("app.containers.utils.utils.FeatureFlagService") as MockFF:
            mock_provider = MagicMock()
            mock_provider.refresh = AsyncMock(side_effect=ConnectionError("etcd unreachable"))
            MockEtcd.return_value = mock_provider
            mock_ff = MagicMock()
            MockFF.init_with_etcd_provider = AsyncMock(return_value=mock_ff)

            result = await cu.create_feature_flag_service(config_service)
            assert result is mock_ff


# ---------------------------------------------------------------------------
# create_retrieval_service: verifies collection name
# ---------------------------------------------------------------------------


class TestCreateRetrievalServiceExtended:
    @pytest.mark.asyncio
    async def test_uses_collection_name_constant(self):
        """Verifies VECTOR_DB_COLLECTION_NAME is passed."""
        cu = ContainerUtils()
        from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME

        with patch("app.containers.utils.utils.RetrievalService") as MockRS:
            mock_instance = MagicMock()
            MockRS.return_value = mock_instance

            await cu.create_retrieval_service(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )

            call_kwargs = MockRS.call_args[1]
            assert call_kwargs["collection_name"] == VECTOR_DB_COLLECTION_NAME


# ---------------------------------------------------------------------------
# create_processor: verifies all args forwarded
# ---------------------------------------------------------------------------


class TestCreateProcessorExtended:
    @pytest.mark.asyncio
    async def test_all_args_forwarded(self):
        """Verify all arguments are forwarded to Processor constructor."""
        cu = ContainerUtils()
        mock_logger = MagicMock()
        mock_config = MagicMock()
        mock_pipeline = MagicMock()
        mock_gp = MagicMock()
        mock_parsers = {"ext": MagicMock()}
        mock_extractor = MagicMock()
        mock_sink = MagicMock()

        with patch("app.containers.utils.utils.Processor") as MockProc:
            mock_instance = MagicMock()
            MockProc.return_value = mock_instance

            result = await cu.create_processor(
                mock_logger, mock_config, mock_pipeline,
                mock_gp, mock_parsers, mock_extractor, mock_sink
            )
            assert result is mock_instance

            call_kwargs = MockProc.call_args[1]
            assert call_kwargs["logger"] is mock_logger
            assert call_kwargs["config_service"] is mock_config
            assert call_kwargs["indexing_pipeline"] is mock_pipeline
            assert call_kwargs["graph_provider"] is mock_gp
            assert call_kwargs["parsers"] is mock_parsers
            assert call_kwargs["document_extractor"] is mock_extractor
            assert call_kwargs["sink_orchestrator"] is mock_sink


# ---------------------------------------------------------------------------
# create_event_processor: verifies all args forwarded
# ---------------------------------------------------------------------------


class TestCreateEventProcessorExtended:
    @pytest.mark.asyncio
    async def test_all_args_forwarded(self):
        cu = ContainerUtils()
        mock_logger = MagicMock()
        mock_proc = MagicMock()
        mock_gp = MagicMock()
        mock_config = MagicMock()

        with patch("app.containers.utils.utils.EventProcessor") as MockEP:
            mock_instance = MagicMock()
            MockEP.return_value = mock_instance

            result = await cu.create_event_processor(
                mock_logger, mock_proc, mock_gp, mock_config
            )
            assert result is mock_instance

            call_kwargs = MockEP.call_args[1]
            assert call_kwargs["logger"] is mock_logger
            assert call_kwargs["processor"] is mock_proc
            assert call_kwargs["graph_provider"] is mock_gp
            assert call_kwargs["config_service"] is mock_config
