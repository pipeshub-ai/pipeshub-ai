# ruff: noqa
"""
Simple Notion API search example.
No pagination, no complexity - just search and print results.
"""
import asyncio
import os

from app.sources.external.discord.discord import DiscordClient, DiscordDataSource
from app.sources.client.discord.discord import DiscordResponse, DiscordTokenConfig
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

    # Build Discord client using configuration service (await the async method)
    try:
        discord_client = await DiscordClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Discord client created successfully: {discord_client}")
    except Exception as e:
        logger.error(f"Failed to create Discord client: {e}")
        print(f"❌ Error creating Discord client: {e}")
        return
    
    # Create data source and use it
    discord_data_source = DiscordDataSource(discord_client)

    # Test getting pages (this will require a valid page ID)
    try:
        response = await discord_data_source.get_data_source()
        print(f"✅ Get data source response: {response}")
    except Exception as e:
        print(f"❌ Error getting data source: {e}")

    finally:
        # Properly close the client session
        await discord_client.get_client().close()

if __name__ == "__main__":
    asyncio.run(main())