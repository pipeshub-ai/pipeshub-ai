import asyncio

from app.config.configuration_service import ConfigurationService
from app.sources.client.gcs.gcs import GCSClient


async def main() -> None:
    config_service = ConfigurationService()
    client = await GCSClient.build_from_services(logger=config_service.logger, config_service=config_service)  # type: ignore
    info = client.get_credentials_info()
    print(info)


if __name__ == "__main__":
    asyncio.run(main())


