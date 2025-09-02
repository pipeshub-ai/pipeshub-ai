# ruff: noqa
"""
Example script to demonstrate how to use the Google Calendar API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.services.graph_db.graph_db_factory import GraphDBFactory
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.calendar.gcalendar import GoogleCalendarDataSource


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

    # individual google account
    individual_google_client = await GoogleClient.build_from_services(
        service_name="calendar",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        graph_db_service=graph_db_service,
        is_individual=True,
    )

    google_calendar_data_source = GoogleCalendarDataSource(individual_google_client.get_client())
    print("Listing events")
    # List events
    events = await google_calendar_data_source.events_list(calendarId="primary")
    print("events", events)
    

    # enterprise google account
    enterprise_google_client = await GoogleClient.build_from_services(
        service_name="calendar",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        graph_db_service=graph_db_service,
    )

    google_calendar_data_source = GoogleCalendarDataSource(enterprise_google_client.get_client())
    print("Listing events")
    # List events
    events = await google_calendar_data_source.events_list(calendarId="primary")
    print("events", events)


if __name__ == "__main__":
    asyncio.run(main())
