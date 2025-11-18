from dataclasses import dataclass
from logging import Logger
from typing import Any, Callable, Dict, List, Optional

from aiolimiter import AsyncLimiter
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.method import Method
from kiota_abstractions.request_information import RequestInformation
from kiota_abstractions.serialization import Parsable, ParseNode, SerializationWriter
from kiota_abstractions.serialization.parsable_factory import ParsableFactory
from msgraph import GraphServiceClient
from msgraph.generated.models.base_delta_function_response import (
    BaseDeltaFunctionResponse,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.group import Group
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from app.models.entities import AppUser, FileRecord
from app.models.permission import Permission, PermissionType


# Map Microsoft Graph roles to permission type
def map_msgraph_role_to_permission_type(role: str) -> PermissionType:
    """Map Microsoft Graph permission roles to application permission types"""
    role_lower = role.lower()
    if role_lower in ["owner", "fullcontrol"]:
        return PermissionType.OWNER
    elif role_lower in ["write", "editor", "contributor", "writeaccess"]:
        return PermissionType.WRITE
    elif role_lower in ["read", "reader", "readaccess"]:
        return PermissionType.READ
    else:
        # Default to read for unknown roles
        return PermissionType.READ



@dataclass
class PermissionChange:
    """Track permission changes for a record"""
    record_id: str
    external_record_id: str
    added_permissions: List[Permission]
    removed_permissions: List[Permission]
    modified_permissions: List[Permission]

@dataclass
class RecordUpdate:
    """Track updates to a record"""
    record: Optional[FileRecord]
    is_new: bool
    is_updated: bool
    is_deleted: bool
    metadata_changed: bool
    content_changed: bool
    permissions_changed: bool
    old_permissions: Optional[List[Permission]] = None
    new_permissions: Optional[List[Permission]] = None
    external_record_id: Optional[str] = None

@dataclass
class DeltaGetResponse(BaseDeltaFunctionResponse, Parsable):
    # The value property
    value: Optional[List[DriveItem]] = None

    @staticmethod
    def create_from_discriminator_value(parse_node: ParseNode) -> "DeltaGetResponse":
        """
        Creates a new instance of the appropriate class based on discriminator value
        param parse_node: The parse node to use to read the discriminator value and create the object
        Returns: DeltaGetResponse
        """
        if parse_node is None:
            raise TypeError("parse_node cannot be null.")
        return DeltaGetResponse()

    def get_field_deserializers(self) -> Dict[str, Callable[[ParseNode], None]]:
        """
        The deserialization information for the current model
        Returns: Dict[str, Callable[[ParseNode], None]]
        """
        fields: Dict[str, Callable[[Any], None]] = {
            "value": lambda n: setattr(self, 'value', n.get_collection_of_object_values(DriveItem)),
        }
        super_fields = super().get_field_deserializers()
        fields.update(super_fields)
        return fields

    def serialize(self, writer: SerializationWriter) -> None:
        """
        Serializes information the current object
        param writer: Serialization writer to use to serialize this model
        Returns: None
        """
        if writer is None:
            raise TypeError("writer cannot be null.")
        super().serialize(writer)
        writer.write_collection_of_object_values("value", self.value)

@dataclass
class GroupDeltaGetResponse(BaseDeltaFunctionResponse, Parsable):
    # The value property specialized for Groups
    value: Optional[List[Group]] = None

    @staticmethod
    def create_from_discriminator_value(parse_node: ParseNode) -> "GroupDeltaGetResponse":
        if parse_node is None:
            raise TypeError("parse_node cannot be null.")
        return GroupDeltaGetResponse()

    def get_field_deserializers(self) -> Dict[str, Callable[[ParseNode], None]]:
        fields: Dict[str, Callable[[Any], None]] = {
            # Use Group here instead of DriveItem
            "value": lambda n: setattr(self, 'value', n.get_collection_of_object_values(Group)),
        }
        super_fields = super().get_field_deserializers()
        fields.update(super_fields)
        return fields

    def serialize(self, writer: SerializationWriter) -> None:
        if writer is None:
            raise TypeError("writer cannot be null.")
        super().serialize(writer)
        writer.write_collection_of_object_values("value", self.value)

class MSGraphClient:
    def __init__(self, app_name: str, client: GraphServiceClient, logger: Logger, max_requests_per_second: int = 10) -> None:
        """
        Initializes the OneDriveSync instance with a rate limiter.

        Args:
            client (GraphServiceClient): The Microsoft Graph API client.
            logger: Logger instance for logging.
            max_requests_per_second (int): Maximum allowed API requests per second.
        """
        self.client = client
        self.app_name = app_name
        self.logger = logger
        self.rate_limiter = AsyncLimiter(max_requests_per_second, 1)

    async def get_all_user_groups(self) -> List[dict]:
        """
        Retrieves a list of all groups in the organization.

        Returns:
            List[dict]: A list of groups with their details.
        """
        try:
            groups = []
            async with self.rate_limiter:
                result = await self.client.groups.get()

            groups.extend(result.value)
            while result.odata_next_link:
                async with self.rate_limiter:
                    result = await self.client.groups.get_next_page(result.odata_next_link)
                groups.extend(result.value)


            self.logger.info(f"Retrieved {len(groups)} groups.")
            return groups
        except ODataError as e:
            self.logger.error(f"Error fetching groups: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching groups: {ex}")
            raise ex

    async def get_group_members(self, group_id: str) -> List[dict]:
        """
        Get all members of a specific group.

        Args:
            group_id: The ID of the group

        Returns:
            List of user IDs who are members of the group
        """
        try:
            members = []
            async with self.rate_limiter:
                result = await self.client.groups.by_group_id(group_id).members.get()

            members.extend(result.value)
            while result.odata_next_link:
                async with self.rate_limiter:
                    result = await self.client.groups.by_group_id(group_id).members.get_next_page(result.odata_next_link)
                members.extend(result.value)

            return members

        except Exception as e:
            self.logger.error(f"Error fetching group members for {group_id}: {e}")
            return []

    async def get_all_users(self) -> List[AppUser]:
        """
        Retrieves a list of all users in the organization.

        Returns:
            List[User]: A list of users with their details.
        """
        try:
            users = []

            async with self.rate_limiter:
                query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=['id', 'displayName', 'userPrincipalName', 'accountEnabled',
                            'mail', 'jobTitle', 'department', 'surname']
                )

                # Create request configuration
                request_configuration = RequestConfiguration(
                    query_parameters=query_params
                )

                result = await self.client.users.get(request_configuration)
                users.extend(result.value)

                while result.odata_next_link:
                    async with self.rate_limiter:
                        result = await self.client.users.get_next_page(result.odata_next_link)
                    users.extend(result.value)
                self.logger.info(f"Retrieved {len(users)} users.")

            user_list: List[AppUser] = []
            for user in users:
                user_list.append(AppUser(
                    app_name=self.app_name,
                    source_user_id=user.id,
                    first_name=user.display_name,
                    last_name=user.surname,
                    full_name=user.display_name,
                    email=user.mail or user.user_principal_name,
                    is_active=user.account_enabled,
                    title=user.job_title,
                    source_created_at=user.created_date_time.timestamp() if user.created_date_time else None,
                ))

            return user_list

        except ODataError as e:
            self.logger.error(f"Error fetching users: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching users: {ex}")
            raise ex

    async def get_user_email(self, user_id: str) -> Optional[str]:
        """
        Fetches the email of a specific user by ID.
        Tries 'mail' first, falls back to 'userPrincipalName'.
        """
        try:
            async with self.rate_limiter:
                # Only select the fields we strictly need to keep it fast
                query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=['id', 'mail', 'userPrincipalName']
                )
                request_configuration = RequestConfiguration(
                    query_parameters=query_params
                )

                user = await self.client.users.by_user_id(user_id).get(request_configuration)

                if user:
                    return user.mail or user.user_principal_name
            return None
        except Exception as ex:
            # Log debug instead of error because sometimes users might be deleted before we fetch them
            self.logger.warning(f"Could not fetch details for user {user_id}: {ex}")
            return None

    async def get_delta_response_sharepoint(self, url: str) -> dict:
        response = {'delta_link': None, 'next_link': None, 'drive_items': []}

        try:
            async with self.rate_limiter:
                ri = RequestInformation()
                ri.http_method = Method.GET
                ri.url = url  # absolute URL
                ri.headers.add("Accept", "application/json")

                error_mapping: Dict[str, type[ParsableFactory]] = {
                    "4XX": ODataError,
                    "5XX": ODataError,
                }

                result = await self.client.request_adapter.send_async(
                    request_info=ri,
                    parsable_factory=DeltaGetResponse,  # or DriveItemCollectionResponse
                    error_map=error_mapping
                )

            if hasattr(result, 'value') and result.value:
                response['drive_items'] = result.value
            if hasattr(result, 'odata_next_link') and result.odata_next_link:
                response['next_link'] = result.odata_next_link
            if hasattr(result, 'odata_delta_link') and result.odata_delta_link:
                response['delta_link'] = result.odata_delta_link

            self.logger.info(f"Retrieved delta response with {len(response['drive_items'])} items")
            return response

        except Exception as ex:
            self.logger.error(f"Error fetching delta response for URL {url}: {ex}")
            raise


    async def get_delta_response(self, url: str) -> dict:
        """
        Retrieves the drive items, delta token and next link for a given Microsoft Graph API URL.

        Args:
            url (str): The full Microsoft Graph API URL to query.

        Returns:
            dict: Dictionary containing 'deltaLink', 'nextLink', and 'driveItems'.
        """
        try:
            response = {
                'delta_link': None,
                'next_link': None,
                'drive_items': []
            }

            async with self.rate_limiter:
                request_info = RequestInformation(Method.GET, url)
                error_mapping: Dict[str, type[ParsableFactory]] = {
                    "4XX": ODataError,
                    "5XX": ODataError,
                }
                # Send request using request_adapter with all required arguments
                result = await self.client.request_adapter.send_async(
                    request_info=request_info,
                    parsable_factory=DeltaGetResponse,
                    error_map=error_mapping
                )

                # Extract the drive items
                if hasattr(result, 'value') and result.value:
                    response['drive_items'] = result.value

                # Extract the next link if available
                if hasattr(result, 'odata_next_link') and result.odata_next_link:
                    response['next_link'] = result.odata_next_link

                # Extract the delta link if available
                if hasattr(result, 'odata_delta_link') and result.odata_delta_link:
                    response['delta_link'] = result.odata_delta_link

                self.logger.info(f"Retrieved delta response with {len(response['drive_items'])} items")
                return response

        except Exception as ex:
            self.logger.error(f"Error fetching delta response for URL {url}: {ex}")
            raise ex

    async def get_groups_delta_response(self, url: str) -> dict:
        """
        Retrieves groups using delta query to track changes.
        Note: This doesn't include members - they need to be fetched separately.

        Args:
            url (str): The full Microsoft Graph API URL to query.

        Returns:
            dict: Dictionary containing 'delta_link', 'next_link', and 'groups'.
        """
        try:
            response = {
                'delta_link': None,
                'next_link': None,
                'groups': []
            }

            async with self.rate_limiter:
                request_info = RequestInformation(Method.GET, url)
                error_mapping: Dict[str, type[ParsableFactory]] = {
                    "4XX": ODataError,
                    "5XX": ODataError,
                }

                # Send request using request_adapter
                result = await self.client.request_adapter.send_async(
                    request_info=request_info,
                    parsable_factory=GroupDeltaGetResponse,
                    error_map=error_mapping
                )

                # Extract the groups
                if hasattr(result, 'value') and result.value:
                    response['groups'] = result.value

                # Extract the next link if available
                if hasattr(result, 'odata_next_link') and result.odata_next_link:
                    response['next_link'] = result.odata_next_link

                # Extract the delta link if available
                if hasattr(result, 'odata_delta_link') and result.odata_delta_link:
                    response['delta_link'] = result.odata_delta_link

            self.logger.info(f"Retrieved groups delta response with {len(response['groups'])} groups")
            return response

        except Exception as ex:
            self.logger.error(f"Error fetching groups delta response for URL {url}: {ex}")
            raise ex


    async def get_file_permission(self, drive_id: str, item_id: str) -> List['Permission']:
        """
        Retrieves permissions for a specified file by Drive ID and File ID.

        Args:
            drive_id (str): The ID of the drive containing the file
            item_id (str): The ID of the file

        Returns:
            List[Permission]: A list of Permission objects associated with the file
        """
        try:
            permissions = []
            async with self.rate_limiter:
                result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).permissions.get()

            if result and result.value:
                permissions.extend(result.value)

            while result and hasattr(result, 'odata_next_link') and result.odata_next_link:
                async with self.rate_limiter:
                    result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).permissions.get_next_page(result.odata_next_link)
                if result and result.value:
                    permissions.extend(result.value)

            self.logger.info(f"Retrieved {len(permissions)} permissions for file ID {item_id}.")
            return permissions
        except ODataError as e:
            self.logger.error(f"Error fetching file permissions for File ID {item_id}: {e}")
            return []
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching file permissions for File ID {item_id}: {ex}")
            return []

    async def list_folder_children(self, drive_id: str, folder_id: str) -> List[DriveItem]:
        """
        List all children of a folder.

        Args:
            drive_id: The drive ID
            folder_id: The folder ID

        Returns:
            List of DriveItem objects
        """
        try:
            children = []
            async with self.rate_limiter:
                result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(folder_id).children.get()

            if result and result.value:
                children.extend(result.value)

            # Handle pagination
            while result and hasattr(result, 'odata_next_link') and result.odata_next_link:
                async with self.rate_limiter:
                    result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(folder_id).children.with_url(result.odata_next_link).get()
                if result and result.value:
                    children.extend(result.value)

            self.logger.info(f"Retrieved {len(children)} children for folder {folder_id}")
            return children

        except ODataError as e:
            self.logger.error(f"Error listing folder children for {folder_id}: {e}")
            return []
        except Exception as ex:
            self.logger.error(f"Unexpected error listing folder children for {folder_id}: {ex}")
            return []

    async def get_signed_url(self, drive_id: str, item_id: str) -> Optional[str]:
        """
        Creates a signed URL (sharing link) for a file or folder, valid for the specified duration.

        Args:
            drive_id (str): The ID of the drive.
            item_id (str): The ID of the file or folder.

        Returns:
            str: The signed URL or None if not available.
        """
        try:
            async with self.rate_limiter:
                item = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).get()
                if item and hasattr(item, 'additional_data'):
                    signed_url = item.additional_data.get("@microsoft.graph.downloadUrl")
                    return signed_url
                return None

        except Exception as ex:
            self.logger.error(f"Error creating signed URL for item {item_id} in drive {drive_id}: {ex}")
            return None
