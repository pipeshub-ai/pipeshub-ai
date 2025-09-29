# ruff: noqa
"""
Example script to demonstrate how to use the Google Admin API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.admin.admin import GoogleAdminDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)

    enterprise_google_client = await GoogleClient.build_from_services(
        service_name="admin",
        version="directory_v1",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        key_value_store=etcd3_encrypted_key_value_store,
    )

    google_admin_client = GoogleAdminDataSource(enterprise_google_client.get_client())
    results = await google_admin_client.users_list(customer="my_customer", orderBy="email", projection="full")

    print(results)
    
    users_get = await google_admin_client.users_get(userKey="<placeholder>")
    print(users_get)


if __name__ == "__main__":
    asyncio.run(main())
