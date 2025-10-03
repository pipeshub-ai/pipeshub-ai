# ruff: noqa
"""
Example script to demonstrate how to use the Google Meet API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.meet.meet import GoogleMeetDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)

    meet_google_client = await GoogleClient.build_from_services(
        service_name="meet",
        version="v2",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        scopes=[
            "https://www.googleapis.com/auth/meetings.space.created",
            "https://www.googleapis.com/auth/meetings.space.settings",
            "https://www.googleapis.com/auth/meetings.space.readonly",
        ],
    )

    google_meet_data_source = GoogleMeetDataSource(meet_google_client.get_client())
    print("google_meet_data_source", google_meet_data_source)

    results = await google_meet_data_source.spaces_create()
    print("Created space:", results)


if __name__ == "__main__":
    asyncio.run(main())
