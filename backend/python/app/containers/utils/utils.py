from logging import Logger

from arango import ArangoClient  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import ExtensionTypes
from app.config.constants.service import RedisConfig, config_node_constants
from app.core.ai_arango_service import ArangoService
from app.events.events import EventProcessor
from app.events.processor import Processor
from app.modules.extraction.domain_extraction import DomainExtractor
from app.modules.indexing.run import IndexingPipeline
from app.modules.parsers.csv.csv_parser import CSVParser
from app.modules.parsers.docx.docparser import DocParser
from app.modules.parsers.docx.docx_parser import DocxParser
from app.modules.parsers.excel.excel_parser import ExcelParser
from app.modules.parsers.excel.xls_parser import XLSParser
from app.modules.parsers.html_parser.html_parser import HTMLParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser
from app.modules.parsers.markdown.mdx_parser import MDXParser
from app.modules.parsers.pptx.ppt_parser import PPTParser
from app.modules.parsers.pptx.pptx_parser import PPTXParser
from app.modules.retrieval.retrieval_arango import (
    ArangoService as RetrievalArangoService,
)
from app.modules.retrieval.retrieval_service import RetrievalService
from app.modules.transformers.arango import Arango
from app.modules.transformers.blob_storage import BlobStorage
from app.modules.transformers.document_extraction import DocumentExtraction
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.vectorstore import VectorStore
from app.services.scheduler.redis_scheduler.redis_scheduler import RedisScheduler
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
    ) -> ArangoService:
        """Async factory to create and connect ArangoService"""
        service = ArangoService(logger, arango_client, config_service)
        await service.connect()
        return service

    async def create_indexing_pipeline(
        self,
        logger: Logger,
        config_service: ConfigurationService,
        arango_service: ArangoService,
        vector_db_service: IVectorDBService,
    ) -> IndexingPipeline:
        """Async factory for IndexingPipeline"""
        pipeline = IndexingPipeline(
            logger=logger,
            config_service=config_service,
            arango_service=arango_service,
            collection_name=VECTOR_DB_COLLECTION_NAME,
            vector_db_service=vector_db_service,
        )
        return pipeline


    async def create_domain_extractor(
        self,
        logger: Logger,
        arango_service: ArangoService,
        config_service: ConfigurationService,
    ) -> DomainExtractor:
        """Async factory for DomainExtractor"""
        extractor = DomainExtractor(logger, arango_service, config_service)
        # Add any necessary async initialization
        return extractor

    async def create_vector_store(self, logger, arango_service, config_service, vector_db_service, collection_name) -> VectorStore:
        """Async factory for VectorStore"""
        vector_store = VectorStore(logger, config_service, arango_service, collection_name, vector_db_service)
        return vector_store

    async def create_arango(self, arango_service, logger) -> Arango:
        """Async factory for Arango"""
        arango = Arango(arango_service, logger)
        return arango

    async def create_sink_orchestrator(self, logger, arango, blob_storage, vector_store) -> SinkOrchestrator:
        """Async factory for SinkOrchestrator"""
        orchestrator = SinkOrchestrator(arango=arango, blob_storage=blob_storage, vector_store=vector_store)
        return orchestrator

    async def create_document_extractor(self, logger, arango_service, config_service) -> DocumentExtraction:
        """Async factory for DocumentExtraction"""
        extractor = DocumentExtraction(logger, arango_service, config_service)
        return extractor

    async def create_blob_storage(self, logger, config_service, arango_service) -> BlobStorage:
        """Async factory for BlobStorage"""
        blob_storage = BlobStorage(logger, config_service, arango_service)
        return blob_storage

    async def create_parsers(self, logger: Logger) -> dict:
        """Async factory for Parsers"""
        parsers = {
            ExtensionTypes.DOCX.value: DocxParser(),
            ExtensionTypes.DOC.value: DocParser(),
            ExtensionTypes.PPTX.value: PPTXParser(),
            ExtensionTypes.PPT.value: PPTParser(),
            ExtensionTypes.HTML.value: HTMLParser(),
            ExtensionTypes.MD.value: MarkdownParser(),
            ExtensionTypes.MDX.value: MDXParser(),
            ExtensionTypes.CSV.value: CSVParser(),
            ExtensionTypes.XLSX.value: ExcelParser(logger),
            ExtensionTypes.XLS.value: XLSParser(),
        }
        return parsers

    async def create_processor(
        self,
        logger: Logger,
        config_service: ConfigurationService,
        indexing_pipeline: IndexingPipeline,
        arango_service: ArangoService,
        parsers: dict,
        document_extractor: DocumentExtraction,
        sink_orchestrator: SinkOrchestrator,
        domain_extractor: DomainExtractor,
    ) -> Processor:
        """Async factory for Processor"""
        processor = Processor(
            logger=logger,
            config_service=config_service,
            indexing_pipeline=indexing_pipeline,
            arango_service=arango_service,
            parsers=parsers,
            document_extractor=document_extractor,
            sink_orchestrator=sink_orchestrator,
            domain_extractor=domain_extractor,
        )
        # Add any necessary async initialization
        return processor

    async def create_event_processor(
        self,
        logger: Logger,
        processor: Processor,
        arango_service: ArangoService,
    ) -> EventProcessor:
        """Async factory for EventProcessor"""
        event_processor = EventProcessor(
            logger=logger, processor=processor, arango_service=arango_service
        )
        # Add any necessary async initialization
        return event_processor

    async def create_redis_scheduler(
        self,
        logger: Logger,
        config_service: ConfigurationService,
    ) -> RedisScheduler:
        """Async factory for RedisScheduler"""
        redis_config = await config_service.get_config(
            config_node_constants.REDIS.value
        )
        if redis_config and isinstance(redis_config, dict):
            redis_url = f"redis://{redis_config.get('host', 'localhost')}:{redis_config.get('port', 6379)}/{RedisConfig.REDIS_DB.value}"
        else:
            redis_url = f"redis://localhost:6379/{RedisConfig.REDIS_DB.value}"
        redis_scheduler = RedisScheduler(
            redis_url=redis_url,
            logger=logger,
            config_service=config_service,
            delay_hours=1
        )
        return redis_scheduler

    async def create_retrieval_arango_service(
        self,
        logger: Logger,
        arango_client: ArangoClient,
        config_service: ConfigurationService,
    ) -> RetrievalArangoService:
        """Async factory to create and connect ArangoService"""
        service = RetrievalArangoService(logger, arango_client, config_service)
        await service.connect()
        return service

    async def create_retrieval_service(
        self,
        config_service: ConfigurationService,
        logger: Logger,
        vector_db_service: IVectorDBService,
        arango_service: ArangoService,
    ) -> RetrievalService:
        """Async factory for RetrievalService"""
        service = RetrievalService(
            logger=logger,
            config_service=config_service,
            collection_name=VECTOR_DB_COLLECTION_NAME,
            vector_db_service=vector_db_service,
            arango_service=arango_service,
        )
        return service
