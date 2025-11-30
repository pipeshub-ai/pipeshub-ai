# example_zoom_build_from_services.py
# ruff: noqa

import asyncio
import logging

from app.sources.client.zoom.zoom import ZoomClient
from app.sources.external.zoom.zoom_ import ZoomDataSource

from app.config.configuration_service import ConfigurationService
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("zoom-example")

    print("\n🔧 Initializing Configuration Service...")

    # Create ETCD + ConfigService (as PipesHub expects)
    etcd_store = Etcd3EncryptedKeyValueStore(logger=logger)
    config_service = ConfigurationService(
        logger=logger,
        key_value_store=etcd_store
    )

    print("\n📌 Building Zoom client using build_from_services()...")

    try:
        zoom_client = await ZoomClient.build_from_services(
            logger=logger,
            config_service=config_service
        )
        print("✅ Zoom client successfully built!")
    except Exception as e:
        print("❌ Failed to build Zoom client from ETCD:", str(e))
        return

    # Create datasource
    zoom_ds = ZoomDataSource(zoom_client)

    # ------------------------------
    # TEST: List Meetings
    # ------------------------------
    print("\n📌 Listing meetings for 'me':")
    meetings = await zoom_ds.list_meetings("me", page_size=5)


    print("➡️ Response:")
    print(meetings)

    # You can test more endpoints if needed:
    # print(await zoom_ds.users_list(page_size=10))
    # print(await zoom_ds.get_meeting("123456789"))
    # print(await zoom_ds.create_meeting("me", {...}))


if __name__ == "__main__":
    asyncio.run(main())
