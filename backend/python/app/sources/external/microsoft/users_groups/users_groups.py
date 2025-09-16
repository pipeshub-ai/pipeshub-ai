

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Mapping, Optional

from kiota_abstractions.base_request_configuration import (  # type: ignore
    RequestConfiguration,
)
from msgraph.generated.groups.groups_request_builder import (  # type: ignore
    GroupsRequestBuilder,
)
from msgraph.generated.invitations.invitations_request_builder import (  # type: ignore
    InvitationsRequestBuilder,
)
from msgraph.generated.organization.organization_request_builder import (  # type: ignore
    OrganizationRequestBuilder,
)

# Import MS Graph specific query parameter classes for Users Groups
from msgraph.generated.users.users_request_builder import (  # type: ignore
    UsersRequestBuilder,
)

from app.sources.client.microsoft.microsoft import MSGraphClient


# Users Groups-specific response wrapper
class UsersGroupsResponse:
    """Standardized Users Groups API response wrapper."""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def __init__(self, success: bool, data: Optional[Dict[str, Any]] = None, error: Optional[str] = None, message: Optional[str] = None) -> None:
        self.success = success
        self.data = data
        self.error = error
        self.message = message

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

# Set up logger
logger = logging.getLogger(__name__)

class UsersGroupsDataSource:
    """
    Comprehensive Microsoft Users Groups API client with complete Users, Groups, and Invitations coverage.

    Features:
    - Complete Users Groups API coverage with 250 methods organized by operation type
    - Support for Users, Groups, Invitations, and Directory Objects
    - Complete User operations: CRUD, profile management, authentication, settings
    - Complete Group operations: CRUD, membership management, settings, lifecycle
    - Complete Invitation operations: invite users, manage invitations
    - Directory operations: directory objects, roles, administrative units
    - Organization operations: organization settings, branding, configuration
    - Domain operations: domain management, verification, DNS records
    - Schema Extensions: custom properties and data extensions
    - Authentication operations: multi-factor authentication, methods management
    - License operations: license assignment, management, details
    - Photo operations: user and group photos, profile pictures
    - Microsoft Graph SDK integration with Users Groups-specific optimizations
    - Async snake_case method names for all operations
    - Standardized UsersGroupsResponse format for all responses
    - Comprehensive error handling and Users Groups-specific response processing

    EXCLUDED OPERATIONS (modify EXCLUDED_KEYWORDS list to change):
    - OneDrive operations (/users/{user-id}/drive, /groups/{group-id}/drive)
    - Teams operations (/users/{user-id}/chats, /users/{user-id}/joinedTeams)
    - SharePoint operations (/users/{user-id}/sites, /groups/{group-id}/sites)
    - OneNote operations (/users/{user-id}/onenote, /groups/{group-id}/onenote)
    - Planner operations (/users/{user-id}/planner, /groups/{group-id}/planner)
    - Outlook operations (/users/{user-id}/messages, /users/{user-id}/events)
    - Calendar operations (/users/{user-id}/calendar, /users/{user-id}/calendars)
    - Contact operations (/users/{user-id}/contacts, /users/{user-id}/contactFolders)
    - Device management operations (deviceAppManagement, deviceManagement)
    - Security operations (security, compliance, admin)
    - Analytics operations (analytics, auditLogs)
    - Storage operations (storage, connections)
    - Communications operations (communications, education, identity)

    Operation Types:
    - Users operations: User CRUD, profile, authentication, settings, licenses
    - Groups operations: Group CRUD, membership, settings, lifecycle policies
    - Invitations operations: User invitations and invitation management
    - Directory operations: Directory objects, roles, administrative units
    - Organization operations: Organization settings, branding, configuration
    - Domains operations: Domain management, verification, DNS configuration
    - Roles operations: Directory roles, role templates, scoped memberships
    - Extensions operations: Schema extensions and custom properties
    - Authentication operations: Authentication methods and MFA management
    - Settings operations: User settings, preferences, mailbox settings
    - Subscriptions operations: Subscription management and webhooks
    - General operations: Base Users Groups functionality
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client optimized for Users Groups."""
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "users"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Users Groups client initialized with 250 methods")

    def _handle_users_groups_response(self, response: object) -> UsersGroupsResponse:
        """Handle Users Groups API response with comprehensive error handling."""
        try:
            if response is None:
                return UsersGroupsResponse(success=False, error="Empty response from Users Groups API")

            success = True
            error_msg = None

            # Enhanced error response handling for Users Groups operations
            if hasattr(response, 'error'):
                success = False
                error_msg = str(response.error)
            elif isinstance(response, dict) and 'error' in response:
                success = False
                error_info = response['error']
                if isinstance(error_info, dict):
                    error_code = error_info.get('code', 'Unknown')
                    error_message = error_info.get('message', 'No message')
                    error_msg = f"{error_code}: {error_message}"
                else:
                    error_msg = str(error_info)
            elif hasattr(response, 'code') and hasattr(response, 'message'):
                success = False
                error_msg = f"{response.code}: {response.message}"

            return UsersGroupsResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Users Groups response: {e}")
            return UsersGroupsResponse(success=False, error=str(e))

    def get_data_source(self) -> 'UsersGroupsDataSource':
        """Get the underlying Users Groups client."""
        return self

    # ========== USERS OPERATIONS (91 methods) ==========

    async def groups_list_member_of(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List memberOf.
        Users Groups operation: GET /groups/{group-id}/memberOf
        Operation type: users
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).member_of.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_member_of_as_group(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List memberOf.
        Users Groups operation: GET /groups/{group-id}/memberOf/graph.group
        Operation type: users
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).member_of.graph_group.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_transitive_member_of(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List group transitive memberOf.
        Users Groups operation: GET /groups/{group-id}/transitiveMemberOf
        Operation type: users
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).transitive_member_of.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_transitive_members_as_user(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List group transitive members.
        Users Groups operation: GET /groups/{group-id}/transitiveMembers/graph.user
        Operation type: users
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).transitive_members.graph_user.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def invitations_get_invited_user(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get invitedUser from invitations.
        Users Groups operation: GET /invitations/invitedUser
        Operation type: users
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = InvitationsRequestBuilder.InvitationsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = InvitationsRequestBuilder.InvitationsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.invitations.invited_user.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def invitations_invited_user_list_service_provisioning_errors(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get serviceProvisioningErrors property value.
        Users Groups operation: GET /invitations/invitedUser/serviceProvisioningErrors
        Operation type: users
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = InvitationsRequestBuilder.InvitationsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = InvitationsRequestBuilder.InvitationsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.invitations.invited_user.service_provisioning_errors.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def invitations_list_invited_user_sponsors(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get invitedUserSponsors from invitations.
        Users Groups operation: GET /invitations/invitedUserSponsors
        Operation type: users
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = InvitationsRequestBuilder.InvitationsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = InvitationsRequestBuilder.InvitationsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.invitations.invited_user_sponsors.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_delete_activities(
        self,
        userActivity_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete an activity.
        Users Groups operation: DELETE /me/activities/{userActivity-id}
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_update_activities(
        self,
        userActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property activities in me.
        Users Groups operation: PATCH /me/activities/{userActivity-id}
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_activities_create_history_items(
        self,
        userActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to historyItems for me.
        Users Groups operation: POST /me/activities/{userActivity-id}/historyItems
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).history_items.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_activities_list_history_items(
        self,
        userActivity_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get historyItems from me.
        Users Groups operation: GET /me/activities/{userActivity-id}/historyItems
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).history_items.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_activities_delete_history_items(
        self,
        userActivity_id: str,
        activityHistoryItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property historyItems for me.
        Users Groups operation: DELETE /me/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_activities_get_history_items(
        self,
        userActivity_id: str,
        activityHistoryItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get historyItems from me.
        Users Groups operation: GET /me/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_activities_update_history_items(
        self,
        userActivity_id: str,
        activityHistoryItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete an activityHistoryItem.
        Users Groups operation: PATCH /me/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_activities_history_items_get_activity(
        self,
        userActivity_id: str,
        activityHistoryItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get activity from me.
        Users Groups operation: GET /me/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}/activity
        Operation type: users
        Args:
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).activity.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_presence_clear_user_preferred_presence(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action clearUserPreferredPresence.
        Users Groups operation: POST /me/presence/clearUserPreferredPresence
        Operation type: users
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.presence.clear_user_preferred_presence.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_create_scoped_role_member_of(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to scopedRoleMemberOf for me.
        Users Groups operation: POST /me/scopedRoleMemberOf
        Operation type: users
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.scoped_role_member_of.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_list_scoped_role_member_of(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get scopedRoleMemberOf from me.
        Users Groups operation: GET /me/scopedRoleMemberOf
        Operation type: users
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.scoped_role_member_of.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_delete_scoped_role_member_of(
        self,
        scopedRoleMembership_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property scopedRoleMemberOf for me.
        Users Groups operation: DELETE /me/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: users
        Args:
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_get_scoped_role_member_of(
        self,
        scopedRoleMembership_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get scopedRoleMemberOf from me.
        Users Groups operation: GET /me/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: users
        Args:
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def me_update_scoped_role_member_of(
        self,
        scopedRoleMembership_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property scopedRoleMemberOf in me.
        Users Groups operation: PATCH /me/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: users
        Args:
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_create_user(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create User.
        Users Groups operation: POST /users
        Operation type: users
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_list_user(
        self,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List users.
        Users Groups operation: GET /users
        Operation type: users
        Args:
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_delete_user_by_user_principal_name(
        self,
        userPrincipalName: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete user.
        Users Groups operation: DELETE /users(userPrincipalName='{userPrincipalName}')
        Operation type: users
        Args:
            userPrincipalName (str, required): User identifier: userPrincipalName
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users(user_principal_name='{user_principal_name}').delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_get_user_by_user_principal_name(
        self,
        userPrincipalName: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get user.
        Users Groups operation: GET /users(userPrincipalName='{userPrincipalName}')
        Operation type: users
        Args:
            userPrincipalName (str, required): User identifier: userPrincipalName
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users(user_principal_name='{user_principal_name}').get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_update_user_by_user_principal_name(
        self,
        userPrincipalName: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update user.
        Users Groups operation: PATCH /users(userPrincipalName='{userPrincipalName}')
        Operation type: users
        Args:
            userPrincipalName (str, required): User identifier: userPrincipalName
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users(user_principal_name='{user_principal_name}').patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delta(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke function delta.
        Users Groups operation: GET /users/delta()
        Operation type: users
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.delta().get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_validate_properties(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action validateProperties.
        Users Groups operation: POST /users/validateProperties
        Operation type: users
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.validate_properties.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_delete_user(
        self,
        user_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete user.
        Users Groups operation: DELETE /users/{user-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_get_user(
        self,
        user_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get user.
        Users Groups operation: GET /users/{user-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_update_user(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update user.
        Users Groups operation: PATCH /users/{user-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_create_activities(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to activities for users.
        Users Groups operation: POST /users/{user-id}/activities
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delete_activities(
        self,
        user_id: str,
        userActivity_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property activities for users.
        Users Groups operation: DELETE /users/{user-id}/activities/{userActivity-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_update_activities(
        self,
        user_id: str,
        userActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property activities in users.
        Users Groups operation: PATCH /users/{user-id}/activities/{userActivity-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_activities_create_history_items(
        self,
        user_id: str,
        userActivity_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to historyItems for users.
        Users Groups operation: POST /users/{user-id}/activities/{userActivity-id}/historyItems
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).history_items.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_activities_list_history_items(
        self,
        user_id: str,
        userActivity_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get historyItems from users.
        Users Groups operation: GET /users/{user-id}/activities/{userActivity-id}/historyItems
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).history_items.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_activities_delete_history_items(
        self,
        user_id: str,
        userActivity_id: str,
        activityHistoryItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property historyItems for users.
        Users Groups operation: DELETE /users/{user-id}/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_activities_get_history_items(
        self,
        user_id: str,
        userActivity_id: str,
        activityHistoryItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get historyItems from users.
        Users Groups operation: GET /users/{user-id}/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_activities_update_history_items(
        self,
        user_id: str,
        userActivity_id: str,
        activityHistoryItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property historyItems in users.
        Users Groups operation: PATCH /users/{user-id}/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_activities_history_items_get_activity(
        self,
        user_id: str,
        userActivity_id: str,
        activityHistoryItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get activity from users.
        Users Groups operation: GET /users/{user-id}/activities/{userActivity-id}/historyItems/{activityHistoryItem-id}/activity
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            userActivity_id (str, required): Users Groups userActivity id identifier
            activityHistoryItem_id (str, required): Users Groups activityHistoryItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).activities.by_activitie_id(userActivity_id).history_items.by_historyItem_id(activityHistoryItem_id).activity.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_agreement_acceptances(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get agreementAcceptances from users.
        Users Groups operation: GET /users/{user-id}/agreementAcceptances
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).agreement_acceptances.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_agreement_acceptances(
        self,
        user_id: str,
        agreementAcceptance_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get agreementAcceptances from users.
        Users Groups operation: GET /users/{user-id}/agreementAcceptances/{agreementAcceptance-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            agreementAcceptance_id (str, required): Users Groups agreementAcceptance id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).agreement_acceptances.by_agreementAcceptance_id(agreementAcceptance_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_create_app_role_assignments(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Grant an appRoleAssignment to a user.
        Users Groups operation: POST /users/{user-id}/appRoleAssignments
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).app_role_assignments.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_app_role_assignments(
        self,
        user_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List appRoleAssignments granted to a user.
        Users Groups operation: GET /users/{user-id}/appRoleAssignments
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).app_role_assignments.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delete_app_role_assignments(
        self,
        user_id: str,
        appRoleAssignment_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete appRoleAssignment.
        Users Groups operation: DELETE /users/{user-id}/appRoleAssignments/{appRoleAssignment-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            appRoleAssignment_id (str, required): Users Groups appRoleAssignment id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).app_role_assignments.by_appRoleAssignment_id(appRoleAssignment_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_app_role_assignments(
        self,
        user_id: str,
        appRoleAssignment_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get appRoleAssignments from users.
        Users Groups operation: GET /users/{user-id}/appRoleAssignments/{appRoleAssignment-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            appRoleAssignment_id (str, required): Users Groups appRoleAssignment id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).app_role_assignments.by_appRoleAssignment_id(appRoleAssignment_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_update_app_role_assignments(
        self,
        user_id: str,
        appRoleAssignment_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property appRoleAssignments in users.
        Users Groups operation: PATCH /users/{user-id}/appRoleAssignments/{appRoleAssignment-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            appRoleAssignment_id (str, required): Users Groups appRoleAssignment id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).app_role_assignments.by_appRoleAssignment_id(appRoleAssignment_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_assign_license(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action assignLicense.
        Users Groups operation: POST /users/{user-id}/assignLicense
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).assign_license.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_check_member_objects(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action checkMemberObjects.
        Users Groups operation: POST /users/{user-id}/checkMemberObjects
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).check_member_objects.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delete_cloud_clipboard(
        self,
        user_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property cloudClipboard for users.
        Users Groups operation: DELETE /users/{user-id}/cloudClipboard
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_cloud_clipboard(
        self,
        user_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get cloudClipboard from users.
        Users Groups operation: GET /users/{user-id}/cloudClipboard
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_update_cloud_clipboard(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property cloudClipboard in users.
        Users Groups operation: PATCH /users/{user-id}/cloudClipboard
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_cloud_clipboard_create_items(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to items for users.
        Users Groups operation: POST /users/{user-id}/cloudClipboard/items
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.items.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_cloud_clipboard_list_items(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get items from users.
        Users Groups operation: GET /users/{user-id}/cloudClipboard/items
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.items.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_cloud_clipboard_delete_items(
        self,
        user_id: str,
        cloudClipboardItem_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property items for users.
        Users Groups operation: DELETE /users/{user-id}/cloudClipboard/items/{cloudClipboardItem-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            cloudClipboardItem_id (str, required): Users Groups cloudClipboardItem id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.items.by_item_id(cloudClipboardItem_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_cloud_clipboard_get_items(
        self,
        user_id: str,
        cloudClipboardItem_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get items from users.
        Users Groups operation: GET /users/{user-id}/cloudClipboard/items/{cloudClipboardItem-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            cloudClipboardItem_id (str, required): Users Groups cloudClipboardItem id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.items.by_item_id(cloudClipboardItem_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_cloud_clipboard_update_items(
        self,
        user_id: str,
        cloudClipboardItem_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property items in users.
        Users Groups operation: PATCH /users/{user-id}/cloudClipboard/items/{cloudClipboardItem-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            cloudClipboardItem_id (str, required): Users Groups cloudClipboardItem id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).cloud_clipboard.items.by_item_id(cloudClipboardItem_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_get_mail_tips(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action getMailTips.
        Users Groups operation: POST /users/{user-id}/getMailTips
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).get_mail_tips.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_get_managed_app_diagnostic_statuses(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke function getManagedAppDiagnosticStatuses.
        Users Groups operation: GET /users/{user-id}/getManagedAppDiagnosticStatuses()
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).get_managed_app_diagnostic_statuses().get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_get_managed_app_policies(
        self,
        user_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke function getManagedAppPolicies.
        Users Groups operation: GET /users/{user-id}/getManagedAppPolicies()
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).get_managed_app_policies().get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_managed_app_registrations(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get managedAppRegistrations from users.
        Users Groups operation: GET /users/{user-id}/managedAppRegistrations
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).managed_app_registrations.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_managed_app_registrations(
        self,
        user_id: str,
        managedAppRegistration_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get managedAppRegistrations from users.
        Users Groups operation: GET /users/{user-id}/managedAppRegistrations/{managedAppRegistration-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            managedAppRegistration_id (str, required): Users Groups managedAppRegistration id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).managed_app_registrations.by_managedAppRegistration_id(managedAppRegistration_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_people(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get people from users.
        Users Groups operation: GET /users/{user-id}/people
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).people.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_people(
        self,
        user_id: str,
        person_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get people from users.
        Users Groups operation: GET /users/{user-id}/people/{person-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            person_id (str, required): Users Groups person id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).people.by_people_id(person_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_create_permission_grants(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to permissionGrants for users.
        Users Groups operation: POST /users/{user-id}/permissionGrants
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).permission_grants.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_permission_grants(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List permissionGrants of a user.
        Users Groups operation: GET /users/{user-id}/permissionGrants
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).permission_grants.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delete_permission_grants(
        self,
        user_id: str,
        resourceSpecificPermissionGrant_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property permissionGrants for users.
        Users Groups operation: DELETE /users/{user-id}/permissionGrants/{resourceSpecificPermissionGrant-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            resourceSpecificPermissionGrant_id (str, required): Users Groups resourceSpecificPermissionGrant id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).permission_grants.by_permissionGrant_id(resourceSpecificPermissionGrant_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_permission_grants(
        self,
        user_id: str,
        resourceSpecificPermissionGrant_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get permissionGrants from users.
        Users Groups operation: GET /users/{user-id}/permissionGrants/{resourceSpecificPermissionGrant-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            resourceSpecificPermissionGrant_id (str, required): Users Groups resourceSpecificPermissionGrant id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).permission_grants.by_permissionGrant_id(resourceSpecificPermissionGrant_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_update_permission_grants(
        self,
        user_id: str,
        resourceSpecificPermissionGrant_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property permissionGrants in users.
        Users Groups operation: PATCH /users/{user-id}/permissionGrants/{resourceSpecificPermissionGrant-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            resourceSpecificPermissionGrant_id (str, required): Users Groups resourceSpecificPermissionGrant id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).permission_grants.by_permissionGrant_id(resourceSpecificPermissionGrant_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delete_presence(
        self,
        user_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property presence for users.
        Users Groups operation: DELETE /users/{user-id}/presence
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).presence.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_presence(
        self,
        user_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get presence.
        Users Groups operation: GET /users/{user-id}/presence
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).presence.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_update_presence(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property presence in users.
        Users Groups operation: PATCH /users/{user-id}/presence
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).presence.patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_presence_clear_presence(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action clearPresence.
        Users Groups operation: POST /users/{user-id}/presence/clearPresence
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).presence.clear_presence.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_presence_clear_user_preferred_presence(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action clearUserPreferredPresence.
        Users Groups operation: POST /users/{user-id}/presence/clearUserPreferredPresence
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).presence.clear_user_preferred_presence.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_presence_set_presence(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action setPresence.
        Users Groups operation: POST /users/{user-id}/presence/setPresence
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).presence.set_presence.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_reminder_view(
        self,
        user_id: str,
        StartDateTime: str,
        EndDateTime: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke function reminderView.
        Users Groups operation: GET /users/{user-id}/reminderView(StartDateTime='{StartDateTime}',EndDateTime='{EndDateTime}')
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            StartDateTime (str, required): Users Groups path parameter: StartDateTime
            EndDateTime (str, required): Users Groups path parameter: EndDateTime
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).reminder_view(_start_date_time='{_start_date_time}',_end_date_time='{_end_date_time}').get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_reprocess_license_assignment(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action reprocessLicenseAssignment.
        Users Groups operation: POST /users/{user-id}/reprocessLicenseAssignment
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).reprocess_license_assignment.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_user_retry_service_provisioning(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action retryServiceProvisioning.
        Users Groups operation: POST /users/{user-id}/retryServiceProvisioning
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).retry_service_provisioning.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_create_scoped_role_member_of(
        self,
        user_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to scopedRoleMemberOf for users.
        Users Groups operation: POST /users/{user-id}/scopedRoleMemberOf
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_scoped_role_member_of(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get scopedRoleMemberOf from users.
        Users Groups operation: GET /users/{user-id}/scopedRoleMemberOf
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_delete_scoped_role_member_of(
        self,
        user_id: str,
        scopedRoleMembership_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property scopedRoleMemberOf for users.
        Users Groups operation: DELETE /users/{user-id}/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_get_scoped_role_member_of(
        self,
        user_id: str,
        scopedRoleMembership_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get scopedRoleMemberOf from users.
        Users Groups operation: GET /users/{user-id}/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_update_scoped_role_member_of(
        self,
        user_id: str,
        scopedRoleMembership_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property scopedRoleMemberOf in users.
        Users Groups operation: PATCH /users/{user-id}/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_service_provisioning_errors(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get serviceProvisioningErrors property value.
        Users Groups operation: GET /users/{user-id}/serviceProvisioningErrors
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).service_provisioning_errors.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def users_list_sponsors(
        self,
        user_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List sponsors.
        Users Groups operation: GET /users/{user-id}/sponsors
        Operation type: users
        Args:
            user_id (str, required): Users Groups user id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = UsersRequestBuilder.UsersRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = UsersRequestBuilder.UsersRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.users.by_user_id(user_id).sponsors.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    # ========== GROUPS OPERATIONS (53 methods) ==========

    async def group_lifecycle_policies_group_lifecycle_policy_create_group_lifecycle_policy(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create groupLifecyclePolicy.
        Users Groups operation: POST /groupLifecyclePolicies
        Operation type: groups
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_lifecycle_policies_group_lifecycle_policy_list_group_lifecycle_policy(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List groupLifecyclePolicies.
        Users Groups operation: GET /groupLifecyclePolicies
        Operation type: groups
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_lifecycle_policies_group_lifecycle_policy_delete_group_lifecycle_policy(
        self,
        groupLifecyclePolicy_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete groupLifecyclePolicy.
        Users Groups operation: DELETE /groupLifecyclePolicies/{groupLifecyclePolicy-id}
        Operation type: groups
        Args:
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_lifecycle_policies_group_lifecycle_policy_get_group_lifecycle_policy(
        self,
        groupLifecyclePolicy_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get groupLifecyclePolicy.
        Users Groups operation: GET /groupLifecyclePolicies/{groupLifecyclePolicy-id}
        Operation type: groups
        Args:
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_lifecycle_policies_group_lifecycle_policy_update_group_lifecycle_policy(
        self,
        groupLifecyclePolicy_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update groupLifecyclePolicy.
        Users Groups operation: PATCH /groupLifecyclePolicies/{groupLifecyclePolicy-id}
        Operation type: groups
        Args:
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_lifecycle_policies_group_lifecycle_policy_add_group(
        self,
        groupLifecyclePolicy_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action addGroup.
        Users Groups operation: POST /groupLifecyclePolicies/{groupLifecyclePolicy-id}/addGroup
        Operation type: groups
        Args:
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).add_group.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_lifecycle_policies_group_lifecycle_policy_remove_group(
        self,
        groupLifecyclePolicy_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action removeGroup.
        Users Groups operation: POST /groupLifecyclePolicies/{groupLifecyclePolicy-id}/removeGroup
        Operation type: groups
        Args:
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).remove_group.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_setting_templates_group_setting_template_create_group_setting_template(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Add new entity to groupSettingTemplates.
        Users Groups operation: POST /groupSettingTemplates
        Operation type: groups
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_setting_templates.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_setting_templates_validate_properties(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action validateProperties.
        Users Groups operation: POST /groupSettingTemplates/validateProperties
        Operation type: groups
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_setting_templates.validate_properties.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_setting_templates_group_setting_template_delete_group_setting_template(
        self,
        groupSettingTemplate_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete entity from groupSettingTemplates.
        Users Groups operation: DELETE /groupSettingTemplates/{groupSettingTemplate-id}
        Operation type: groups
        Args:
            groupSettingTemplate_id (str, required): Users Groups groupSettingTemplate id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_setting_templates.by_group_setting_template_id(groupSettingTemplate_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_setting_templates_group_setting_template_update_group_setting_template(
        self,
        groupSettingTemplate_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update entity in groupSettingTemplates.
        Users Groups operation: PATCH /groupSettingTemplates/{groupSettingTemplate-id}
        Operation type: groups
        Args:
            groupSettingTemplate_id (str, required): Users Groups groupSettingTemplate id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_setting_templates.by_group_setting_template_id(groupSettingTemplate_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def group_setting_templates_group_setting_template_check_member_objects(
        self,
        groupSettingTemplate_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action checkMemberObjects.
        Users Groups operation: POST /groupSettingTemplates/{groupSettingTemplate-id}/checkMemberObjects
        Operation type: groups
        Args:
            groupSettingTemplate_id (str, required): Users Groups groupSettingTemplate id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.group_setting_templates.by_group_setting_template_id(groupSettingTemplate_id).check_member_objects.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_create_group(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create group.
        Users Groups operation: POST /groups
        Operation type: groups
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_get_group_by_unique_name(
        self,
        uniqueName: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get group.
        Users Groups operation: GET /groups(uniqueName='{uniqueName}')
        Operation type: groups
        Args:
            uniqueName (str, required): Users Groups path parameter: uniqueName
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups(unique_name='{unique_name}').get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_update_group_by_unique_name(
        self,
        uniqueName: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Upsert group.
        Users Groups operation: PATCH /groups(uniqueName='{uniqueName}')
        Operation type: groups
        Args:
            uniqueName (str, required): Users Groups path parameter: uniqueName
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups(unique_name='{unique_name}').patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delta(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke function delta.
        Users Groups operation: GET /groups/delta()
        Operation type: groups
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_orderby (List[str], optional): Order items by property values
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.delta().get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_validate_properties(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action validateProperties.
        Users Groups operation: POST /groups/validateProperties
        Operation type: groups
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.validate_properties.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_get_group(
        self,
        group_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get group.
        Users Groups operation: GET /groups/{group-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_update_group(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Upsert group.
        Users Groups operation: PATCH /groups/{group-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delete_ref_accepted_senders(
        self,
        group_id: str,
        at_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Remove acceptedSender.
        Users Groups operation: DELETE /groups/{group-id}/acceptedSenders/$ref
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            If_Match (str, optional): ETag
            at_id (str, required): The delete Uri
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).accepted_senders.ref.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_app_role_assignments(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List appRoleAssignments granted to a group.
        Users Groups operation: GET /groups/{group-id}/appRoleAssignments
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).app_role_assignments.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delete_app_role_assignments(
        self,
        group_id: str,
        appRoleAssignment_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete appRoleAssignment.
        Users Groups operation: DELETE /groups/{group-id}/appRoleAssignments/{appRoleAssignment-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            appRoleAssignment_id (str, required): Users Groups appRoleAssignment id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).app_role_assignments.by_appRoleAssignment_id(appRoleAssignment_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_get_app_role_assignments(
        self,
        group_id: str,
        appRoleAssignment_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get appRoleAssignments from groups.
        Users Groups operation: GET /groups/{group-id}/appRoleAssignments/{appRoleAssignment-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            appRoleAssignment_id (str, required): Users Groups appRoleAssignment id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).app_role_assignments.by_appRoleAssignment_id(appRoleAssignment_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_update_app_role_assignments(
        self,
        group_id: str,
        appRoleAssignment_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property appRoleAssignments in groups.
        Users Groups operation: PATCH /groups/{group-id}/appRoleAssignments/{appRoleAssignment-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            appRoleAssignment_id (str, required): Users Groups appRoleAssignment id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).app_role_assignments.by_appRoleAssignment_id(appRoleAssignment_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_check_granted_permissions_for_app(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action checkGrantedPermissionsForApp.
        Users Groups operation: POST /groups/{group-id}/checkGrantedPermissionsForApp
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).check_granted_permissions_for_app.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_check_member_objects(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action checkMemberObjects.
        Users Groups operation: POST /groups/{group-id}/checkMemberObjects
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).check_member_objects.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_create_group_lifecycle_policies(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to groupLifecyclePolicies for groups.
        Users Groups operation: POST /groups/{group-id}/groupLifecyclePolicies
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_group_lifecycle_policies(
        self,
        group_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List groupLifecyclePolicies.
        Users Groups operation: GET /groups/{group-id}/groupLifecyclePolicies
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delete_group_lifecycle_policies(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property groupLifecyclePolicies for groups.
        Users Groups operation: DELETE /groups/{group-id}/groupLifecyclePolicies/{groupLifecyclePolicy-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_get_group_lifecycle_policies(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get groupLifecyclePolicies from groups.
        Users Groups operation: GET /groups/{group-id}/groupLifecyclePolicies/{groupLifecyclePolicy-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_update_group_lifecycle_policies(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property groupLifecyclePolicies in groups.
        Users Groups operation: PATCH /groups/{group-id}/groupLifecyclePolicies/{groupLifecyclePolicy-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_group_lifecycle_policies_group_lifecycle_policy_add_group(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action addGroup.
        Users Groups operation: POST /groups/{group-id}/groupLifecyclePolicies/{groupLifecyclePolicy-id}/addGroup
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).add_group.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_group_lifecycle_policies_group_lifecycle_policy_remove_group(
        self,
        group_id: str,
        groupLifecyclePolicy_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action removeGroup.
        Users Groups operation: POST /groups/{group-id}/groupLifecyclePolicies/{groupLifecyclePolicy-id}/removeGroup
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            groupLifecyclePolicy_id (str, required): Users Groups groupLifecyclePolicy id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).group_lifecycle_policies.by_groupLifecyclePolicie_id(groupLifecyclePolicy_id).remove_group.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delete_ref_members(
        self,
        group_id: str,
        at_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Remove member.
        Users Groups operation: DELETE /groups/{group-id}/members/$ref
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            If_Match (str, optional): ETag
            at_id (str, required): The delete Uri
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).members.ref.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_members_with_license_errors(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get membersWithLicenseErrors from groups.
        Users Groups operation: GET /groups/{group-id}/membersWithLicenseErrors
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).members_with_license_errors.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_owners(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List group owners.
        Users Groups operation: GET /groups/{group-id}/owners
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).owners.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_ref_owners(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List group owners.
        Users Groups operation: GET /groups/{group-id}/owners/$ref
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).owners.ref.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_create_permission_grants(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to permissionGrants for groups.
        Users Groups operation: POST /groups/{group-id}/permissionGrants
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).permission_grants.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_permission_grants(
        self,
        group_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List permissionGrants of a group.
        Users Groups operation: GET /groups/{group-id}/permissionGrants
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).permission_grants.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delete_permission_grants(
        self,
        group_id: str,
        resourceSpecificPermissionGrant_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property permissionGrants for groups.
        Users Groups operation: DELETE /groups/{group-id}/permissionGrants/{resourceSpecificPermissionGrant-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            resourceSpecificPermissionGrant_id (str, required): Users Groups resourceSpecificPermissionGrant id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).permission_grants.by_permissionGrant_id(resourceSpecificPermissionGrant_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_get_permission_grants(
        self,
        group_id: str,
        resourceSpecificPermissionGrant_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get permissionGrants from groups.
        Users Groups operation: GET /groups/{group-id}/permissionGrants/{resourceSpecificPermissionGrant-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            resourceSpecificPermissionGrant_id (str, required): Users Groups resourceSpecificPermissionGrant id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).permission_grants.by_permissionGrant_id(resourceSpecificPermissionGrant_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_update_permission_grants(
        self,
        group_id: str,
        resourceSpecificPermissionGrant_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property permissionGrants in groups.
        Users Groups operation: PATCH /groups/{group-id}/permissionGrants/{resourceSpecificPermissionGrant-id}
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            resourceSpecificPermissionGrant_id (str, required): Users Groups resourceSpecificPermissionGrant id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).permission_grants.by_permissionGrant_id(resourceSpecificPermissionGrant_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_delete_ref_rejected_senders(
        self,
        group_id: str,
        at_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Remove rejectedSender.
        Users Groups operation: DELETE /groups/{group-id}/rejectedSenders/$ref
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            If_Match (str, optional): ETag
            at_id (str, required): The delete Uri
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).rejected_senders.ref.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_remove_favorite(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action removeFavorite.
        Users Groups operation: POST /groups/{group-id}/removeFavorite
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).remove_favorite.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_group_retry_service_provisioning(
        self,
        group_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action retryServiceProvisioning.
        Users Groups operation: POST /groups/{group-id}/retryServiceProvisioning
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).retry_service_provisioning.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_service_provisioning_errors(
        self,
        group_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get serviceProvisioningErrors property value.
        Users Groups operation: GET /groups/{group-id}/serviceProvisioningErrors
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).service_provisioning_errors.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_transitive_members(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List group transitive members.
        Users Groups operation: GET /groups/{group-id}/transitiveMembers
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).transitive_members.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def groups_list_transitive_members_as_group(
        self,
        group_id: str,
        ConsistencyLevel: Optional[str] = None,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List group transitive members.
        Users Groups operation: GET /groups/{group-id}/transitiveMembers/graph.group
        Operation type: groups
        Args:
            group_id (str, required): Users Groups group id identifier
            ConsistencyLevel (str, optional): Indicates the requested consistency level. Documentation URL: https://docs.microsoft.com/graph/aad-advanced-queries
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = GroupsRequestBuilder.GroupsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = GroupsRequestBuilder.GroupsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.groups.by_group_id(group_id).transitive_members.graph_group.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def scoped_role_memberships_scoped_role_membership_create_scoped_role_membership(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Add new entity to scopedRoleMemberships.
        Users Groups operation: POST /scopedRoleMemberships
        Operation type: groups
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.scoped_role_memberships.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def scoped_role_memberships_scoped_role_membership_list_scoped_role_membership(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get entities from scopedRoleMemberships.
        Users Groups operation: GET /scopedRoleMemberships
        Operation type: groups
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.scoped_role_memberships.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def scoped_role_memberships_scoped_role_membership_delete_scoped_role_membership(
        self,
        scopedRoleMembership_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete entity from scopedRoleMemberships.
        Users Groups operation: DELETE /scopedRoleMemberships/{scopedRoleMembership-id}
        Operation type: groups
        Args:
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.scoped_role_memberships.by_scopedRoleMembership_id(scopedRoleMembership_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def scoped_role_memberships_scoped_role_membership_get_scoped_role_membership(
        self,
        scopedRoleMembership_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get entity from scopedRoleMemberships by key.
        Users Groups operation: GET /scopedRoleMemberships/{scopedRoleMembership-id}
        Operation type: groups
        Args:
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.scoped_role_memberships.by_scopedRoleMembership_id(scopedRoleMembership_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def scoped_role_memberships_scoped_role_membership_update_scoped_role_membership(
        self,
        scopedRoleMembership_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update entity in scopedRoleMemberships.
        Users Groups operation: PATCH /scopedRoleMemberships/{scopedRoleMembership-id}
        Operation type: groups
        Args:
            scopedRoleMembership_id (str, required): Users Groups scopedRoleMembership id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.scoped_role_memberships.by_scopedRoleMembership_id(scopedRoleMembership_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    # ========== INVITATIONS OPERATIONS (2 methods) ==========

    async def invitations_invitation_create_invitation(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create invitation.
        Users Groups operation: POST /invitations
        Operation type: invitations
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.invitations.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def invitations_invitation_list_invitation(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get entities from invitations.
        Users Groups operation: GET /invitations
        Operation type: invitations
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = InvitationsRequestBuilder.InvitationsRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = InvitationsRequestBuilder.InvitationsRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.invitations.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    # ========== ORGANIZATION OPERATIONS (68 methods) ==========

    async def organization_organization_create_organization(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Add new entity to organization.
        Users Groups operation: POST /organization
        Operation type: organization
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_organization_list_organization(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List organizations.
        Users Groups operation: GET /organization
        Operation type: organization
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_validate_properties(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action validateProperties.
        Users Groups operation: POST /organization/validateProperties
        Operation type: organization
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.validate_properties.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_organization_delete_organization(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete entity from organization.
        Users Groups operation: DELETE /organization/{organization-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_organization_update_organization(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update organization.
        Users Groups operation: PATCH /organization/{organization-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete organizationalBranding.
        Users Groups operation: DELETE /organization/{organization-id}/branding
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding(
        self,
        organization_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get organizationalBranding.
        Users Groups operation: GET /organization/{organization-id}/branding
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update organizationalBranding.
        Users Groups operation: PATCH /organization/{organization-id}/branding
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_background_image(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete backgroundImage for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/backgroundImage
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.background_image.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_background_image(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get backgroundImage for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/backgroundImage
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.background_image.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_background_image(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update backgroundImage for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/backgroundImage
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.background_image.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_banner_logo(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete bannerLogo for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/bannerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.banner_logo.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_banner_logo(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get bannerLogo for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/bannerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.banner_logo.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_banner_logo(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update bannerLogo for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/bannerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.banner_logo.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_custom_css(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete customCSS for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/customCSS
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.custom_css.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_custom_css(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get customCSS for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/customCSS
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.custom_css.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_custom_css(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update customCSS for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/customCSS
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.custom_css.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_favicon(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete favicon for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/favicon
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.favicon.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_favicon(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get favicon for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/favicon
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.favicon.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_favicon(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update favicon for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/favicon
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.favicon.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_header_logo(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete headerLogo for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/headerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.header_logo.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_header_logo(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get headerLogo for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/headerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.header_logo.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_header_logo(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update headerLogo for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/headerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.header_logo.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_create_localizations(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create organizationalBrandingLocalization.
        Users Groups operation: POST /organization/{organization-id}/branding/localizations
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_list_localizations(
        self,
        organization_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List localizations.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete organizationalBrandingLocalization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get organizationalBrandingLocalization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update organizationalBrandingLocalization.
        Users Groups operation: PATCH /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_background_image(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete backgroundImage for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/backgroundImage
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).background_image.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_background_image(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get backgroundImage for the navigation property localizations from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/backgroundImage
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).background_image.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_background_image(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update backgroundImage for the navigation property localizations in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/backgroundImage
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).background_image.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_banner_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete bannerLogo for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/bannerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).banner_logo.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_banner_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get organizationalBrandingLocalization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/bannerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).banner_logo.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_banner_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update organizationalBrandingLocalization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/bannerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).banner_logo.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_custom_css(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete customCSS for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/customCSS
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).custom_css.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_custom_css(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get customCSS for the navigation property localizations from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/customCSS
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).custom_css.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_custom_css(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update customCSS for the navigation property localizations in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/customCSS
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).custom_css.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_favicon(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete favicon for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/favicon
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).favicon.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_favicon(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get favicon for the navigation property localizations from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/favicon
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).favicon.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_favicon(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update favicon for the navigation property localizations in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/favicon
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).favicon.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_header_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete headerLogo for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/headerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).header_logo.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_header_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get headerLogo for the navigation property localizations from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/headerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).header_logo.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_header_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update headerLogo for the navigation property localizations in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/headerLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).header_logo.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_square_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete squareLogo for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/squareLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_square_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get squareLogo for the navigation property localizations from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/squareLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_square_logo(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update squareLogo for the navigation property localizations in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/squareLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_delete_localizations_square_logo_dark(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete squareLogoDark for the navigation property localizations in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/squareLogoDark
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo_dark.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_get_localizations_square_logo_dark(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get squareLogoDark for the navigation property localizations from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/squareLogoDark
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo_dark.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_branding_update_localizations_square_logo_dark(
        self,
        organization_id: str,
        organizationalBrandingLocalization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update squareLogoDark for the navigation property localizations in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/localizations/{organizationalBrandingLocalization-id}/squareLogoDark
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            organizationalBrandingLocalization_id (str, required): Users Groups organizationalBrandingLocalization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.localizations.by_organizational_branding_localization_id(organizationalBrandingLocalization_id).square_logo_dark.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_square_logo(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete squareLogo for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/squareLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_square_logo(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get squareLogo for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/squareLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_square_logo(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update squareLogo for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/squareLogo
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_branding_square_logo_dark(
        self,
        organization_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete squareLogoDark for the navigation property branding in organization.
        Users Groups operation: DELETE /organization/{organization-id}/branding/squareLogoDark
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo_dark.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_branding_square_logo_dark(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get squareLogoDark for the navigation property branding from organization.
        Users Groups operation: GET /organization/{organization-id}/branding/squareLogoDark
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo_dark.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_update_branding_square_logo_dark(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update squareLogoDark for the navigation property branding in organization.
        Users Groups operation: PUT /organization/{organization-id}/branding/squareLogoDark
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).branding.square_logo_dark.put(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_create_certificate_based_auth_configuration(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create certificateBasedAuthConfiguration.
        Users Groups operation: POST /organization/{organization-id}/certificateBasedAuthConfiguration
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_list_certificate_based_auth_configuration(
        self,
        organization_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List certificateBasedAuthConfigurations.
        Users Groups operation: GET /organization/{organization-id}/certificateBasedAuthConfiguration
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_delete_certificate_based_auth_configuration(
        self,
        organization_id: str,
        certificateBasedAuthConfiguration_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete certificateBasedAuthConfiguration.
        Users Groups operation: DELETE /organization/{organization-id}/certificateBasedAuthConfiguration/{certificateBasedAuthConfiguration-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            certificateBasedAuthConfiguration_id (str, required): Users Groups certificateBasedAuthConfiguration id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.by_certificate_based_auth_configuration_id(certificateBasedAuthConfiguration_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_get_certificate_based_auth_configuration(
        self,
        organization_id: str,
        certificateBasedAuthConfiguration_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get certificateBasedAuthConfiguration.
        Users Groups operation: GET /organization/{organization-id}/certificateBasedAuthConfiguration/{certificateBasedAuthConfiguration-id}
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            certificateBasedAuthConfiguration_id (str, required): Users Groups certificateBasedAuthConfiguration id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = OrganizationRequestBuilder.OrganizationRequestBuilderGetQueryParameters()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = OrganizationRequestBuilder.OrganizationRequestBuilderGetRequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).certificate_based_auth_configuration.by_certificate_based_auth_configuration_id(certificateBasedAuthConfiguration_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def organization_organization_check_member_objects(
        self,
        organization_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action checkMemberObjects.
        Users Groups operation: POST /organization/{organization-id}/checkMemberObjects
        Operation type: organization
        Args:
            organization_id (str, required): Users Groups organization id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.organization.by_organization_id(organization_id).check_member_objects.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def policies_cross_tenant_access_policy_templates_delete_multi_tenant_organization_partner_configuration(
        self,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property multiTenantOrganizationPartnerConfiguration for policies.
        Users Groups operation: DELETE /policies/crossTenantAccessPolicy/templates/multiTenantOrganizationPartnerConfiguration
        Operation type: organization
        Args:
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.policies.cross_tenant_access_policy.templates.multi_tenant_organization_partner_configuration.delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_get_multi_tenant_organization(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get multiTenantOrganization.
        Users Groups operation: GET /tenantRelationships/multiTenantOrganization
        Operation type: organization
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_update_multi_tenant_organization(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update multiTenantOrganization.
        Users Groups operation: PATCH /tenantRelationships/multiTenantOrganization
        Operation type: organization
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_get_join_request(
        self,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get multiTenantOrganizationJoinRequestRecord.
        Users Groups operation: GET /tenantRelationships/multiTenantOrganization/joinRequest
        Operation type: organization
        Args:
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.join_request.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_list_tenants(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List multiTenantOrganizationMembers.
        Users Groups operation: GET /tenantRelationships/multiTenantOrganization/tenants
        Operation type: organization
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_delete_tenants(
        self,
        multiTenantOrganizationMember_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Remove multiTenantOrganizationMember.
        Users Groups operation: DELETE /tenantRelationships/multiTenantOrganization/tenants/{multiTenantOrganizationMember-id}
        Operation type: organization
        Args:
            multiTenantOrganizationMember_id (str, required): Users Groups multiTenantOrganizationMember id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_get_tenants(
        self,
        multiTenantOrganizationMember_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get multiTenantOrganizationMember.
        Users Groups operation: GET /tenantRelationships/multiTenantOrganization/tenants/{multiTenantOrganizationMember-id}
        Operation type: organization
        Args:
            multiTenantOrganizationMember_id (str, required): Users Groups multiTenantOrganizationMember id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def tenant_relationships_multi_tenant_organization_update_tenants(
        self,
        multiTenantOrganizationMember_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property tenants in tenantRelationships.
        Users Groups operation: PATCH /tenantRelationships/multiTenantOrganization/tenants/{multiTenantOrganizationMember-id}
        Operation type: organization
        Args:
            multiTenantOrganizationMember_id (str, required): Users Groups multiTenantOrganizationMember id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    # ========== DOMAINS OPERATIONS (29 methods) ==========

    async def domain_dns_records_domain_dns_record_create_domain_dns_record(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Add new entity to domainDnsRecords.
        Users Groups operation: POST /domainDnsRecords
        Operation type: domains
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domain_dns_records.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domain_dns_records_domain_dns_record_list_domain_dns_record(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get entities from domainDnsRecords.
        Users Groups operation: GET /domainDnsRecords
        Operation type: domains
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domain_dns_records.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domain_dns_records_domain_dns_record_delete_domain_dns_record(
        self,
        domainDnsRecord_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete entity from domainDnsRecords.
        Users Groups operation: DELETE /domainDnsRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domain_dns_records.by_domainDnsRecord_id(domainDnsRecord_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domain_dns_records_domain_dns_record_get_domain_dns_record(
        self,
        domainDnsRecord_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get entity from domainDnsRecords by key.
        Users Groups operation: GET /domainDnsRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domain_dns_records.by_domainDnsRecord_id(domainDnsRecord_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domain_dns_records_domain_dns_record_update_domain_dns_record(
        self,
        domainDnsRecord_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update entity in domainDnsRecords.
        Users Groups operation: PATCH /domainDnsRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domain_dns_records.by_domainDnsRecord_id(domainDnsRecord_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_create_domain(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create domain.
        Users Groups operation: POST /domains
        Operation type: domains
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_list_domain(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List domains.
        Users Groups operation: GET /domains
        Operation type: domains
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_delete_domain(
        self,
        domain_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete domain.
        Users Groups operation: DELETE /domains/{domain-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_get_domain(
        self,
        domain_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get domain.
        Users Groups operation: GET /domains/{domain-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_update_domain(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update domain.
        Users Groups operation: PATCH /domains/{domain-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_create_federation_configuration(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create internalDomainFederation.
        Users Groups operation: POST /domains/{domain-id}/federationConfiguration
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_list_federation_configuration(
        self,
        domain_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List internalDomainFederations.
        Users Groups operation: GET /domains/{domain-id}/federationConfiguration
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_delete_federation_configuration(
        self,
        domain_id: str,
        internalDomainFederation_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete internalDomainFederation.
        Users Groups operation: DELETE /domains/{domain-id}/federationConfiguration/{internalDomainFederation-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            internalDomainFederation_id (str, required): Users Groups internalDomainFederation id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.by_federationConfiguration_id(internalDomainFederation_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_get_federation_configuration(
        self,
        domain_id: str,
        internalDomainFederation_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get internalDomainFederation.
        Users Groups operation: GET /domains/{domain-id}/federationConfiguration/{internalDomainFederation-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            internalDomainFederation_id (str, required): Users Groups internalDomainFederation id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.by_federationConfiguration_id(internalDomainFederation_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_update_federation_configuration(
        self,
        domain_id: str,
        internalDomainFederation_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update internalDomainFederation.
        Users Groups operation: PATCH /domains/{domain-id}/federationConfiguration/{internalDomainFederation-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            internalDomainFederation_id (str, required): Users Groups internalDomainFederation id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).federation_configuration.by_federationConfiguration_id(internalDomainFederation_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_force_delete(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action forceDelete.
        Users Groups operation: POST /domains/{domain-id}/forceDelete
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).force_delete.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_promote(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action promote.
        Users Groups operation: POST /domains/{domain-id}/promote
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).promote.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_get_root_domain(
        self,
        domain_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get rootDomain.
        Users Groups operation: GET /domains/{domain-id}/rootDomain
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).root_domain.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_create_service_configuration_records(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to serviceConfigurationRecords for domains.
        Users Groups operation: POST /domains/{domain-id}/serviceConfigurationRecords
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_list_service_configuration_records(
        self,
        domain_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List serviceConfigurationRecords.
        Users Groups operation: GET /domains/{domain-id}/serviceConfigurationRecords
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_delete_service_configuration_records(
        self,
        domain_id: str,
        domainDnsRecord_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property serviceConfigurationRecords for domains.
        Users Groups operation: DELETE /domains/{domain-id}/serviceConfigurationRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.by_domain_dns_record_id(domainDnsRecord_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_get_service_configuration_records(
        self,
        domain_id: str,
        domainDnsRecord_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get serviceConfigurationRecords from domains.
        Users Groups operation: GET /domains/{domain-id}/serviceConfigurationRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.by_domain_dns_record_id(domainDnsRecord_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_update_service_configuration_records(
        self,
        domain_id: str,
        domainDnsRecord_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property serviceConfigurationRecords in domains.
        Users Groups operation: PATCH /domains/{domain-id}/serviceConfigurationRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).service_configuration_records.by_domain_dns_record_id(domainDnsRecord_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_create_verification_dns_records(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create new navigation property to verificationDnsRecords for domains.
        Users Groups operation: POST /domains/{domain-id}/verificationDnsRecords
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_list_verification_dns_records(
        self,
        domain_id: str,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List verificationDnsRecords.
        Users Groups operation: GET /domains/{domain-id}/verificationDnsRecords
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_delete_verification_dns_records(
        self,
        domain_id: str,
        domainDnsRecord_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete navigation property verificationDnsRecords for domains.
        Users Groups operation: DELETE /domains/{domain-id}/verificationDnsRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.by_domain_dns_record_id(domainDnsRecord_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_get_verification_dns_records(
        self,
        domain_id: str,
        domainDnsRecord_id: str,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get verificationDnsRecords from domains.
        Users Groups operation: GET /domains/{domain-id}/verificationDnsRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.by_domain_dns_record_id(domainDnsRecord_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_update_verification_dns_records(
        self,
        domain_id: str,
        domainDnsRecord_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update the navigation property verificationDnsRecords in domains.
        Users Groups operation: PATCH /domains/{domain-id}/verificationDnsRecords/{domainDnsRecord-id}
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            domainDnsRecord_id (str, required): Users Groups domainDnsRecord id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).verification_dns_records.by_domain_dns_record_id(domainDnsRecord_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def domains_domain_verify(
        self,
        domain_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action verify.
        Users Groups operation: POST /domains/{domain-id}/verify
        Operation type: domains
        Args:
            domain_id (str, required): Users Groups domain id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.domains.by_domain_id(domain_id).verify.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    # ========== SUBSCRIPTIONS OPERATIONS (6 methods) ==========

    async def subscriptions_subscription_create_subscription(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Create subscription.
        Users Groups operation: POST /subscriptions
        Operation type: subscriptions
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.subscriptions.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def subscriptions_subscription_list_subscription(
        self,
        dollar_orderby: Optional[List[str]] = None,
        dollar_select: Optional[List[str]] = None,
        dollar_expand: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """List subscriptions.
        Users Groups operation: GET /subscriptions
        Operation type: subscriptions
        Args:
            dollar_orderby (List[str], optional): Order items by property values
            dollar_select (List[str], optional): Select properties to be returned
            dollar_expand (List[str], optional): Expand related entities
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.subscriptions.get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def subscriptions_subscription_delete_subscription(
        self,
        subscription_id: str,
        If_Match: Optional[str] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Delete subscription.
        Users Groups operation: DELETE /subscriptions/{subscription-id}
        Operation type: subscriptions
        Args:
            subscription_id (str, required): Users Groups subscription id identifier
            If_Match (str, optional): ETag
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.subscriptions.by_subscription_id(subscription_id).delete(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def subscriptions_subscription_get_subscription(
        self,
        subscription_id: str,
        dollar_select: Optional[List[str]] = None,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Get subscription.
        Users Groups operation: GET /subscriptions/{subscription-id}
        Operation type: subscriptions
        Args:
            subscription_id (str, required): Users Groups subscription id identifier
            dollar_select (List[str], optional): Select properties to be returned
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.subscriptions.by_subscription_id(subscription_id).get(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def subscriptions_subscription_update_subscription(
        self,
        subscription_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Update subscription.
        Users Groups operation: PATCH /subscriptions/{subscription-id}
        Operation type: subscriptions
        Args:
            subscription_id (str, required): Users Groups subscription id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.subscriptions.by_subscription_id(subscription_id).patch(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    async def subscriptions_subscription_reauthorize(
        self,
        subscription_id: str,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action reauthorize.
        Users Groups operation: POST /subscriptions/{subscription-id}/reauthorize
        Operation type: subscriptions
        Args:
            subscription_id (str, required): Users Groups subscription id identifier
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.subscriptions.by_subscription_id(subscription_id).reauthorize.post(request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )

    # ========== GENERAL OPERATIONS (1 methods) ==========

    async def me_assign_license(
        self,
        select: Optional[List[str]] = None,
        expand: Optional[List[str]] = None,
        filter: Optional[str] = None,
        orderby: Optional[str] = None,
        search: Optional[str] = None,
        top: Optional[int] = None,
        skip: Optional[int] = None,
        request_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        **kwargs
    ) -> UsersGroupsResponse:
        """Invoke action assignLicense.
        Users Groups operation: POST /me/assignLicense
        Operation type: general
        Args:
            select (optional): Select specific properties to return
            expand (optional): Expand related entities (e.g., manager, memberOf, directReports)
            filter (optional): Filter the results using OData syntax
            orderby (optional): Order the results by specified properties
            search (optional): Search for users, groups, or directory objects by content
            top (optional): Limit number of results returned
            skip (optional): Skip number of results for pagination
            request_body (optional): Request body data for Users Groups operations
            headers (optional): Additional headers for the request
            **kwargs: Additional query parameters
        Returns:
            UsersGroupsResponse: Users Groups response wrapper with success/data/error
        """
        # Build query parameters including OData for Users Groups
        try:
            # Use typed query parameters
            query_params = RequestConfiguration()

            # Set query parameters using typed object properties
            if select:
                query_params.select = select if isinstance(select, list) else [select]
            if expand:
                query_params.expand = expand if isinstance(expand, list) else [expand]
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top is not None:
                query_params.top = top
            if skip is not None:
                query_params.skip = skip

            # Create proper typed request configuration
            config = RequestConfiguration()
            config.query_parameters = query_params

            if headers:
                config.headers = headers

            # Add consistency level for search operations in Users Groups
            if search:
                if not config.headers:
                    config.headers = {}
                config.headers['ConsistencyLevel'] = 'eventual'

            response = await self.client.me.assign_license.post(body=request_body, request_configuration=config)
            return self._handle_users_groups_response(response)
        except Exception as e:
            return UsersGroupsResponse(
                success=False,
                error=f"Users Groups API call failed: {str(e)}",
            )



