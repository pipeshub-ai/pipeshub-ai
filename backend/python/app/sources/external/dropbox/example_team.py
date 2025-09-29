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
from app.models.entities import AppUser, RecordGroup, RecordGroupType
from app.sources.external.dropbox.dropbox_ import DropboxResponse
from dropbox.team_log import EventCategory


ACCESS_TOKEN = os.getenv("DROPBOX_TEAM_TOKEN")

def get_app_users(users: DropboxResponse):
        app_users: List[AppUser] = []
        for member in users.data.members:
            profile = member.profile
            app_users.append(
                AppUser(
                    # source_user_id=member.team_member_id,
                    app_name="DROPBOX",
                    source_user_id=profile.team_member_id,
                    # first_name=profile.name.given_name,
                    # last_name=profile.name.surname,
                    full_name=profile.name.display_name,
                    email=profile.email,
                    is_active=(profile.status._tag == "active"),
                    title=member.role._tag,
                    
                )
            )
        return app_users

async def main() -> None:
    config = DropboxTokenConfig(token=ACCESS_TOKEN)
    client = await DropboxClient.build_with_config(config, is_team=True)
    data_source = DropboxDataSource(client)

    #list all team members
    print("\nListing team members:")
    team_members = await data_source.team_members_list()
    print((team_members.data.members))

    # #list team folder items
    # print("\nListing team folder:")
    # team_files = await data_source.files_list_folder(path="",team_member_id=team_members.data.members[2].profile.team_member_id, team_folder_id="13131350499", recursive=True)
    # print(to_pretty_json(team_files))

    # #list all folder groups in team:
    # folder_groups = await data_source.team_team_folder_list()
    # print(folder_groups)

    #list mebers of team folder
    # print("\nListing team folder members:")
    # team_folder_members = await data_source.sharing_list_folder_members(shared_folder_id="13131350499", team_member_id=team_members.data.members[2].profile.team_member_id, as_admin=True)
    # print(team_folder_members)

    # print("\nListing team folder members2:")
    # team_folder_members = await data_source.sharing_list_folder_members(shared_folder_id="13160107251", team_member_id=team_members.data.members[2].profile.team_member_id, as_admin=True)
    # print(team_folder_members)

    # #list dropbox user groups
    # print("\nListing dropbox user groups:")
    # dropbox_groups = await data_source.team_groups_list()
    # print(dropbox_groups.data.groups)

    #List dropbox logs events
    print("\nListing dropbox logs events:")
    dropbox_logs = await data_source.team_log_get_events_continue(cursor="AAHUbyqbAtZDtmERCX2iKTtDiDbRsAoV8ks8PqED0qyYOvylM7zDT4cd7Z-4ndpdpIQrLU-IuqblMKJpA4uquSK_vo_h1HyVis_VGfpNMOS3YVEHvSA6H_Nt6iouB5V39ZQ_cI_-uPgbw6HcVL3sqSB15qINJBAE670sdlxCvZOG7RnZtgtD3Fi0V_bcBkIHiLAxBo-odgUUDhItgeBiDlozlXNpd_0hqsX8f7ea9sVeARxyCKu5bogATZn7Ws0gVKO-O0LTYG3dvO0Fm3p0srJwsAVGk4PogCL6nTPXyh9NVzwfzxKFuN_NFCnM1TBJx5PTzuXgScQQTOhubDXUOcOYdQOcO-gpiO82QpEpOIf9F4VeCPAyyLiEpjy1NN42cjg6m4s0uKbtFU40SVd0yQsL4GHLB4QskUw-onkm5YTq3A")
    print(dropbox_logs)
    
    # def get_parent_path_from_path(path: str):
    #     """Extracts the parent path from a file/folder path."""
    #     if not path or path == "/" or "/" not in path.lstrip("/"):
    #         return None  # Root directory has no parent path in this context
    #     parent_path = "/".join(path.strip("/").split("/")[:-1])
    #     return f"/{parent_path}" if parent_path else "/"

    # parent_path = get_parent_path_from_path("/hi/test1")
    
    # parent_metadata = await data_source.files_get_metadata(parent_path, team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(parent_metadata)

    #file metadata
    # file_metadata = await data_source.files_get_metadata(path="/hi/test1", team_folder_id="13131350499", team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(file_metadata)

    # #list all the folders I have access to 
    # shared_folders = await data_source.sharing_list_folders(team_member_id=team_members.data.members[2].profile.team_member_id)
    # # print(to_pretty_json(shared_folders))
    # for folder in shared_folders.data.entries:
    #     print("name: ", folder.name)
    #     print("id: ", folder.shared_folder_id)
    #     print()



    # for folder_group in folder_groups.data.team_folders:
    #     rg = RecordGroup(
    #         name=folder_group.name,
    #         external_group_id=folder_group.team_folder_id,
    #         connector_name=Connectors.DROPBOX,
    #         group_type=RecordGroupType.DRIVE,
    #     )

    #     print("name: ", folder_group.name)
    #     print("id: ", type(folder_group.team_folder_id))
    #     print("status:",folder_group.status._tag)
    #     print()
    
    #list of file memebers
    # print("\nListing file members:")
    # file_members = await data_source.sharing_list_file_members(file="id:6s5PkC7SrNcAAAAAAAAAKQ", team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(file_members)


    # for member in team_members.data["members"]:
    #     print(member)
    #     print()
    #     print()

    # if not team_members or not team_members.data.get("members"):
    #     print("Could not find any team members.")
    #     return

    # members = team_members.data["members"]
    # print(f"Found {len(members)} team members.")
    
    #list my personal fodler
    # print("\nListing my personal folder:")
    # my_personal_folder = await data_source.files_list_folder(path="", 
    # team_member_id=members[2].source_user_id, 
    # recursive=True)
    # print(to_pretty_json(my_personal_folder))

    

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
