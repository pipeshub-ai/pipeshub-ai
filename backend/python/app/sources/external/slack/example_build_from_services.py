# ruff: noqa
import asyncio
import os

from app.sources.client.slack.slack import SlackClient, SlackTokenConfig
from app.sources.external.slack.slack import SlackDataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main() -> None:
    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build Slack client using configuration service (await the async method)
    try:
        slack_client = await SlackClient.build_from_services(
            logger=logger,
            config_service=config_service,
        )
        print(f"Slack client created successfully: {slack_client}")
    except Exception as e:
        logger.error(f"Failed to create Slack client: {e}")
        print(f"❌ Error creating Slack client: {e}")
        return
    
    # Create data source and use it
    slack_data_source = SlackDataSource(slack_client)
    
    # Test conversations list
    try:
        response = await slack_data_source.conversations_list()
        print(f"✅ Conversations list response: {response}")
    except Exception as e:
        print(f"❌ Error getting conversations list: {e}")


if __name__ == "__main__":
    asyncio.run(main())
