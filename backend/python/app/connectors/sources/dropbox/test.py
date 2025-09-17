# ruff: noqa
import asyncio
import os
import json
import time
import uuid
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.services.kafka_consumer import KafkaConsumerManager
from app.sources.client.dropbox.dropbox_ import DropboxAppKeySecretConfig, DropboxClient, DropboxTokenConfig
from app.sources.external.dropbox.dropbox_ import DropboxDataSource
from app.utils.logger import create_logger

from dropbox.files import FileMetadata, FolderMetadata
from datetime import datetime


def is_valid_email(email: str) -> bool:
    return email is not None and email != "" and "@" in email


async def test_run() -> None:
    access_token_team = os.getenv("DROPBOX_TOKEN_TEAM")
    access_token = os.getenv("DROPBOX_TOKEN")
    org_id = "dropbox_org_1"
    
    logger = create_logger("dropbox_connector")

    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, "../../../config/default_config.json")

    key_value_store = InMemoryKeyValueStore(logger, config_path)
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    
    if not access_token_team:
        logger.error("DROPBOX_TOKEN_TEAM environment variable not set.")
        return

    # Create a test organization in ArangoDB
    org = {
        "_key": org_id,
        "accountType": "enterprise",
        "name": "Dropbox Test Org",
        "isActive": True,
        "createdAtTimestamp": 1718745600,
        "updatedAtTimestamp": 1718745600,
    }
    await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)

    # Initialize Dropbox Client
    config = DropboxTokenConfig(token=access_token_team)
    client = await DropboxClient.build_with_config(config, is_team=True)
    data_source = DropboxDataSource(client)

    # Fetch and sync team members to ArangoDB
    print("\nFetching team members from Dropbox...")
    team_members = await data_source.team_members_list()
    team_members_data = team_members.data
    
    users_to_upsert = []
    edges_to_create = []

    for member in team_members_data.members:
        member_email = member.profile.email
        if is_valid_email(member_email):
            user_key = member_email
            user = {
                "_key": user_key,
                "email": member_email,
                "userId": member.profile.team_member_id,
                "orgId": org_id,
                "isActive": member.profile.status._tag == "active",
                "createdAtTimestamp": 1718745600,
                "updatedAtTimestamp": 1718745600,
            }
            users_to_upsert.append(user)

            edge = {
                "_from": f"{CollectionNames.USERS.value}/{user_key}",
                "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                "entityType": "ORGANIZATION",
                "createdAtTimestamp": 1718745600,
                "updatedAtTimestamp": 1718745600,
            }
            edges_to_create.append(edge)

    print(f"Upserting {len(users_to_upsert)} users to ArangoDB...")
    await arango_service.batch_upsert_nodes(users_to_upsert, CollectionNames.USERS.value)
    
    print(f"Creating {len(edges_to_create)} 'BELONGS_TO' edges in ArangoDB...")
    await arango_service.batch_create_edges(edges_to_create, CollectionNames.BELONGS_TO.value)

    print("Dropbox user data successfully synced to ArangoDB.")

    # --- User account client ---
    config = DropboxTokenConfig(token=access_token)
    client = await DropboxClient.build_with_config(config, is_team=False)
    data_source = DropboxDataSource(client)
    print("\nFetching files and folders from Dropbox...")

    account = await data_source.users_get_current_account()
    current_user = account.data.name.display_name
    print("Current user:", current_user)

    #fetch files and folders from personal folder
    personal_files_result = await data_source.files_list_folder(path="", recursive=True)
    personal_files_and_folders = personal_files_result.data.entries

    records_to_upsert = [] # for_files
    file_records_to_upsert = []; #for folders (and files??)

    print("Entries:", personal_files_and_folders)
    print("\n\n")
    i = 1
    for entry in personal_files_and_folders:
        print("Entry", i,":", entry)
        i+=1
        record_type = None
        mime_type = None
        source_last_modified_timestamp = None
        external_revision_id = None
        external_parent_id = None


        if entry.path_display != '{}/':
            # The parent ID is the ID of the folder containing the current entry.
            # Dropbox doesn't give a direct parent ID, so we derive it from the path.
            # This is a simplification; a full solution would require more complex path traversal.
            parent_path_display = os.path.dirname(entry.path_display)
            if parent_path_display == '':
                external_parent_id = None # Root folder has no parent
            else:
                # This is a placeholder; you would need to map the parent path to its ID
                # This is a limitation of the list_folder API call, which doesn't provide parent IDs.
                external_parent_id = "/{}".format(current_user) + parent_path_display # Using the path as a temporary identifier
        
        if isinstance(entry, FileMetadata):
            record_type = "FILE"
            mime_type = "application/octet-stream" 
            source_last_modified_timestamp = entry.server_modified.timestamp()
            external_revision_id = entry.rev
            
        elif isinstance(entry, FolderMetadata):
            record_type = "FOLDER"
            isFile = False 
            
            
        else:
            logger.warning(f"Skipping unsupported entry type: {type(entry)}")
            continue

        if(record_type=="FILE"):
            record = {
                "_key": entry.id,
                "orgId": org_id,
                "recordName": entry.name,
                "externalRecordId": entry.id,
                "externalParentId": external_parent_id,
                "externalRevisionId": external_revision_id,
                "recordType": record_type,
                "origin": "CONNECTOR",
                "connectorName": "DROPBOX",
                "mimeType": mime_type,
                # "webUrl": None,
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
                "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
                "lastSyncTimestamp": get_epoch_timestamp_in_ms(),
                # "sourceCreatedAtTimestamp": None, 
                "sourceLastModifiedTimestamp": source_last_modified_timestamp,
                "isLatestVersion": True,
                "isDirty": False,
            }
            records_to_upsert.append(record)

            # file_record = {
            #     "_key": entry.id,
            #     "orgId": org_id,
            #     "name": entry.name,
            #     "isFile": True,
            #     "path": "/{}".format(current_user) + entry.path_display,
            #     "sizeInBytes": entry.size,
            #     # Other fields like extension, mimeType, etag, hashes are null rn
            # }
            # file_records_to_upsert.append(file_record)
        
        if(record_type=='FOLDER'):
            file_record = {
                #implement here
                "_key": entry.id,
                "orgId": org_id,
                "name": entry.name,
                "isFile": False,
                "path": "/{}".format(current_user) + entry.path_display,
                # Other fields like extension, mimeType, etag, hashes, sizeInBytes are null rn
            }
            file_records_to_upsert.append(file_record)

    if records_to_upsert:
        print(f"Upserting {len(records_to_upsert)} records to ArangoDB...")
        await arango_service.batch_upsert_nodes(records_to_upsert, CollectionNames.RECORDS.value)
        print("Dropbox records successfully synced to ArangoDB.")
    if file_records_to_upsert:
        print(f"Upserting {len(file_records_to_upsert)} folder records to ArangoDB...")
        await arango_service.batch_upsert_nodes(file_records_to_upsert, CollectionNames.FILES.value)
        print("Dropbox file records successfully synced to ArangoDB.")
    else:
        print("No files or folders found to sync.")

    
    # now fetch from shared folders
    print("\nListing shared folders:")
    shared_folders = await data_source.sharing_list_folders()

    """
    workflow:
    1. fetch shared folders
    2. for each shared folder, create a dict or pair, with key as folder_id and value as folder_name or vice versa
    3. for each shared folder, fetch files and folders similar to above
    4. upsert to arangodb
    """

    """
    questions:
    1. how to defien mimeTypes
    2. how drive and all impelement m5check ?
    3. how 
    """


if __name__ == "__main__":
    asyncio.run(test_run())