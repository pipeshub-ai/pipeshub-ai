# ruff: noqa
import asyncio
import os

from dropbox.exceptions import ApiError

from app.sources.client.dropbox.dropbox_ import DropboxAppKeySecretConfig, DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.sources.external.dropbox.pretty_print import to_pretty_json

from app.connectors.services.base_arango_service import BaseArangoService
from arango import ArangoClient
from app.services.kafka_consumer import KafkaConsumerManager
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.utils.logger import create_logger
from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors

ACCESS_TOKEN = os.getenv("DROPBOX_TEAM_TOKEN")

async def main() -> None:
    config = DropboxTokenConfig(token=ACCESS_TOKEN)
    client = await DropboxClient.build_with_config(config, is_team=True)
    data_source = DropboxDataSource(client)

    # #list all team members
    print("\nListing team members:")
    team_members = await data_source.team_members_list()

    print(team_members)
    # for member in team_members.data["members"]:
    #     print(member)
    #     print()
    #     print()

    if not team_members or not team_members.data.get("members"):
        print("Could not find any team members.")
        return

    members = team_members.data["members"]
    print(f"Found {len(members)} team members.")
    
    #list my personal fodler
    print("\nListing my personal folder:")
    my_personal_folder = await data_source.files_list_folder(path="", 
    team_member_id=members[2].source_user_id, 
    recursive=True)
    print(to_pretty_json(my_personal_folder))

    #list team folder
    # print("\nListing team folder:")
    # team_files = await data_source.files_list_folder(path="",team_member_id=members[2].source_user_id, team_folder_id="13131350499", recursive=True)
    # print(to_pretty_json(team_files))

    # #list using cursor
    # print("\nListing using cursor:")
    # # cursor = my_personal_folder.data["cursor"]
    # cursor = "AATxehrQlH_dGIO0PA18ltaaSsXPk4OfZqCxddl4xiyAxVzCWLOlawaNTs7xf7jgXQPOCbCo-e9deSolU1EZWfwmwxVUHcr22S_HsAUJTWEdlggUXK5-uH1icd8fV4aeNsSx7v9dNFgvPJDhZx6WS0oMWn6dn1hNLld2d3N0yFWb1C0nNZQW4F8iVAgX4dKgmTE"
    # cursor_result = await data_source.files_list_folder_continue(cursor, 
    # team_member_id=members[2].source_user_id)
    # print(cursor_result)

    # print("\nget tempory link:")
    # temp_link_result = await data_source.files_get_temporary_link(path="/Resume_Harshit.pdf", team_member_id=members[2].source_user_id)
    # print(temp_link_result)
    

    # List all  groups and memebrer
    # print("\nListing groups:")
    # groups = await data_source.team_groups_list()
    # print(to_pretty_json(groups))

    #Call to arangodb
    # logger = create_logger("onedrive_connector")
    # base_dir = os.path.dirname(os.path.abspath(__file__))
    # config_path = "/home/rogue/Programs/pipeshub-ai/backend/python/app/config/default_config.json"
    # key_value_store = InMemoryKeyValueStore(logger, config_path)
    # config_service = ConfigurationService(logger, key_value_store)
    # kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    # arango_client = ArangoClient()
    # arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    # await arango_service.connect()

    # result = await arango_service.get_record_by_path(connector_name=Connectors.DROPBOX, path="/resume_harshit.pdf")
    # print(result)

    # user = await arango_service.get_user_by_id(user_id=)
    
    


if __name__ == "__main__":
    asyncio.run(main())
