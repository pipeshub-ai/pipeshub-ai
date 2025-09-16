

import json
import logging
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from kiota_abstractions.base_request_configuration import (  # type: ignore
    RequestConfiguration,
)
from msgraph.generated.chats.chats_request_builder import (  # type: ignore
    ChatsRequestBuilder,
)
from msgraph.generated.models.chat import Chat  #type: ignore
from msgraph.generated.teams.item.channels.channels_request_builder import (  # type: ignore
    ChannelsRequestBuilder,
)
from msgraph.generated.teams.item.members.members_request_builder import (  # type: ignore
    MembersRequestBuilder,
)
from msgraph.generated.teams.teams_request_builder import (  # type: ignore
    TeamsRequestBuilder,
)

from app.sources.client.microsoft.microsoft import MSGraphClient


# Teams-specific response wrapper
class TeamsResponse:
    """Standardized Teams API response wrapper."""
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

class TeamsDataSource:
    """
    Microsoft Teams API client with comprehensive endpoint coverage.

    Supports Teams operations including:
    - Team management: Create, update, delete, archive teams
    - Channel operations: Manage team channels and channel messages
    - Chat operations: Handle team chats and chat messages
    - Member management: Add, remove, update team and chat members
    - App operations: Manage team and chat apps
    - Message operations: Send, read, reply to messages
    - General operations: Team settings and configurations

    Generated methods: 727

    Operation categories:
    - apps: 32 methods
    - channels: 19 methods
    - chats: 74 methods
    - general: 435 methods
    - members: 6 methods
    - teams: 95 methods
    - teamwork: 66 methods
    """

    def __init__(self, client: MSGraphClient) -> None:
        """Initialize with Microsoft Graph SDK client optimized for Teams."""
        self.client = client.get_client().get_ms_graph_service_client()
        if not hasattr(self.client, "me"):
            raise ValueError("Client must be a Microsoft Graph SDK client")
        logger.info("Teams client initialized with 727 methods")

    def _handle_teams_response(self, response: object) -> TeamsResponse:
        """Handle Teams API response with comprehensive error handling."""
        try:
            if response is None:
                return TeamsResponse(success=False, error="Empty response from Teams API")

            success = True
            error_msg = None

            # Enhanced error response handling for Teams operations
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
            return TeamsResponse(
                success=success,
                data=response,
                error=error_msg,
            )
        except Exception as e:
            logger.error(f"Error handling Teams response: {e}")
            return TeamsResponse(success=False, error=str(e))

    def get_data_source(self) -> 'TeamsDataSource':
        """Get the underlying Teams client."""
        return self

    # ========== TEAMS OPERATIONS (95 methods) ==========


    async def teams_team_delete_team(self, team_id: str) -> TeamsResponse:

        """
        Delete entity from teams
        Teams operation: DELETE /teams/{team-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_delete_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_get_team(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get team
        Teams operation: GET /teams/{team-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_get_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_update_team(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update team
        Teams operation: PATCH /teams/{team-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_update_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_group(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get group from teams
        Teams operation: GET /teams/{team-id}/group
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_create_installed_apps(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add app to team
        Teams operation: POST /teams/{team-id}/installedApps
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_installed_apps(self, team_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Remove app from team
        Teams operation: DELETE /teams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_installed_apps(self, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installed app in team
        Teams operation: GET /teams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_update_installed_apps(self, team_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in teams
        Teams operation: PATCH /teams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_installed_apps_get_teams_app(self, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from teams
        Teams operation: GET /teams/{team-id}/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_installed_apps_get_teams_app_definition(self, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from teams
        Teams operation: GET /teams/{team-id}/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_installed_apps_teams_app_installation_upgrade(self, team_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /teams/{team-id}/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_create_operations(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to operations for teams
        Teams operation: POST /teams/{team-id}/operations
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).operations.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_create_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_operations(self, team_id: str, teamsAsyncOperation_id: str) -> TeamsResponse:

        """
        Delete navigation property operations for teams
        Teams operation: DELETE /teams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_operations(self, team_id: str, teamsAsyncOperation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get operations from teams
        Teams operation: GET /teams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_update_operations(self, team_id: str, teamsAsyncOperation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property operations in teams
        Teams operation: PATCH /teams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_update_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_photo(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get profilePhoto
        Teams operation: GET /teams/{team-id}/photo
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).photo.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_primary_channel(self, team_id: str) -> TeamsResponse:

        """
        Delete navigation property primaryChannel for teams
        Teams operation: DELETE /teams/{team-id}/primaryChannel
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_primary_channel(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get primaryChannel
        Teams operation: GET /teams/{team-id}/primaryChannel
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).primary_channel.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_update_primary_channel(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property primaryChannel in teams
        Teams operation: PATCH /teams/{team-id}/primaryChannel
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_update_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_create_all_members(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for teams
        Teams operation: POST /teams/{team-id}/primaryChannel/allMembers
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_primary_channel_all_members_add(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teams/{team-id}/primaryChannel/allMembers/add
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_primary_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_primary_channel_all_members_remove(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teams/{team-id}/primaryChannel/allMembers/remove
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_primary_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_delete_all_members(self, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for teams
        Teams operation: DELETE /teams/{team-id}/primaryChannel/allMembers/{conversationMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_update_all_members(self, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in teams
        Teams operation: PATCH /teams/{team-id}/primaryChannel/allMembers/{conversationMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_get_files_folder(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from teams
        Teams operation: GET /teams/{team-id}/primaryChannel/filesFolder
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).primary_channel.files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_create_members(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for teams
        Teams operation: POST /teams/{team-id}/primaryChannel/members
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_primary_channel_members_add(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teams/{team-id}/primaryChannel/members/add
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_primary_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_primary_channel_members_remove(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teams/{team-id}/primaryChannel/members/remove
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_primary_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_delete_members(self, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for teams
        Teams operation: DELETE /teams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_get_members(self, team_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from teams
        Teams operation: GET /teams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_update_members(self, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in teams
        Teams operation: PATCH /teams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_primary_channel_provision_email(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /teams/{team-id}/primaryChannel/provisionEmail
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_primary_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_primary_channel_remove_email(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /teams/{team-id}/primaryChannel/removeEmail
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_primary_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_schedule(self, team_id: str) -> TeamsResponse:

        """
        Delete navigation property schedule for teams
        Teams operation: DELETE /teams/{team-id}/schedule
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_schedule(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedule
        Teams operation: GET /teams/{team-id}/schedule
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_set_schedule(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create or replace schedule
        Teams operation: PUT /teams/{team-id}/schedule
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.put(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_set_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_day_notes(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to dayNotes for teams
        Teams operation: POST /teams/{team-id}/schedule/dayNotes
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.day_notes.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_day_notes(self, team_id: str, dayNote_id: str) -> TeamsResponse:

        """
        Delete navigation property dayNotes for teams
        Teams operation: DELETE /teams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_day_notes(self, team_id: str, dayNote_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get dayNotes from teams
        Teams operation: GET /teams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_day_notes(self, team_id: str, dayNote_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property dayNotes in teams
        Teams operation: PATCH /teams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_offer_shift_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create offerShiftRequest
        Teams operation: POST /teams/{team-id}/schedule/offerShiftRequests
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.offer_shift_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_offer_shift_requests(self, team_id: str, offerShiftRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property offerShiftRequests for teams
        Teams operation: DELETE /teams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_offer_shift_requests(self, team_id: str, offerShiftRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get offerShiftRequest
        Teams operation: GET /teams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_offer_shift_requests(self, team_id: str, offerShiftRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property offerShiftRequests in teams
        Teams operation: PATCH /teams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_open_shift_change_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create openShiftChangeRequest
        Teams operation: POST /teams/{team-id}/schedule/openShiftChangeRequests
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.open_shift_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_open_shift_change_requests(self, team_id: str, openShiftChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property openShiftChangeRequests for teams
        Teams operation: DELETE /teams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_open_shift_change_requests(self, team_id: str, openShiftChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShiftChangeRequest
        Teams operation: GET /teams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_open_shift_change_requests(self, team_id: str, openShiftChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShiftChangeRequests in teams
        Teams operation: PATCH /teams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_open_shifts(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create openShift
        Teams operation: POST /teams/{team-id}/schedule/openShifts
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.open_shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_open_shifts(self, team_id: str, openShift_id: str) -> TeamsResponse:

        """
        Delete openShift
        Teams operation: DELETE /teams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_open_shifts(self, team_id: str, openShift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShift
        Teams operation: GET /teams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_open_shifts(self, team_id: str, openShift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update openShift
        Teams operation: PATCH /teams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_scheduling_groups(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create schedulingGroup
        Teams operation: POST /teams/{team-id}/schedule/schedulingGroups
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.scheduling_groups.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_scheduling_groups(self, team_id: str, schedulingGroup_id: str) -> TeamsResponse:

        """
        Delete schedulingGroup
        Teams operation: DELETE /teams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_scheduling_groups(self, team_id: str, schedulingGroup_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedulingGroup
        Teams operation: GET /teams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_scheduling_groups(self, team_id: str, schedulingGroup_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Replace schedulingGroup
        Teams operation: PATCH /teams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_shifts(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create shift
        Teams operation: POST /teams/{team-id}/schedule/shifts
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_shifts(self, team_id: str, shift_id: str) -> TeamsResponse:

        """
        Delete shift
        Teams operation: DELETE /teams/{team-id}/schedule/shifts/{shift-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.shifts.by_shift_id(shift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_shifts(self, team_id: str, shift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get shift
        Teams operation: GET /teams/{team-id}/schedule/shifts/{shift-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.shifts.by_shift_id(shift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_shifts(self, team_id: str, shift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Replace shift
        Teams operation: PATCH /teams/{team-id}/schedule/shifts/{shift-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.shifts.by_shift_id(shift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_swap_shifts_change_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create swapShiftsChangeRequest
        Teams operation: POST /teams/{team-id}/schedule/swapShiftsChangeRequests
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.swap_shifts_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_swap_shifts_change_requests(self, team_id: str, swapShiftsChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property swapShiftsChangeRequests for teams
        Teams operation: DELETE /teams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_swap_shifts_change_requests(self, team_id: str, swapShiftsChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get swapShiftsChangeRequest
        Teams operation: GET /teams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_swap_shifts_change_requests(self, team_id: str, swapShiftsChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property swapShiftsChangeRequests in teams
        Teams operation: PATCH /teams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_time_cards(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create timeCard
        Teams operation: POST /teams/{team-id}/schedule/timeCards
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_schedule_time_cards_clock_in(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockIn
        Teams operation: POST /teams/{team-id}/schedule/timeCards/clockIn
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.clock_in.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_schedule_time_cards_clock_in: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_time_cards(self, team_id: str, timeCard_id: str) -> TeamsResponse:

        """
        Delete timeCard
        Teams operation: DELETE /teams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_time_cards(self, team_id: str, timeCard_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeCards from teams
        Teams operation: GET /teams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_time_cards(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeCards in teams
        Teams operation: PATCH /teams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_schedule_time_cards_time_card_clock_out(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockOut
        Teams operation: POST /teams/{team-id}/schedule/timeCards/{timeCard-id}/clockOut
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).clock_out.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_schedule_time_cards_time_card_clock_out: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_schedule_time_cards_time_card_confirm(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action confirm
        Teams operation: POST /teams/{team-id}/schedule/timeCards/{timeCard-id}/confirm
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).confirm.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_schedule_time_cards_time_card_confirm: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_schedule_time_cards_time_card_end_break(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action endBreak
        Teams operation: POST /teams/{team-id}/schedule/timeCards/{timeCard-id}/endBreak
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).end_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_schedule_time_cards_time_card_end_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_schedule_time_cards_time_card_start_break(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action startBreak
        Teams operation: POST /teams/{team-id}/schedule/timeCards/{timeCard-id}/startBreak
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).start_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_schedule_time_cards_time_card_start_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_time_off_reasons(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create timeOffReason
        Teams operation: POST /teams/{team-id}/schedule/timeOffReasons
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_reasons.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_time_off_reasons(self, team_id: str, timeOffReason_id: str) -> TeamsResponse:

        """
        Delete timeOffReason
        Teams operation: DELETE /teams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_time_off_reasons(self, team_id: str, timeOffReason_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffReason
        Teams operation: GET /teams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_time_off_reasons(self, team_id: str, timeOffReason_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Replace timeOffReason
        Teams operation: PATCH /teams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_time_off_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create timeOffRequest
        Teams operation: POST /teams/{team-id}/schedule/timeOffRequests
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_time_off_requests(self, team_id: str, timeOffRequest_id: str) -> TeamsResponse:

        """
        Delete timeOffRequest
        Teams operation: DELETE /teams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_time_off_requests(self, team_id: str, timeOffRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffRequest
        Teams operation: GET /teams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_time_off_requests(self, team_id: str, timeOffRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffRequests in teams
        Teams operation: PATCH /teams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_create_times_off(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create timeOff
        Teams operation: POST /teams/{team-id}/schedule/timesOff
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.times_off.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_create_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_delete_times_off(self, team_id: str, timeOff_id: str) -> TeamsResponse:

        """
        Delete timeOff
        Teams operation: DELETE /teams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_delete_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_get_times_off(self, team_id: str, timeOff_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOff
        Teams operation: GET /teams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_get_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_schedule_update_times_off(self, team_id: str, timeOff_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Replace timeOff
        Teams operation: PATCH /teams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_schedule_update_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_send_activity_notification(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /teams/{team-id}/sendActivityNotification
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_create_tags(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create teamworkTag
        Teams operation: POST /teams/{team-id}/tags
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).tags.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_create_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_tags(self, team_id: str, teamworkTag_id: str) -> TeamsResponse:

        """
        Delete teamworkTag
        Teams operation: DELETE /teams/{team-id}/tags/{teamworkTag-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_tags(self, team_id: str, teamworkTag_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamworkTag
        Teams operation: GET /teams/{team-id}/tags/{teamworkTag-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_update_tags(self, team_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update teamworkTag
        Teams operation: PATCH /teams/{team-id}/tags/{teamworkTag-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_update_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_tags_create_members(self, team_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create teamworkTagMember
        Teams operation: POST /teams/{team-id}/tags/{teamworkTag-id}/members
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_tags_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_tags_delete_members(self, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str) -> TeamsResponse:

        """
        Delete teamworkTagMember
        Teams operation: DELETE /teams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_tags_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_tags_get_members(self, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamworkTagMember
        Teams operation: GET /teams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_tags_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_tags_update_members(self, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in teams
        Teams operation: PATCH /teams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_tags_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_template(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get template from teams
        Teams operation: GET /teams/{team-id}/template
        Operation type: teams
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).template.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_template: {e}")
            return TeamsResponse(success=False, error=str(e))

    # ========== CHANNELS OPERATIONS (19 methods) ==========


    async def teams_create_channels(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create channel
        Teams operation: POST /teams/{team-id}/channels
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_create_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_channels(self, team_id: str, channel_id: str) -> TeamsResponse:

        """
        Delete channel
        Teams operation: DELETE /teams/{team-id}/channels/{channel-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_update_channels(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Patch channel
        Teams operation: PATCH /teams/{team-id}/channels/{channel-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_update_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_create_all_members(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for teams
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/allMembers
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_channels_channel_all_members_add(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/allMembers/add
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_channels_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_channels_channel_all_members_remove(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/allMembers/remove
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_channels_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_delete_all_members(self, team_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for teams
        Teams operation: DELETE /teams/{team-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_update_all_members(self, team_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in teams
        Teams operation: PATCH /teams/{team-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_get_files_folder(self, team_id: str, channel_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder
        Teams operation: GET /teams/{team-id}/channels/{channel-id}/filesFolder
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_create_members(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add conversationMember
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/members
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_channels_channel_members_add(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/members/add
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_channels_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_channels_channel_members_remove(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/members/remove
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_channels_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_get_members(self, team_id: str, channel_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get member of channel
        Teams operation: GET /teams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_update_members(self, team_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update conversationMember
        Teams operation: PATCH /teams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_channels_channel_provision_email(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/provisionEmail
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_channels_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_channels_channel_remove_email(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/removeEmail
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_channels_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_create_tabs(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add tab to channel
        Teams operation: POST /teams/{team-id}/channels/{channel-id}/tabs
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_delete_tabs(self, team_id: str, channel_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete tab from channel
        Teams operation: DELETE /teams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_channels_get_tabs(self, team_id: str, channel_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tab
        Teams operation: GET /teams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: channels
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_channels_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))

    # ========== CHATS OPERATIONS (74 methods) ==========


    async def chats_chat_create_chat(self, body: Chat) -> TeamsResponse:

        """
        Create chat
        Teams operation: POST /chats
        Operation type: chats
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_create_chat: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_update_chat(self, chat_id: str, body: Chat) -> TeamsResponse:

        """
        Update chat
        Teams operation: PATCH /chats/{chat-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_update_chat: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_hide_for_user(self, chat_id: str, body: Chat) -> TeamsResponse:

        """
        Invoke action hideForUser
        Teams operation: POST /chats/{chat-id}/hideForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).hide_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_hide_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_create_installed_apps(self, chat_id: str, body: Chat) -> TeamsResponse:

        """
        Add app to chat
        Teams operation: POST /chats/{chat-id}/installedApps
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_delete_installed_apps(self, chat_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Uninstall app in a chat
        Teams operation: DELETE /chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_get_installed_apps(self, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installed app in chat
        Teams operation: GET /chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_update_installed_apps(self, chat_id: str, teamsAppInstallation_id: str, body: Chat) -> TeamsResponse:

        """
        Update the navigation property installedApps in chats
        Teams operation: PATCH /chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_installed_apps_get_teams_app(self, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from chats
        Teams operation: GET /chats/{chat-id}/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_installed_apps_get_teams_app_definition(self, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from chats
        Teams operation: GET /chats/{chat-id}/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_installed_apps_teams_app_installation_upgrade(self, chat_id: str, teamsAppInstallation_id: str, body: Chat) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /chats/{chat-id}/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_mark_chat_read_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action markChatReadForUser
        Teams operation: POST /chats/{chat-id}/markChatReadForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).mark_chat_read_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_mark_chat_read_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_mark_chat_unread_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action markChatUnreadForUser
        Teams operation: POST /chats/{chat-id}/markChatUnreadForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).mark_chat_unread_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_mark_chat_unread_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_create_members(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add member to a chat
        Teams operation: POST /chats/{chat-id}/members
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_members_add(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /chats/{chat-id}/members/add
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_members_remove(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /chats/{chat-id}/members/remove
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_delete_members(self, chat_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Remove member from chat
        Teams operation: DELETE /chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_get_members(self, chat_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get conversationMember
        Teams operation: GET /chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_update_members(self, chat_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in chats
        Teams operation: PATCH /chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_send_activity_notification(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /chats/{chat-id}/sendActivityNotification
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_create_tabs(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add tab to chat
        Teams operation: POST /chats/{chat-id}/tabs
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_delete_tabs(self, chat_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete tab from chat
        Teams operation: DELETE /chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_get_tabs(self, chat_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tab in chat
        Teams operation: GET /chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def chats_chat_unhide_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action unhideForUser
        Teams operation: POST /chats/{chat-id}/unhideForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.chats.by_chat_id(chat_id).unhide_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in chats_chat_unhide_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_create_chats(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to chats for me
        Teams operation: POST /me/chats
        Operation type: chats
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_create_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_delete_chats(self, chat_id: str) -> TeamsResponse:

        """
        Delete navigation property chats for me
        Teams operation: DELETE /me/chats/{chat-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_delete_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_chats(self, chat_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get chats from me
        Teams operation: GET /me/chats/{chat-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.chats.by_chat_id(chat_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_update_chats(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property chats in me
        Teams operation: PATCH /me/chats/{chat-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_update_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_hide_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action hideForUser
        Teams operation: POST /me/chats/{chat-id}/hideForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).hide_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_hide_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_create_installed_apps(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to installedApps for me
        Teams operation: POST /me/chats/{chat-id}/installedApps
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_delete_installed_apps(self, chat_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Delete navigation property installedApps for me
        Teams operation: DELETE /me/chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_get_installed_apps(self, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installedApps from me
        Teams operation: GET /me/chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_update_installed_apps(self, chat_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in me
        Teams operation: PATCH /me/chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_installed_apps_get_teams_app(self, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from me
        Teams operation: GET /me/chats/{chat-id}/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_installed_apps_get_teams_app_definition(self, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from me
        Teams operation: GET /me/chats/{chat-id}/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_installed_apps_teams_app_installation_upgrade(self, chat_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /me/chats/{chat-id}/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_mark_chat_read_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action markChatReadForUser
        Teams operation: POST /me/chats/{chat-id}/markChatReadForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).mark_chat_read_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_mark_chat_read_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_mark_chat_unread_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action markChatUnreadForUser
        Teams operation: POST /me/chats/{chat-id}/markChatUnreadForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).mark_chat_unread_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_mark_chat_unread_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_create_members(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for me
        Teams operation: POST /me/chats/{chat-id}/members
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_members_add(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /me/chats/{chat-id}/members/add
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_members_remove(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /me/chats/{chat-id}/members/remove
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_delete_members(self, chat_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for me
        Teams operation: DELETE /me/chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_get_members(self, chat_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from me
        Teams operation: GET /me/chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_update_members(self, chat_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in me
        Teams operation: PATCH /me/chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_send_activity_notification(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /me/chats/{chat-id}/sendActivityNotification
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_create_tabs(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for me
        Teams operation: POST /me/chats/{chat-id}/tabs
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_delete_tabs(self, chat_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for me
        Teams operation: DELETE /me/chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_get_tabs(self, chat_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from me
        Teams operation: GET /me/chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_update_tabs(self, chat_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in me
        Teams operation: PATCH /me/chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_chats_chat_unhide_for_user(self, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action unhideForUser
        Teams operation: POST /me/chats/{chat-id}/unhideForUser
        Operation type: chats
        Args:
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.chats.by_chat_id(chat_id).unhide_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_chats_chat_unhide_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_create_chats(self, user_id: str, body: Chat) -> TeamsResponse:

        """
        Create new navigation property to chats for users
        Teams operation: POST /users/{user-id}/chats
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_create_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_delete_chats(self, user_id: str, chat_id: str) -> TeamsResponse:

        """
        Delete navigation property chats for users
        Teams operation: DELETE /users/{user-id}/chats/{chat-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_delete_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_update_chats(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property chats in users
        Teams operation: PATCH /users/{user-id}/chats/{chat-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_update_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_hide_for_user(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action hideForUser
        Teams operation: POST /users/{user-id}/chats/{chat-id}/hideForUser
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).hide_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_hide_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_create_installed_apps(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to installedApps for users
        Teams operation: POST /users/{user-id}/chats/{chat-id}/installedApps
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_delete_installed_apps(self, user_id: str, chat_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Delete navigation property installedApps for users
        Teams operation: DELETE /users/{user-id}/chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_get_installed_apps(self, user_id: str, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installedApps from users
        Teams operation: GET /users/{user-id}/chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_update_installed_apps(self, user_id: str, chat_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in users
        Teams operation: PATCH /users/{user-id}/chats/{chat-id}/installedApps/{teamsAppInstallation-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_installed_apps_get_teams_app(self, user_id: str, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from users
        Teams operation: GET /users/{user-id}/chats/{chat-id}/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_installed_apps_get_teams_app_definition(self, user_id: str, chat_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from users
        Teams operation: GET /users/{user-id}/chats/{chat-id}/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_installed_apps_teams_app_installation_upgrade(self, user_id: str, chat_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /users/{user-id}/chats/{chat-id}/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_mark_chat_read_for_user(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action markChatReadForUser
        Teams operation: POST /users/{user-id}/chats/{chat-id}/markChatReadForUser
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).mark_chat_read_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_mark_chat_read_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_mark_chat_unread_for_user(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action markChatUnreadForUser
        Teams operation: POST /users/{user-id}/chats/{chat-id}/markChatUnreadForUser
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).mark_chat_unread_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_mark_chat_unread_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_create_members(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for users
        Teams operation: POST /users/{user-id}/chats/{chat-id}/members
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_members_add(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /users/{user-id}/chats/{chat-id}/members/add
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_members_remove(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /users/{user-id}/chats/{chat-id}/members/remove
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_delete_members(self, user_id: str, chat_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for users
        Teams operation: DELETE /users/{user-id}/chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_get_members(self, user_id: str, chat_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from users
        Teams operation: GET /users/{user-id}/chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_update_members(self, user_id: str, chat_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in users
        Teams operation: PATCH /users/{user-id}/chats/{chat-id}/members/{conversationMember-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_send_activity_notification(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /users/{user-id}/chats/{chat-id}/sendActivityNotification
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_create_tabs(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for users
        Teams operation: POST /users/{user-id}/chats/{chat-id}/tabs
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_delete_tabs(self, user_id: str, chat_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for users
        Teams operation: DELETE /users/{user-id}/chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_get_tabs(self, user_id: str, chat_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from users
        Teams operation: GET /users/{user-id}/chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChatsRequestBuilder.ChatsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChatsRequestBuilder.ChatsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_chats_update_tabs(self, user_id: str, chat_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in users
        Teams operation: PATCH /users/{user-id}/chats/{chat-id}/tabs/{teamsTab-id}
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_chats_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_chats_chat_unhide_for_user(self, user_id: str, chat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action unhideForUser
        Teams operation: POST /users/{user-id}/chats/{chat-id}/unhideForUser
        Operation type: chats
        Args:
        user_id: Teams user id identifier
        chat_id: Teams chat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).chats.by_chat_id(chat_id).unhide_for_user.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_chats_chat_unhide_for_user: {e}")
            return TeamsResponse(success=False, error=str(e))

    # ========== MEMBERS OPERATIONS (6 methods) ==========


    async def teams_create_members(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add member to team
        Teams operation: POST /teams/{team-id}/members
        Operation type: members
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_members_add(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teams/{team-id}/members/add
        Operation type: members
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_members_remove(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teams/{team-id}/members/remove
        Operation type: members
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_delete_members(self, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Remove member from team
        Teams operation: DELETE /teams/{team-id}/members/{conversationMember-id}
        Operation type: members
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_get_members(self, team_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get member of team
        Teams operation: GET /teams/{team-id}/members/{conversationMember-id}
        Operation type: members
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_update_members(self, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update member in team
        Teams operation: PATCH /teams/{team-id}/members/{conversationMember-id}
        Operation type: members
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))

    # ========== APPS OPERATIONS (32 methods) ==========


    async def groups_team_channels_create_tabs(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for groups
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/tabs
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_delete_tabs(self, group_id: str, channel_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for groups
        Teams operation: DELETE /groups/{group-id}/team/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_get_tabs(self, group_id: str, channel_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from groups
        Teams operation: GET /groups/{group-id}/team/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_update_tabs(self, group_id: str, channel_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in groups
        Teams operation: PATCH /groups/{group-id}/team/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_create_tabs(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for groups
        Teams operation: POST /groups/{group-id}/team/primaryChannel/tabs
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_delete_tabs(self, group_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for groups
        Teams operation: DELETE /groups/{group-id}/team/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_get_tabs(self, group_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from groups
        Teams operation: GET /groups/{group-id}/team/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_update_tabs(self, group_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in groups
        Teams operation: PATCH /groups/{group-id}/team/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        group_id: Teams group id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_create_tabs(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for me
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/tabs
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_delete_tabs(self, team_id: str, channel_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_get_tabs(self, team_id: str, channel_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from me
        Teams operation: GET /me/joinedTeams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_update_tabs(self, team_id: str, channel_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_create_tabs(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for me
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/tabs
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_delete_tabs(self, team_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_get_tabs(self, team_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from me
        Teams operation: GET /me/joinedTeams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_update_tabs(self, team_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_create_tabs(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for teams
        Teams operation: POST /teams/{team-id}/primaryChannel/tabs
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_delete_tabs(self, team_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for teams
        Teams operation: DELETE /teams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_get_tabs(self, team_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from teams
        Teams operation: GET /teams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams.by_team_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_primary_channel_update_tabs(self, team_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in teams
        Teams operation: PATCH /teams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.by_team_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_primary_channel_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_create_tabs(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for teamwork
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/tabs
        Operation type: apps
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_delete_tabs(self, deletedTeam_id: str, channel_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for teamwork
        Teams operation: DELETE /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_get_tabs(self, deletedTeam_id: str, channel_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from teamwork
        Teams operation: GET /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_update_tabs(self, deletedTeam_id: str, channel_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in teamwork
        Teams operation: PATCH /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_create_tabs(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/tabs
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_delete_tabs(self, user_id: str, team_id: str, channel_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_get_tabs(self, user_id: str, team_id: str, channel_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_update_tabs(self, user_id: str, team_id: str, channel_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_create_tabs(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tabs for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/tabs
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_create_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_delete_tabs(self, user_id: str, team_id: str, teamsTab_id: str) -> TeamsResponse:

        """
        Delete navigation property tabs for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_delete_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_get_tabs(self, user_id: str, team_id: str, teamsTab_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tabs from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_get_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_update_tabs(self, user_id: str, team_id: str, teamsTab_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tabs in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/primaryChannel/tabs/{teamsTab-id}
        Operation type: apps
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsTab_id: Teams teamsTab id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.tabs.by_tab_id(teamsTab_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_update_tabs: {e}")
            return TeamsResponse(success=False, error=str(e))

    # ========== TEAMWORK OPERATIONS (66 methods) ==========


    async def me_delete_teamwork(self) -> TeamsResponse:

        """
        Delete navigation property teamwork for me
        Teams operation: DELETE /me/teamwork
        Operation type: teamwork
        Args:
            None
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_delete_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_teamwork(self, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamwork from me
        Teams operation: GET /me/teamwork
        Operation type: teamwork
        Args:
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.teamwork.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_update_teamwork(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property teamwork in me
        Teams operation: PATCH /me/teamwork
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_update_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_create_associated_teams(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to associatedTeams for me
        Teams operation: POST /me/teamwork/associatedTeams
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.associated_teams.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_create_associated_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_delete_associated_teams(self, associatedTeamInfo_id: str) -> TeamsResponse:

        """
        Delete navigation property associatedTeams for me
        Teams operation: DELETE /me/teamwork/associatedTeams/{associatedTeamInfo-id}
        Operation type: teamwork
        Args:
        associatedTeamInfo_id: Teams associatedTeamInfo id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.associated_teams.by_associatedTeam_id(associatedTeamInfo_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_delete_associated_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_update_associated_teams(self, associatedTeamInfo_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property associatedTeams in me
        Teams operation: PATCH /me/teamwork/associatedTeams/{associatedTeamInfo-id}
        Operation type: teamwork
        Args:
        associatedTeamInfo_id: Teams associatedTeamInfo id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.associated_teams.by_associatedTeam_id(associatedTeamInfo_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_update_associated_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_associated_teams_get_team(self, associatedTeamInfo_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get team from me
        Teams operation: GET /me/teamwork/associatedTeams/{associatedTeamInfo-id}/team
        Operation type: teamwork
        Args:
        associatedTeamInfo_id: Teams associatedTeamInfo id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.teamwork.associated_teams.by_associatedTeam_id(associatedTeamInfo_id).team.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_associated_teams_get_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_create_installed_apps(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to installedApps for me
        Teams operation: POST /me/teamwork/installedApps
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_delete_installed_apps(self, userScopeTeamsAppInstallation_id: str) -> TeamsResponse:

        """
        Delete navigation property installedApps for me
        Teams operation: DELETE /me/teamwork/installedApps/{userScopeTeamsAppInstallation-id}
        Operation type: teamwork
        Args:
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_get_installed_apps(self, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installedApps from me
        Teams operation: GET /me/teamwork/installedApps/{userScopeTeamsAppInstallation-id}
        Operation type: teamwork
        Args:
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_update_installed_apps(self, userScopeTeamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in me
        Teams operation: PATCH /me/teamwork/installedApps/{userScopeTeamsAppInstallation-id}
        Operation type: teamwork
        Args:
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_installed_apps_get_chat(self, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get chat from me
        Teams operation: GET /me/teamwork/installedApps/{userScopeTeamsAppInstallation-id}/chat
        Operation type: teamwork
        Args:
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).chat.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_installed_apps_get_chat: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_installed_apps_get_teams_app(self, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from me
        Teams operation: GET /me/teamwork/installedApps/{userScopeTeamsAppInstallation-id}/teamsApp
        Operation type: teamwork
        Args:
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_installed_apps_get_teams_app_definition(self, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from me
        Teams operation: GET /me/teamwork/installedApps/{userScopeTeamsAppInstallation-id}/teamsAppDefinition
        Operation type: teamwork
        Args:
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_teamwork_send_activity_notification(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /me/teamwork/sendActivityNotification
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.teamwork.send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_teamwork_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_teamwork_get_teamwork(self, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamwork
        Teams operation: GET /teamwork
        Operation type: teamwork
        Args:
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_teamwork_get_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_teamwork_update_teamwork(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update teamwork
        Teams operation: PATCH /teamwork
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_teamwork_update_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_create_deleted_chats(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to deletedChats for teamwork
        Teams operation: POST /teamwork/deletedChats
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_chats.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_create_deleted_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_delete_deleted_chats(self, deletedChat_id: str) -> TeamsResponse:

        """
        Delete navigation property deletedChats for teamwork
        Teams operation: DELETE /teamwork/deletedChats/{deletedChat-id}
        Operation type: teamwork
        Args:
        deletedChat_id: Teams deletedChat id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_chats.by_deletedChat_id(deletedChat_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_delete_deleted_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_get_deleted_chats(self, deletedChat_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get deletedChat
        Teams operation: GET /teamwork/deletedChats/{deletedChat-id}
        Operation type: teamwork
        Args:
        deletedChat_id: Teams deletedChat id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.deleted_chats.by_deletedChat_id(deletedChat_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_get_deleted_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_update_deleted_chats(self, deletedChat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property deletedChats in teamwork
        Teams operation: PATCH /teamwork/deletedChats/{deletedChat-id}
        Operation type: teamwork
        Args:
        deletedChat_id: Teams deletedChat id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_chats.by_deletedChat_id(deletedChat_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_update_deleted_chats: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_chats_deleted_chat_undo_delete(self, deletedChat_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action undoDelete
        Teams operation: POST /teamwork/deletedChats/{deletedChat-id}/undoDelete
        Operation type: teamwork
        Args:
        deletedChat_id: Teams deletedChat id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_chats.by_deletedChat_id(deletedChat_id).undo_delete.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_chats_deleted_chat_undo_delete: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_create_deleted_teams(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to deletedTeams for teamwork
        Teams operation: POST /teamwork/deletedTeams
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_create_deleted_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_delete_deleted_teams(self, deletedTeam_id: str) -> TeamsResponse:

        """
        Delete navigation property deletedTeams for teamwork
        Teams operation: DELETE /teamwork/deletedTeams/{deletedTeam-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_delete_deleted_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_get_deleted_teams(self, deletedTeam_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get deletedTeams from teamwork
        Teams operation: GET /teamwork/deletedTeams/{deletedTeam-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_get_deleted_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_update_deleted_teams(self, deletedTeam_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property deletedTeams in teamwork
        Teams operation: PATCH /teamwork/deletedTeams/{deletedTeam-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_update_deleted_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_create_channels(self, deletedTeam_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to channels for teamwork
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_create_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_delete_channels(self, deletedTeam_id: str, channel_id: str) -> TeamsResponse:

        """
        Delete navigation property channels for teamwork
        Teams operation: DELETE /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_delete_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_update_channels(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property channels in teamwork
        Teams operation: PATCH /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_update_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_create_all_members(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for teamwork
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/allMembers
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_deleted_team_channels_channel_all_members_add(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/allMembers/add
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_deleted_team_channels_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_deleted_team_channels_channel_all_members_remove(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/allMembers/remove
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_deleted_team_channels_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_delete_all_members(self, deletedTeam_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for teamwork
        Teams operation: DELETE /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_update_all_members(self, deletedTeam_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in teamwork
        Teams operation: PATCH /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_get_files_folder(self, deletedTeam_id: str, channel_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from teamwork
        Teams operation: GET /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/filesFolder
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_create_members(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for teamwork
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/members
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_deleted_team_channels_channel_members_add(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/members/add
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_deleted_team_channels_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_deleted_team_channels_channel_members_remove(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/members/remove
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_deleted_team_channels_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_delete_members(self, deletedTeam_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for teamwork
        Teams operation: DELETE /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_get_members(self, deletedTeam_id: str, channel_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from teamwork
        Teams operation: GET /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_channels_update_members(self, deletedTeam_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in teamwork
        Teams operation: PATCH /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_channels_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_deleted_team_channels_channel_provision_email(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/provisionEmail
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_deleted_team_channels_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_deleted_teams_deleted_team_channels_channel_remove_email(self, deletedTeam_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /teamwork/deletedTeams/{deletedTeam-id}/channels/{channel-id}/removeEmail
        Operation type: teamwork
        Args:
        deletedTeam_id: Teams deletedTeam id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.deleted_teams.by_deletedTeam_id(deletedTeam_id).channels.by_channel_id(channel_id).remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_deleted_teams_deleted_team_channels_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_send_activity_notification_to_recipients(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotificationToRecipients
        Teams operation: POST /teamwork/sendActivityNotificationToRecipients
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.send_activity_notification_to_recipients.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_send_activity_notification_to_recipients: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_delete_teams_app_settings(self) -> TeamsResponse:

        """
        Delete navigation property teamsAppSettings for teamwork
        Teams operation: DELETE /teamwork/teamsAppSettings
        Operation type: teamwork
        Args:
            None
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.teams_app_settings.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_delete_teams_app_settings: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_get_teams_app_settings(self, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppSettings
        Teams operation: GET /teamwork/teamsAppSettings
        Operation type: teamwork
        Args:
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.teams_app_settings.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_get_teams_app_settings: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_update_teams_app_settings(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update teamsAppSettings
        Teams operation: PATCH /teamwork/teamsAppSettings
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.teams_app_settings.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_update_teams_app_settings: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_create_workforce_integrations(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create workforceIntegration
        Teams operation: POST /teamwork/workforceIntegrations
        Operation type: teamwork
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.workforce_integrations.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_create_workforce_integrations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_delete_workforce_integrations(self, workforceIntegration_id: str) -> TeamsResponse:

        """
        Delete workforceIntegration
        Teams operation: DELETE /teamwork/workforceIntegrations/{workforceIntegration-id}
        Operation type: teamwork
        Args:
        workforceIntegration_id: Teams workforceIntegration id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.workforce_integrations.by_workforceIntegration_id(workforceIntegration_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_delete_workforce_integrations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_get_workforce_integrations(self, workforceIntegration_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get workforceIntegration
        Teams operation: GET /teamwork/workforceIntegrations/{workforceIntegration-id}
        Operation type: teamwork
        Args:
        workforceIntegration_id: Teams workforceIntegration id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.teamwork.workforce_integrations.by_workforceIntegration_id(workforceIntegration_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_get_workforce_integrations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teamwork_update_workforce_integrations(self, workforceIntegration_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update workforceIntegration
        Teams operation: PATCH /teamwork/workforceIntegrations/{workforceIntegration-id}
        Operation type: teamwork
        Args:
        workforceIntegration_id: Teams workforceIntegration id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teamwork.workforce_integrations.by_workforceIntegration_id(workforceIntegration_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teamwork_update_workforce_integrations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_delete_teamwork(self, user_id: str) -> TeamsResponse:

        """
        Delete navigation property teamwork for users
        Teams operation: DELETE /users/{user-id}/teamwork
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_delete_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_teamwork(self, user_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get userTeamwork
        Teams operation: GET /users/{user-id}/teamwork
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).teamwork.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_update_teamwork(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property teamwork in users
        Teams operation: PATCH /users/{user-id}/teamwork
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_update_teamwork: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_create_associated_teams(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to associatedTeams for users
        Teams operation: POST /users/{user-id}/teamwork/associatedTeams
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.associated_teams.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_create_associated_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_delete_associated_teams(self, user_id: str, associatedTeamInfo_id: str) -> TeamsResponse:

        """
        Delete navigation property associatedTeams for users
        Teams operation: DELETE /users/{user-id}/teamwork/associatedTeams/{associatedTeamInfo-id}
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        associatedTeamInfo_id: Teams associatedTeamInfo id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.associated_teams.by_associatedTeam_id(associatedTeamInfo_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_delete_associated_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_update_associated_teams(self, user_id: str, associatedTeamInfo_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property associatedTeams in users
        Teams operation: PATCH /users/{user-id}/teamwork/associatedTeams/{associatedTeamInfo-id}
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        associatedTeamInfo_id: Teams associatedTeamInfo id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.associated_teams.by_associatedTeam_id(associatedTeamInfo_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_update_associated_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_associated_teams_get_team(self, user_id: str, associatedTeamInfo_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get team from users
        Teams operation: GET /users/{user-id}/teamwork/associatedTeams/{associatedTeamInfo-id}/team
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        associatedTeamInfo_id: Teams associatedTeamInfo id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).teamwork.associated_teams.by_associatedTeam_id(associatedTeamInfo_id).team.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_associated_teams_get_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_create_installed_apps(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Install app for user
        Teams operation: POST /users/{user-id}/teamwork/installedApps
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_delete_installed_apps(self, user_id: str, userScopeTeamsAppInstallation_id: str) -> TeamsResponse:

        """
        Uninstall app for user
        Teams operation: DELETE /users/{user-id}/teamwork/installedApps/{userScopeTeamsAppInstallation-id}
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_get_installed_apps(self, user_id: str, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installed app for user
        Teams operation: GET /users/{user-id}/teamwork/installedApps/{userScopeTeamsAppInstallation-id}
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_update_installed_apps(self, user_id: str, userScopeTeamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in users
        Teams operation: PATCH /users/{user-id}/teamwork/installedApps/{userScopeTeamsAppInstallation-id}
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_installed_apps_get_chat(self, user_id: str, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get chat between user and teamsApp
        Teams operation: GET /users/{user-id}/teamwork/installedApps/{userScopeTeamsAppInstallation-id}/chat
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).chat.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_installed_apps_get_chat: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_installed_apps_get_teams_app(self, user_id: str, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from users
        Teams operation: GET /users/{user-id}/teamwork/installedApps/{userScopeTeamsAppInstallation-id}/teamsApp
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_teamwork_installed_apps_get_teams_app_definition(self, user_id: str, userScopeTeamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from users
        Teams operation: GET /users/{user-id}/teamwork/installedApps/{userScopeTeamsAppInstallation-id}/teamsAppDefinition
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        userScopeTeamsAppInstallation_id: Teams userScopeTeamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).teamwork.installed_apps.by_installed_app_id(userScopeTeamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_teamwork_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_teamwork_send_activity_notification(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /users/{user-id}/teamwork/sendActivityNotification
        Operation type: teamwork
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).teamwork.send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_teamwork_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))

    # ========== GENERAL OPERATIONS (435 methods) ==========


    async def app_catalogs_delete_teams_apps(self, teamsApp_id: str) -> TeamsResponse:

        """
        Delete teamsApp
        Teams operation: DELETE /appCatalogs/teamsApps/{teamsApp-id}
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_delete_teams_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_get_teams_apps(self, teamsApp_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApps from appCatalogs
        Teams operation: GET /appCatalogs/teamsApps/{teamsApp-id}
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_get_teams_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_update_teams_apps(self, teamsApp_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property teamsApps in appCatalogs
        Teams operation: PATCH /appCatalogs/teamsApps/{teamsApp-id}
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_update_teams_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_teams_apps_create_app_definitions(self, teamsApp_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update teamsApp
        Teams operation: POST /appCatalogs/teamsApps/{teamsApp-id}/appDefinitions
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).app_definitions.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_teams_apps_create_app_definitions: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_teams_apps_delete_app_definitions(self, teamsApp_id: str, teamsAppDefinition_id: str) -> TeamsResponse:

        """
        Delete navigation property appDefinitions for appCatalogs
        Teams operation: DELETE /appCatalogs/teamsApps/{teamsApp-id}/appDefinitions/{teamsAppDefinition-id}
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        teamsAppDefinition_id: Teams teamsAppDefinition id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).app_definitions.by_appDefinition_id(teamsAppDefinition_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_teams_apps_delete_app_definitions: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_teams_apps_get_app_definitions(self, teamsApp_id: str, teamsAppDefinition_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get appDefinitions from appCatalogs
        Teams operation: GET /appCatalogs/teamsApps/{teamsApp-id}/appDefinitions/{teamsAppDefinition-id}
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        teamsAppDefinition_id: Teams teamsAppDefinition id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).app_definitions.by_appDefinition_id(teamsAppDefinition_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_teams_apps_get_app_definitions: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_teams_apps_app_definitions_delete_bot(self, teamsApp_id: str, teamsAppDefinition_id: str) -> TeamsResponse:

        """
        Delete navigation property bot for appCatalogs
        Teams operation: DELETE /appCatalogs/teamsApps/{teamsApp-id}/appDefinitions/{teamsAppDefinition-id}/bot
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        teamsAppDefinition_id: Teams teamsAppDefinition id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).app_definitions.by_appDefinition_id(teamsAppDefinition_id).bot.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_teams_apps_app_definitions_delete_bot: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_teams_apps_app_definitions_get_bot(self, teamsApp_id: str, teamsAppDefinition_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamworkBot
        Teams operation: GET /appCatalogs/teamsApps/{teamsApp-id}/appDefinitions/{teamsAppDefinition-id}/bot
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        teamsAppDefinition_id: Teams teamsAppDefinition id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).app_definitions.by_appDefinition_id(teamsAppDefinition_id).bot.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_teams_apps_app_definitions_get_bot: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def app_catalogs_teams_apps_app_definitions_update_bot(self, teamsApp_id: str, teamsAppDefinition_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property bot in appCatalogs
        Teams operation: PATCH /appCatalogs/teamsApps/{teamsApp-id}/appDefinitions/{teamsAppDefinition-id}/bot
        Operation type: general
        Args:
        teamsApp_id: Teams teamsApp id identifier
        teamsAppDefinition_id: Teams teamsAppDefinition id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.app_catalogs.teams_apps.by_teamsApp_id(teamsApp_id).app_definitions.by_appDefinition_id(teamsAppDefinition_id).bot.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in app_catalogs_teams_apps_app_definitions_update_bot: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def contracts_contract_check_member_objects(self, contract_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /contracts/{contract-id}/checkMemberObjects
        Operation type: general
        Args:
        contract_id: Teams contract id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.contracts.by_contract_id(contract_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in contracts_contract_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_deleted_items_directory_object_check_member_objects(self, directoryObject_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /directory/deletedItems/{directoryObject-id}/checkMemberObjects
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.directory.deleted_items.by_deletedItem_id(directoryObject_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_deleted_items_directory_object_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_role_templates_directory_role_template_check_member_objects(self, directoryRoleTemplate_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /directoryRoleTemplates/{directoryRoleTemplate-id}/checkMemberObjects
        Operation type: general
        Args:
        directoryRoleTemplate_id: Teams directoryRoleTemplate id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.directory_role_templates.by_directoryRoleTemplate_id(directoryRoleTemplate_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_role_templates_directory_role_template_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_directory_role_check_member_objects(self, directoryRole_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /directoryRoles/{directoryRole-id}/checkMemberObjects
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_directory_role_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_get_members_as_group(self, directoryRole_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /directoryRoles/{directoryRole-id}/members/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).members.by_membership_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_get_members_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_get_members_as_service_principal(self, directoryRole_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.servicePrincipal
        Teams operation: GET /directoryRoles/{directoryRole-id}/members/{directoryObject-id}/graph.servicePrincipal
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).members.by_membership_id(directoryObject_id).graph_service_principal.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_get_members_as_service_principal: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_get_members_as_user(self, directoryRole_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.user
        Teams operation: GET /directoryRoles/{directoryRole-id}/members/{directoryObject-id}/graph.user
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).members.by_membership_id(directoryObject_id).graph_user.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_get_members_as_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_create_scoped_members(self, directoryRole_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to scopedMembers for directoryRoles
        Teams operation: POST /directoryRoles/{directoryRole-id}/scopedMembers
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).scoped_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_create_scoped_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_delete_scoped_members(self, directoryRole_id: str, scopedRoleMembership_id: str) -> TeamsResponse:

        """
        Delete navigation property scopedMembers for directoryRoles
        Teams operation: DELETE /directoryRoles/{directoryRole-id}/scopedMembers/{scopedRoleMembership-id}
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).scoped_members.by_scopedMember_id(scopedRoleMembership_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_delete_scoped_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def directory_roles_update_scoped_members(self, directoryRole_id: str, scopedRoleMembership_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property scopedMembers in directoryRoles
        Teams operation: PATCH /directoryRoles/{directoryRole-id}/scopedMembers/{scopedRoleMembership-id}
        Operation type: general
        Args:
        directoryRole_id: Teams directoryRole id identifier
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.directory_roles.by_directoryRole_id(directoryRole_id).scoped_members.by_scopedMember_id(scopedRoleMembership_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in directory_roles_update_scoped_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def education_classes_create_ref_members(self, educationClass_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add a student
        Teams operation: POST /education/classes/{educationClass-id}/members/$ref
        Operation type: general
        Args:
        educationClass_id: Teams educationClass id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.education.classes.by_classe_id(educationClass_id).members.dollar_ref.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in education_classes_create_ref_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def education_classes_delete_ref_members(self, educationClass_id: str) -> TeamsResponse:

        """
        Remove member from educationClass
        Teams operation: DELETE /education/classes/{educationClass-id}/members/$ref
        Operation type: general
        Args:
        educationClass_id: Teams educationClass id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.education.classes.by_classe_id(educationClass_id).members.dollar_ref.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in education_classes_delete_ref_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def education_classes_members_delete_ref_education_user(self, educationClass_id: str, educationUser_id: str) -> TeamsResponse:

        """
        Remove member from educationClass
        Teams operation: DELETE /education/classes/{educationClass-id}/members/{educationUser-id}/$ref
        Operation type: general
        Args:
        educationClass_id: Teams educationClass id identifier
        educationUser_id: Teams educationUser id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.education.classes.by_classe_id(educationClass_id).members.by_membership_id(educationUser_id).dollar_ref.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in education_classes_members_delete_ref_education_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def group_setting_templates_group_setting_template_check_member_objects(self, groupSettingTemplate_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /groupSettingTemplates/{groupSettingTemplate-id}/checkMemberObjects
        Operation type: general
        Args:
        groupSettingTemplate_id: Teams groupSettingTemplate id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.group_setting_templates.by_groupSettingTemplate_id(groupSettingTemplate_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in group_setting_templates_group_setting_template_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_check_member_objects(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /groups/{group-id}/checkMemberObjects
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_member_of(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get memberOf from groups
        Teams operation: GET /groups/{group-id}/memberOf/{directoryObject-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).member_of.by_memberOf_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_delete_ref_members(self, group_id: str) -> TeamsResponse:

        """
        Remove member
        Teams operation: DELETE /groups/{group-id}/members/$ref
        Operation type: general
        Args:
        group_id: Teams group id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).members.dollar_ref.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_delete_ref_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_members_delete_ref_directory_object(self, group_id: str, directoryObject_id: str) -> TeamsResponse:

        """
        Remove member
        Teams operation: DELETE /groups/{group-id}/members/{directoryObject-id}/$ref
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).members.by_membership_id(directoryObject_id).dollar_ref.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_members_delete_ref_directory_object: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_members_as_group(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /groups/{group-id}/members/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).members.by_membership_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_members_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_members_as_service_principal(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.servicePrincipal
        Teams operation: GET /groups/{group-id}/members/{directoryObject-id}/graph.servicePrincipal
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).members.by_membership_id(directoryObject_id).graph_service_principal.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_members_as_service_principal: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_members_with_license_errors_as_group(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /groups/{group-id}/membersWithLicenseErrors/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).members_with_license_errors.by_membersWithLicenseError_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_members_with_license_errors_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_members_with_license_errors_as_service_principal(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.servicePrincipal
        Teams operation: GET /groups/{group-id}/membersWithLicenseErrors/{directoryObject-id}/graph.servicePrincipal
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).members_with_license_errors.by_membersWithLicenseError_id(directoryObject_id).graph_service_principal.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_members_with_license_errors_as_service_principal: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_members_with_license_errors_as_user(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.user
        Teams operation: GET /groups/{group-id}/membersWithLicenseErrors/{directoryObject-id}/graph.user
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).members_with_license_errors.by_membersWithLicenseError_id(directoryObject_id).graph_user.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_members_with_license_errors_as_user: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_delete_team(self, group_id: str) -> TeamsResponse:

        """
        Delete navigation property team for groups
        Teams operation: DELETE /groups/{group-id}/team
        Operation type: general
        Args:
        group_id: Teams group id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_delete_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_team(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get team from groups
        Teams operation: GET /groups/{group-id}/team
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_set_team(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create team from group
        Teams operation: PUT /groups/{group-id}/team
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.put(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_set_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_create_channels(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to channels for groups
        Teams operation: POST /groups/{group-id}/team/channels
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_create_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_channels(self, group_id: str, channel_id: str) -> TeamsResponse:

        """
        Delete navigation property channels for groups
        Teams operation: DELETE /groups/{group-id}/team/channels/{channel-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_channels(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property channels in groups
        Teams operation: PATCH /groups/{group-id}/team/channels/{channel-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_create_all_members(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for groups
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/allMembers
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_channels_channel_all_members_add(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/allMembers/add
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_channels_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_channels_channel_all_members_remove(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/allMembers/remove
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_channels_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_delete_all_members(self, group_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for groups
        Teams operation: DELETE /groups/{group-id}/team/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_update_all_members(self, group_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in groups
        Teams operation: PATCH /groups/{group-id}/team/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_get_files_folder(self, group_id: str, channel_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from groups
        Teams operation: GET /groups/{group-id}/team/channels/{channel-id}/filesFolder
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_create_members(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for groups
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/members
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_channels_channel_members_add(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/members/add
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_channels_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_channels_channel_members_remove(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/members/remove
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_channels_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_delete_members(self, group_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for groups
        Teams operation: DELETE /groups/{group-id}/team/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_get_members(self, group_id: str, channel_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from groups
        Teams operation: GET /groups/{group-id}/team/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_channels_update_members(self, group_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in groups
        Teams operation: PATCH /groups/{group-id}/team/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_channels_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_channels_channel_provision_email(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/provisionEmail
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_channels_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_channels_channel_remove_email(self, group_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /groups/{group-id}/team/channels/{channel-id}/removeEmail
        Operation type: general
        Args:
        group_id: Teams group id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.channels.by_channel_id(channel_id).remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_channels_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_group(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get group from groups
        Teams operation: GET /groups/{group-id}/team/group
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_create_installed_apps(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to installedApps for groups
        Teams operation: POST /groups/{group-id}/team/installedApps
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_installed_apps(self, group_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Delete navigation property installedApps for groups
        Teams operation: DELETE /groups/{group-id}/team/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_installed_apps(self, group_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installedApps from groups
        Teams operation: GET /groups/{group-id}/team/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_installed_apps(self, group_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in groups
        Teams operation: PATCH /groups/{group-id}/team/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_installed_apps_get_teams_app(self, group_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from groups
        Teams operation: GET /groups/{group-id}/team/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_installed_apps_get_teams_app_definition(self, group_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from groups
        Teams operation: GET /groups/{group-id}/team/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_installed_apps_teams_app_installation_upgrade(self, group_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /groups/{group-id}/team/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_create_members(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for groups
        Teams operation: POST /groups/{group-id}/team/members
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_members_add(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /groups/{group-id}/team/members/add
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_members_remove(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /groups/{group-id}/team/members/remove
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_members(self, group_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for groups
        Teams operation: DELETE /groups/{group-id}/team/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_members(self, group_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from groups
        Teams operation: GET /groups/{group-id}/team/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_members(self, group_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in groups
        Teams operation: PATCH /groups/{group-id}/team/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_create_operations(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to operations for groups
        Teams operation: POST /groups/{group-id}/team/operations
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.operations.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_create_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_operations(self, group_id: str, teamsAsyncOperation_id: str) -> TeamsResponse:

        """
        Delete navigation property operations for groups
        Teams operation: DELETE /groups/{group-id}/team/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.operations.by_operation_id(teamsAsyncOperation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_operations(self, group_id: str, teamsAsyncOperation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get operations from groups
        Teams operation: GET /groups/{group-id}/team/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.operations.by_operation_id(teamsAsyncOperation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_operations(self, group_id: str, teamsAsyncOperation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property operations in groups
        Teams operation: PATCH /groups/{group-id}/team/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.operations.by_operation_id(teamsAsyncOperation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_photo(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get photo from groups
        Teams operation: GET /groups/{group-id}/team/photo
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.photo.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_photo(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property photo in groups
        Teams operation: PATCH /groups/{group-id}/team/photo
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.photo.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_primary_channel(self, group_id: str) -> TeamsResponse:

        """
        Delete navigation property primaryChannel for groups
        Teams operation: DELETE /groups/{group-id}/team/primaryChannel
        Operation type: general
        Args:
        group_id: Teams group id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_primary_channel(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get primaryChannel from groups
        Teams operation: GET /groups/{group-id}/team/primaryChannel
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_primary_channel(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property primaryChannel in groups
        Teams operation: PATCH /groups/{group-id}/team/primaryChannel
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_create_all_members(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for groups
        Teams operation: POST /groups/{group-id}/team/primaryChannel/allMembers
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_primary_channel_all_members_add(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /groups/{group-id}/team/primaryChannel/allMembers/add
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_primary_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_primary_channel_all_members_remove(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /groups/{group-id}/team/primaryChannel/allMembers/remove
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_primary_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_delete_all_members(self, group_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for groups
        Teams operation: DELETE /groups/{group-id}/team/primaryChannel/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_update_all_members(self, group_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in groups
        Teams operation: PATCH /groups/{group-id}/team/primaryChannel/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_get_files_folder(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from groups
        Teams operation: GET /groups/{group-id}/team/primaryChannel/filesFolder
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_create_members(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for groups
        Teams operation: POST /groups/{group-id}/team/primaryChannel/members
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_primary_channel_members_add(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /groups/{group-id}/team/primaryChannel/members/add
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_primary_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_primary_channel_members_remove(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /groups/{group-id}/team/primaryChannel/members/remove
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_primary_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_delete_members(self, group_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for groups
        Teams operation: DELETE /groups/{group-id}/team/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_get_members(self, group_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from groups
        Teams operation: GET /groups/{group-id}/team/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_primary_channel_update_members(self, group_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in groups
        Teams operation: PATCH /groups/{group-id}/team/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_primary_channel_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_primary_channel_provision_email(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /groups/{group-id}/team/primaryChannel/provisionEmail
        Operation type: general
        Args:
        group_id: Teams group id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_primary_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_primary_channel_remove_email(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /groups/{group-id}/team/primaryChannel/removeEmail
        Operation type: general
        Args:
        group_id: Teams group id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.primary_channel.remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_primary_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_schedule(self, group_id: str) -> TeamsResponse:

        """
        Delete navigation property schedule for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule
        Operation type: general
        Args:
        group_id: Teams group id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_schedule(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedule from groups
        Teams operation: GET /groups/{group-id}/team/schedule
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_set_schedule(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property schedule in groups
        Teams operation: PUT /groups/{group-id}/team/schedule
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.put(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_set_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_day_notes(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to dayNotes for groups
        Teams operation: POST /groups/{group-id}/team/schedule/dayNotes
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.day_notes.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_day_notes(self, group_id: str, dayNote_id: str) -> TeamsResponse:

        """
        Delete navigation property dayNotes for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        dayNote_id: Teams dayNote id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.day_notes.by_dayNote_id(dayNote_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_day_notes(self, group_id: str, dayNote_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get dayNotes from groups
        Teams operation: GET /groups/{group-id}/team/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        dayNote_id: Teams dayNote id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.day_notes.by_dayNote_id(dayNote_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_day_notes(self, group_id: str, dayNote_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property dayNotes in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        dayNote_id: Teams dayNote id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.day_notes.by_dayNote_id(dayNote_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_offer_shift_requests(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to offerShiftRequests for groups
        Teams operation: POST /groups/{group-id}/team/schedule/offerShiftRequests
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.offer_shift_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_offer_shift_requests(self, group_id: str, offerShiftRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property offerShiftRequests for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_offer_shift_requests(self, group_id: str, offerShiftRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get offerShiftRequests from groups
        Teams operation: GET /groups/{group-id}/team/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_offer_shift_requests(self, group_id: str, offerShiftRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property offerShiftRequests in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_open_shift_change_requests(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to openShiftChangeRequests for groups
        Teams operation: POST /groups/{group-id}/team/schedule/openShiftChangeRequests
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shift_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_open_shift_change_requests(self, group_id: str, openShiftChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property openShiftChangeRequests for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_open_shift_change_requests(self, group_id: str, openShiftChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShiftChangeRequests from groups
        Teams operation: GET /groups/{group-id}/team/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_open_shift_change_requests(self, group_id: str, openShiftChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShiftChangeRequests in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_open_shifts(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to openShifts for groups
        Teams operation: POST /groups/{group-id}/team/schedule/openShifts
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_open_shifts(self, group_id: str, openShift_id: str) -> TeamsResponse:

        """
        Delete navigation property openShifts for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        openShift_id: Teams openShift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shifts.by_openShift_id(openShift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_open_shifts(self, group_id: str, openShift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShifts from groups
        Teams operation: GET /groups/{group-id}/team/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        openShift_id: Teams openShift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shifts.by_openShift_id(openShift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_open_shifts(self, group_id: str, openShift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShifts in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        openShift_id: Teams openShift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.open_shifts.by_openShift_id(openShift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_scheduling_groups(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to schedulingGroups for groups
        Teams operation: POST /groups/{group-id}/team/schedule/schedulingGroups
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.scheduling_groups.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_scheduling_groups(self, group_id: str, schedulingGroup_id: str) -> TeamsResponse:

        """
        Delete navigation property schedulingGroups for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_scheduling_groups(self, group_id: str, schedulingGroup_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedulingGroups from groups
        Teams operation: GET /groups/{group-id}/team/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_scheduling_groups(self, group_id: str, schedulingGroup_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property schedulingGroups in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_shifts(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to shifts for groups
        Teams operation: POST /groups/{group-id}/team/schedule/shifts
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_shifts(self, group_id: str, shift_id: str) -> TeamsResponse:

        """
        Delete navigation property shifts for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        shift_id: Teams shift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.shifts.by_shift_id(shift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_shifts(self, group_id: str, shift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get shifts from groups
        Teams operation: GET /groups/{group-id}/team/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        shift_id: Teams shift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.shifts.by_shift_id(shift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_shifts(self, group_id: str, shift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property shifts in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        shift_id: Teams shift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.shifts.by_shift_id(shift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_swap_shifts_change_requests(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to swapShiftsChangeRequests for groups
        Teams operation: POST /groups/{group-id}/team/schedule/swapShiftsChangeRequests
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.swap_shifts_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_swap_shifts_change_requests(self, group_id: str, swapShiftsChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property swapShiftsChangeRequests for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_swap_shifts_change_requests(self, group_id: str, swapShiftsChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get swapShiftsChangeRequests from groups
        Teams operation: GET /groups/{group-id}/team/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_swap_shifts_change_requests(self, group_id: str, swapShiftsChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property swapShiftsChangeRequests in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_time_cards(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeCards for groups
        Teams operation: POST /groups/{group-id}/team/schedule/timeCards
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_schedule_time_cards_clock_in(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockIn
        Teams operation: POST /groups/{group-id}/team/schedule/timeCards/clockIn
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.clock_in.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_schedule_time_cards_clock_in: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_time_cards(self, group_id: str, timeCard_id: str) -> TeamsResponse:

        """
        Delete navigation property timeCards for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_time_cards(self, group_id: str, timeCard_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeCards from groups
        Teams operation: GET /groups/{group-id}/team/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_time_cards(self, group_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeCards in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_schedule_time_cards_time_card_clock_out(self, group_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockOut
        Teams operation: POST /groups/{group-id}/team/schedule/timeCards/{timeCard-id}/clockOut
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).clock_out.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_schedule_time_cards_time_card_clock_out: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_schedule_time_cards_time_card_confirm(self, group_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action confirm
        Teams operation: POST /groups/{group-id}/team/schedule/timeCards/{timeCard-id}/confirm
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).confirm.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_schedule_time_cards_time_card_confirm: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_schedule_time_cards_time_card_end_break(self, group_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action endBreak
        Teams operation: POST /groups/{group-id}/team/schedule/timeCards/{timeCard-id}/endBreak
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).end_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_schedule_time_cards_time_card_end_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_schedule_time_cards_time_card_start_break(self, group_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action startBreak
        Teams operation: POST /groups/{group-id}/team/schedule/timeCards/{timeCard-id}/startBreak
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_cards.by_timeCard_id(timeCard_id).start_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_schedule_time_cards_time_card_start_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_time_off_reasons(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeOffReasons for groups
        Teams operation: POST /groups/{group-id}/team/schedule/timeOffReasons
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_reasons.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_time_off_reasons(self, group_id: str, timeOffReason_id: str) -> TeamsResponse:

        """
        Delete navigation property timeOffReasons for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_time_off_reasons(self, group_id: str, timeOffReason_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffReasons from groups
        Teams operation: GET /groups/{group-id}/team/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_time_off_reasons(self, group_id: str, timeOffReason_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffReasons in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_time_off_requests(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeOffRequests for groups
        Teams operation: POST /groups/{group-id}/team/schedule/timeOffRequests
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_time_off_requests(self, group_id: str, timeOffRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property timeOffRequests for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_time_off_requests(self, group_id: str, timeOffRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffRequests from groups
        Teams operation: GET /groups/{group-id}/team/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_time_off_requests(self, group_id: str, timeOffRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffRequests in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_create_times_off(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timesOff for groups
        Teams operation: POST /groups/{group-id}/team/schedule/timesOff
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.times_off.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_create_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_delete_times_off(self, group_id: str, timeOff_id: str) -> TeamsResponse:

        """
        Delete navigation property timesOff for groups
        Teams operation: DELETE /groups/{group-id}/team/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOff_id: Teams timeOff id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.times_off.by_timesOff_id(timeOff_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_delete_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_get_times_off(self, group_id: str, timeOff_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timesOff from groups
        Teams operation: GET /groups/{group-id}/team/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOff_id: Teams timeOff id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.schedule.times_off.by_timesOff_id(timeOff_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_get_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_schedule_update_times_off(self, group_id: str, timeOff_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timesOff in groups
        Teams operation: PATCH /groups/{group-id}/team/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        timeOff_id: Teams timeOff id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.schedule.times_off.by_timesOff_id(timeOff_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_schedule_update_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_group_team_send_activity_notification(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /groups/{group-id}/team/sendActivityNotification
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_group_team_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_create_tags(self, group_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tags for groups
        Teams operation: POST /groups/{group-id}/team/tags
        Operation type: general
        Args:
        group_id: Teams group id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.tags.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_create_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_delete_tags(self, group_id: str, teamworkTag_id: str) -> TeamsResponse:

        """
        Delete navigation property tags for groups
        Teams operation: DELETE /groups/{group-id}/team/tags/{teamworkTag-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_delete_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_tags(self, group_id: str, teamworkTag_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tags from groups
        Teams operation: GET /groups/{group-id}/team/tags/{teamworkTag-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_update_tags(self, group_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tags in groups
        Teams operation: PATCH /groups/{group-id}/team/tags/{teamworkTag-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_update_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_tags_create_members(self, group_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for groups
        Teams operation: POST /groups/{group-id}/team/tags/{teamworkTag-id}/members
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_tags_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_tags_delete_members(self, group_id: str, teamworkTag_id: str, teamworkTagMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for groups
        Teams operation: DELETE /groups/{group-id}/team/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_tags_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_tags_get_members(self, group_id: str, teamworkTag_id: str, teamworkTagMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from groups
        Teams operation: GET /groups/{group-id}/team/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_tags_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_tags_update_members(self, group_id: str, teamworkTag_id: str, teamworkTagMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in groups
        Teams operation: PATCH /groups/{group-id}/team/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.groups.by_group_id(group_id).team.tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_tags_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_team_get_template(self, group_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get template from groups
        Teams operation: GET /groups/{group-id}/team/template
        Operation type: general
        Args:
        group_id: Teams group id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).team.template.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_team_get_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_transitive_member_of(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get transitiveMemberOf from groups
        Teams operation: GET /groups/{group-id}/transitiveMemberOf/{directoryObject-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_transitive_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_transitive_member_of_as_group(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /groups/{group-id}/transitiveMemberOf/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_transitive_member_of_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_transitive_members(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get transitiveMembers from groups
        Teams operation: GET /groups/{group-id}/transitiveMembers/{directoryObject-id}
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).transitive_members.by_transitiveMember_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_transitive_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def groups_get_transitive_members_as_service_principal(self, group_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.servicePrincipal
        Teams operation: GET /groups/{group-id}/transitiveMembers/{directoryObject-id}/graph.servicePrincipal
        Operation type: general
        Args:
        group_id: Teams group id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.groups.by_group_id(group_id).transitive_members.by_transitiveMember_id(directoryObject_id).graph_service_principal.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in groups_get_transitive_members_as_service_principal: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_create_joined_teams(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to joinedTeams for me
        Teams operation: POST /me/joinedTeams
        Operation type: general
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_create_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_delete_joined_teams(self, team_id: str) -> TeamsResponse:

        """
        Delete navigation property joinedTeams for me
        Teams operation: DELETE /me/joinedTeams/{team-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_delete_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_joined_teams(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get joinedTeams from me
        Teams operation: GET /me/joinedTeams/{team-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_update_joined_teams(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property joinedTeams in me
        Teams operation: PATCH /me/joinedTeams/{team-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_update_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_create_channels(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to channels for me
        Teams operation: POST /me/joinedTeams/{team-id}/channels
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_create_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_channels(self, team_id: str, channel_id: str) -> TeamsResponse:

        """
        Delete navigation property channels for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/channels/{channel-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_channels(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property channels in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/channels/{channel-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_create_all_members(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for me
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/allMembers
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_channels_channel_all_members_add(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/allMembers/add
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_channels_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_channels_channel_all_members_remove(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/allMembers/remove
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_channels_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_delete_all_members(self, team_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_update_all_members(self, team_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_get_files_folder(self, team_id: str, channel_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from me
        Teams operation: GET /me/joinedTeams/{team-id}/channels/{channel-id}/filesFolder
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_create_members(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for me
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/members
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_channels_channel_members_add(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/members/add
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_channels_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_channels_channel_members_remove(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/members/remove
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_channels_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_delete_members(self, team_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_get_members(self, team_id: str, channel_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from me
        Teams operation: GET /me/joinedTeams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_channels_update_members(self, team_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_channels_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_channels_channel_provision_email(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/provisionEmail
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_channels_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_channels_channel_remove_email(self, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /me/joinedTeams/{team-id}/channels/{channel-id}/removeEmail
        Operation type: general
        Args:
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_channels_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_group(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get group from me
        Teams operation: GET /me/joinedTeams/{team-id}/group
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_create_installed_apps(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to installedApps for me
        Teams operation: POST /me/joinedTeams/{team-id}/installedApps
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_installed_apps(self, team_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Delete navigation property installedApps for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_installed_apps(self, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installedApps from me
        Teams operation: GET /me/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_installed_apps(self, team_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_installed_apps_get_teams_app(self, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from me
        Teams operation: GET /me/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_installed_apps_get_teams_app_definition(self, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from me
        Teams operation: GET /me/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_installed_apps_teams_app_installation_upgrade(self, team_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /me/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_create_members(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for me
        Teams operation: POST /me/joinedTeams/{team-id}/members
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_members_add(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /me/joinedTeams/{team-id}/members/add
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_members_remove(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /me/joinedTeams/{team-id}/members/remove
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_members(self, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_members(self, team_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from me
        Teams operation: GET /me/joinedTeams/{team-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_members(self, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_create_operations(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to operations for me
        Teams operation: POST /me/joinedTeams/{team-id}/operations
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).operations.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_create_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_operations(self, team_id: str, teamsAsyncOperation_id: str) -> TeamsResponse:

        """
        Delete navigation property operations for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_operations(self, team_id: str, teamsAsyncOperation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get operations from me
        Teams operation: GET /me/joinedTeams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_operations(self, team_id: str, teamsAsyncOperation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property operations in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_photo(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get photo from me
        Teams operation: GET /me/joinedTeams/{team-id}/photo
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).photo.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_photo(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property photo in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/photo
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).photo.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_primary_channel(self, team_id: str) -> TeamsResponse:

        """
        Delete navigation property primaryChannel for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/primaryChannel
        Operation type: general
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_primary_channel(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get primaryChannel from me
        Teams operation: GET /me/joinedTeams/{team-id}/primaryChannel
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_primary_channel(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property primaryChannel in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/primaryChannel
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_create_all_members(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for me
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/allMembers
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_primary_channel_all_members_add(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/allMembers/add
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_primary_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_primary_channel_all_members_remove(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/allMembers/remove
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_primary_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_delete_all_members(self, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/primaryChannel/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_update_all_members(self, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/primaryChannel/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_get_files_folder(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from me
        Teams operation: GET /me/joinedTeams/{team-id}/primaryChannel/filesFolder
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_create_members(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for me
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/members
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_primary_channel_members_add(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/members/add
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_primary_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_primary_channel_members_remove(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/members/remove
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_primary_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_delete_members(self, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_get_members(self, team_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from me
        Teams operation: GET /me/joinedTeams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_primary_channel_update_members(self, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_primary_channel_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_primary_channel_provision_email(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/provisionEmail
        Operation type: general
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_primary_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_primary_channel_remove_email(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /me/joinedTeams/{team-id}/primaryChannel/removeEmail
        Operation type: general
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).primary_channel.remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_primary_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_schedule(self, team_id: str) -> TeamsResponse:

        """
        Delete navigation property schedule for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule
        Operation type: general
        Args:
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_schedule(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedule from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_set_schedule(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property schedule in me
        Teams operation: PUT /me/joinedTeams/{team-id}/schedule
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.put(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_set_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_day_notes(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to dayNotes for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/dayNotes
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_day_notes(self, team_id: str, dayNote_id: str) -> TeamsResponse:

        """
        Delete navigation property dayNotes for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_day_notes(self, team_id: str, dayNote_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get dayNotes from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_day_notes(self, team_id: str, dayNote_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property dayNotes in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_offer_shift_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to offerShiftRequests for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/offerShiftRequests
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_offer_shift_requests(self, team_id: str, offerShiftRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property offerShiftRequests for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_offer_shift_requests(self, team_id: str, offerShiftRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get offerShiftRequests from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_offer_shift_requests(self, team_id: str, offerShiftRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property offerShiftRequests in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_open_shift_change_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to openShiftChangeRequests for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/openShiftChangeRequests
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_open_shift_change_requests(self, team_id: str, openShiftChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property openShiftChangeRequests for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_open_shift_change_requests(self, team_id: str, openShiftChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShiftChangeRequests from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_open_shift_change_requests(self, team_id: str, openShiftChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShiftChangeRequests in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_open_shifts(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to openShifts for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/openShifts
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_open_shifts(self, team_id: str, openShift_id: str) -> TeamsResponse:

        """
        Delete navigation property openShifts for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_open_shifts(self, team_id: str, openShift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShifts from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_open_shifts(self, team_id: str, openShift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShifts in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_scheduling_groups(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to schedulingGroups for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/schedulingGroups
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_scheduling_groups(self, team_id: str, schedulingGroup_id: str) -> TeamsResponse:

        """
        Delete navigation property schedulingGroups for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_scheduling_groups(self, team_id: str, schedulingGroup_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedulingGroups from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_scheduling_groups(self, team_id: str, schedulingGroup_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property schedulingGroups in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_shifts(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to shifts for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/shifts
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_shifts(self, team_id: str, shift_id: str) -> TeamsResponse:

        """
        Delete navigation property shifts for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.shifts.by_shift_id(shift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_shifts(self, team_id: str, shift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get shifts from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.shifts.by_shift_id(shift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_shifts(self, team_id: str, shift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property shifts in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.shifts.by_shift_id(shift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_swap_shifts_change_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to swapShiftsChangeRequests for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_swap_shifts_change_requests(self, team_id: str, swapShiftsChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property swapShiftsChangeRequests for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_swap_shifts_change_requests(self, team_id: str, swapShiftsChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get swapShiftsChangeRequests from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_swap_shifts_change_requests(self, team_id: str, swapShiftsChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property swapShiftsChangeRequests in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_time_cards(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeCards for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeCards
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_schedule_time_cards_clock_in(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockIn
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeCards/clockIn
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.clock_in.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_schedule_time_cards_clock_in: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_time_cards(self, team_id: str, timeCard_id: str) -> TeamsResponse:

        """
        Delete navigation property timeCards for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_time_cards(self, team_id: str, timeCard_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeCards from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_time_cards(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeCards in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_schedule_time_cards_time_card_clock_out(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockOut
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/clockOut
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).clock_out.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_schedule_time_cards_time_card_clock_out: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_schedule_time_cards_time_card_confirm(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action confirm
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/confirm
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).confirm.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_schedule_time_cards_time_card_confirm: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_schedule_time_cards_time_card_end_break(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action endBreak
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/endBreak
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).end_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_schedule_time_cards_time_card_end_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_schedule_time_cards_time_card_start_break(self, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action startBreak
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/startBreak
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).start_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_schedule_time_cards_time_card_start_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_time_off_reasons(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeOffReasons for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeOffReasons
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_time_off_reasons(self, team_id: str, timeOffReason_id: str) -> TeamsResponse:

        """
        Delete navigation property timeOffReasons for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_time_off_reasons(self, team_id: str, timeOffReason_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffReasons from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_time_off_reasons(self, team_id: str, timeOffReason_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffReasons in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_time_off_requests(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeOffRequests for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timeOffRequests
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_time_off_requests(self, team_id: str, timeOffRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property timeOffRequests for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_time_off_requests(self, team_id: str, timeOffRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffRequests from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_time_off_requests(self, team_id: str, timeOffRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffRequests in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_create_times_off(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timesOff for me
        Teams operation: POST /me/joinedTeams/{team-id}/schedule/timesOff
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.times_off.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_create_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_delete_times_off(self, team_id: str, timeOff_id: str) -> TeamsResponse:

        """
        Delete navigation property timesOff for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_delete_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_get_times_off(self, team_id: str, timeOff_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timesOff from me
        Teams operation: GET /me/joinedTeams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_get_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_schedule_update_times_off(self, team_id: str, timeOff_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timesOff in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_schedule_update_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_team_send_activity_notification(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /me/joinedTeams/{team-id}/sendActivityNotification
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_team_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_create_tags(self, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tags for me
        Teams operation: POST /me/joinedTeams/{team-id}/tags
        Operation type: general
        Args:
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_create_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_delete_tags(self, team_id: str, teamworkTag_id: str) -> TeamsResponse:

        """
        Delete navigation property tags for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/tags/{teamworkTag-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_delete_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_tags(self, team_id: str, teamworkTag_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tags from me
        Teams operation: GET /me/joinedTeams/{team-id}/tags/{teamworkTag-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_update_tags(self, team_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tags in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/tags/{teamworkTag-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_update_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_tags_create_members(self, team_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for me
        Teams operation: POST /me/joinedTeams/{team-id}/tags/{teamworkTag-id}/members
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_tags_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_tags_delete_members(self, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for me
        Teams operation: DELETE /me/joinedTeams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_tags_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_tags_get_members(self, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from me
        Teams operation: GET /me/joinedTeams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_tags_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_tags_update_members(self, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in me
        Teams operation: PATCH /me/joinedTeams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_tags_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_joined_teams_get_template(self, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get template from me
        Teams operation: GET /me/joinedTeams/{team-id}/template
        Operation type: general
        Args:
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.joined_teams.by_joinedTeam_id(team_id).template.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_joined_teams_get_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_license_details_get_teams_licensing_details(self, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Invoke function getTeamsLicensingDetails
        Teams operation: GET /me/licenseDetails/getTeamsLicensingDetails()
        Operation type: general
        Args:
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.license_details.get_teams_licensing_details.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_license_details_get_teams_licensing_details: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_member_of(self, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get memberOf from me
        Teams operation: GET /me/memberOf/{directoryObject-id}
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.member_of.by_memberOf_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_member_of_as_directory_role(self, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.directoryRole
        Teams operation: GET /me/memberOf/{directoryObject-id}/graph.directoryRole
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.member_of.by_memberOf_id(directoryObject_id).graph_directory_role.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_member_of_as_directory_role: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_member_of_as_group(self, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /me/memberOf/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.member_of.by_memberOf_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_member_of_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_create_scoped_role_member_of(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to scopedRoleMemberOf for me
        Teams operation: POST /me/scopedRoleMemberOf
        Operation type: general
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.scoped_role_member_of.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_create_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_delete_scoped_role_member_of(self, scopedRoleMembership_id: str) -> TeamsResponse:

        """
        Delete navigation property scopedRoleMemberOf for me
        Teams operation: DELETE /me/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: general
        Args:
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_delete_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_scoped_role_member_of(self, scopedRoleMembership_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get scopedRoleMemberOf from me
        Teams operation: GET /me/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: general
        Args:
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_update_scoped_role_member_of(self, scopedRoleMembership_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property scopedRoleMemberOf in me
        Teams operation: PATCH /me/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: general
        Args:
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.me.scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_update_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_transitive_member_of(self, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get transitiveMemberOf from me
        Teams operation: GET /me/transitiveMemberOf/{directoryObject-id}
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_transitive_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_transitive_member_of_as_directory_role(self, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.directoryRole
        Teams operation: GET /me/transitiveMemberOf/{directoryObject-id}/graph.directoryRole
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).graph_directory_role.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_transitive_member_of_as_directory_role: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def me_get_transitive_member_of_as_group(self, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /me/transitiveMemberOf/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.me.transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in me_get_transitive_member_of_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def organization_organization_check_member_objects(self, organization_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /organization/{organization-id}/checkMemberObjects
        Operation type: general
        Args:
        organization_id: Teams organization id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.organization.by_organization_id(organization_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in organization_organization_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def privacy_subject_rights_requests_get_team(self, subjectRightsRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get team from privacy
        Teams operation: GET /privacy/subjectRightsRequests/{subjectRightsRequest-id}/team
        Operation type: general
        Args:
        subjectRightsRequest_id: Teams subjectRightsRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.privacy.subject_rights_requests.by_subjectRightsRequest_id(subjectRightsRequest_id).team.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in privacy_subject_rights_requests_get_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def reports_get_teams_team_activity_detail_391d(self, date: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Invoke function getTeamsTeamActivityDetail
        Teams operation: GET /reports/getTeamsTeamActivityDetail(date={date})
        Operation type: general
        Args:
        date: Teams path parameter: date
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.reports.get_teams_team_activity_detail_date_date.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in reports_get_teams_team_activity_detail_391d: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def reports_get_teams_team_activity_detail_ee18(self, period: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Invoke function getTeamsTeamActivityDetail
        Teams operation: GET /reports/getTeamsTeamActivityDetail(period='{period}')
        Operation type: general
        Args:
        period: Teams path parameter: period
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.reports.get_teams_team_activity_detail_period_period.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in reports_get_teams_team_activity_detail_ee18: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def reports_get_teams_user_activity_user_detail_fba7(self, date: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Invoke function getTeamsUserActivityUserDetail
        Teams operation: GET /reports/getTeamsUserActivityUserDetail(date={date})
        Operation type: general
        Args:
        date: Teams path parameter: date
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.reports.get_teams_user_activity_user_detail_date_date.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in reports_get_teams_user_activity_user_detail_fba7: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def reports_get_teams_user_activity_user_detail_7554(self, period: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Invoke function getTeamsUserActivityUserDetail
        Teams operation: GET /reports/getTeamsUserActivityUserDetail(period='{period}')
        Operation type: general
        Args:
        period: Teams path parameter: period
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.reports.get_teams_user_activity_user_detail_period_period.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in reports_get_teams_user_activity_user_detail_7554: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def scoped_role_memberships_scoped_role_membership_create_scoped_role_membership(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add new entity to scopedRoleMemberships
        Teams operation: POST /scopedRoleMemberships
        Operation type: general
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.scoped_role_memberships.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in scoped_role_memberships_scoped_role_membership_create_scoped_role_membership: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def scoped_role_memberships_scoped_role_membership_delete_scoped_role_membership(self, scopedRoleMembership_id: str) -> TeamsResponse:

        """
        Delete entity from scopedRoleMemberships
        Teams operation: DELETE /scopedRoleMemberships/{scopedRoleMembership-id}
        Operation type: general
        Args:
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.scoped_role_memberships.by_scopedRoleMembership_id(scopedRoleMembership_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in scoped_role_memberships_scoped_role_membership_delete_scoped_role_membership: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def scoped_role_memberships_scoped_role_membership_get_scoped_role_membership(self, scopedRoleMembership_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get entity from scopedRoleMemberships by key
        Teams operation: GET /scopedRoleMemberships/{scopedRoleMembership-id}
        Operation type: general
        Args:
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.scoped_role_memberships.by_scopedRoleMembership_id(scopedRoleMembership_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in scoped_role_memberships_scoped_role_membership_get_scoped_role_membership: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def scoped_role_memberships_scoped_role_membership_update_scoped_role_membership(self, scopedRoleMembership_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update entity in scopedRoleMemberships
        Teams operation: PATCH /scopedRoleMemberships/{scopedRoleMembership-id}
        Operation type: general
        Args:
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.scoped_role_memberships.by_scopedRoleMembership_id(scopedRoleMembership_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in scoped_role_memberships_scoped_role_membership_update_scoped_role_membership: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_team_create_team(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create team
        Teams operation: POST /teams
        Operation type: general
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_team_create_team: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_templates_teams_template_create_teams_template(self, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Add new entity to teamsTemplates
        Teams operation: POST /teamsTemplates
        Operation type: general
        Args:
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams_templates.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_templates_teams_template_create_teams_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_templates_teams_template_delete_teams_template(self, teamsTemplate_id: str) -> TeamsResponse:

        """
        Delete entity from teamsTemplates
        Teams operation: DELETE /teamsTemplates/{teamsTemplate-id}
        Operation type: general
        Args:
        teamsTemplate_id: Teams teamsTemplate id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams_templates.by_teamsTemplate_id(teamsTemplate_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_templates_teams_template_delete_teams_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_templates_teams_template_get_teams_template(self, teamsTemplate_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get entity from teamsTemplates by key
        Teams operation: GET /teamsTemplates/{teamsTemplate-id}
        Operation type: general
        Args:
        teamsTemplate_id: Teams teamsTemplate id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.teams_templates.by_teamsTemplate_id(teamsTemplate_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_templates_teams_template_get_teams_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def teams_templates_teams_template_update_teams_template(self, teamsTemplate_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update entity in teamsTemplates
        Teams operation: PATCH /teamsTemplates/{teamsTemplate-id}
        Operation type: general
        Args:
        teamsTemplate_id: Teams teamsTemplate id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.teams_templates.by_teamsTemplate_id(teamsTemplate_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in teams_templates_teams_template_update_teams_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def tenant_relationships_multi_tenant_organization_delete_tenants(self, multiTenantOrganizationMember_id: str) -> TeamsResponse:

        """
        Remove multiTenantOrganizationMember
        Teams operation: DELETE /tenantRelationships/multiTenantOrganization/tenants/{multiTenantOrganizationMember-id}
        Operation type: general
        Args:
        multiTenantOrganizationMember_id: Teams multiTenantOrganizationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in tenant_relationships_multi_tenant_organization_delete_tenants: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def tenant_relationships_multi_tenant_organization_get_tenants(self, multiTenantOrganizationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get multiTenantOrganizationMember
        Teams operation: GET /tenantRelationships/multiTenantOrganization/tenants/{multiTenantOrganizationMember-id}
        Operation type: general
        Args:
        multiTenantOrganizationMember_id: Teams multiTenantOrganizationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in tenant_relationships_multi_tenant_organization_get_tenants: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def tenant_relationships_multi_tenant_organization_update_tenants(self, multiTenantOrganizationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tenants in tenantRelationships
        Teams operation: PATCH /tenantRelationships/multiTenantOrganization/tenants/{multiTenantOrganizationMember-id}
        Operation type: general
        Args:
        multiTenantOrganizationMember_id: Teams multiTenantOrganizationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.tenant_relationships.multi_tenant_organization.tenants.by_tenant_id(multiTenantOrganizationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in tenant_relationships_multi_tenant_organization_update_tenants: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_check_member_objects(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action checkMemberObjects
        Teams operation: POST /users/{user-id}/checkMemberObjects
        Operation type: general
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).check_member_objects.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_check_member_objects: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_create_joined_teams(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to joinedTeams for users
        Teams operation: POST /users/{user-id}/joinedTeams
        Operation type: general
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_create_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_delete_joined_teams(self, user_id: str, team_id: str) -> TeamsResponse:

        """
        Delete navigation property joinedTeams for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_delete_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_joined_teams(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get joinedTeams from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_update_joined_teams(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property joinedTeams in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_update_joined_teams: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_create_channels(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to channels for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_create_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_channels(self, user_id: str, team_id: str, channel_id: str) -> TeamsResponse:

        """
        Delete navigation property channels for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_channels(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property channels in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_channels: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_create_all_members(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/allMembers
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_channels_channel_all_members_add(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/allMembers/add
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_channels_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_channels_channel_all_members_remove(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/allMembers/remove
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_channels_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_delete_all_members(self, user_id: str, team_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_update_all_members(self, user_id: str, team_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_get_files_folder(self, user_id: str, team_id: str, channel_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/filesFolder
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_create_members(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/members
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_channels_channel_members_add(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/members/add
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_channels_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_channels_channel_members_remove(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/members/remove
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_channels_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_delete_members(self, user_id: str, team_id: str, channel_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_get_members(self, user_id: str, team_id: str, channel_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = ChannelsRequestBuilder.ChannelsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = ChannelsRequestBuilder.ChannelsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_channels_update_members(self, user_id: str, team_id: str, channel_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_channels_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_channels_channel_provision_email(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/provisionEmail
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_channels_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_channels_channel_remove_email(self, user_id: str, team_id: str, channel_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/channels/{channel-id}/removeEmail
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        channel_id: Teams channel id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).channels.by_channel_id(channel_id).remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_channels_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_group(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get group from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/group
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_group: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_create_installed_apps(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to installedApps for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/installedApps
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_create_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_installed_apps(self, user_id: str, team_id: str, teamsAppInstallation_id: str) -> TeamsResponse:

        """
        Delete navigation property installedApps for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_installed_apps(self, user_id: str, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get installedApps from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_installed_apps(self, user_id: str, team_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property installedApps in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_installed_apps: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_installed_apps_get_teams_app(self, user_id: str, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsApp from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}/teamsApp
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_installed_apps_get_teams_app: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_installed_apps_get_teams_app_definition(self, user_id: str, team_id: str, teamsAppInstallation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get teamsAppDefinition from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}/teamsAppDefinition
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = TeamsRequestBuilder.TeamsRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = TeamsRequestBuilder.TeamsRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).teams_app_definition.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_installed_apps_get_teams_app_definition: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_installed_apps_teams_app_installation_upgrade(self, user_id: str, team_id: str, teamsAppInstallation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action upgrade
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/installedApps/{teamsAppInstallation-id}/upgrade
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAppInstallation_id: Teams teamsAppInstallation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).installed_apps.by_installed_app_id(teamsAppInstallation_id).upgrade.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_installed_apps_teams_app_installation_upgrade: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_create_members(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/members
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_members_add(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/members/add
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_members_remove(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/members/remove
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_members(self, user_id: str, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_members(self, user_id: str, team_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_members(self, user_id: str, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_create_operations(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to operations for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/operations
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).operations.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_create_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_operations(self, user_id: str, team_id: str, teamsAsyncOperation_id: str) -> TeamsResponse:

        """
        Delete navigation property operations for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_operations(self, user_id: str, team_id: str, teamsAsyncOperation_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get operations from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_operations(self, user_id: str, team_id: str, teamsAsyncOperation_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property operations in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/operations/{teamsAsyncOperation-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamsAsyncOperation_id: Teams teamsAsyncOperation id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).operations.by_operation_id(teamsAsyncOperation_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_operations: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_photo(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get photo from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/photo
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).photo.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_photo(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property photo in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/photo
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).photo.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_photo: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_primary_channel(self, user_id: str, team_id: str) -> TeamsResponse:

        """
        Delete navigation property primaryChannel for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/primaryChannel
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_primary_channel(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get primaryChannel from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/primaryChannel
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_primary_channel(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property primaryChannel in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/primaryChannel
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_primary_channel: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_create_all_members(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to allMembers for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/allMembers
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_create_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_primary_channel_all_members_add(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/allMembers/add
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_primary_channel_all_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_primary_channel_all_members_remove(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/allMembers/remove
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_primary_channel_all_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_delete_all_members(self, user_id: str, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property allMembers for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/primaryChannel/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.by_allMember_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_delete_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_update_all_members(self, user_id: str, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property allMembers in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/primaryChannel/allMembers/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.all_members.by_allMember_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_update_all_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_get_files_folder(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get filesFolder from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/primaryChannel/filesFolder
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.files_folder.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_get_files_folder: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_create_members(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/members
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_primary_channel_members_add(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action add
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/members/add
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.members.add.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_primary_channel_members_add: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_primary_channel_members_remove(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action remove
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/members/remove
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.members.remove.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_primary_channel_members_remove: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_delete_members(self, user_id: str, team_id: str, conversationMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_get_members(self, user_id: str, team_id: str, conversationMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_primary_channel_update_members(self, user_id: str, team_id: str, conversationMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/primaryChannel/members/{conversationMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        conversationMember_id: Teams conversationMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.members.by_membership_id(conversationMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_primary_channel_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_primary_channel_provision_email(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action provisionEmail
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/provisionEmail
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.provision_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_primary_channel_provision_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_primary_channel_remove_email(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action removeEmail
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/primaryChannel/removeEmail
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).primary_channel.remove_email.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_primary_channel_remove_email: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_schedule(self, user_id: str, team_id: str) -> TeamsResponse:

        """
        Delete navigation property schedule for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_schedule(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedule from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_set_schedule(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property schedule in users
        Teams operation: PUT /users/{user-id}/joinedTeams/{team-id}/schedule
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.put(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_set_schedule: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_day_notes(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to dayNotes for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/dayNotes
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_day_notes(self, user_id: str, team_id: str, dayNote_id: str) -> TeamsResponse:

        """
        Delete navigation property dayNotes for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_day_notes(self, user_id: str, team_id: str, dayNote_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get dayNotes from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_day_notes(self, user_id: str, team_id: str, dayNote_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property dayNotes in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/dayNotes/{dayNote-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        dayNote_id: Teams dayNote id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.day_notes.by_dayNote_id(dayNote_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_day_notes: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_offer_shift_requests(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to offerShiftRequests for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/offerShiftRequests
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_offer_shift_requests(self, user_id: str, team_id: str, offerShiftRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property offerShiftRequests for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_offer_shift_requests(self, user_id: str, team_id: str, offerShiftRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get offerShiftRequests from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_offer_shift_requests(self, user_id: str, team_id: str, offerShiftRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property offerShiftRequests in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/offerShiftRequests/{offerShiftRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        offerShiftRequest_id: Teams offerShiftRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.offer_shift_requests.by_offerShiftRequest_id(offerShiftRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_offer_shift_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_open_shift_change_requests(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to openShiftChangeRequests for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/openShiftChangeRequests
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_open_shift_change_requests(self, user_id: str, team_id: str, openShiftChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property openShiftChangeRequests for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_open_shift_change_requests(self, user_id: str, team_id: str, openShiftChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShiftChangeRequests from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_open_shift_change_requests(self, user_id: str, team_id: str, openShiftChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShiftChangeRequests in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/openShiftChangeRequests/{openShiftChangeRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        openShiftChangeRequest_id: Teams openShiftChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shift_change_requests.by_openShiftChangeRequest_id(openShiftChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_open_shift_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_open_shifts(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to openShifts for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/openShifts
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_open_shifts(self, user_id: str, team_id: str, openShift_id: str) -> TeamsResponse:

        """
        Delete navigation property openShifts for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_open_shifts(self, user_id: str, team_id: str, openShift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get openShifts from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_open_shifts(self, user_id: str, team_id: str, openShift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property openShifts in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/openShifts/{openShift-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        openShift_id: Teams openShift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.open_shifts.by_openShift_id(openShift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_open_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_scheduling_groups(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to schedulingGroups for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/schedulingGroups
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_scheduling_groups(self, user_id: str, team_id: str, schedulingGroup_id: str) -> TeamsResponse:

        """
        Delete navigation property schedulingGroups for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_scheduling_groups(self, user_id: str, team_id: str, schedulingGroup_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get schedulingGroups from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_scheduling_groups(self, user_id: str, team_id: str, schedulingGroup_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property schedulingGroups in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/schedulingGroups/{schedulingGroup-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        schedulingGroup_id: Teams schedulingGroup id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.scheduling_groups.by_schedulingGroup_id(schedulingGroup_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_scheduling_groups: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_shifts(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to shifts for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/shifts
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.shifts.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_shifts(self, user_id: str, team_id: str, shift_id: str) -> TeamsResponse:

        """
        Delete navigation property shifts for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.shifts.by_shift_id(shift_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_shifts(self, user_id: str, team_id: str, shift_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get shifts from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.shifts.by_shift_id(shift_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_shifts(self, user_id: str, team_id: str, shift_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property shifts in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/shifts/{shift-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        shift_id: Teams shift id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.shifts.by_shift_id(shift_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_shifts: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_swap_shifts_change_requests(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to swapShiftsChangeRequests for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_swap_shifts_change_requests(self, user_id: str, team_id: str, swapShiftsChangeRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property swapShiftsChangeRequests for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_swap_shifts_change_requests(self, user_id: str, team_id: str, swapShiftsChangeRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get swapShiftsChangeRequests from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_swap_shifts_change_requests(self, user_id: str, team_id: str, swapShiftsChangeRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property swapShiftsChangeRequests in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/swapShiftsChangeRequests/{swapShiftsChangeRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        swapShiftsChangeRequest_id: Teams swapShiftsChangeRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.swap_shifts_change_requests.by_swapShiftsChangeRequest_id(swapShiftsChangeRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_swap_shifts_change_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_time_cards(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeCards for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_schedule_time_cards_clock_in(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockIn
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/clockIn
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.clock_in.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_schedule_time_cards_clock_in: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_time_cards(self, user_id: str, team_id: str, timeCard_id: str) -> TeamsResponse:

        """
        Delete navigation property timeCards for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_time_cards(self, user_id: str, team_id: str, timeCard_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeCards from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_time_cards(self, user_id: str, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeCards in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_time_cards: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_schedule_time_cards_time_card_clock_out(self, user_id: str, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action clockOut
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/clockOut
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).clock_out.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_schedule_time_cards_time_card_clock_out: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_schedule_time_cards_time_card_confirm(self, user_id: str, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action confirm
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/confirm
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).confirm.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_schedule_time_cards_time_card_confirm: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_schedule_time_cards_time_card_end_break(self, user_id: str, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action endBreak
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/endBreak
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).end_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_schedule_time_cards_time_card_end_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_schedule_time_cards_time_card_start_break(self, user_id: str, team_id: str, timeCard_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action startBreak
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeCards/{timeCard-id}/startBreak
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeCard_id: Teams timeCard id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_cards.by_timeCard_id(timeCard_id).start_break.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_schedule_time_cards_time_card_start_break: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_time_off_reasons(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeOffReasons for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffReasons
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_time_off_reasons(self, user_id: str, team_id: str, timeOffReason_id: str) -> TeamsResponse:

        """
        Delete navigation property timeOffReasons for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_time_off_reasons(self, user_id: str, team_id: str, timeOffReason_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffReasons from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_time_off_reasons(self, user_id: str, team_id: str, timeOffReason_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffReasons in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffReasons/{timeOffReason-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOffReason_id: Teams timeOffReason id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_reasons.by_timeOffReason_id(timeOffReason_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_time_off_reasons: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_time_off_requests(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timeOffRequests for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffRequests
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_time_off_requests(self, user_id: str, team_id: str, timeOffRequest_id: str) -> TeamsResponse:

        """
        Delete navigation property timeOffRequests for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_time_off_requests(self, user_id: str, team_id: str, timeOffRequest_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timeOffRequests from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_time_off_requests(self, user_id: str, team_id: str, timeOffRequest_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timeOffRequests in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/timeOffRequests/{timeOffRequest-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOffRequest_id: Teams timeOffRequest id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.time_off_requests.by_timeOffRequest_id(timeOffRequest_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_time_off_requests: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_create_times_off(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to timesOff for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/schedule/timesOff
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.times_off.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_create_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_delete_times_off(self, user_id: str, team_id: str, timeOff_id: str) -> TeamsResponse:

        """
        Delete navigation property timesOff for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_delete_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_get_times_off(self, user_id: str, team_id: str, timeOff_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get timesOff from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_get_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_schedule_update_times_off(self, user_id: str, team_id: str, timeOff_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property timesOff in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/schedule/timesOff/{timeOff-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        timeOff_id: Teams timeOff id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).schedule.times_off.by_timesOff_id(timeOff_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_schedule_update_times_off: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_joined_teams_team_send_activity_notification(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Invoke action sendActivityNotification
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/sendActivityNotification
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).send_activity_notification.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_joined_teams_team_send_activity_notification: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_create_tags(self, user_id: str, team_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to tags for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/tags
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_create_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_delete_tags(self, user_id: str, team_id: str, teamworkTag_id: str) -> TeamsResponse:

        """
        Delete navigation property tags for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_delete_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_tags(self, user_id: str, team_id: str, teamworkTag_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get tags from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_update_tags(self, user_id: str, team_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property tags in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_update_tags: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_tags_create_members(self, user_id: str, team_id: str, teamworkTag_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to members for users
        Teams operation: POST /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}/members
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_tags_create_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_tags_delete_members(self, user_id: str, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str) -> TeamsResponse:

        """
        Delete navigation property members for users
        Teams operation: DELETE /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_tags_delete_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_tags_get_members(self, user_id: str, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get members from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = MembersRequestBuilder.MembersRequestBuilderGetQueryParameters()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = MembersRequestBuilder.MembersRequestBuilderGetRequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_tags_get_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_tags_update_members(self, user_id: str, team_id: str, teamworkTag_id: str, teamworkTagMember_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property members in users
        Teams operation: PATCH /users/{user-id}/joinedTeams/{team-id}/tags/{teamworkTag-id}/members/{teamworkTagMember-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        teamworkTag_id: Teams teamworkTag id identifier
        teamworkTagMember_id: Teams teamworkTagMember id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).tags.by_tag_id(teamworkTag_id).members.by_membership_id(teamworkTagMember_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_tags_update_members: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_joined_teams_get_template(self, user_id: str, team_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get template from users
        Teams operation: GET /users/{user-id}/joinedTeams/{team-id}/template
        Operation type: general
        Args:
        user_id: Teams user id identifier
        team_id: Teams team id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).joined_teams.by_joinedTeam_id(team_id).template.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_joined_teams_get_template: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_user_license_details_get_teams_licensing_details(self, user_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Invoke function getTeamsLicensingDetails
        Teams operation: GET /users/{user-id}/licenseDetails/getTeamsLicensingDetails()
        Operation type: general
        Args:
        user_id: Teams user id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).license_details.get_teams_licensing_details.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_user_license_details_get_teams_licensing_details: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_member_of(self, user_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get memberOf from users
        Teams operation: GET /users/{user-id}/memberOf/{directoryObject-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).member_of.by_memberOf_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_member_of_as_directory_role(self, user_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.directoryRole
        Teams operation: GET /users/{user-id}/memberOf/{directoryObject-id}/graph.directoryRole
        Operation type: general
        Args:
        user_id: Teams user id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).member_of.by_memberOf_id(directoryObject_id).graph_directory_role.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_member_of_as_directory_role: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_create_scoped_role_member_of(self, user_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Create new navigation property to scopedRoleMemberOf for users
        Teams operation: POST /users/{user-id}/scopedRoleMemberOf
        Operation type: general
        Args:
        user_id: Teams user id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.post(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_create_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_delete_scoped_role_member_of(self, user_id: str, scopedRoleMembership_id: str) -> TeamsResponse:

        """
        Delete navigation property scopedRoleMemberOf for users
        Teams operation: DELETE /users/{user-id}/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).delete()
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_delete_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_scoped_role_member_of(self, user_id: str, scopedRoleMembership_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get scopedRoleMemberOf from users
        Teams operation: GET /users/{user-id}/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_update_scoped_role_member_of(self, user_id: str, scopedRoleMembership_id: str, body: Optional[Dict[str, Any]] = None) -> TeamsResponse:

        """
        Update the navigation property scopedRoleMemberOf in users
        Teams operation: PATCH /users/{user-id}/scopedRoleMemberOf/{scopedRoleMembership-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        scopedRoleMembership_id: Teams scopedRoleMembership id identifier
        body: Request body data
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            response = await self.client.users.by_user_id(user_id).scoped_role_member_of.by_scopedRoleMemberOf_id(scopedRoleMembership_id).patch(body=body)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_update_scoped_role_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_transitive_member_of(self, user_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get transitiveMemberOf from users
        Teams operation: GET /users/{user-id}/transitiveMemberOf/{directoryObject-id}
        Operation type: general
        Args:
        user_id: Teams user id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_transitive_member_of: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_transitive_member_of_as_directory_role(self, user_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.directoryRole
        Teams operation: GET /users/{user-id}/transitiveMemberOf/{directoryObject-id}/graph.directoryRole
        Operation type: general
        Args:
        user_id: Teams user id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).graph_directory_role.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_transitive_member_of_as_directory_role: {e}")
            return TeamsResponse(success=False, error=str(e))


    async def users_get_transitive_member_of_as_group(self, user_id: str, directoryObject_id: str, select: Optional[List[str]] = None, expand: Optional[List[str]] = None, filter: Optional[str] = None, orderby: Optional[List[str]] = None, search: Optional[str] = None, top: Optional[int] = None, skip: Optional[int] = None) -> TeamsResponse:

        """
        Get the item of type microsoft.graph.directoryObject as microsoft.graph.group
        Teams operation: GET /users/{user-id}/transitiveMemberOf/{directoryObject-id}/graph.group
        Operation type: general
        Args:
        user_id: Teams user id identifier
        directoryObject_id: Teams directoryObject id identifier
        select: Select specific fields
        expand: Expand related entities
        filter: Filter results
        orderby: Order results
        search: Search in results
        top: Limit number of results
        skip: Skip number of results
        Returns:
            TeamsResponse: Teams API response with success status and data
        """
        try:
            # Build query parameters
            query_params = RequestConfiguration()
            if select:
                query_params.select = select
            if expand:
                query_params.expand = expand
            if filter:
                query_params.filter = filter
            if orderby:
                query_params.orderby = orderby
            if search:
                query_params.search = search
            if top:
                query_params.top = top
            if skip:
                query_params.skip = skip
            config = RequestConfiguration(query_parameters=query_params)
            response = await self.client.users.by_user_id(user_id).transitive_member_of.by_transitiveMemberOf_id(directoryObject_id).graph_group.get(request_configuration=config)
            return self._handle_teams_response(response)
        except Exception as e:
            logger.error(f"Error in users_get_transitive_member_of_as_group: {e}")
            return TeamsResponse(success=False, error=str(e))



