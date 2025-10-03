# ruff: noqa
import asyncio
import os

from app.sources.client.microsoft.microsoft import GraphMode, MSGraphClient, MSGraphClientWithClientIdSecretConfig
from app.sources.external.microsoft.one_note.one_note import OneNoteDataSource, OneNoteResponse
from app.config.configuration_service import ConfigurationService
import logging

from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore

async def main():
    # tenant_id = os.getenv("OUTLOOK_CLIENT_TENANT_ID")
    # client_id = os.getenv("OUTLOOK_CLIENT_ID")
    # client_secret = os.getenv("OUTLOOK_CLIENT_SECRET")
    # if not tenant_id or not client_id or not client_secret:
    #     raise Exception("OUTLOOK_CLIENT_TENANT_ID, OUTLOOK_CLIENT_ID, and OUTLOOK_CLIENT_SECRET must be set")

    # # testing for enterprise account
    # client: MSGraphClient = MSGraphClient.build_with_config(
    #     MSGraphClientWithClientIdSecretConfig(client_id, client_secret, tenant_id), 
    #     mode=GraphMode.APP)
    # print(client)
    # print("****************************")
    # one_note_data_source: OneNoteDataSource = OneNoteDataSource(client)
    # print("one_note_data_source:", one_note_data_source)
    # print("Getting drive...")
    # print("****************************")
    # user_id_or_upn = os.getenv("USER_ID_OR_UPN")
    # if not user_id_or_upn:
    #     raise Exception("USER_ID_OR_UPN must be set")
    # response: OneNoteResponse = await one_note_data_source.users_get_onenote(user_id=user_id_or_upn)
    # print(response.data)
    # print(response.error)
    # print(response.success)

    # Set up logging
    logger = logging.getLogger(__name__)
    logging.basicConfig(level=logging.INFO)

    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logger)

    # create configuration service
    config_service = ConfigurationService(logger=logger, key_value_store=etcd3_encrypted_key_value_store)

    # Build Microsoft Graph client using configuration service (await the async method)
    try:
        ms_graph_client = await MSGraphClient.build_from_services(
            service_name="onenote",
            logger=logger,
            config_service=config_service,
            mode=GraphMode.APP,
        )
        print(f"Microsoft Graph client created successfully: {ms_graph_client}")
    except Exception as e:
        logger.error(f"Failed to create Microsoft Graph client: {e}")
        print(f"❌ Error creating Microsoft Graph client: {e}")
        return
    
    # Create data source and use it
    one_note_data_source = OneNoteDataSource(ms_graph_client)
    
    # Test getting notebooks
    try:
        response = await one_note_data_source.me_get_onenote()
        print(f"✅ Get notebooks response: {response.data}")
    except Exception as e:
        print(f"❌ Error getting notebooks: {e}")
    

if __name__ == "__main__":
    asyncio.run(main())
