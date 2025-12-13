"""
Build-from-services example for Zoom
"""

import asyncio
import logging

from backend.python.app.config.configuration_service import ConfigurationService
from backend.python.app.config.providers.etcd.etcd3_encrypted_store import (
    Etcd3EncryptedKeyValueStore,
)
from backend.python.app.sources.client.zoom.zoom import ZoomClient
from backend.python.app.sources.external.zoom.zoom import ZoomDataSource


async def main() -> None:
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # Create ETCD store
    etcd_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # Create configuration service
    config_service = ConfigurationService(
        logger=logger,
        key_value_store=etcd_store,
    )

    # Build Zoom client using configuration service
    try:
        zoom_client = await ZoomClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print("✅ Zoom client created successfully")
    except Exception as e:
        logger.error(f"Failed to create Zoom client: {e}")
        print(f"❌ Error creating Zoom client: {e}")
        return

    # Create data source
    zoom_data_source = ZoomDataSource(zoom_client)

    # Test a simple API call (sanity check)
    try:
        response = await zoom_data_source.users()
        print(f"✅ Users response: {response}")
    except Exception as e:
        print(f"❌ Error calling users API: {e}")


if __name__ == "__main__":
    asyncio.run(main())
