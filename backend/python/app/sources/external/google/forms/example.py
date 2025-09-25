# ruff: noqa
"""
Example script to demonstrate how to use the Google Calendar API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.forms.forms import GoogleFormsDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)

    # enterprise google account
    enterprise_google_client = await GoogleClient.build_from_services(
        service_name="forms",
        version="v1",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        scopes=[
            "https://www.googleapis.com/auth/forms",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file"
        ],
        is_individual=False,
    )

    google_calendar_data_source = GoogleFormsDataSource(enterprise_google_client.get_client())
    kwargs = {
        "body": {
            "info": {
                "title": "Test Form by Agent"
            }
        }
    }
    created_form = await google_calendar_data_source.forms_create(unpublished=True, **kwargs)
    print("created_form", created_form)


if __name__ == "__main__":
    asyncio.run(main())
