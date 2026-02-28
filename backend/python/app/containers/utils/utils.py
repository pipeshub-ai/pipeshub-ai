import os
from logging import Logger

from arango import ArangoClient  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import ExtensionTypes
from app.connectors.services.base_arango_service import BaseArangoService
from app.events.events import EventProcessor
from app.events.processor import Processor
from app.modules.indexing.run import IndexingPipeline
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.docx.docx_parser import DocxParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.xls_parser import XLSParser
from app.modules.parsers.html_parser.html_parser import HTMLParser
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.markdown.mdx_parser import MDXParser
from app.modules.parsers.pptx.ppt_parser import PPTParser
from app.modules.parsers.pptx.pptx_parser import PPTXParser
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.graphdb import GraphDBTransformer
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.vectorstore import VectorStore
from app.services.featureflag.featureflag import FeatureFlagService
from app.services.featureflag.provider.etcd import EtcdProvider
from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.const.const import (
    VECTOR_DB_COLLECTION_NAME,
    VECTOR_DB_SERVICE_NAME,
)
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.vector_db_factory import VectorDBFactory
from app.utils.logger import create_logger


# Note - Cannot make this a singleton as it is used in the container and DI does not work with static methods
class ContainerUtils:
    """Utility class for container operations"""
    def __init__(self) -> None:
        self.logger = create_logger("container_utils")

    async def get_vector_db_service(
        self,
        config_service: ConfigurationService,
    ) -> IVectorDBService:
        return await VectorDBFactory.create_vector_db_service(
            service_type=VECTOR_DB_SERVICE_NAME,
            config=config_service,
            is_async=False,
        )

    async def create_arango_service(
        self,
        logger: Logger,
        arango_client: ArangoClient,
        config_service: ConfigurationService,
        kafka_service,
    ) -> BaseArangoService | None:
        """Async factory to create and connect BaseArangoService (without schema init)"""
        # Skip ArangoDB service creation if using a different graph database
        data_store = os.getenv("DATA_STORE", "arangodb").lower()
        if data_store != "arangodb":
            logger.info(f"⏭️ Skipping ArangoDB service creation (DATA_STORE={data_store})")
            return None

        service = BaseArangoService(
            logger,
            arango_client,
            config_service,
            kafka_service,
            enable_schema_init=False,
        )
        await service.connect()
        return service

    async def create_graph_provider(
        self,
        logger: Logger,
        config_service: ConfigurationService,
    ) -> IGraphDBProvider:
        """Async factory to create and connect graph database provider"""
        return await GraphDBProviderFactory.create_provider(
            logger=logger,
            config_service=config_service,
        )

    async def create_indexing_pipeline(
        self,
        logger: Logger,
        config_service: ConfigurationService,
        graph_provider: IGraphDBProvider,
        vector_db_service: IVectorDBService,
    ) -> IndexingPipeline:
        """Async factory for IndexingPipeline"""
        pipeline = IndexingPipeline(
            logger=logger,
            config_service=config_service,
            graph_provider=graph_provider,
            collection_name=VECTOR_DB_COLLECTION_NAME,
            vector_db_service=vector_db_service,
        )
        return pipeline


    async def create_vector_store(self, logger, graph_provider, config_service, vector_db_service, collection_name) -> VectorStore:
        """Async factory for VectorStore"""
        vector_store = VectorStore(logger, config_service, graph_provider, collection_name, vector_db_service)
        return vector_store

    async def create_graphdb(self, graph_provider, logger) -> GraphDBTransformer:
        """Async factory for GraphDB transformer (uses graph_provider for transactions)"""
        graphdb = GraphDBTransformer(graph_provider, logger)
        return graphdb

    async def create_sink_orchestrator(self, logger: Logger, graphdb: GraphDBTransformer, blob_storage: BlobStorage, vector_store: VectorStore, graph_provider: IGraphDBProvider) -> SinkOrchestrator:
        """Async factory for SinkOrchestrator"""
        orchestrator = SinkOrchestrator(graphdb=graphdb, blob_storage=blob_storage, vector_store=vector_store, graph_provider=graph_provider)
        return orchestrator

    async def create_document_extractor(self, logger, graph_provider: IGraphDBProvider, config_service) -> DocumentExtraction:
        """Async factory for DocumentExtraction"""
        extractor = DocumentExtraction(logger, graph_provider, config_service)
        return extractor

    async def create_blob_storage(self, logger, config_service, graph_provider: IGraphDBProvider) -> BlobStorage:
        """Async factory for BlobStorage"""
        blob_storage = BlobStorage(logger, config_service, graph_provider)
        return blob_storage

    async def create_parsers(self, logger: Logger) -> dict:
        """Async factory for Parsers"""
        image_parser = ImageParser(logger)

        parsers = {
            ExtensionTypes.DOCX.value: DocxParser(),
            ExtensionTypes.DOC.value: DocParser(),
            ExtensionTypes.PPTX.value: PPTXParser(),
            ExtensionTypes.PPT.value: PPTParser(),
            ExtensionTypes.HTML.value: HTMLParser(),
            ExtensionTypes.MD.value: MarkdownParser(),
            ExtensionTypes.MDX.value: MDXParser(),
            ExtensionTypes.CSV.value: CSVParser(),
            ExtensionTypes.TSV.value: CSVParser(delimiter="\t"),
            ExtensionTypes.XLSX.value: ExcelParser(logger),
            ExtensionTypes.XLS.value: XLSParser(),
            ExtensionTypes.PNG.value: image_parser,
            ExtensionTypes.JPG.value: image_parser,
            ExtensionTypes.JPEG.value: image_parser,
            ExtensionTypes.WEBP.value: image_parser,
            ExtensionTypes.SVG.value: image_parser,
            ExtensionTypes.HEIC.value: image_parser,
            ExtensionTypes.HEIF.value: image_parser,
        }
        return parsers

    async def create_processor(
        self,
        logger: Logger,
        config_service: ConfigurationService,
        indexing_pipeline: IndexingPipeline,
        graph_provider: IGraphDBProvider,
        parsers: dict,
        document_extractor: DocumentExtraction,
        sink_orchestrator: SinkOrchestrator,
    ) -> Processor:
        """Async factory for Processor"""
        processor = Processor(
            logger=logger,
            config_service=config_service,
            indexing_pipeline=indexing_pipeline,
            graph_provider=graph_provider,
            parsers=parsers,
            document_extractor=document_extractor,
            sink_orchestrator=sink_orchestrator
        )
        # Add any necessary async initialization
        return processor

    async def create_event_processor(
        self,
        logger: Logger,
        processor: Processor,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
    ) -> EventProcessor:
        """Async factory for EventProcessor"""
        event_processor = EventProcessor(
            logger=logger, processor=processor, graph_provider=graph_provider, config_service=config_service
        )
        # Add any necessary async initialization
        return event_processor

    async def create_retrieval_service(
        self,
        config_service: ConfigurationService,
        logger: Logger,
        vector_db_service: IVectorDBService,
        graph_provider: IGraphDBProvider,
        blob_store: BlobStorage,
    ) -> RetrievalService:
        """Async factory for RetrievalService"""
        service = RetrievalService(
            logger=logger,
            config_service=config_service,
            collection_name=VECTOR_DB_COLLECTION_NAME,
            vector_db_service=vector_db_service,
            graph_provider=graph_provider,
            blob_store=blob_store,
        )
        return service

    async def create_feature_flag_service(
        self,
        config_service: ConfigurationService | None = None,
    ) -> FeatureFlagService:
        """Async factory for FeatureFlagService

        Preference order:
        1) EtcdProvider-backed provider (uses get_config under the hood)
        2) Env provider fallback
        """
        if config_service is not None:
            print("Creating EtcdProvider")
            provider = EtcdProvider(config_service)
            try:
                await provider.refresh()
            except Exception as e:
                self.logger.debug(f"Feature flag provider refresh failed: {e}")
            return await FeatureFlagService.init_with_etcd_provider(provider, self.logger)
        else:
            print("Creating EnvFileProvider")
            return FeatureFlagService.get_service()
