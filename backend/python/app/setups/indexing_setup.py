
from arango import ArangoClient
from dependency_injector import containers, providers
from dotenv import load_dotenv
from qdrant_client import QdrantClient

from app.config.configuration_service import (
    ConfigurationService,
    RedisConfig,
    config_node_constants,
)
from app.config.utils.named_constants.arangodb_constants import (
    ExtensionTypes,
    QdrantCollectionNames,
)
from app.core.ai_arango_service import ArangoService
from app.core.redis_scheduler import RedisScheduler
from app.events.events import EventProcessor
from app.events.processor import Processor
from app.health.health import Health
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
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger

load_dotenv(override=True)


class AppContainer(containers.DeclarativeContainer):
    """Dependency injection container for the application."""

    # Log when container is initialized
    logger = providers.Singleton(create_logger, "indexing_service")

    logger().info("🚀 Initializing AppContainer")

    # Core services that don't depend on account type
    config_service = providers.Singleton(ConfigurationService, logger=logger)

    async def _fetch_arango_host(config_service) -> str:
        """Fetch ArangoDB host URL from etcd asynchronously."""
        arango_config = await config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        return arango_config["url"]

    async def _create_arango_client(config_service) -> ArangoClient:
        """Async factory method to initialize ArangoClient."""
        hosts = await AppContainer._fetch_arango_host(config_service)
        return ArangoClient(hosts=hosts)

    arango_client = providers.Resource(
        _create_arango_client, config_service=config_service
    )

    # First create an async factory for the connected ArangoService
    async def _create_arango_service(logger, arango_client, config) -> ArangoService:
        """Async factory to create and connect ArangoService"""
        service = ArangoService(logger, arango_client, config)
        await service.connect()
        return service

    arango_service = providers.Resource(
        _create_arango_service,
        logger=logger,
        arango_client=arango_client,
        config=config_service,
    )

    # Vector search service
    async def _get_qdrant_config(config_service: ConfigurationService) -> dict:
        """Async factory method to get Qdrant configuration."""
        qdrant_config = await config_service.get_config(
            config_node_constants.QDRANT.value
        )
        return {
            "apiKey": qdrant_config["apiKey"],
            "host": qdrant_config["host"],
            "grpcPort": qdrant_config["grpcPort"],
        }

    qdrant_config = providers.Resource(
        _get_qdrant_config, config_service=config_service
    )

    async def _get_qdrant_client(qdrant_config) -> QdrantClient:
        """Async factory method to get Qdrant client."""
        return QdrantClient(
            host=qdrant_config["host"],
            grpc_port=qdrant_config["grpcPort"],
            api_key=qdrant_config["apiKey"],
            prefer_grpc=True,
            https=False,
            timeout=180,
        )


    qdrant_client =  providers.Resource(
        _get_qdrant_client,
        qdrant_config=qdrant_config,
    )

    # Indexing pipeline
    async def _create_indexing_pipeline(logger, config_service, arango_service, qdrant_client) -> IndexingPipeline:
        """Async factory for IndexingPipeline"""
        pipeline = IndexingPipeline(
            logger=logger,
            config_service=config_service,
            arango_service=arango_service,
            collection_name=QdrantCollectionNames.RECORDS.value,
            qdrant_client=qdrant_client,
        )
        return pipeline

    indexing_pipeline = providers.Resource(
        _create_indexing_pipeline,
        logger=logger,
        config_service=config_service,
        arango_service=arango_service,
        qdrant_client=qdrant_client,
    )

    # Domain extraction service - depends on arango_service
    async def _create_domain_extractor(logger, arango_service, config_service) -> DomainExtractor:
        """Async factory for DomainExtractor"""
        extractor = DomainExtractor(logger, arango_service, config_service)
        # Add any necessary async initialization
        return extractor

    domain_extractor = providers.Resource(
        _create_domain_extractor,
        logger=logger,
        arango_service=arango_service,
        config_service=config_service,
    )

    # Parsers
    async def _create_parsers(logger) -> dict:
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

    parsers = providers.Resource(_create_parsers, logger=logger)

    # Processor - depends on domain_extractor, indexing_pipeline, and arango_service
    async def _create_processor(
        logger,
        config_service,
        domain_extractor,
        indexing_pipeline,
        arango_service,
        parsers,
    ) -> Processor:
        """Async factory for Processor"""
        processor = Processor(
            logger=logger,
            config_service=config_service,
            domain_extractor=domain_extractor,
            indexing_pipeline=indexing_pipeline,
            arango_service=arango_service,
            parsers=parsers,
        )
        # Add any necessary async initialization
        return processor

    processor = providers.Resource(
        _create_processor,
        logger=logger,
        config_service=config_service,
        domain_extractor=domain_extractor,
        indexing_pipeline=indexing_pipeline,
        arango_service=arango_service,
        parsers=parsers,
    )

    # Event processor - depends on processor
    async def _create_event_processor(logger, processor, arango_service) -> EventProcessor:
        """Async factory for EventProcessor"""
        event_processor = EventProcessor(
            logger=logger, processor=processor, arango_service=arango_service
        )
        # Add any necessary async initialization
        return event_processor

    event_processor = providers.Resource(
        _create_event_processor,
        logger=logger,
        processor=processor,
        arango_service=arango_service,
    )

    # Redis scheduler
    async def _create_redis_scheduler(logger, config_service) -> RedisScheduler:
        """Async factory for RedisScheduler"""
        redis_config = await config_service.get_config(
            config_node_constants.REDIS.value
        )
        redis_url = f"redis://{redis_config['host']}:{redis_config['port']}/{RedisConfig.REDIS_DB.value}"

        redis_scheduler = RedisScheduler(redis_url=redis_url, logger=logger, delay_hours=1)
        return redis_scheduler

    redis_scheduler = providers.Resource(
        _create_redis_scheduler, logger=logger, config_service=config_service
    )

    # Kafka consumer with async initialization
    async def _create_kafka_consumer(logger, config_service, event_processor, redis_scheduler) -> KafkaConsumerManager:
        """Async factory for KafkaConsumerManager"""
        consumer = KafkaConsumerManager(
            logger=logger,
            config_service=config_service,
            event_processor=event_processor,
            redis_scheduler=redis_scheduler,
        )
        # Add any necessary async initialization
        return consumer

    kafka_consumer = providers.Resource(
        _create_kafka_consumer,
        logger=logger,
        config_service=config_service,
        event_processor=event_processor,
        redis_scheduler=redis_scheduler,
    )

    # Wire everything up
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.indexing_main",
            "app.services.kafka_consumer",
            "app.modules.extraction.domain_extraction",
        ]
    )

async def initialize_container(container: AppContainer) -> bool:
    """Initialize container resources"""
    logger = container.logger()
    logger.info("🚀 Initializing application resources")

    try:
        # Connect to ArangoDB and Redis
        logger.info("Connecting to ArangoDB")
        arango_service = await container.arango_service()
        if arango_service:
            arango_connected = await arango_service.connect()
            if not arango_connected:
                raise Exception("Failed to connect to ArangoDB")
            logger.info("✅ Connected to ArangoDB")
        else:
            raise Exception("Failed to connect to ArangoDB")

        # Initialize Kafka consumer
        logger.info("Initializing Kafka consumer")
        consumer = await container.kafka_consumer()
        await consumer.start()
        logger.info("✅ Kafka consumer initialized")
        logger.info("Running health checks for indexing services...")
        await Health.system_health_check(container)
        logger.info("✅ All health checks completed successfully")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to initialize resources: {str(e)}")
        raise
