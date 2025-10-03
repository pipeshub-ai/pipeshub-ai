# ruff: noqa
"""
Example script to demonstrate how to use the Google Drive API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.drive.drive import GoogleDriveDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)

    # individual google account
    individual_google_client = await GoogleClient.build_from_services(
        service_name="drive",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        is_individual=True,
    )

    google_drive_client = GoogleDriveDataSource(individual_google_client.get_client())
    print("Listing files")
    results = await google_drive_client.files_list()
    print(results)

    # enterprise google account
    enterprise_google_client = await GoogleClient.build_from_services(
        service_name="drive",
        logger=logging.getLogger(__name__),
        config_service=config_service,
    )

    google_drive_client = GoogleDriveDataSource(enterprise_google_client.get_client())
    print("google_drive_client", google_drive_client)
    print("Listing files")
    results = await google_drive_client.files_list()
    print(results)

if __name__ == "__main__":
    asyncio.run(main())
