# ruff: noqa
"""
Simple Notion API search example.
No pagination, no complexity - just search and print results.
"""
import asyncio
import os

from app.sources.external.posthog.posthog import PostHogClient, PostHogDataSource
from app.sources.client.posthog.posthog import PostHogResponse, PostHogTokenConfig
from app.config.configuration_service import ConfigurationService
import logging
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main():
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build PostHog client using configuration service (await the async method)
    try:
        posthog_client = await PostHogClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"PostHog client created successfully: {posthog_client}")
    except Exception as e:
        logger.error(f"Failed to create PostHog client: {e}")
        print(f"❌ Error creating PostHog client: {e}")
        return
    
    # Create data source and use it
    posthog_data_source = PostHogDataSource(posthog_client)
    
    # Test getting events
    try:
        response = await posthog_data_source.events(
            limit=10,
            event="button_clicked"
        )
        print(f"✅ Get events response: {response}")
    except Exception as e:
        print(f"❌ Error getting events: {e}")

    finally:
        # Properly close the client session
        await posthog_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())