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
from dropbox.sharing import SharedLinkSettings, LinkAudience
from dropbox.team import UserSelectorArg



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
    print("\nListing team folder:")
    team_files = await data_source.files_list_folder(path="",team_member_id=team_members.data.members[2].profile.team_member_id, team_folder_id="13131350499", recursive=True)
    print(to_pretty_json(team_files))

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
    dropbox_logs = await data_source.team_log_get_events_continue(cursor="AAEn8UrLyLYX4s-XZwIvyVNsLfcbT57cOyAcmgNTzFIwVlJrJpNqHNdAoJskXXh03GpbbH-GonYgnX9KQ42Vf0kd_3Hx0uaAv16Ue4sutj8A5LuGhShyFVf28-PzT4zXAUw5wYhyxDiqa6bgbJHX7yMF9tFp7OIUY1w6cwAQBa0-Kc4NZDUFOfGRJzjOlZ5DHlzze1rSyQ17SNBK4g-i6kHJwFqSvlrdLEVu6831qeNeU-WovSU9XZsnkhwExiIjy9yznFAWDzLsC21p6Px426HjNHUosfPW2Y_bCy1LY9KzpFVYnH6n6gXSFIkK88FgbKFx_Fmgbe6qYzK8CqbOUK9Js0g68pn9_xIAHlTCN_SqvdCOxfCfWtlL8MfV_rsjKWXfOqbKqRfUnEFtz8BWtLRa7s1gXLa1cBZnkkXqiWdaEg")
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
    # print("\nListing folder metadata:")
    # file_metadata = await data_source.files_get_metadata(path="ns:12814702001", team_folder_id="12814702001", team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(file_metadata)

    # file_metadata = await data_source.files_get_metadata(path="id:ao7lcW3O2rQAAAAAAAAAXg", team_folder_id="12814702001", team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(file_metadata)

    #sahred folder metadata
    # print("\nListing shared folder metadata:")
    # shared_folder_metadata = await data_source.sharing_get_folder_metadata(shared_folder_id="12814702001", team_member_id=team_members.data.members[2].profile.team_member_id)
    # print((shared_folder_metadata))

    #generate link
    # print("\nGenerating link:")
    # link = await data_source.sharing_create_shared_link_with_settings(path="id:7ycJU6IBbZkAAAAAAAAACw", team_folder_id="13131350499", team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(link)

    # link_settings = SharedLinkSettings(
    #     audience=LinkAudience('no_one'), 
    #     allow_download=True
    # )
    # print("\nGenerating link2:")
    # link = await data_source.sharing_create_shared_link_with_settings(path="id:7ycJU6IBbZkAAAAAAAAABw", team_folder_id="13131350499", settings=link_settings, team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(link)

    # #get team member info
    # memebers = [UserSelectorArg("email","harshit@pipeshub.app")]
    # print("\nGetting team member info:")
    # team_member_info = await data_source.team_members_get_info_v2(memebers)
    # print(team_member_info)
    # print(team_member_info.data.members_info[0].get_member_info().profile.team_member_id)


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
    # cursor = "AAQ_D2q1fkCpj__eB3aTmdEHXLDwCx_qTTPSIPflwZdGNwjQ_DgpRMPj0HqVucDxI-SLRGnCud7K0h4NoZ_h12rBE-9nCo-z-IGcbTz5pHDpC_cx3MB6ver_gKExeLUm8S1NnqiRHbrKbIQPWNWxuEmJ2AA3fKusvqvsIotok5pYOgKcTUjXiYG0jW288rABP3VVNPln_UypnfSFbDWzVBBm3U1fvZk1smOYJWhPqv7tJ0YhiFjpEGB30NlS1_U-f0IvzWCPPbn4PuVR6XA5RkcilAo9rSqt-gbKFUPD3ShQ5Q"
    # cursor_result = await data_source.files_list_folder_continue(cursor, 
    # team_member_id=team_members.data.members[2].profile.team_member_id,
    # team_folder_id="13160107251")
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
