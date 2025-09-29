# ruff: noqa
import asyncio
import os

from app.sources.client.slack.slack import SlackClient, SlackTokenConfig
from app.sources.external.slack.slack import SlackDataSource
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main() -> None:
    # token = os.getenv("SLACK_TOKEN")
    # if not token:
    #     raise Exception("SLACK_TOKEN is not set")

    # # create configuration service client
    # etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # slack_client : SlackClient = SlackClient.build_with_config(
    #     SlackTokenConfig(
    #         token=token,
    #     ),
    # )
    # print(slack_client)
    # slack_data_source = SlackDataSource(slack_client)
    # print(slack_data_source)
    # print(asyncio.run(slack_data_source.conversations_list()))
    # print(asyncio.run(slack_data_source.conversations_info(channel="xx")))
    # print(asyncio.run(slack_data_source.conversations_list()))
    # print(asyncio.run(slack_data_source.conversations_members(channel="xx")))
    # print(asyncio.run(slack_data_source.conversations_info(channel="xx")))
    # print(asyncio.run(slack_data_source.conversations_history(channel="xx")))
    # print(asyncio.run(slack_data_source.conversations_join(channel="xx")))
    # print(asyncio.run(slack_data_source.conversations_create(name="test")))
    # print(asyncio.run(slack_data_source.conversations_invite(channel="xx", users=['xx', 'xx', 'xx', 'xx', 'xx', 'xx'])))


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
