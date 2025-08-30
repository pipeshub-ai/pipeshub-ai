# ruff: noqa
"""
Example script to demonstrate how to use the Google Admin API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.services.graph_db.graph_db_factory import GraphDBFactory
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.admin.admin import GoogleAdminDataSource
from app.sources.external.google.drive.drive import GoogleDriveDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)
    # create graph db service
    graph_db_service = await GraphDBFactory.create_service("arango", logger=logging.getLogger(__name__), config_service=config_service)
    if not graph_db_service:
        raise Exception("Graph DB service not found")
    await graph_db_service.connect()

    enterprise_google_client = await GoogleClient.build_from_services(
        service_name="admin",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        graph_db_service=graph_db_service,
    )

    google_admin_client = GoogleAdminDataSource(enterprise_google_client.get_client())
    results = await google_admin_client.users_list(customer="my_customer", orderBy="email", projection="full")

    print(results)
    
    users_get = await google_admin_client.users_get(userKey="xxx")
    print(users_get)


    individual_google_client = await GoogleClient.build_from_services(
        service_name="drive",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        graph_db_service=graph_db_service,
        is_individual=True,
    )

    google_drive_client = GoogleDriveDataSource(individual_google_client.get_client())

    results = await google_drive_client.about_get()
    print(results)



if __name__ == "__main__":
    asyncio.run(main())
