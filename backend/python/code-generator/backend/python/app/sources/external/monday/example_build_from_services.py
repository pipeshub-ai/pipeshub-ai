import asyncio
import logging

from app.config.configuration_service import ConfigurationService
from app.sources.client.monday.monday import MondayClient
from app.sources.external.monday.monday import MondayDataSource

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    # Initialize configuration service
    config_service = ConfigurationService()

    # Build client from services
    client = await MondayClient.build_from_services(
        logger=logger,
        config_service=config_service,
    )

    # Initialize datasource
    datasource = MondayDataSource(client)

    # Example operation
    response = await datasource.get_boards()

    if response.success:
        logger.info("Successfully fetched boards")
        logger.info(response.data)
    else:
        logger.error(f"Failed to fetch boards: {response.error}")


if __name__ == "__main__":
    asyncio.run(main())
