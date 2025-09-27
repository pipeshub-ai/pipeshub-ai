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
    print("\nListing dropbox user groups:")
    dropbox_groups = await data_source.team_groups_list()
    print(dropbox_groups.data.groups)

    #List dropbox logs events
    print("\nListing dropbox logs events:")
    dropbox_logs = await data_source.team_log_get_events_continue(cursor="AAEDfcOmtBtrP9IRur0EwN9tSMFw9sSgVc5BV8E9JL0wlrAtLvm6SNSCbDhufcJhvp7oEhalzKzT47SWwwX532QBRIZKuUUK5lCt-XZGib_95YaqcF8IpyKsX-XflyNwdriDs0PfJkNQeNFPjpvhfqruUHr2bNUybBFtCHlVf1g22Xz8GW-8VSoNs8MHtfeqw-j9VIr91xcdj80y7iACQmaV8hnXc4_MleX5ukKaCnKp45TXnN-AVC0lIV6RAWvkIL02GKhPur9T-moAeV06ePO3GiIqiuRYBoPDzQroQ7hPJOADtWN7_8OXAe-uTBposyhb2J-eSM5fvfxsKqI6FshBiiomOKhvEdxSx0kQe3Yd589Igk_b-nSm7b-Joluw3oR7w9VIK8UgMkjfxYBOdMFG0JXp9EOXWyqq4p8GJHVDwA")
    print(dropbox_logs)
    
    # #list members of dropbox group
    # print("\nListing dropbox groups:")
    # # --- 1. Get ALL groups, handling pagination ---
    # all_groups = []
    # # (Assuming data_source.team_groups_list and data_source.team_groups_list_continue exist)
    # try:
    #     groups_response = await data_source.team_groups_list()
    #     if not groups_response.success:
    #         raise Exception(f"Error fetching groups: {groups_response.error}")

    #     all_groups.extend(groups_response.data.groups)
    #     cursor = groups_response.data.cursor
    #     has_more = groups_response.data.has_more

    #     while has_more:
    #         print("Fetching more groups...")
    #         groups_response = await data_source.team_groups_list_continue(cursor)
    #         if not groups_response.success:
    #             raise Exception(f"Error fetching more groups: {groups_response.error}")
                
    #         all_groups.extend(groups_response.data.groups)
    #         cursor = groups_response.data.cursor
    #         has_more = groups_response.data.has_more
        
    #     print(f"Total groups found: {len(all_groups)}")

    #     # --- 2. Get ALL members for each group, handling pagination ---
    #     print("\nListing dropbox group members:")
    #     for group in all_groups:
    #         print(f"\n--- Group: {group.group_name} ---")
    #         all_members = []
            
    #         # Get first page of members
    #         members_response = await data_source.team_groups_members_list(group=group.group_id)
    #         if not members_response.success:
    #             print(f"  Error fetching members: {members_response.error}")
    #             continue  # Skip to the next group
            
    #         print(members_response.data)
    #         all_members.extend(members_response.data.members)
    #         member_cursor = members_response.data.cursor
    #         member_has_more = members_response.data.has_more
            
    #         # Get all other pages of members
    #         while member_has_more:
    #             print(f"  Fetching more members for {group.group_name}...")
    #             members_response = await data_source.team_groups_members_list_continue(member_cursor)
                
    #             if not members_response.success:
    #                 print(f"  Error fetching more members: {members_response.error}")
    #                 break  # Stop processing this group
                    
    #             all_members.extend(members_response.data.members)
    #             member_cursor = members_response.data.cursor
    #             member_has_more = members_response.data.has_more

    #         # Now print the full, paginated list of members for this group
    #         if not all_members:
    #             print("  No members found.")
    #         else:
    #             for member in all_members:
    #                 # Print the actual member details
    #                 print(f"  - {member.profile.name} ({member.profile.email})")

    # except Exception as e:
    #     print(f"An error occurred: {e}")

    # users = []
    # for member in team_members.data.members:
    #     profile = member.profile
    #     print(member)
    #     print("\n")
    #     users.append(
    #         AppUser(
    #             # source_user_id=member.team_member_id,
    #             app_name="DROPBOX",
    #             source_user_id=profile.team_member_id,
    #             first_name=profile.name.given_name,
    #             last_name=profile.name.surname,
    #             full_name=profile.name.display_name,
    #             email=profile.email,
    #             is_active=(profile.status._tag == "active"),
    #             title=member.role._tag,
                
    #         )
    #     )
    # # print(users)
    
    # def get_parent_path_from_path(path: str):
    #     """Extracts the parent path from a file/folder path."""
    #     if not path or path == "/" or "/" not in path.lstrip("/"):
    #         return None  # Root directory has no parent path in this context
    #     parent_path = "/".join(path.strip("/").split("/")[:-1])
    #     return f"/{parent_path}" if parent_path else "/"

    # parent_path = get_parent_path_from_path("/hi/test1")
    
    # parent_metadata = await data_source.files_get_metadata(parent_path, team_member_id=team_members.data.members[2].profile.team_member_id)
    # print(parent_metadata)

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
