# ruff: noqa
"""
Example script to demonstrate how to use the Gmail API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.services.graph_db.graph_db_factory import GraphDBFactory
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.gmail.gmail import GoogleGmailDataSource
from app.sources.external.google.gmail.utils import GmailUtils

async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)
    # create graph db service
    graph_db_service = await GraphDBFactory.create_service("arango", logger=logging.getLogger(__name__), config_service=config_service)
    if not graph_db_service:
        raise RuntimeError("Graph DB service not found")
    await graph_db_service.connect()

    # individual google account
    # individual_google_client = await GoogleClient.build_from_services(
    #     service_name="gmail",
    #     version="v1",
    #     logger=logging.getLogger(__name__),
    #     config_service=config_service,
    #     graph_db_service=graph_db_service,
    #     is_individual=True,
    # )

    # gmail_data_source = GoogleGmailDataSource(individual_google_client.get_client())
    # print("gmail_data_source", gmail_data_source)
    # print("Listing messages")
    # results = await gmail_data_source.messages_list()
    # print(results)


    enterprise_google_client = await GoogleClient.build_from_services(
        service_name="gmail",
        version="v1",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        graph_db_service=graph_db_service,
        scopes=[
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.compose",
        ],
    )

    gmail_data_source = GoogleGmailDataSource(enterprise_google_client.get_client())
    print("gmail_data_source", gmail_data_source)
    print("Listing messages")
    # List messages
    #results = await gmail_data_source.users_get_profile(userId="<placeholder>")
    #print(results)


    #results = await gmail_data_source.users_messages_list(userId="<placeholder>")
    #print(results)

    body = GmailUtils.transform_message_body(
        mail_to=["<placeholder>"],
        mail_subject="Test Email body",
        mail_body="<h1>Test Email</h1>",
        mail_attachments=["<placeholder>"],
    )
    print("body", body)
    results = await gmail_data_source.users_messages_send(userId="<placeholder>", body=body)
    print(results)

if __name__ == "__main__":
    asyncio.run(main())
