"""Tests for app.containers.utils.utils — ContainerUtils factory methods."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.containers.utils.utils import ContainerUtils


class TestContainerUtilsInit:
    def test_creates_logger(self):
        cu = ContainerUtils()
        assert cu.logger is not None


class TestGetVectorDbService:
    @pytest.mark.asyncio
    async def test_delegates_to_factory(self):
        cu = ContainerUtils()
        config_service = MagicMock()
        with patch(
            "app.containers.utils.utils.VectorDBFactory.create_vector_db_service",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_factory:
            result = await cu.get_vector_db_service(config_service)
            mock_factory.assert_called_once()
            assert result is not None


class TestCreateArangoService:
    @pytest.mark.asyncio
    async def test_creates_service_when_arangodb(self):
        cu = ContainerUtils()
        logger = MagicMock()
        arango_client = MagicMock()
        config_service = MagicMock()
        kafka_service = MagicMock()
        with patch.dict("os.environ", {"DATA_STORE": "arangodb"}):
            with patch(
                "app.containers.utils.utils.BaseArangoService"
            ) as MockService:
                mock_instance = MagicMock()
                mock_instance.connect = AsyncMock()
                MockService.return_value = mock_instance
                result = await cu.create_arango_service(
                    logger, arango_client, config_service, kafka_service
                )
                assert result is mock_instance
                mock_instance.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_not_arangodb(self):
        cu = ContainerUtils()
        with patch.dict("os.environ", {"DATA_STORE": "neo4j"}):
            result = await cu.create_arango_service(
                MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )
            assert result is None


class TestCreateGraphProvider:
    @pytest.mark.asyncio
    async def test_delegates_to_factory(self):
        cu = ContainerUtils()
        with patch(
            "app.containers.utils.utils.GraphDBProviderFactory.create_provider",
            new_callable=AsyncMock,
            return_value=MagicMock(),
        ) as mock_factory:
            result = await cu.create_graph_provider(MagicMock(), MagicMock())
            mock_factory.assert_called_once()
            assert result is not None


class TestCreateIndexingPipeline:
    @pytest.mark.asyncio
    async def test_creates_pipeline(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.IndexingPipeline") as MockPipeline:
            mock_instance = MagicMock()
            MockPipeline.return_value = mock_instance
            result = await cu.create_indexing_pipeline(
                MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateVectorStore:
    @pytest.mark.asyncio
    async def test_creates_vector_store(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.VectorStore") as MockVS:
            mock_instance = MagicMock()
            MockVS.return_value = mock_instance
            result = await cu.create_vector_store(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(), "test_collection"
            )
            assert result is mock_instance


class TestCreateGraphdb:
    @pytest.mark.asyncio
    async def test_creates_graphdb(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.GraphDBTransformer") as MockGDB:
            mock_instance = MagicMock()
            MockGDB.return_value = mock_instance
            result = await cu.create_graphdb(MagicMock(), MagicMock())
            assert result is mock_instance


class TestCreateSinkOrchestrator:
    @pytest.mark.asyncio
    async def test_creates_orchestrator(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.SinkOrchestrator") as MockSO:
            mock_instance = MagicMock()
            MockSO.return_value = mock_instance
            result = await cu.create_sink_orchestrator(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateDocumentExtractor:
    @pytest.mark.asyncio
    async def test_creates_extractor(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.DocumentExtraction") as MockDE:
            mock_instance = MagicMock()
            MockDE.return_value = mock_instance
            result = await cu.create_document_extractor(
                MagicMock(), MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateBlobStorage:
    @pytest.mark.asyncio
    async def test_creates_blob_storage(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.BlobStorage") as MockBS:
            mock_instance = MagicMock()
            MockBS.return_value = mock_instance
            result = await cu.create_blob_storage(
                MagicMock(), MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateParsers:
    @pytest.mark.asyncio
    async def test_creates_all_parsers(self):
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
            assert isinstance(parsers, dict)
            assert len(parsers) > 0


class TestCreateProcessor:
    @pytest.mark.asyncio
    async def test_creates_processor(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.Processor") as MockProc:
            mock_instance = MagicMock()
            MockProc.return_value = mock_instance
            result = await cu.create_processor(
                MagicMock(), MagicMock(), MagicMock(),
                MagicMock(), {}, MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateEventProcessor:
    @pytest.mark.asyncio
    async def test_creates_event_processor(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.EventProcessor") as MockEP:
            mock_instance = MagicMock()
            MockEP.return_value = mock_instance
            result = await cu.create_event_processor(
                MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateRetrievalService:
    @pytest.mark.asyncio
    async def test_creates_service(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.RetrievalService") as MockRS:
            mock_instance = MagicMock()
            MockRS.return_value = mock_instance
            result = await cu.create_retrieval_service(
                MagicMock(), MagicMock(), MagicMock(), MagicMock(), MagicMock()
            )
            assert result is mock_instance


class TestCreateFeatureFlagService:
    @pytest.mark.asyncio
    async def test_with_config_service(self):
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

    @pytest.mark.asyncio
    async def test_without_config_service(self):
        cu = ContainerUtils()
        with patch("app.containers.utils.utils.FeatureFlagService") as MockFF:
            mock_ff = MagicMock()
            MockFF.get_service.return_value = mock_ff
            result = await cu.create_feature_flag_service(None)
            assert result is mock_ff

    @pytest.mark.asyncio
    async def test_etcd_refresh_failure_handled(self):
        cu = ContainerUtils()
        config_service = MagicMock()
        with patch("app.containers.utils.utils.EtcdProvider") as MockEtcd, \
             patch("app.containers.utils.utils.FeatureFlagService") as MockFF:
            mock_provider = MagicMock()
            mock_provider.refresh = AsyncMock(side_effect=Exception("etcd down"))
            MockEtcd.return_value = mock_provider
            mock_ff = MagicMock()
            MockFF.init_with_etcd_provider = AsyncMock(return_value=mock_ff)
            result = await cu.create_feature_flag_service(config_service)
            assert result is mock_ff
