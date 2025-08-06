
import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from aiolimiter import AsyncLimiter
from kiota_abstractions.base_request_configuration import RequestConfiguration
from kiota_abstractions.method import Method
from kiota_abstractions.request_information import RequestInformation
from kiota_abstractions.serialization import Parsable, ParseNode, SerializationWriter
from kiota_abstractions.serialization.parsable_factory import ParsableFactory
from msgraph import GraphServiceClient
from msgraph.generated.groups.groups_request_builder import GroupsRequestBuilder
from msgraph.generated.models.base_delta_function_response import (
    BaseDeltaFunctionResponse,
)
from msgraph.generated.models.drive_item import DriveItem
from msgraph.generated.models.o_data_errors.o_data_error import ODataError
from msgraph.generated.models.permission import Permission
from msgraph.generated.models.subscription import Subscription
from msgraph.generated.users.users_request_builder import UsersRequestBuilder

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import Connectors, OriginTypes, RecordTypes
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.sources.microsoft.onedrive.arango_service import ArangoService
from app.connectors.sources.microsoft.onedrive.data_source_entities_processor import (
    App,
    DataSourceEntitiesProcessor,
)
from app.models.records import FileRecord, Record
from app.models.users import User, UserGroup
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


class OneDriveApp(App):
    def get_app_name(self) -> str:
        return Connectors.ONEDRIVE.value


@dataclass
class DeltaGetResponse(BaseDeltaFunctionResponse, Parsable):
    # The value property
    value: Optional[List[DriveItem]] = None

    @staticmethod
    def create_from_discriminator_value(parse_node: ParseNode):
        """
        Creates a new instance of the appropriate class based on discriminator value
        param parse_node: The parse node to use to read the discriminator value and create the object
        Returns: DeltaGetResponse
        """
        if parse_node is None:
            raise TypeError("parse_node cannot be null.")
        return DeltaGetResponse()

    def get_field_deserializers(self,) -> Dict[str, Callable[[ParseNode], None]]:
        """
        The deserialization information for the current model
        Returns: Dict[str, Callable[[ParseNode], None]]
        """
        # logger.debug("----------------------------")
        # logger.debug("----------------------------")
        # logger.debug(self.value)
        # logger.debug("----------------------------")
        # logger.debug("----------------------------")

        fields: Dict[str, Callable[[Any], None]] = {
            "value": lambda n : setattr(self, 'value', n.get_collection_of_object_values(DriveItem)),
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

class OneDriveClient:
    def __init__(self, client: GraphServiceClient, logger, max_requests_per_second: int = 10):
        """
        Initializes the OneDriveSync instance with a rate limiter.

        Args:
            client (GraphServiceClient): The Microsoft Graph API client.
            logger: Logger instance for logging.
            max_requests_per_second (int): Maximum allowed API requests per second.
        """
        self.client = client
        self.logger = logger
        self.rate_limiter = AsyncLimiter(max_requests_per_second, 1)

    async def get_all_user_groups(self) -> List[UserGroup]:
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

            user_groups: List[UserGroup] = []
            self.logger.info(groups, "... groups ...")
            for group in groups:
                user_groups.append(UserGroup(
                    source_user_group_id=group.id,
                    name=group.display_name,
                    mail=group.mail,
                    description=group.description,
                    created_at_timestamp=group.created_date_time.timestamp(),
                ))

            self.logger.info(f"Retrieved {len(user_groups)} groups.")
            return user_groups
        except ODataError as e:
            self.logger.error(f"Error fetching groups: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching groups: {ex}")
            raise ex

    async def get_all_users(self) -> List[User]:
        """
        Retrieves a list of all users in the organization.

        Returns:
            List[dict]: A list of users with their details.
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
                        self.logger.info(result.value, "result.......")
                    users.extend(result.value)
                self.logger.info(f"Retrieved {len(users)} users.")

            user_list: List[User] = []
            for user in users:
                user_list.append(User(
                    source_user_id=user.id,
                    first_name=user.display_name,
                    last_name=user.surname,
                    full_name=user.display_name,
                    email=user.mail,
                    is_active=user.account_enabled,
                    title=user.job_title,
                ))

            print(user_list, "... users ...")
            return user_list

        except ODataError as e:
            self.logger.error(f"Error fetching users: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching users: {ex}")
            raise ex

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
                        "XXX": ODataError,
                }
                # Send request using request_adapter with all required arguments
                result = await self.client.request_adapter.send_async(
                    request_info=request_info,
                    parsable_factory=DeltaGetResponse,  # Factory for parsing the response
                    error_map=error_mapping  # Empty error map if no special error handling needed
                )

                # Extract the drive items
                if hasattr(result, 'value'):
                    response['drive_items'] = result.value

                # Extract the next link if available
                if hasattr(result, 'odata_next_link') and result.odata_next_link:
                    response['next_link'] = result.odata_next_link

                # Extract the delta link if available
                if hasattr(result, 'odata_delta_link') and result.odata_delta_link:
                    response['delta_link'] = result.odata_delta_link

                self.logger.info(f"Retrieved delta response for URL: {url}")
                print(response, "response.......")
                return response

        except Exception as ex:
            self.logger.error(f"Error fetching delta response for URL {url}: {ex}")
            raise ex

    async def get_user_files_with_delta(self, user_id: str) -> List[dict]:
        """
        Retrieves all files (both owned and shared) for a user using delta tracking.

        Args:
            user_id (str): The ID of the user.

        Returns:
            List[dict]: List of all accessible files with their properties.
        """
        try:
            files = []

            async with self.rate_limiter:
                url = f"/users/{user_id}/drive/root/delta"

                request_info = RequestInformation(Method.GET, "{+baseurl}" + url)
                error_mapping: Dict[str, type[ParsableFactory]] = {
                        "XXX": ODataError,
                }
                # Send request using request_adapter with all required arguments
                result = await self.client.request_adapter.send_async(
                    request_info=request_info,
                    parsable_factory=DeltaGetResponse,  # Factory for parsing the response
                    error_map=error_mapping  # Empty error map if no special error handling needed
                )
                # logger.debug("=============================")
                # logger.debug("=============================")
                # logger.debug(result)
                # files.extend(result.value)
                # logger.debug("=============================")
                # logger.debug("=============================")
                # Handle pagination
                while hasattr(result, 'odata_next_link') and result.odata_next_link:

                    next_url = result.odata_next_link
                    next_request_info = RequestInformation(Method.GET, next_url)

                    result = await self.client.request_adapter.send_async(
                        request_info=next_request_info,
                        parsable_factory=DeltaGetResponse,
                        error_map=error_mapping
                    )
                    # logger.debug("=============================")
                    # logger.debug("=============================")
                    # logger.debug(result)
                    files.extend(result.value)
                    # logger.debug("=============================")
                    # logger.debug("=============================")

            self.logger.info(f"Retrieved {len(files)} files (owned + shared) for user ID {user_id}")
            return files

        except Exception as ex:
            self.logger.error(f"Error fetching files for User ID {user_id}: {ex}")
            raise ex

    async def _make_request(self, request_func: callable):
            """Make a request with rate limit handling and retries."""
            try:
                async with self.rate_limiter:
                    return await request_func()
            except ODataError as e:
                if e.error.code == 429:
                    int(e.response.headers.get('Retry-After', 30))
                    # raise RateLimitExceeded(retry_after)
                raise e

    async def get_user_properties(self, user_id: str) -> Optional[Dict]:
        """
        Retrieves the properties of a specific user.

        Args:
            user_id (str): The ID of the user.

        Returns:
            Optional[Dict]: The user's properties, or None if not found.
        """
        try:
            async with self.rate_limiter:
                user = await self.client.users.by_user_id(user_id).get()
            self.logger.info(f"Retrieved properties for user ID {user_id}.")
            return user.serialize()
        except ODataError as e:
            self.logger.error(f"Error fetching properties for user {user_id}: {e}")
            return None
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching properties for user {user_id}: {ex}")
            return None

    async def get_members_of_group(self, group_id: str) -> List[Dict]:
        """
        Retrieves the members of a specific group.

        Args:
            group_id (str): The ID of the group.

        Returns:
            List[Dict]: A list of members in the group.
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
            self.logger.info(f"Retrieved {len(members)} members for group ID {group_id}.")
            return [member.serialize() for member in members]
        except ODataError as e:
            self.logger.error(f"Error fetching members for group {group_id}: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching members for group {group_id}: {ex}")
            raise ex

    async def get_file_permission(self, drive_id: str, item_id: str) -> List['Permission']:
        """
        Retrieves permissions for a specified file by Drive ID and File ID.

        Args:
            drive_id (str): The ID of the drive containing the file
            file_id (str): The ID of the file

        Returns:
            List[Permission]: A list of Permission objects associated with the file
        """
        try:
            permissions = []
            async with self.rate_limiter:
                # Correct syntax for accessing drive item permissions
                result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).permissions.get()
            permissions.extend(result.value)
            while result.odata_next_link:
                async with self.rate_limiter:
                    result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).permissions.get_next_page(result.odata_next_link)
                permissions.extend(result.value)
            print(permissions, "permissions...")
            self.logger.info(f"Retrieved {len(permissions)} permissions for file ID {item_id}.")
            return permissions
        except ODataError as e:
            self.logger.error(f"Error fetching file permissions for File ID {item_id}: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching file permissions for File ID {item_id}: {ex}")
            raise ex

    async def subscribe_to_webhook(self, user_id: str, notification_url: str) -> Optional[Subscription]:
        """
        Creates a subscription to a webhook for OneDrive events.

        Args:
            user_id (str): The ID of the user for whom the subscription is being created.
            notification_url (str): The endpoint URL that will receive the webhook notifications.

        Returns:
            Subscription: The subscription response object if successful.
        """
        try:
            expiration_datetime = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()

            subscription = Subscription(
                change_type="updated",  # Or specify types like "created, updated, deleted"
                notification_url=notification_url,
                resource=f"users/{user_id}/drive/root",
                expiration_date_time=expiration_datetime,
            )

            # Create the subscription
            async with self.rate_limiter:
                subscription_response = await self.client.subscriptions.post(subscription)
            self.logger.info("Successfully subscribed to webhook.")
            return subscription_response

        except ODataError as e:
            self.logger.error(f"Error creating subscription: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error during subscription creation: {ex}")
            raise ex

    async def list_drive_items(self, drive_id: str, item_id: Optional[str] = None) -> List[dict]:
        """
        Lists the children of the root or a specific folder in a drive.

        Args:
            drive_id (str): The ID of the drive.
            item_id (str, optional): The ID of the folder. If None, lists root items.

        Returns:
            List[dict]: A list of items in the drive.
        """
        try:
            items = []
            async with self.rate_limiter:
                if item_id:
                    result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id(item_id).children.get()
                else:
                    result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id('root').children.get()

                items.extend(result.value)

            return items

        except Exception as ex:
            self.logger.error(f"Error listing items in drive {drive_id}: {ex}")
            raise ex

    async def get_drive_root_children(self, drive_id: str) -> List[dict]:
        """`
        Retrieves the children of the root folder in a specified drive.
        """
        try:
            items = []
            async with self.rate_limiter:
                result = await self.client.drives.by_drive_id(drive_id).items.by_drive_item_id('root').children.get()
                items.extend(result.value)

            self.logger.info(f"Retrieved {len(items)} items from the root folder of drive {drive_id}.")

            # Convert DriveItem objects to dictionaries that match Graph's field naming
            return items
        except Exception as ex:
            self.logger.error(f"Error fetching root folder children for drive {drive_id}: {ex}")
            raise ex

    async def get_item_metadata(self, drive_id: str, item_id: str) -> Dict:
        """
        Retrieves metadata for a specific file or folder.

        Args:
            drive_id (str): The ID of the drive.
            item_id (str): The ID of the item.

        Returns:
            Dict: The metadata of the item.
        """
        try:
            async with self.rate_limiter:
                item = await self.client.drives.by_drive_id(drive_id).items[item_id].get()
            return item.serialize()
        except Exception as ex:
            self.logger.error(f"Error fetching metadata for item {item_id} in drive {drive_id}: {ex}")
            raise ex

    async def download_file_url(self, drive_id: str, item_id: str) -> bytes:
        """
        Downloads the content of a file.

        Args:
            drive_id (str): The ID of the drive.
            item_id (str): The ID of the file.

        Returns:
            bytes: The file content.
        """
        try:
            async with self.rate_limiter:
                file_content = await self.client.drives.by_drive_id(drive_id).items[item_id].content.get()
            return file_content
        except Exception as ex:
            self.logger.error(f"Error downloading file {item_id} from drive {drive_id}: {ex}")
            raise ex

    async def upload_file(self, drive_id: str, parent_item_id: str, filename: str, content: bytes) -> Dict:
        """
        Uploads a file to a specific folder in the drive.

        Args:
            drive_id (str): The ID of the drive.
            parent_item_id (str): The ID of the parent folder.
            filename (str): The name of the file.
            content (bytes): The file content.

        Returns:
            Dict: Metadata of the uploaded file.
        """
        try:
            async with self.rate_limiter:
                item = await self.client.drives.by_drive_id(drive_id).items[parent_item_id].item_with_path(filename).content.put(content)
            return item.serialize()
        except Exception as ex:
            self.logger.error(f"Error uploading file {filename} to drive {drive_id}: {ex}")
            raise ex

    async def search_files(self, drive_id: str, query: str) -> List[Dict]:
        """
        Searches for files or folders in a drive.

        Args:
            drive_id (str): The ID of the drive.
            query (str): The search query.

        Returns:
            List[Dict]: A list of items matching the query.
        """
        try:
            async with self.rate_limiter:
                result = await self.client.drives.by_drive_id(drive_id).search(q=query).get()
            return [item.serialize() for item in result.value]
        except Exception as ex:
            self.logger.error(f"Error searching files in drive {drive_id} with query '{query}': {ex}")
            raise ex

    async def delta_query(self, drive_id: str, token: Optional[str] = None) -> Dict:
        """
        Performs a delta query to track changes in the drive.

        Args:
            drive_id (str): The ID of the drive.
            token (str, optional): The delta token for continuation.

        Returns:
            Dict: The delta query result including changes and next delta token.
        """
        try:
            async with self.rate_limiter:
                if token:
                    result = await self.client.drives.by_drive_id(drive_id).root.delta.get(query_parameters={"$skipToken": token})
                else:
                    result = await self.client.drives.by_drive_id(drive_id).root.delta.get()
            return {
                "changes": [item.serialize() for item in result.value],
                "next_delta_link": result.delta_link,
                "next_skip_token": result.skip_token,
            }
        except Exception as ex:
            self.logger.error(f"Error performing delta query for drive {drive_id}: {ex}")
            raise ex

    async def create_signed_url(self, drive_id: str, item_id: str, duration_minutes: int) -> str:
        """
        Creates a signed URL (sharing link) for a file or folder, valid for the specified duration.

        Args:
            drive_id (str): The ID of the drive.
            item_id (str): The ID of the file or folder.
            duration_minutes (int): The duration in minutes for which the signed URL will be valid.

        Returns:
            str: The signed URL.
        """
        try:
            expiration_datetime = datetime.now(timezone.utc) + timedelta(minutes=duration_minutes)
            link_payload = {
                "type": "view",  # Other types: "view", "embed", etc.
                "scope": "anonymous",  # Anonymous link does not require authentication
                "expirationDateTime": expiration_datetime.isoformat() + "Z"
            }

            async with self.rate_limiter:
                response = await self.client.drives.by_drive_id(drive_id).items[item_id].create_link.post(link_payload)

            signed_url = response.link.web_url
            self.logger.info(f"Created signed URL for item {item_id} in drive {drive_id}, valid until {expiration_datetime}.")
            return signed_url

        except Exception as ex:
            self.logger.error(f"Error creating signed URL for item {item_id} in drive {drive_id}: {ex}")
            raise ex

    # Todo: Handle Pagination

    async def get_subscriptions(self) -> List[dict]:
        """
        Retrieves all active subscriptions.

        Returns:
            List[dict]: A list of active subscriptions.
        """
        try:
            async with self.rate_limiter:
                result = await self.client.subscriptions.get()
            return [subscription.serialize() for subscription in result.value]
        except Exception as ex:
            self.logger.error(f"Error fetching subscriptions: {ex}")
            raise ex

    async def renew_subscription(self, subscription_id: str, expiration_minutes: int) -> Dict:
        """
        Renews an active subscription.

        Args:
            subscription_id (str): The ID of the subscription to renew.
            expiration_minutes (int): The extended expiration duration in minutes.

        Returns:
            Dict: The updated subscription details.
        """
        try:
            expiration_datetime = (datetime.utcnow() + timedelta(minutes=expiration_minutes)).isoformat() + "Z"
            payload = {
                "expirationDateTime": expiration_datetime
            }

            async with self.rate_limiter:
                updated_subscription = await self.client.subscriptions[subscription_id].patch(payload)
            self.logger.info(f"Renewed subscription {subscription_id} until {expiration_datetime}.")
            return updated_subscription.serialize()
        except Exception as ex:
            self.logger.error(f"Error renewing subscription {subscription_id}: {ex}")
            raise ex

    async def get_user_by_upn_or_email(self, upn_or_email: str) -> Dict:
        """
        Retrieves user details using their UPN or email address.

        Args:
            upn_or_email (str): The User Principal Name (UPN) or email address of the user.

        Returns:
            Dict: The user details.
        """
        try:
            async with self.rate_limiter:
                user = await self.client.users.by_user_id(upn_or_email).get()
                user_details = {
                    "id": user.id,
                    "displayName": user.display_name,
                    "mail": user.mail,
                    "userPrincipalName": user.user_principal_name,
                    # Add other properties you need
                }
                self.logger.info(f"User {user}")
                return user_details
        except Exception as ex:
            self.logger.error(f"Error fetching user details for {upn_or_email}: {ex}")
            raise ex


class OneDriveAdminClient:
    def __init__(self, client: GraphServiceClient, logger, max_requests_per_second: int = 10):
        """
        Initializes the OneDriveSync instance with a rate limiter.

        Args:
            client (GraphServiceClient): The Microsoft Graph API client.
            logger: Logger instance for logging.
            max_requests_per_second (int): Maximum allowed API requests per second.
        """
        self.client = client
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
            query_params = GroupsRequestBuilder.GetQueryParameters(
                    select=['id', 'createdDateTime', 'deletedDateTime', 'description',
                            'displayName', 'isArchived', 'mail', 'mailEnabled', 'renewedDateTime',
                            'expirationDateTime']
                )

            # Create request configuration
            request_configuration = RequestConfiguration(
                query_parameters=query_params
            )

            async with self.rate_limiter:
                result = await self.client.groups.get(request_configuration)
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

    async def get_all_users(self) -> List[dict]:
        """
        Retrieves a list of all users in the organization.

        Returns:
            List[dict]: A list of users with their details.
        """
        try:
            users = []

            async with self.rate_limiter:
                query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters(
                    select=['id', 'displayName', 'userPrincipalName', 'accountEnabled',
                            'mail', 'jobTitle', 'department']
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

            print(users, "... users ...")
            return users

        except ODataError as e:
            self.logger.error(f"Error fetching users: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching users: {ex}")
            raise ex

    async def get_members_of_group(self, group_id: str) -> List[Dict]:
        """
        Retrieves the members of a specific group.

        Args:
            group_id (str): The ID of the group.

        Returns:
            List[Dict]: A list of members in the group.
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
            self.logger.info(f"Retrieved {len(members)} members for group ID {group_id}.")
            return members

        except ODataError as e:
            self.logger.error(f"Error fetching members for group {group_id}: {e}")
            raise e
        except Exception as ex:
            self.logger.error(f"Unexpected error fetching members for group {group_id}: {ex}")
            raise ex

class OneDriveConnector:
    def __init__(self, logger, data_entities_processor: DataSourceEntitiesProcessor):
        # self.config_service = config_service
        # self.arango_service = arango_service
        # self.kafka_service = kafka_service
        from azure.identity.aio import ClientSecretCredential

        self.logger = logger
        self.data_entities_processor = data_entities_processor
        credential = ClientSecretCredential(
            tenant_id=os.getenv("AZURE_TENANT_ID"),
            client_id=os.getenv("AZURE_CLIENT_ID"),
            client_secret=os.getenv("AZURE_CLIENT_SECRET")
        )
        self.client = GraphServiceClient(credential, scopes=["https://graph.microsoft.com/.default"])
        self.onedrive_client = OneDriveClient(self.client, self.logger)
        self.onedrive_admin_client = OneDriveAdminClient(self.client, self.logger)

    async def _process_delta_items(self, delta_items: List[dict]) -> None:
        """
        Process the delta items received from Microsoft Graph API.

        Args:
            delta_items (List[dict]): List of delta items to process.
        """
        try:
            self.logger.info(f"Processing {len(delta_items)} delta items")

            for item in delta_items:
                # Check if the item has been deleted
                if hasattr(item, 'deleted') and item.deleted is not None:
                    # Handle deleted item
                    self.logger.info(f"Item {item.id} has been deleted")
                    continue

                # Process existing or new item
                # Handle file
                self.logger.info(f"Processing item: {item.name}")
                FileRecord(
                    external_record_id=item.id,
                    external_revision_id=item.etag,
                    is_file=item.get("folder") is None,
                    extension=item.name.split(".")[-1],
                    mime_type=item.get("file") is not None and item.get("file").get("mimeType") or None,
                    size_in_bytes=item.size,
                    web_url=item.web_url,
                    created_at_timestamp=item.created_date_time.timestamp(),
                    updated_at_timestamp=item.last_modified_date_time.timestamp(),
                )
                Record(
                    external_record_id=item.id,
                    external_revision_id=item.etag,
                    record_name=item.name,
                    web_url=item.web_url,
                    mime_type=item.get("file") is not None and item.get("file").get("mimeType") or None,
                    created_at_timestamp=item.created_date_time.timestamp(),
                    updated_at_timestamp=item.last_modified_date_time.timestamp(),
                    connector_name=Connectors.ONEDRIVE.value,
                    record_type=RecordTypes.FILE.value,
                    origin=OriginTypes.CONNECTOR.value,
                    is_latest_version=True,
                    is_deleted=False,
                    is_archived=False,
                    is_dirty=False,
                )
                permission_result = await self.onedrive_client.get_file_permission(item.parent_reference.drive_id, item.id)
                print(permission_result, "permissions")
                print(item, "item")
                #self.drive_item_service.upsert(item, permissions=permission_result)

        except Exception as ex:
            self.logger.error(f"Error processing delta items: {ex}")
            raise ex

    async def _run_sync(self, user_id: str) -> None:
        """
        Synchronizes drive contents using delta API and maintains state in ArangoDB.

        Args:
            user_id: The user identifier
            drive_id: The drive identifier
        """
        try:
            # Get current sync state
            #sync_state = self.sync_state_service.get_drive_state(user_id)
            sync_state = None
            # Start URL - use deltaLink if available, otherwise start fresh
            root_url = f"/users/{user_id}/drive/root/delta"
            url = sync_state.get('deltaLink') if sync_state else ("{+baseurl}" + root_url)
            print(url, "url....")

            while True:
                # Fetch delta changes
                result = await self.onedrive_client.get_delta_response(url)
                drive_items = result.get('drive_items')
                if not result or not drive_items:
                    break

                # Process items from this page
                await self._process_delta_items(drive_items)

                # Update sync state with next_link
                next_link = result.get('next_link')
                if next_link:
                    self._update_sync_state(user_id,
                        next_link=next_link,
                        delta_link=None
                    )
                    url = next_link
                else:
                    # No more pages - store deltaLink and clear nextLink
                    delta_link = result.get('delta_link', None)
                    self._update_sync_state(user_id,
                        next_link=None,
                        delta_link=delta_link
                    )
                    break

            self.logger.info(f"Completed delta sync for user {user_id}")
            #print("Subscribing with url", self.notification_url)
            #await self.onedrive_client.subscribe_to_webhook(user_id=user_id, notification_url=self.notification_url)

        except Exception as ex:
            self.logger.error(f"Error in delta sync for user {user_id} : {ex}")
            raise

    async def _update_sync_state(
        self,
        user_id: str,
        next_link: Optional[str],
        delta_link: Optional[str]
    ) -> None:
        """
        Updates the sync state in ArangoDB.

        Args:
            user_id: The user identifier
            drive_id: The drive identifier
            next_link: URL for next page of changes
            delta_link: URL for future delta queries
        """
        state_data = {
            "nextLink": next_link,
            "deltaLink": delta_link
        }
        self.sync_state_service.upsert(user_id, state_data)

    async def run(self):
        print("Running OneDrive Connector")
        print("Getting all users")
        # Todo: Get all users from our platform
        # For each of platform user get all drives
        # For each of drive get all files

        users = await self.onedrive_client.get_all_users()
        await self.data_entities_processor.on_new_users(users)

        print("Getting all user groups")
        await self.onedrive_client.get_all_user_groups()
        print("Getting all drives")
        for user in users:
            await self._run_sync("24cd1f96-b8ee-4db7-91f5-ac3ac338519d")
        # print("Getting all subscriptions")
        # await self.onedrive_client.get_all_subscriptions()
        # print("Getting all drives")




if __name__ == "__main__":
    logger = create_logger("onedrive_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)

    arango_service = ArangoService(logger, config_service, kafka_service)

    data_entities_processor = DataSourceEntitiesProcessor(logger, OneDriveApp(), arango_service)
    print(config_service.get_config("test"))
    asyncio.run(OneDriveConnector(logger, data_entities_processor).run())
