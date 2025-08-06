from abc import abstractmethod
from dataclasses import dataclass
from typing import List, Optional, Tuple

from app.connectors.sources.microsoft.onedrive.arango_service import ArangoService
from app.models.permission import Permission
from app.models.records import Record, RecordGroup
from app.models.users import User, UserGroup


@dataclass
class RecordWithPermissions:
    record: Record
    users: List[Tuple[User, Permission]]
    user_groups: List[Tuple[UserGroup, Permission]]
    anyone_with_link: bool = False
    anyone_same_org: bool = False
    anyone_same_domain: bool = False
    record_group_id: Optional[str] = None
    parent_record_id: Optional[str] = None


@dataclass
class RecordGroupWithPermissions:
    record_group: RecordGroup
    users: List[Tuple[User, Permission]]
    user_groups: List[Tuple[UserGroup, Permission]]
    anyone_with_link: bool = False
    anyone_same_org: bool = False
    anyone_same_domain: bool = False

@dataclass
class UserGroupWithMembers:
    user_group: UserGroup
    users: List[Tuple[User, Permission]]

class App:
    @abstractmethod
    def get_app_name(self) -> str:
        pass

class DataSourceEntitiesProcessor:
    def __init__(self, logger, app: App, arango_service: ArangoService):
        self.logger = logger
        self.app = app
        self.arango_service = arango_service

    async def on_new_records(self, records: List[RecordWithPermissions]):
        # Create a transaction

        for record in records:

            # Create record if it doesn't exist
            # Create a permission edge between the record and the user if it doesn't exist
            # Create a permission edge between the record and the user group if it doesn't exist
            # Create a permission edge between the record and the org if it doesn't exist
            # Create a permission edge between the record and the app with sync status if it doesn't exist
            # Create a edge between the record and the record group if it doesn't exist and if record_group_id is provided
            # Create a edge between the record and the parent record if it doesn't exist and if parent_record_id is provided
            # Create a edge between the base record and the specific record if it doesn't exist - isOfType


            print(record)
        # Commit the transaction

    async def on_updated_record_permissions(self, record: RecordWithPermissions):
        pass

    async def on_record_content_update(self, record: Record):
        pass

    async def on_record_metadata_update(self, record: Record):
        pass

    async def on_record_deleted(self, record_id: str):
        # Remove all edges from the record
        # Remove the record
        pass


    async def on_new_record_groups(self, record_groups: List[RecordGroupWithPermissions]):
        # Create a transaction
        for record_group in record_groups:

            # Create record group if it doesn't exist
            # Create a permission edge between the record group and the org if it doesn't exist
            # Create a permission edge between the record group and the user if it doesn't exist
            # Create a permission edge between the record group and the user group if it doesn't exist
            # Create a permission edge between the record group and the org if it doesn't exist
            # Create a edge between the record group and the app with sync status if it doesn't exist


            print(record_group)

        # Commit the transaction


    async def on_new_users(self, users: List[User]):
        # Create a transaction

        for user in users:

            # Create user if it doesn't exist
            # Create app if it doesn't exist
            # Create a edge between the user and the app with sync status if it doesn't exist
            # Create a edge between the user and the org if it doesn't exist
            # Create a edge between the user and app if it doesn't exist

            print(user)
        # Commit the transaction


    async def on_new_user_groups(self, user_groups: List[UserGroupWithMembers]):
        # Create a transaction

        for user_group in user_groups:

            # Create user group if it doesn't exist
            # Create a edge between the user and user group

            print(user_group)
        # Commit the transaction
