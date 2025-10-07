# ruff: noqa
"""
Example script to demonstrate how to use the Google Slides API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.slides.slides import GoogleSlidesDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)

    slides_google_client = await GoogleClient.build_from_services(
        service_name="slides",
        version="v1",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        scopes=[
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/presentations.readonly",
        ],
    )

    google_slides_data_source = GoogleSlidesDataSource(slides_google_client.get_client())
    print("google_slides_data_source", google_slides_data_source)
    
    body = {
        "title": "Test Presentation",
        "locale": "en-US",
        "revisionId": "1",  
    }
    results = await google_slides_data_source.presentations_create(body=body)
    print("Created presentation:", results)

    # results = await google_slides_data_source.presentations_get(presentationId="1TkA-wruOGBNFYJmuIAPmyeTIJ-UC1Fghx1SLDUQd7fM")
    # print("Presentation:", results)


if __name__ == "__main__":
    asyncio.run(main())
