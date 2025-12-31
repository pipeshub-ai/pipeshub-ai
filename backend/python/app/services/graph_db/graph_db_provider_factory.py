"""
Graph Database Provider Factory

This factory creates the appropriate graph database provider based on configuration.
Uses HTTP-based ArangoDB provider for fully async operations.
Neo4j support to be added in the future.

Design Pattern: Factory Method Pattern
- Encapsulates provider creation logic
- Returns IGraphDBProvider interface
- Hides implementation details from clients
"""

from logging import Logger

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


class GraphDBProviderFactory:
    """
    Factory for creating graph database provider instances.

    This factory abstracts the creation of database providers, allowing
    the application to work with any graph database through the common
    IGraphDBProvider interface.

    Current Support:
    - ArangoDB (HTTP-based, fully async)

    Future Support:
    - Neo4j
    - Other graph databases
    """

    @staticmethod
    async def create_provider(
        logger: Logger,
        config_service: ConfigurationService,
    ) -> IGraphDBProvider:
        """
        Create and initialize a graph database provider.

        Creates an HTTP-based provider for fully async operations.

        Args:
            logger: Logger instance for logging operations
            config_service: Configuration service for database credentials

        Returns:
            IGraphDBProvider: Connected database provider instance

        Raises:
            ValueError: If required parameters are missing
            ConnectionError: If unable to connect to the database

        Example:
            ```python
            # Create HTTP provider (fully async)
            provider = await GraphDBProviderFactory.create_provider(
                logger=logger,
                config_service=config_service,
            )

            # Use provider
            doc = await provider.get_document("key", "collection")
            ```
        """
        try:
            logger.info("🏭 GraphDBProviderFactory: Creating database provider...")

            # TODO: In future, read from config to determine provider type
            # graphdb_config = await config_service.get_config("/services/graphdb")
            # provider_type = graphdb_config.get("provider", "arangodb")

            provider_type = "arangodb"
            logger.info(f"📦 Creating {provider_type} HTTP provider (fully async)...")

            # Create HTTP-based ArangoDB provider
            if provider_type == "arangodb":
                provider = await GraphDBProviderFactory._create_arango_http_provider(
                    logger=logger,
                    config_service=config_service
                )
                return provider

            # Future: Add Neo4j support
            # elif provider_type == "neo4j":
            #     provider = await GraphDBProviderFactory._create_neo4j_provider(
            #         logger=logger,
            #         config_service=config_service
            #     )
            #     return provider

            else:
                raise ValueError(f"Unsupported graph database provider: {provider_type}")

        except Exception as e:
            logger.error(f"❌ GraphDBProviderFactory: Failed to create provider: {str(e)}")
            raise

    @staticmethod
    async def _create_arango_http_provider(
        logger: Logger,
        config_service: ConfigurationService,
    ) -> ArangoHTTPProvider:
        """
        Create and connect an ArangoDB HTTP provider (fully async).

        This provider uses REST API for all operations, avoiding the synchronous
        python-arango SDK. All operations are non-blocking.

        Args:
            logger: Logger instance
            config_service: Configuration service

        Returns:
            ArangoHTTPProvider: Connected ArangoDB HTTP provider

        Raises:
            ConnectionError: If unable to connect to ArangoDB
        """
        try:
            logger.debug("🔧 Creating ArangoDB HTTP provider...")
            provider = ArangoHTTPProvider(
                logger=logger,
                config_service=config_service
            )
            logger.debug("🔌 Connecting ArangoDB HTTP provider...")
            connected = await provider.connect()
            if not connected:
                raise ConnectionError("Failed to connect ArangoDB HTTP provider to database")

            logger.info("✅ ArangoDB HTTP provider created and connected successfully")
            return provider

        except Exception as e:
            logger.error(f"❌ Failed to create ArangoDB HTTP provider: {str(e)}")
            raise

    # Future: Neo4j provider creation
    # @staticmethod
    # async def _create_neo4j_provider(
    #     logger: Logger,
    #     config_service: ConfigurationService,
    # ) -> Neo4jProvider:
    #     """
    #     Create and connect a Neo4j provider.
    #
    #     Args:
    #         logger: Logger instance
    #         config_service: Configuration service
    #
    #     Returns:
    #         Neo4jProvider: Connected Neo4j provider
    #     """
    #     try:
    #         logger.debug("🔧 Creating Neo4j provider...")
    #
    #         # Get Neo4j configuration
    #         neo4j_config = await config_service.get_config("/services/neo4j")
    #
    #         if not neo4j_config:
    #             raise ValueError("Neo4j configuration not found")
    #
    #         # Create provider instance
    #         provider = Neo4jProvider(
    #             logger=logger,
    #             config=neo4j_config
    #         )
    #
    #         logger.debug("🔌 Connecting Neo4j provider...")
    #
    #         # Connect to database
    #         connected = await provider.connect()
    #
    #         if not connected:
    #             raise ConnectionError("Failed to connect Neo4j provider to database")
    #
    #         logger.info("✅ Neo4j provider created and connected successfully")
    #         return provider
    #
    #     except Exception as e:
    #         logger.error(f"❌ Failed to create Neo4j provider: {str(e)}")
    #         raise



# Convenience function
async def create_graph_db_provider(
    logger: Logger,
    config_service: ConfigurationService,
) -> IGraphDBProvider:
    """
    Convenience function to create a graph database provider.

    This is a simple wrapper around GraphDBProviderFactory.create_provider()
    for easier imports and usage. Always creates HTTP-based provider.

    Args:
        logger: Logger instance
        config_service: Configuration service

    Returns:
        IGraphDBProvider: Connected database provider (HTTP-based, fully async)

    Example:
        ```python
        from app.services.graph_db.graph_db_provider_factory import create_graph_db_provider

        # HTTP provider (fully async)
        provider = await create_graph_db_provider(logger, config_service)
        ```
    """
    return await GraphDBProviderFactory.create_provider(
        logger=logger,
        config_service=config_service
    )

