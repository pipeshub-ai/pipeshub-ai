import logging
from typing import Any, Dict, List, Optional

from app.sources.client.slack.slack import SlackClient, SlackResponse

# Set up logger
logger = logging.getLogger(__name__)

class SlackDataSource:
    """Auto-generated Slack Web API client wrapper.
    - Uses the **official** SDK client passed as `SlackClient`
    - **Snake_case** method names; internally call SDK aliases (camelCase preserved)
    - **No fallback** to `api_call`: if SDK alias missing, returns SlackResponse with error
    - All responses wrapped in standardized SlackResponse format
    """
    def __init__(self, client: SlackClient) -> None:
        self.client = client.get_web_client()

    async def _handle_slack_response(self, response: Any) -> SlackResponse:  # noqa: ANN401
        """Handle Slack API response and convert to standardized format"""
        try:
            if not response:
                return SlackResponse(success=False, error="Empty response from Slack API")
            # Extract data from SlackResponse object
            if hasattr(response, 'data'):
                data = response.data
            elif hasattr(response, 'get'):
                # Handle dict-like responses
                data = dict(response)
            else:
                data = {"raw_response": str(response)}

            # Check if response indicates success
            success = True
            error_msg = None

            # Most Slack API responses have an 'ok' field
            if isinstance(data, dict):
                if 'ok' in data:
                    success = data.get('ok', False)
                    if not success:
                        error_msg = data.get('error', 'Unknown Slack API error')
                elif 'error' in data:
                    success = False
                    error_msg = data.get('error')
            return SlackResponse(
                success=success,
                data=data,
                error=error_msg
            )
        except Exception as e:
            logger.error(f"Error handling Slack response: {e}")
            return SlackResponse(success=False, error=str(e))

    async def _handle_slack_error(self, error: Exception) -> SlackResponse:
        """Handle Slack API errors and convert to standardized format"""
        error_msg = str(error)
        logger.error(f"Slack API error: {error_msg}")

        # Provide more specific error messages for common token issues
        if "not_allowed_token_type" in error_msg:
            return SlackResponse(
                success=False,
                error="Slack token type not allowed for this operation. Please ensure you're using a bot token (xoxb-) with the required scopes. For search operations, you need the 'search:read' scope."
            )
        elif "invalid_auth" in error_msg:
            return SlackResponse(
                success=False,
                error="Invalid Slack token. Please check your token configuration."
            )
        elif "missing_scope" in error_msg:
            return SlackResponse(
                success=False,
                error="Missing required Slack scope. Please add the necessary scopes to your bot token."
            )
        elif "account_inactive" in error_msg:
            return SlackResponse(
                success=False,
                error="Slack account is inactive. Please check your workspace status."
            )
        elif "token_revoked" in error_msg:
            return SlackResponse(
                success=False,
                error="Slack token has been revoked. Please generate a new token."
            )
        elif "channel_not_found" in error_msg:
            return SlackResponse(
                success=False,
                error="Channel not found. The channel may not exist, be private, or the bot may not have access to it."
            )
        elif "not_in_channel" in error_msg:
            return SlackResponse(
                success=False,
                error="Bot is not a member of this channel. Please invite the bot to the channel first."
            )

        return SlackResponse(success=False, error=error_msg)

    async def check_token_scopes(self) -> SlackResponse:
        """Check what scopes the current token has access to"""
        try:
            # Use auth.test to get token info
            response = getattr(self.client, 'auth_test')()
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)


    async def admin_apps_approve(self,
        *,
        app_id: Optional[str] = None,
        request_id: Optional[str] = None,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_apps_approve

        Slack method: `admin_apps_approve`  (HTTP POST /admin.apps.approve)
        Requires scope: `admin.apps:write`

        Args:
            app_id (optional): The id of the app to approve.
            request_id (optional): The id of the request to approve.
            team_id (optional): The ID of the team.
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_apps_approve`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if app_id is not None:
            kwargs_api['app_id'] = app_id
        if request_id is not None:
            kwargs_api['request_id'] = request_id
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_apps_approve') or not callable(getattr(self.client, 'admin_apps_approve')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_apps_approve"
            )

        try:
            response = getattr(self.client, 'admin_apps_approve')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_apps_approved_list(self,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        team_id: Optional[str] = None,
        enterprise_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_apps_approved_list

        Slack method: `admin_apps_approved_list`  (HTTP GET /admin.apps.approved.list)
        Requires scope: `admin.apps:read`
        Args:
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page
            team_id (optional): The ID of the team.
            enterprise_id (optional): The ID of the enterprise.
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_apps_approved_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if enterprise_id is not None:
            kwargs_api['enterprise_id'] = enterprise_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_apps_approved_list') or not callable(getattr(self.client, 'admin_apps_approved_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_apps_approved_list"
            )

        try:
            response = getattr(self.client, 'admin_apps_approved_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_apps_requests_list(self,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_apps_requests_list

        Slack method: `admin_apps_requests_list`  (HTTP GET /admin.apps.requests.list)
        Requires scope: `admin.apps:read`
        Args:
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page
            team_id (optional): The ID of the team.
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_apps_requests_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_apps_requests_list') or not callable(getattr(self.client, 'admin_apps_requests_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_apps_requests_list"
            )

        try:
            response = getattr(self.client, 'admin_apps_requests_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_apps_restrict(self,
        *,
        app_id: Optional[str] = None,
        request_id: Optional[str] = None,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_apps_restrict

        Slack method: `admin_apps_restrict`  (HTTP POST /admin.apps.restrict)

        Requires scope: `admin.apps:write`
        Args:
            app_id (optional): The id of the app to restrict.
            request_id (optional): The id of the request to restrict.
            team_id (optional): The ID of the team.
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_apps_restrict`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if app_id is not None:
            kwargs_api['app_id'] = app_id
        if request_id is not None:
            kwargs_api['request_id'] = request_id
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_apps_restrict') or not callable(getattr(self.client, 'admin_apps_restrict')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_apps_restrict"
            )

        try:
            response = getattr(self.client, 'admin_apps_restrict')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_apps_restricted_list(self,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        team_id: Optional[str] = None,
        enterprise_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_apps_restricted_list

        Slack method: `admin_apps_restricted_list`  (HTTP GET /admin.apps.restricted.list)

        Requires scope: `admin.apps:read`
        Args:
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page
            team_id (optional): The ID of the team.
            enterprise_id (optional): The ID of the enterprise.
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_apps_restricted_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if enterprise_id is not None:
            kwargs_api['enterprise_id'] = enterprise_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_apps_restricted_list') or not callable(getattr(self.client, 'admin_apps_restricted_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_apps_restricted_list"
            )

        try:
            response = getattr(self.client, 'admin_apps_restricted_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_archive(self,
        *,
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_archive

        Slack method: `admin_conversations_archive`  (HTTP POST /admin.conversations.archive)
        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to archive.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_archive`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_archive') or not callable(getattr(self.client, 'admin_conversations_archive')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_archive"
            )

        try:
            response = getattr(self.client, 'admin_conversations_archive')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_convert_to_private(self,
        *,
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_convertToPrivate

        Slack method: `admin_conversations_convertToPrivate`  (HTTP POST /admin.conversations.convertToPrivate)
        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to convert to private.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_convertToPrivate`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_convertToPrivate') or not callable(getattr(self.client, 'admin_conversations_convertToPrivate')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_convertToPrivate"
            )

        try:
            response = getattr(self.client, 'admin_conversations_convertToPrivate')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_create(self,
        *,
        name: str,
        is_private: bool,
        description: Optional[str] = None,
        org_wide: Optional[bool] = None,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_create

        Slack method: `admin_conversations_create`  (HTTP POST /admin.conversations.create)

        Requires scope: `admin.conversations:write`
        Args:
            name (required): Name of the public or private channel to create.
            description (optional): Description of the public or private channel to create.
            is_private (required): When `true`, creates a private channel instead of a public channel
            org_wide (optional): When `true`, the channel will be available org-wide. Note: if the channel is not `org_wide=true`, you must specify a `team_id` for this channel
            team_id (optional): The workspace to create the channel in. Note: this argument is required unless you set `org_wide=true`.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_create`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if name is not None:
            kwargs_api['name'] = name
        if description is not None:
            kwargs_api['description'] = description
        if is_private is not None:
            kwargs_api['is_private'] = is_private
        if org_wide is not None:
            kwargs_api['org_wide'] = org_wide
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_create') or not callable(getattr(self.client, 'admin_conversations_create')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_create"
            )

        try:
            response = getattr(self.client, 'admin_conversations_create')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_delete(self,
        *,
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_delete

        Slack method: `admin_conversations_delete`  (HTTP POST /admin.conversations.delete)

        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to delete.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_delete`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_delete') or not callable(getattr(self.client, 'admin_conversations_delete')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_delete"
            )

        try:
            response = getattr(self.client, 'admin_conversations_delete')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_disconnect_shared(self,
        *,
        channel_id: str,
        leaving_team_ids: Optional[List[str]] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_disconnectShared

        Slack method: `admin_conversations_disconnectShared`  (HTTP POST /admin.conversations.disconnectShared)

        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to be disconnected from some workspaces.
            leaving_team_ids (optional): The team to be removed from the channel. Currently only a single team id can be specified.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_disconnectShared`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if leaving_team_ids is not None:
            kwargs_api['leaving_team_ids'] = leaving_team_ids
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_disconnectShared') or not callable(getattr(self.client, 'admin_conversations_disconnectShared')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_disconnectShared"
            )

        try:
            response = getattr(self.client, 'admin_conversations_disconnectShared')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_ekm_list_original_connected_channel_info(self,
        *,
        channel_ids: Optional[List[str]] = None,
        team_ids: Optional[List[str]] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_ekm_listOriginalConnectedChannelInfo

        Slack method: `admin_conversations_ekm_listOriginalConnectedChannelInfo`  (HTTP GET /admin.conversations.ekm.listOriginalConnectedChannelInfo)
        Requires scope: `admin.conversations:read`
        Args:
            channel_ids (optional): A comma-separated list of channels to filter to.
            team_ids (optional): A comma-separated list of the workspaces to which the channels you would like returned belong.
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_ekm_listOriginalConnectedChannelInfo`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel_ids is not None:
            kwargs_api['channel_ids'] = channel_ids
        if team_ids is not None:
            kwargs_api['team_ids'] = team_ids
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_ekm_listOriginalConnectedChannelInfo') or not callable(getattr(self.client, 'admin_conversations_ekm_listOriginalConnectedChannelInfo')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_ekm_listOriginalConnectedChannelInfo"
            )

        try:
            response = getattr(self.client, 'admin_conversations_ekm_listOriginalConnectedChannelInfo')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_get_conversation_prefs(self,
        *,
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_getConversationPrefs

        Slack method: `admin_conversations_getConversationPrefs`  (HTTP GET /admin.conversations.getConversationPrefs)

        Requires scope: `admin.conversations:read`
        Args:
            channel_id (required): The channel to get preferences for.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_getConversationPrefs`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)
        if not hasattr(self.client, 'admin_conversations_getConversationPrefs') or not callable(getattr(self.client, 'admin_conversations_getConversationPrefs')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_getConversationPrefs"
            )

        try:
            response = getattr(self.client, 'admin_conversations_getConversationPrefs')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_get_teams(self,
        *,
        channel_id: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_getTeams

        Slack method: `admin_conversations_getTeams`  (HTTP GET /admin.conversations.getTeams)

        Requires scope: `admin.conversations:read`
        Args:
            channel_id (required): The channel to determine connected workspaces within the organization for.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_getTeams`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_getTeams') or not callable(getattr(self.client, 'admin_conversations_getTeams')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_getTeams"
            )

        try:
            response = getattr(self.client, 'admin_conversations_getTeams')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_invite(self,
        *,
        user_ids: List[str],
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_invite

        Slack method: `admin_conversations_invite`  (HTTP POST /admin.conversations.invite)
        Requires scope: `admin.conversations:write`
        Args:
            user_ids (required): The users to invite.
            channel_id (required): The channel that the users will be invited to.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_invite`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user_ids is not None:
            kwargs_api['user_ids'] = user_ids
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_invite') or not callable(getattr(self.client, 'admin_conversations_invite')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_invite"
            )

        try:
            response = getattr(self.client, 'admin_conversations_invite')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_rename(self,
        *,
        channel_id: str,
        name: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_rename

        Slack method: `admin_conversations_rename`  (HTTP POST /admin.conversations.rename)

        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to rename.
            name (required):
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_rename`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if name is not None:
            kwargs_api['name'] = name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_rename') or not callable(getattr(self.client, 'admin_conversations_rename')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_rename"
            )

        try:
            response = getattr(self.client, 'admin_conversations_rename')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_restrict_access_add_group(self,
        *,
        group_id: str,
        channel_id: str,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_restrictAccess_addGroup

        Slack method: `admin_conversations_restrictAccess_addGroup`  (HTTP POST /admin.conversations.restrictAccess.addGroup)

        Requires scope: `admin.conversations:write`
        Args:
            team_id (optional): The workspace where the channel exists. This argument is required for channels only tied to one workspace, and optional for channels that are shared across an organization.
            group_id (required): The [IDP Group](https://slack.com/help/articles/115001435788-Connect-identity-provider-groups-to-your-Enterprise-Grid-org) ID to be an allowlist for the private channel.
            channel_id (required): The channel to link this group to.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_restrictAccess_addGroup`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if group_id is not None:
            kwargs_api['group_id'] = group_id
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_restrictAccess_addGroup') or not callable(getattr(self.client, 'admin_conversations_restrictAccess_addGroup')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_restrictAccess_addGroup"
            )

        try:
            response = getattr(self.client, 'admin_conversations_restrictAccess_addGroup')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_restrict_access_list_groups(self,
        *,
        channel_id: str,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_restrictAccess_listGroups

        Slack method: `admin_conversations_restrictAccess_listGroups`  (HTTP GET /admin.conversations.restrictAccess.listGroups)

        Requires scope: `admin.conversations:read`
        Args:
            channel_id (required): The channel to list groups for.
            team_id (optional): The workspace where the channel exists. This argument is required for channels only tied to one workspace, and optional for channels that are shared across an organization.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_restrictAccess_listGroups`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_restrictAccess_listGroups') or not callable(getattr(self.client, 'admin_conversations_restrictAccess_listGroups')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_restrictAccess_listGroups"
            )

        try:
            response = getattr(self.client, 'admin_conversations_restrictAccess_listGroups')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_restrict_access_remove_group(self,
        *,
        team_id: str,
        group_id: str,
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_restrictAccess_removeGroup

        Slack method: `admin_conversations_restrictAccess_removeGroup`  (HTTP POST /admin.conversations.restrictAccess.removeGroup)

        Requires scope: `admin.conversations:write`
        Args:
            team_id (required): The workspace where the channel exists. This argument is required for channels only tied to one workspace, and optional for channels that are shared across an organization.
            group_id (required): The [IDP Group](https://slack.com/help/articles/115001435788-Connect-identity-provider-groups-to-your-Enterprise-Grid-org) ID to remove from the private channel.
            channel_id (required): The channel to remove the linked group from.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_restrictAccess_removeGroup`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if group_id is not None:
            kwargs_api['group_id'] = group_id
        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_restrictAccess_removeGroup') or not callable(getattr(self.client, 'admin_conversations_restrictAccess_removeGroup')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_restrictAccess_removeGroup"
            )

        try:
            response = getattr(self.client, 'admin_conversations_restrictAccess_removeGroup')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_search(self,
        *,
        team_ids: Optional[List[str]] = None,
        query: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        search_channel_types: Optional[str] = None,
        sort: Optional[str] = None,
        sort_dir: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_search

        Slack method: `admin_conversations_search`  (HTTP GET /admin.conversations.search)

        Requires scope: `admin.conversations:read`
        Args:
            team_ids (optional): Comma separated string of team IDs, signifying the workspaces to search through.
            query (optional): Name of the the channel to query by.
            limit (optional): Maximum number of items to be returned. Must be between 1 - 20 both inclusive. Default is 10.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page.
            search_channel_types (optional): The type of channel to include or exclude in the search. For example `private` will search private channels, while `private_exclude` will exclude them. For a full list of types, check the [Types section](#types).
            sort (optional): Possible values are `relevant` (search ranking based on what we think is closest), `name` (alphabetical), `member_count` (number of users in the channel), and `created` (date channel was created). You can optionally pair this with the `sort_dir` arg to change how it is sorted
            sort_dir (optional): Sort direction. Possible values are `asc` for ascending order like (1, 2, 3) or (a, b, c), and `desc` for descending order like (3, 2, 1) or (c, b, a)

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_search`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_ids is not None:
            kwargs_api['team_ids'] = team_ids
        if query is not None:
            kwargs_api['query'] = query
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if search_channel_types is not None:
            kwargs_api['search_channel_types'] = search_channel_types
        if sort is not None:
            kwargs_api['sort'] = sort
        if sort_dir is not None:
            kwargs_api['sort_dir'] = sort_dir
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_search') or not callable(getattr(self.client, 'admin_conversations_search')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_search"
            )

        try:
            response = getattr(self.client, 'admin_conversations_search')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_set_conversation_prefs(self,
        *,
        channel_id: str,
        prefs: Dict[str, Any],
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_setConversationPrefs

        Slack method: `admin_conversations_setConversationPrefs`  (HTTP POST /admin.conversations.setConversationPrefs)

        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to set the prefs for
            prefs (required): The prefs for this channel in a stringified JSON format.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_setConversationPrefs`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if prefs is not None:
            kwargs_api['prefs'] = prefs
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_setConversationPrefs') or not callable(getattr(self.client, 'admin_conversations_setConversationPrefs')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_setConversationPrefs"
            )

        try:
            response = getattr(self.client, 'admin_conversations_setConversationPrefs')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_set_teams(self,
        *,
        channel_id: str,
        team_id: Optional[str] = None,
        target_team_ids: Optional[List[str]] = None,
        org_channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_setTeams

        Slack method: `admin_conversations_setTeams`  (HTTP POST /admin.conversations.setTeams)

        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The encoded `channel_id` to add or remove to workspaces.
            team_id (optional): The workspace to which the channel belongs. Omit this argument if the channel is a cross-workspace shared channel.
            target_team_ids (optional): A comma-separated list of workspaces to which the channel should be shared. Not required if the channel is being shared org-wide.
            org_channel (optional): True if channel has to be converted to an org channel

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_setTeams`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if target_team_ids is not None:
            kwargs_api['target_team_ids'] = target_team_ids
        if org_channel is not None:
            kwargs_api['org_channel'] = org_channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_setTeams') or not callable(getattr(self.client, 'admin_conversations_setTeams')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_setTeams"
            )

        try:
            response = getattr(self.client, 'admin_conversations_setTeams')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_conversations_unarchive(self,
        *,
        channel_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_conversations_unarchive

        Slack method: `admin_conversations_unarchive`  (HTTP POST /admin.conversations.unarchive)

        Requires scope: `admin.conversations:write`
        Args:
            channel_id (required): The channel to unarchive.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_conversations_unarchive`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel_id is not None:
            kwargs_api['channel_id'] = channel_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_conversations_unarchive') or not callable(getattr(self.client, 'admin_conversations_unarchive')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_conversations_unarchive"
            )

        try:
            response = getattr(self.client, 'admin_conversations_unarchive')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_emoji_add(self,
        *,
        name: str,
        url: str,
        **kwargs
    ) -> SlackResponse:
        """admin_emoji_add

        Slack method: `admin_emoji_add`  (HTTP POST /admin.emoji.add)

        Requires scope: `admin.teams:write`
        Args:
            name (required): The name of the emoji to be removed. Colons (`:myemoji:`) around the value are not required, although they may be included.
            url (required): The URL of a file to use as an image for the emoji. Square images under 128KB and with transparent backgrounds work best.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_emoji_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if name is not None:
            kwargs_api['name'] = name
        if url is not None:
            kwargs_api['url'] = url
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_emoji_add') or not callable(getattr(self.client, 'admin_emoji_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_emoji_add"
            )

        try:
            response = getattr(self.client, 'admin_emoji_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_emoji_add_alias(self,
        *,
        name: str,
        alias_for: str,
        **kwargs
    ) -> SlackResponse:
        """admin_emoji_addAlias

        Slack method: `admin_emoji_addAlias`  (HTTP POST /admin.emoji.addAlias)
        Requires scope: `admin.teams:write`
        Args:
            name (required): The name of the emoji to be aliased. Colons (`:myemoji:`) around the value are not required, although they may be included.
            alias_for (required): The alias of the emoji.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_emoji_addAlias`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if name is not None:
            kwargs_api['name'] = name
        if alias_for is not None:
            kwargs_api['alias_for'] = alias_for
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_emoji_addAlias') or not callable(getattr(self.client, 'admin_emoji_addAlias')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_emoji_addAlias"
            )

        try:
            response = getattr(self.client, 'admin_emoji_addAlias')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_emoji_list(self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_emoji_list

        Slack method: `admin_emoji_list`  (HTTP GET /admin.emoji.list)

        Requires scope: `admin.teams:read`
        Args:
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_emoji_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_emoji_list') or not callable(getattr(self.client, 'admin_emoji_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_emoji_list"
            )

        try:
            response = getattr(self.client, 'admin_emoji_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_emoji_remove(self,
        *,
        name: str,
        **kwargs
    ) -> SlackResponse:
        """admin_emoji_remove

        Slack method: `admin_emoji_remove`  (HTTP POST /admin.emoji.remove)

        Requires scope: `admin.teams:write`
        Args:
            name (required): The name of the emoji to be removed. Colons (`:myemoji:`) around the value are not required, although they may be included.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_emoji_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if name is not None:
            kwargs_api['name'] = name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_emoji_remove') or not callable(getattr(self.client, 'admin_emoji_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_emoji_remove"
            )

        try:
            response = getattr(self.client, 'admin_emoji_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_emoji_rename(self,
        *,
        name: str,
        new_name: str,
        **kwargs
    ) -> SlackResponse:
        """admin_emoji_rename

        Slack method: `admin_emoji_rename`  (HTTP POST /admin.emoji.rename)

        Requires scope: `admin.teams:write`
        Args:
            name (required): The name of the emoji to be renamed. Colons (`:myemoji:`) around the value are not required, although they may be included.
            new_name (required): The new name of the emoji.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_emoji_rename`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if name is not None:
            kwargs_api['name'] = name
        if new_name is not None:
            kwargs_api['new_name'] = new_name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_emoji_rename') or not callable(getattr(self.client, 'admin_emoji_rename')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_emoji_rename"
            )

        try:
            response = getattr(self.client, 'admin_emoji_rename')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_invite_requests_approve(self,
        *,
        invite_request_id: str,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_inviteRequests_approve

        Slack method: `admin_inviteRequests_approve`  (HTTP POST /admin.inviteRequests.approve)
        Requires scope: `admin.invites:write`
        Args:
            team_id (optional): ID for the workspace where the invite request was made.
            invite_request_id (required): ID of the request to invite.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_inviteRequests_approve`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if invite_request_id is not None:
            kwargs_api['invite_request_id'] = invite_request_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_inviteRequests_approve') or not callable(getattr(self.client, 'admin_inviteRequests_approve')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_inviteRequests_approve"
            )

        try:
            response = getattr(self.client, 'admin_inviteRequests_approve')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_invite_requests_approved_list(self,
        *,
        team_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_inviteRequests_approved_list

        Slack method: `admin_inviteRequests_approved_list`  (HTTP GET /admin.inviteRequests.approved.list)

        Requires scope: `admin.invites:read`
        Args:
            team_id (optional): ID for the workspace where the invite requests were made.
            cursor (optional): Value of the `next_cursor` field sent as part of the previous API response
            limit (optional): The number of results that will be returned by the API on each invocation. Must be between 1 - 1000, both inclusive

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_inviteRequests_approved_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_inviteRequests_approved_list') or not callable(getattr(self.client, 'admin_inviteRequests_approved_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_inviteRequests_approved_list"
            )

        try:
            response = getattr(self.client, 'admin_inviteRequests_approved_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_invite_requests_denied_list(self,
        *,
        team_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_inviteRequests_denied_list

        Slack method: `admin_inviteRequests_denied_list`  (HTTP GET /admin.inviteRequests.denied.list)

        Requires scope: `admin.invites:read`
        Args:
            team_id (optional): ID for the workspace where the invite requests were made.
            cursor (optional): Value of the `next_cursor` field sent as part of the previous api response
            limit (optional): The number of results that will be returned by the API on each invocation. Must be between 1 - 1000 both inclusive

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_inviteRequests_denied_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_inviteRequests_denied_list') or not callable(getattr(self.client, 'admin_inviteRequests_denied_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_inviteRequests_denied_list"
            )

        try:
            response = getattr(self.client, 'admin_inviteRequests_denied_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_invite_requests_deny(self,
        *,
        invite_request_id: str,
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_inviteRequests_deny

        Slack method: `admin_inviteRequests_deny`  (HTTP POST /admin.inviteRequests.deny)

        Requires scope: `admin.invites:write`
        Args:
            team_id (optional): ID for the workspace where the invite request was made.
            invite_request_id (required): ID of the request to invite.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_inviteRequests_deny`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if invite_request_id is not None:
            kwargs_api['invite_request_id'] = invite_request_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_inviteRequests_deny') or not callable(getattr(self.client, 'admin_inviteRequests_deny')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_inviteRequests_deny"
            )

        try:
            response = getattr(self.client, 'admin_inviteRequests_deny')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_invite_requests_list(self,
        *,
        team_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_inviteRequests_list

        Slack method: `admin_inviteRequests_list`  (HTTP GET /admin.inviteRequests.list)

        Requires scope: `admin.invites:read`
        Args:
            team_id (optional): ID for the workspace where the invite requests were made.
            cursor (optional): Value of the `next_cursor` field sent as part of the previous API response
            limit (optional): The number of results that will be returned by the API on each invocation. Must be between 1 - 1000, both inclusive

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_inviteRequests_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_inviteRequests_list') or not callable(getattr(self.client, 'admin_inviteRequests_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_inviteRequests_list"
            )

        try:
            response = getattr(self.client, 'admin_inviteRequests_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_admins_list(self,
        *,
        team_id: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_admins_list

        Slack method: `admin_teams_admins_list`  (HTTP GET /admin.teams.admins.list)

        Requires scope: `admin.teams:read`
        Args:
            limit (optional): The maximum number of items to return.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page.
            team_id (required):
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_admins_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_admins_list') or not callable(getattr(self.client, 'admin_teams_admins_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_admins_list"
            )

        try:
            response = getattr(self.client, 'admin_teams_admins_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_create(self,
        *,
        team_domain: str,
        team_name: str,
        team_description: Optional[str] = None,
        team_discoverability: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_create

        Slack method: `admin_teams_create`  (HTTP POST /admin.teams.create)

        Requires scope: `admin.teams:write`
        Args:
            team_domain (required): Team domain (for example, slacksoftballteam).
            team_name (required): Team name (for example, Slack Softball Team).
            team_description (optional): Description for the team.
            team_discoverability (optional): Who can join the team. A team's discoverability can be `open`, `closed`, `invite_only`, or `unlisted`.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_create`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_domain is not None:
            kwargs_api['team_domain'] = team_domain
        if team_name is not None:
            kwargs_api['team_name'] = team_name
        if team_description is not None:
            kwargs_api['team_description'] = team_description
        if team_discoverability is not None:
            kwargs_api['team_discoverability'] = team_discoverability
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_create') or not callable(getattr(self.client, 'admin_teams_create')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_create"
            )

        try:
            response = getattr(self.client, 'admin_teams_create')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_list(self,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_list

        Slack method: `admin_teams_list`  (HTTP GET /admin.teams.list)

        Requires scope: `admin.teams:read`
        Args:
            limit (optional): The maximum number of items to return. Must be between 1 - 100 both inclusive.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_list') or not callable(getattr(self.client, 'admin_teams_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_list"
            )

        try:
            response = getattr(self.client, 'admin_teams_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_owners_list(self,
        *,
        team_id: str,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_owners_list

        Slack method: `admin_teams_owners_list`  (HTTP GET /admin.teams.owners.list)

        Requires scope: `admin.teams:read`
        Args:
            team_id (required):
            limit (optional): The maximum number of items to return. Must be between 1 - 1000 both inclusive.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_owners_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_owners_list') or not callable(getattr(self.client, 'admin_teams_owners_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_owners_list"
            )

        try:
            response = getattr(self.client, 'admin_teams_owners_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_settings_info(self,
        *,
        team_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_settings_info

        Slack method: `admin_teams_settings_info`  (HTTP GET /admin.teams.settings.info)

        Requires scope: `admin.teams:read`
        Args:
            team_id (required):
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_settings_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_settings_info') or not callable(getattr(self.client, 'admin_teams_settings_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_settings_info"
            )

        try:
            response = getattr(self.client, 'admin_teams_settings_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_settings_set_default_channels(self,
        *,
        team_id: str,
        channel_ids: List[str],
        **kwargs
    ) -> SlackResponse:
        """admin_teams_settings_setDefaultChannels

        Slack method: `admin_teams_settings_setDefaultChannels`  (HTTP POST /admin.teams.settings.setDefaultChannels)

        Requires scope: `admin.teams:write`
        Args:
            team_id (required): ID for the workspace to set the default channel for.
            channel_ids (required): An array of channel IDs.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_settings_setDefaultChannels`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if channel_ids is not None:
            kwargs_api['channel_ids'] = channel_ids
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_settings_setDefaultChannels') or not callable(getattr(self.client, 'admin_teams_settings_setDefaultChannels')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_settings_setDefaultChannels"
            )

        try:
            response = getattr(self.client, 'admin_teams_settings_setDefaultChannels')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_settings_set_description(self,
        *,
        team_id: str,
        description: str,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_settings_setDescription

        Slack method: `admin_teams_settings_setDescription`  (HTTP POST /admin.teams.settings.setDescription)

        Requires scope: `admin.teams:write`
        Args:
            team_id (required): ID for the workspace to set the description for.
            description (required): The new description for the workspace.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_settings_setDescription`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if description is not None:
            kwargs_api['description'] = description
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_settings_setDescription') or not callable(getattr(self.client, 'admin_teams_settings_setDescription')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_settings_setDescription"
            )

        try:
            response = getattr(self.client, 'admin_teams_settings_setDescription')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_settings_set_discoverability(self,
        *,
        team_id: str,
        discoverability: str,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_settings_setDiscoverability

        Slack method: `admin_teams_settings_setDiscoverability`  (HTTP POST /admin.teams.settings.setDiscoverability)

        Requires scope: `admin.teams:write`
        Args:
            team_id (required): The ID of the workspace to set discoverability on.
            discoverability (required): This workspace's discovery setting. It must be set to one of `open`, `invite_only`, `closed`, or `unlisted`.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_settings_setDiscoverability`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if discoverability is not None:
            kwargs_api['discoverability'] = discoverability
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_settings_setDiscoverability') or not callable(getattr(self.client, 'admin_teams_settings_setDiscoverability')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_settings_setDiscoverability"
            )

        try:
            response = getattr(self.client, 'admin_teams_settings_setDiscoverability')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_settings_set_icon(self,
        *,
        image_url: str,
        team_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_settings_setIcon

        Slack method: `admin_teams_settings_setIcon`  (HTTP POST /admin.teams.settings.setIcon)

        Requires scope: `admin.teams:write`
        Args:
            image_url (required): Image URL for the icon
            team_id (required): ID for the workspace to set the icon for.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_settings_setIcon`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if image_url is not None:
            kwargs_api['image_url'] = image_url
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_settings_setIcon') or not callable(getattr(self.client, 'admin_teams_settings_setIcon')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_settings_setIcon"
            )

        try:
            response = getattr(self.client, 'admin_teams_settings_setIcon')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_teams_settings_set_name(self,
        *,
        team_id: str,
        name: str,
        **kwargs
    ) -> SlackResponse:
        """admin_teams_settings_setName

        Slack method: `admin_teams_settings_setName`  (HTTP POST /admin.teams.settings.setName)

        Requires scope: `admin.teams:write`
        Args:
            team_id (required): ID for the workspace to set the name for.
            name (required): The new name of the workspace.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_teams_settings_setName`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if name is not None:
            kwargs_api['name'] = name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_teams_settings_setName') or not callable(getattr(self.client, 'admin_teams_settings_setName')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_teams_settings_setName"
            )

        try:
            response = getattr(self.client, 'admin_teams_settings_setName')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_usergroups_add_channels(self,
        *,
        usergroup_id: str,
        channel_ids: List[str],
        team_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_usergroups_addChannels

        Slack method: `admin_usergroups_addChannels`  (HTTP POST /admin.usergroups.addChannels)

        Requires scope: `admin.usergroups:write`
        Args:
            usergroup_id (required): ID of the IDP group to add default channels for.
            team_id (optional): The workspace to add default channels in.
            channel_ids (required): Comma separated string of channel IDs.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_usergroups_addChannels`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if usergroup_id is not None:
            kwargs_api['usergroup_id'] = usergroup_id
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if channel_ids is not None:
            kwargs_api['channel_ids'] = channel_ids
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_usergroups_addChannels') or not callable(getattr(self.client, 'admin_usergroups_addChannels')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_usergroups_addChannels"
            )

        try:
            response = getattr(self.client, 'admin_usergroups_addChannels')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_usergroups_add_teams(self,
        *,
        usergroup_id: str,
        team_ids: List[str],
        auto_provision: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_usergroups_addTeams

        Slack method: `admin_usergroups_addTeams`  (HTTP POST /admin.usergroups.addTeams)

        Requires scope: `admin.teams:write`
        Args:
            usergroup_id (required): An encoded usergroup (IDP Group) ID.
            team_ids (required): A comma separated list of encoded team (workspace) IDs. Each workspace *MUST* belong to the organization associated with the token.
            auto_provision (optional): When `true`, this method automatically creates new workspace accounts for the IDP group members.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_usergroups_addTeams`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if usergroup_id is not None:
            kwargs_api['usergroup_id'] = usergroup_id
        if team_ids is not None:
            kwargs_api['team_ids'] = team_ids
        if auto_provision is not None:
            kwargs_api['auto_provision'] = auto_provision
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_usergroups_addTeams') or not callable(getattr(self.client, 'admin_usergroups_addTeams')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_usergroups_addTeams"
            )

        try:
            response = getattr(self.client, 'admin_usergroups_addTeams')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_usergroups_list_channels(self,
        *,
        usergroup_id: str,
        team_id: Optional[str] = None,
        include_num_members: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_usergroups_listChannels

        Slack method: `admin_usergroups_listChannels`  (HTTP GET /admin.usergroups.listChannels)

        Requires scope: `admin.usergroups:read`
        Args:
            usergroup_id (required): ID of the IDP group to list default channels for.
            team_id (optional): ID of the the workspace.
            include_num_members (optional): Flag to include or exclude the count of members per channel.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_usergroups_listChannels`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if usergroup_id is not None:
            kwargs_api['usergroup_id'] = usergroup_id
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if include_num_members is not None:
            kwargs_api['include_num_members'] = include_num_members
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_usergroups_listChannels') or not callable(getattr(self.client, 'admin_usergroups_listChannels')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_usergroups_listChannels"
            )

        try:
            response = getattr(self.client, 'admin_usergroups_listChannels')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_usergroups_remove_channels(self,
        *,
        usergroup_id: str,
        channel_ids: List[str],
        **kwargs
    ) -> SlackResponse:
        """admin_usergroups_removeChannels

        Slack method: `admin_usergroups_removeChannels`  (HTTP POST /admin.usergroups.removeChannels)

        Requires scope: `admin.usergroups:write`
        Args:
            usergroup_id (required): ID of the IDP Group
            channel_ids (required): Comma-separated string of channel IDs

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_usergroups_removeChannels`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if usergroup_id is not None:
            kwargs_api['usergroup_id'] = usergroup_id
        if channel_ids is not None:
            kwargs_api['channel_ids'] = channel_ids
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_usergroups_removeChannels') or not callable(getattr(self.client, 'admin_usergroups_removeChannels')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_usergroups_removeChannels"
            )

        try:
            response = getattr(self.client, 'admin_usergroups_removeChannels')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_assign(self,
        *,
        team_id: str,
        user_id: str,
        is_restricted: Optional[bool] = None,
        is_ultra_restricted: Optional[bool] = None,
        channel_ids: Optional[List[str]] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_users_assign

        Slack method: `admin_users_assign`  (HTTP POST /admin.users.assign)
        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            user_id (required): The ID of the user to add to the workspace.
            is_restricted (optional): True if user should be added to the workspace as a guest.
            is_ultra_restricted (optional): True if user should be added to the workspace as a single-channel guest.
            channel_ids (optional): Comma separated values of channel IDs to add user in the new workspace.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_assign`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if is_restricted is not None:
            kwargs_api['is_restricted'] = is_restricted
        if is_ultra_restricted is not None:
            kwargs_api['is_ultra_restricted'] = is_ultra_restricted
        if channel_ids is not None:
            kwargs_api['channel_ids'] = channel_ids
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_assign') or not callable(getattr(self.client, 'admin_users_assign')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_assign"
            )

        try:
            response = getattr(self.client, 'admin_users_assign')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_invite(self,
        *,
        team_id: str,
        email: str,
        channel_ids: List[str],
        custom_message: Optional[str] = None,
        real_name: Optional[str] = None,
        resend: Optional[bool] = None,
        is_restricted: Optional[bool] = None,
        is_ultra_restricted: Optional[bool] = None,
        guest_expiration_ts: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_users_invite

        Slack method: `admin_users_invite`  (HTTP POST /admin.users.invite)

        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            email (required): The email address of the person to invite.
            channel_ids (required): A comma-separated list of `channel_id`s for this user to join. At least one channel is required.
            custom_message (optional): An optional message to send to the user in the invite email.
            real_name (optional): Full name of the user.
            resend (optional): Allow this invite to be resent in the future if a user has not signed up yet. (default: false)
            is_restricted (optional): Is this user a multi-channel guest user? (default: false)
            is_ultra_restricted (optional): Is this user a single channel guest user? (default: false)
            guest_expiration_ts (optional): Timestamp when guest account should be disabled. Only include this timestamp if you are inviting a guest user and you want their account to expire on a certain date.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_invite`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if email is not None:
            kwargs_api['email'] = email
        if channel_ids is not None:
            kwargs_api['channel_ids'] = channel_ids
        if custom_message is not None:
            kwargs_api['custom_message'] = custom_message
        if real_name is not None:
            kwargs_api['real_name'] = real_name
        if resend is not None:
            kwargs_api['resend'] = resend
        if is_restricted is not None:
            kwargs_api['is_restricted'] = is_restricted
        if is_ultra_restricted is not None:
            kwargs_api['is_ultra_restricted'] = is_ultra_restricted
        if guest_expiration_ts is not None:
            kwargs_api['guest_expiration_ts'] = guest_expiration_ts
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_invite') or not callable(getattr(self.client, 'admin_users_invite')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_invite"
            )

        try:
            response = getattr(self.client, 'admin_users_invite')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_list(self,
        *,
        team_id: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_users_list

        Slack method: `admin_users_list`  (HTTP GET /admin.users.list)
        Requires scope: `admin.users:read`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            cursor (optional): Set `cursor` to `next_cursor` returned by the previous call to list items in the next page.
            limit (optional): Limit for how many users to be retrieved per page

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_list') or not callable(getattr(self.client, 'admin_users_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_list"
            )

        try:
            response = getattr(self.client, 'admin_users_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_remove(self,
        *,
        team_id: str,
        user_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_users_remove

        Slack method: `admin_users_remove`  (HTTP POST /admin.users.remove)
        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            user_id (required): The ID of the user to remove.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_remove') or not callable(getattr(self.client, 'admin_users_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_remove"
            )

        try:
            response = getattr(self.client, 'admin_users_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_session_invalidate(self,
        *,
        team_id: str,
        session_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_users_session_invalidate

        Slack method: `admin_users_session_invalidate`  (HTTP POST /admin.users.session.invalidate)
        Requires scope: `admin.users:write`
        Args:
            team_id (required): ID of the team that the session belongs to
            session_id (required):
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_session_invalidate`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if session_id is not None:
            kwargs_api['session_id'] = session_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_session_invalidate') or not callable(getattr(self.client, 'admin_users_session_invalidate')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_session_invalidate"
            )

        try:
            response = getattr(self.client, 'admin_users_session_invalidate')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_session_reset(self,
        *,
        user_id: str,
        mobile_only: Optional[bool] = None,
        web_only: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """admin_users_session_reset

        Slack method: `admin_users_session_reset`  (HTTP POST /admin.users.session.reset)

        Requires scope: `admin.users:write`
        Args:
            user_id (required): The ID of the user to wipe sessions for
            mobile_only (optional): Only expire mobile sessions (default: false)
            web_only (optional): Only expire web sessions (default: false)

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_session_reset`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if mobile_only is not None:
            kwargs_api['mobile_only'] = mobile_only
        if web_only is not None:
            kwargs_api['web_only'] = web_only
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_session_reset') or not callable(getattr(self.client, 'admin_users_session_reset')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_session_reset"
            )

        try:
            response = getattr(self.client, 'admin_users_session_reset')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_set_admin(self,
        *,
        team_id: str,
        user_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_users_setAdmin

        Slack method: `admin_users_setAdmin`  (HTTP POST /admin.users.setAdmin)

        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            user_id (required): The ID of the user to designate as an admin.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_setAdmin`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_setAdmin') or not callable(getattr(self.client, 'admin_users_setAdmin')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_setAdmin"
            )

        try:
            response = getattr(self.client, 'admin_users_setAdmin')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_set_expiration(self,
        *,
        team_id: str,
        user_id: str,
        expiration_ts: str,
        **kwargs
    ) -> SlackResponse:
        """admin_users_setExpiration

        Slack method: `admin_users_setExpiration`  (HTTP POST /admin.users.setExpiration)

        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            user_id (required): The ID of the user to set an expiration for.
            expiration_ts (required): Timestamp when guest account should be disabled.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_setExpiration`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if expiration_ts is not None:
            kwargs_api['expiration_ts'] = expiration_ts
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_setExpiration') or not callable(getattr(self.client, 'admin_users_setExpiration')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_setExpiration"
            )

        try:
            response = getattr(self.client, 'admin_users_setExpiration')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_set_owner(self,
        *,
        team_id: str,
        user_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_users_setOwner

        Slack method: `admin_users_setOwner`  (HTTP POST /admin.users.setOwner)

        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            user_id (required): Id of the user to promote to owner.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_setOwner`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_setOwner') or not callable(getattr(self.client, 'admin_users_setOwner')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_setOwner"
            )

        try:
            response = getattr(self.client, 'admin_users_setOwner')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def admin_users_set_regular(self,
        *,
        team_id: str,
        user_id: str,
        **kwargs
    ) -> SlackResponse:
        """admin_users_setRegular

        Slack method: `admin_users_setRegular`  (HTTP POST /admin.users.setRegular)
        Requires scope: `admin.users:write`
        Args:
            team_id (required): The ID (`T1234`) of the workspace.
            user_id (required): The ID of the user to designate as a regular user.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.admin_users_setRegular`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'admin_users_setRegular') or not callable(getattr(self.client, 'admin_users_setRegular')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: admin_users_setRegular"
            )

        try:
            response = getattr(self.client, 'admin_users_setRegular')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def api_test(self,
        *,
        error: Optional[str] = None,
        foo: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """api_test

        Slack method: `api_test`  (HTTP GET /api.test)
        Requires scope: `none`
        Args:
            error (optional): Error response to return
            foo (optional): example property to return

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.api_test`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if error is not None:
            kwargs_api['error'] = error
        if foo is not None:
            kwargs_api['foo'] = foo
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'api_test') or not callable(getattr(self.client, 'api_test')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: api_test"
            )

        try:
            response = getattr(self.client, 'api_test')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_event_authorizations_list(self,
        *,
        event_context: str,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """apps_event_authorizations_list

        Slack method: `apps_event_authorizations_list`  (HTTP GET /apps.event.authorizations.list)

        Requires scope: `authorizations:read`
        Args:
            event_context (required):
            cursor (optional):
            limit (optional):
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_event_authorizations_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if event_context is not None:
            kwargs_api['event_context'] = event_context
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_event_authorizations_list') or not callable(getattr(self.client, 'apps_event_authorizations_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_event_authorizations_list"
            )

        try:
            response = getattr(self.client, 'apps_event_authorizations_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_permissions_info(self, **kwargs) -> SlackResponse:
        """apps_permissions_info

        Slack method: `apps_permissions_info`  (HTTP GET /apps.permissions.info)

        Requires scope: `none`
        Args:

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_permissions_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_permissions_info') or not callable(getattr(self.client, 'apps_permissions_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_permissions_info"
            )

        try:
            response = getattr(self.client, 'apps_permissions_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_permissions_request(self,
        *,
        scopes: str,
        trigger_id: str,
        **kwargs
    ) -> SlackResponse:
        """apps_permissions_request

        Slack method: `apps_permissions_request`  (HTTP GET /apps.permissions.request)

        Requires scope: `none`
        Args:
            scopes (required): A comma separated list of scopes to request for
            trigger_id (required): Token used to trigger the permissions API

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_permissions_request`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if scopes is not None:
            kwargs_api['scopes'] = scopes
        if trigger_id is not None:
            kwargs_api['trigger_id'] = trigger_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_permissions_request') or not callable(getattr(self.client, 'apps_permissions_request')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_permissions_request"
            )

        try:
            response = getattr(self.client, 'apps_permissions_request')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_permissions_resources_list(self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """apps_permissions_resources_list

        Slack method: `apps_permissions_resources_list`  (HTTP GET /apps.permissions.resources.list)

        Requires scope: `none`
        Args:
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.
            limit (optional): The maximum number of items to return.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_permissions_resources_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_permissions_resources_list') or not callable(getattr(self.client, 'apps_permissions_resources_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_permissions_resources_list"
            )

        try:
            response = getattr(self.client, 'apps_permissions_resources_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_permissions_scopes_list(self, **kwargs) -> SlackResponse:
        """apps_permissions_scopes_list

        Slack method: `apps_permissions_scopes_list`  (HTTP GET /apps.permissions.scopes.list)

        Requires scope: `none`
        Args:

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_permissions_scopes_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_permissions_scopes_list') or not callable(getattr(self.client, 'apps_permissions_scopes_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_permissions_scopes_list"
            )

        try:
            response = getattr(self.client, 'apps_permissions_scopes_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_permissions_users_list(self,
        *,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """apps_permissions_users_list

        Slack method: `apps_permissions_users_list`  (HTTP GET /apps.permissions.users.list)

        Requires scope: `none`
        Args:
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.
            limit (optional): The maximum number of items to return.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_permissions_users_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_permissions_users_list') or not callable(getattr(self.client, 'apps_permissions_users_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_permissions_users_list"
            )

        try:
            response = getattr(self.client, 'apps_permissions_users_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_permissions_users_request(self,
        *,
        scopes: str,
        trigger_id: str,
        user: str,
        **kwargs
    ) -> SlackResponse:
        """apps_permissions_users_request

        Slack method: `apps_permissions_users_request`  (HTTP GET /apps.permissions.users.request)

        Requires scope: `none`
        Args:
            scopes (required): A comma separated list of user scopes to request for
            trigger_id (required): Token used to trigger the request
            user (required): The user this scope is being requested for

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_permissions_users_request`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if scopes is not None:
            kwargs_api['scopes'] = scopes
        if trigger_id is not None:
            kwargs_api['trigger_id'] = trigger_id
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_permissions_users_request') or not callable(getattr(self.client, 'apps_permissions_users_request')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_permissions_users_request"
            )

        try:
            response = getattr(self.client, 'apps_permissions_users_request')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def apps_uninstall(self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """apps_uninstall

        Slack method: `apps_uninstall`  (HTTP GET /apps.uninstall)

        Requires scope: `none`
        Args:
            client_id (optional): Issued when you created your application.
            client_secret (optional): Issued when you created your application.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.apps_uninstall`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if client_id is not None:
            kwargs_api['client_id'] = client_id
        if client_secret is not None:
            kwargs_api['client_secret'] = client_secret
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'apps_uninstall') or not callable(getattr(self.client, 'apps_uninstall')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: apps_uninstall"
            )

        try:
            response = getattr(self.client, 'apps_uninstall')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def auth_revoke(self,
        *,
        test: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """auth_revoke

        Slack method: `auth_revoke`  (HTTP GET /auth.revoke)

        Requires scope: `none`
        Args:
            test (optional): Setting this parameter to `1` triggers a _testing mode_ where the specified token will not actually be revoked.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.auth_revoke`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if test is not None:
            kwargs_api['test'] = test
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'auth_revoke') or not callable(getattr(self.client, 'auth_revoke')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: auth_revoke"
            )

        try:
            response = getattr(self.client, 'auth_revoke')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def auth_test(self, **kwargs) -> SlackResponse:
        """auth_test

        Slack method: `auth_test`  (HTTP GET /auth.test)
        Requires scope: `none`
        Args:
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.auth_test`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'auth_test') or not callable(getattr(self.client, 'auth_test')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: auth_test"
            )

        try:
            response = getattr(self.client, 'auth_test')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def bots_info(self,
        *,
        bot: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """bots_info

        Slack method: `bots_info`  (HTTP GET /bots.info)
        Requires scope: `users:read`
        Args:
            bot (optional): Bot user to get info on

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.bots_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if bot is not None:
            kwargs_api['bot'] = bot
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'bots_info') or not callable(getattr(self.client, 'bots_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: bots_info"
            )

        try:
            response = getattr(self.client, 'bots_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def calls_add(self,
        *,
        external_unique_id: str,
        join_url: str,
        external_display_id: Optional[str] = None,
        desktop_app_join_url: Optional[str] = None,
        date_start: Optional[str] = None,
        title: Optional[str] = None,
        created_by: Optional[str] = None,
        users: Optional[List[str]] = None,
        **kwargs
    ) -> SlackResponse:
        """calls_add

        Slack method: `calls_add`  (HTTP POST /calls.add)
        Requires scope: `calls:write`
        Args:
            external_unique_id (required): An ID supplied by the 3rd-party Call provider. It must be unique across all Calls from that service.
            external_display_id (optional): An optional, human-readable ID supplied by the 3rd-party Call provider. If supplied, this ID will be displayed in the Call object.
            join_url (required): The URL required for a client to join the Call.
            desktop_app_join_url (optional): When supplied, available Slack clients will attempt to directly launch the 3rd-party Call with this URL.
            date_start (optional): Call start time in UTC UNIX timestamp format
            title (optional): The name of the Call.
            created_by (optional): The valid Slack user ID of the user who created this Call. When this method is called with a user token, the `created_by` field is optional and defaults to the authed user of the token. Otherwise, the field is required.
            users (optional): The list of users to register as participants in the Call. [Read more on how to specify users here](/apis/calls#users).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.calls_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if external_unique_id is not None:
            kwargs_api['external_unique_id'] = external_unique_id
        if external_display_id is not None:
            kwargs_api['external_display_id'] = external_display_id
        if join_url is not None:
            kwargs_api['join_url'] = join_url
        if desktop_app_join_url is not None:
            kwargs_api['desktop_app_join_url'] = desktop_app_join_url
        if date_start is not None:
            kwargs_api['date_start'] = date_start
        if title is not None:
            kwargs_api['title'] = title
        if created_by is not None:
            kwargs_api['created_by'] = created_by
        if users is not None:
            kwargs_api['users'] = users
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'calls_add') or not callable(getattr(self.client, 'calls_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: calls_add"
            )

        try:
            response = getattr(self.client, 'calls_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def calls_end(self,
        *,
        id: str,
        duration: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """calls_end

        Slack method: `calls_end`  (HTTP POST /calls.end)
        Requires scope: `calls:write`
        Args:
            id (required): `id` returned when registering the call using the [`calls.add`](/methods/calls.add) method.
            duration (optional): Call duration in seconds

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.calls_end`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if id is not None:
            kwargs_api['id'] = id
        if duration is not None:
            kwargs_api['duration'] = duration
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'calls_end') or not callable(getattr(self.client, 'calls_end')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: calls_end"
            )

        try:
            response = getattr(self.client, 'calls_end')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def calls_info(self,
        *,
        id: str,
        **kwargs
    ) -> SlackResponse:
        """calls_info

        Slack method: `calls_info`  (HTTP GET /calls.info)
        Requires scope: `calls:read`
        Args:
            id (required): `id` of the Call returned by the [`calls.add`](/methods/calls.add) method.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.calls_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if id is not None:
            kwargs_api['id'] = id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'calls_info') or not callable(getattr(self.client, 'calls_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: calls_info"
            )

        try:
            response = getattr(self.client, 'calls_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def calls_participants_add(self,
        *,
        id: str,
        users: List[str],
        **kwargs
    ) -> SlackResponse:
        """calls_participants_add

        Slack method: `calls_participants_add`  (HTTP POST /calls.participants.add)
        Requires scope: `calls:write`
        Args:
            id (required): `id` returned by the [`calls.add`](/methods/calls.add) method.
            users (required): The list of users to add as participants in the Call. [Read more on how to specify users here](/apis/calls#users).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.calls_participants_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if id is not None:
            kwargs_api['id'] = id
        if users is not None:
            kwargs_api['users'] = users
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'calls_participants_add') or not callable(getattr(self.client, 'calls_participants_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: calls_participants_add"
            )

        try:
            response = getattr(self.client, 'calls_participants_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def calls_participants_remove(self,
        *,
        id: str,
        users: List[str],
        **kwargs
    ) -> SlackResponse:
        """calls_participants_remove

        Slack method: `calls_participants_remove`  (HTTP POST /calls.participants.remove)
        Requires scope: `calls:write`
        Args:
            id (required): `id` returned by the [`calls.add`](/methods/calls.add) method.
            users (required): The list of users to remove as participants in the Call. [Read more on how to specify users here](/apis/calls#users).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.calls_participants_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if id is not None:
            kwargs_api['id'] = id
        if users is not None:
            kwargs_api['users'] = users
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'calls_participants_remove') or not callable(getattr(self.client, 'calls_participants_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: calls_participants_remove"
            )

        try:
            response = getattr(self.client, 'calls_participants_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def calls_update(self,
        *,
        id: str,
        title: Optional[str] = None,
        join_url: Optional[str] = None,
        desktop_app_join_url: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """calls_update

        Slack method: `calls_update`  (HTTP POST /calls.update)
        Requires scope: `calls:write`
        Args:
            id (required): `id` returned by the [`calls.add`](/methods/calls.add) method.
            title (optional): The name of the Call.
            join_url (optional): The URL required for a client to join the Call.
            desktop_app_join_url (optional): When supplied, available Slack clients will attempt to directly launch the 3rd-party Call with this URL.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.calls_update`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if id is not None:
            kwargs_api['id'] = id
        if title is not None:
            kwargs_api['title'] = title
        if join_url is not None:
            kwargs_api['join_url'] = join_url
        if desktop_app_join_url is not None:
            kwargs_api['desktop_app_join_url'] = desktop_app_join_url
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'calls_update') or not callable(getattr(self.client, 'calls_update')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: calls_update"
            )

        try:
            response = getattr(self.client, 'calls_update')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_delete(self,
        *,
        ts: Optional[str] = None,
        channel: Optional[str] = None,
        as_user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_delete

        Slack method: `chat_delete`  (HTTP POST /chat.delete)
        Requires scope: `chat:write`
        Args:
            ts (optional): Timestamp of the message to be deleted.
            channel (optional): Channel containing the message to be deleted.
            as_user (optional): Pass true to delete the message as the authed user with `chat:write:user` scope. [Bot users](/bot-users) in this context are considered authed users. If unused or false, the message will be deleted with `chat:write:bot` scope.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_delete`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if ts is not None:
            kwargs_api['ts'] = ts
        if channel is not None:
            kwargs_api['channel'] = channel
        if as_user is not None:
            kwargs_api['as_user'] = as_user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_delete') or not callable(getattr(self.client, 'chat_delete')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_delete"
            )

        try:
            response = getattr(self.client, 'chat_delete')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_delete_scheduled_message(self,
        *,
        channel: str,
        scheduled_message_id: str,
        as_user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_deleteScheduledMessage

        Slack method: `chat_deleteScheduledMessage`  (HTTP POST /chat.deleteScheduledMessage)
        Requires scope: `chat:write`
        Args:
            as_user (optional): Pass true to delete the message as the authed user with `chat:write:user` scope. [Bot users](/bot-users) in this context are considered authed users. If unused or false, the message will be deleted with `chat:write:bot` scope.
            channel (required): The channel the scheduled_message is posting to
            scheduled_message_id (required): `scheduled_message_id` returned from call to chat.scheduleMessage

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_deleteScheduledMessage`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if as_user is not None:
            kwargs_api['as_user'] = as_user
        if channel is not None:
            kwargs_api['channel'] = channel
        if scheduled_message_id is not None:
            kwargs_api['scheduled_message_id'] = scheduled_message_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_deleteScheduledMessage') or not callable(getattr(self.client, 'chat_deleteScheduledMessage')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_deleteScheduledMessage"
            )

        try:
            response = getattr(self.client, 'chat_deleteScheduledMessage')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_get_permalink(self,
        *,
        channel: str,
        message_ts: str,
        **kwargs
    ) -> SlackResponse:
        """chat_getPermalink

        Slack method: `chat_getPermalink`  (HTTP GET /chat.getPermalink)
        Requires scope: `none`

        Args:
            channel (required): The ID of the conversation or channel containing the message
            message_ts (required): A message's `ts` value, uniquely identifying it within a channel

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_getPermalink`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if message_ts is not None:
            kwargs_api['message_ts'] = message_ts
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_getPermalink') or not callable(getattr(self.client, 'chat_getPermalink')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_getPermalink"
            )

        try:
            response = getattr(self.client, 'chat_getPermalink')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_me_message(self,
        *,
        channel: Optional[str] = None,
        text: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_meMessage

        Slack method: `chat_meMessage`  (HTTP POST /chat.meMessage)
        Requires scope: `chat:write`
        Args:
            channel (optional): Channel to send message to. Can be a public channel, private group or IM channel. Can be an encoded ID, or a name.
            text (optional): Text of the message to send.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_meMessage`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if text is not None:
            kwargs_api['text'] = text
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_meMessage') or not callable(getattr(self.client, 'chat_meMessage')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_meMessage"
            )

        try:
            response = getattr(self.client, 'chat_meMessage')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_post_ephemeral(self,
        *,
        channel: str,
        user: str,
        as_user: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        icon_emoji: Optional[str] = None,
        icon_url: Optional[str] = None,
        link_names: Optional[bool] = None,
        parse: Optional[str] = None,
        text: Optional[str] = None,
        thread_ts: Optional[str] = None,
        username: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_postEphemeral

        Slack method: `chat_postEphemeral`  (HTTP POST /chat.postEphemeral)
        Requires scope: `chat:write`
        Args:
            as_user (optional): Pass true to post the message as the authed user. Defaults to true if the chat:write:bot scope is not included. Otherwise, defaults to false.
            attachments (optional): A JSON-based array of structured attachments, presented as a URL-encoded string.
            blocks (optional): A JSON-based array of structured blocks, presented as a URL-encoded string.
            channel (required): Channel, private group, or IM channel to send message to. Can be an encoded ID, or a name.
            icon_emoji (optional): Emoji to use as the icon for this message. Overrides `icon_url`. Must be used in conjunction with `as_user` set to `false`, otherwise ignored. See [authorship](#authorship) below.
            icon_url (optional): URL to an image to use as the icon for this message. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below.
            link_names (optional): Find and link channel names and usernames.
            parse (optional): Change how messages are treated. Defaults to `none`. See [below](#formatting).
            text (optional): How this field works and whether it is required depends on other fields you use in your API call. [See below](#text_usage) for more detail.
            thread_ts (optional): Provide another message's `ts` value to post this message in a thread. Avoid using a reply's `ts` value; use its parent's value instead. Ephemeral messages in threads are only shown if there is already an active thread.
            user (required): `id` of the user who will receive the ephemeral message. The user should be in the channel specified by the `channel` argument.
            username (optional): Set your bot's user name. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_postEphemeral`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if as_user is not None:
            kwargs_api['as_user'] = as_user
        if attachments is not None:
            kwargs_api['attachments'] = attachments
        if blocks is not None:
            kwargs_api['blocks'] = blocks
        if channel is not None:
            kwargs_api['channel'] = channel
        if icon_emoji is not None:
            kwargs_api['icon_emoji'] = icon_emoji
        if icon_url is not None:
            kwargs_api['icon_url'] = icon_url
        if link_names is not None:
            kwargs_api['link_names'] = link_names
        if parse is not None:
            kwargs_api['parse'] = parse
        if text is not None:
            kwargs_api['text'] = text
        if thread_ts is not None:
            kwargs_api['thread_ts'] = thread_ts
        if user is not None:
            kwargs_api['user'] = user
        if username is not None:
            kwargs_api['username'] = username
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_postEphemeral') or not callable(getattr(self.client, 'chat_postEphemeral')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_postEphemeral"
            )

        try:
            response = getattr(self.client, 'chat_postEphemeral')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_post_message(self,
        *,
        channel: str,
        as_user: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        icon_emoji: Optional[str] = None,
        icon_url: Optional[str] = None,
        link_names: Optional[bool] = None,
        mrkdwn: Optional[bool] = None,
        parse: Optional[str] = None,
        reply_broadcast: Optional[bool] = None,
        text: Optional[str] = None,
        thread_ts: Optional[str] = None,
        unfurl_links: Optional[bool] = None,
        unfurl_media: Optional[bool] = None,
        username: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_postMessage

        Slack method: `chat_postMessage`  (HTTP POST /chat.postMessage)

        Args:
            as_user (optional): Pass true to post the message as the authed user, instead of as a bot. Defaults to false. See [authorship](#authorship) below.
            attachments (optional): A JSON-based array of structured attachments, presented as a URL-encoded string.
            blocks (optional): A JSON-based array of structured blocks, presented as a URL-encoded string.
            channel (required): Channel, private group, or IM channel to send message to. Can be an encoded ID, or a name. See [below](#channels) for more details.
            icon_emoji (optional): Emoji to use as the icon for this message. Overrides `icon_url`. Must be used in conjunction with `as_user` set to `false`, otherwise ignored. See [authorship](#authorship) below.
            icon_url (optional): URL to an image to use as the icon for this message. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below.
            link_names (optional): Find and link channel names and usernames.
            mrkdwn (optional): Disable Slack markup parsing by setting to `false`. Enabled by default.
            parse (optional): Change how messages are treated. Defaults to `none`. See [below](#formatting).
            reply_broadcast (optional): Used in conjunction with `thread_ts` and indicates whether reply should be made visible to everyone in the channel or conversation. Defaults to `false`.
            text (optional): How this field works and whether it is required depends on other fields you use in your API call. [See below](#text_usage) for more detail.
            thread_ts (optional): Provide another message's `ts` value to make this message a reply. Avoid using a reply's `ts` value; use its parent instead.
            unfurl_links (optional): Pass true to enable unfurling of primarily text-based content.
            unfurl_media (optional): Pass false to disable unfurling of media content.
            username (optional): Set your bot's user name. Must be used in conjunction with `as_user` set to false, otherwise ignored. See [authorship](#authorship) below.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_postMessage`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if as_user is not None:
            kwargs_api['as_user'] = as_user
        if attachments is not None:
            kwargs_api['attachments'] = attachments
        if blocks is not None:
            kwargs_api['blocks'] = blocks
        if channel is not None:
            kwargs_api['channel'] = channel
        if icon_emoji is not None:
            kwargs_api['icon_emoji'] = icon_emoji
        if icon_url is not None:
            kwargs_api['icon_url'] = icon_url
        if link_names is not None:
            kwargs_api['link_names'] = link_names
        if mrkdwn is not None:
            kwargs_api['mrkdwn'] = mrkdwn
        if parse is not None:
            kwargs_api['parse'] = parse
        if reply_broadcast is not None:
            kwargs_api['reply_broadcast'] = reply_broadcast
        if text is not None:
            kwargs_api['text'] = text
        if thread_ts is not None:
            kwargs_api['thread_ts'] = thread_ts
        if unfurl_links is not None:
            kwargs_api['unfurl_links'] = unfurl_links
        if unfurl_media is not None:
            kwargs_api['unfurl_media'] = unfurl_media
        if username is not None:
            kwargs_api['username'] = username
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_postMessage') or not callable(getattr(self.client, 'chat_postMessage')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_postMessage"
            )

        try:
            response = getattr(self.client, 'chat_postMessage')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_schedule_message(self,
        *,
        channel: Optional[str] = None,
        text: Optional[str] = None,
        post_at: Optional[str] = None,
        parse: Optional[str] = None,
        as_user: Optional[str] = None,
        link_names: Optional[bool] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        unfurl_links: Optional[bool] = None,
        unfurl_media: Optional[bool] = None,
        thread_ts: Optional[str] = None,
        reply_broadcast: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_scheduleMessage

        Slack method: `chat_scheduleMessage`  (HTTP POST /chat.scheduleMessage)
        Requires scope: `chat:write`
        Args:
            channel (optional): Channel, private group, or DM channel to send message to. Can be an encoded ID, or a name. See [below](#channels) for more details.
            text (optional): How this field works and whether it is required depends on other fields you use in your API call. [See below](#text_usage) for more detail.
            post_at (optional): Unix EPOCH timestamp of time in future to send the message.
            parse (optional): Change how messages are treated. Defaults to `none`. See [chat.postMessage](chat.postMessage#formatting).
            as_user (optional): Pass true to post the message as the authed user, instead of as a bot. Defaults to false. See [chat.postMessage](chat.postMessage#authorship).
            link_names (optional): Find and link channel names and usernames.
            attachments (optional): A JSON-based array of structured attachments, presented as a URL-encoded string.
            blocks (optional): A JSON-based array of structured blocks, presented as a URL-encoded string.
            unfurl_links (optional): Pass true to enable unfurling of primarily text-based content.
            unfurl_media (optional): Pass false to disable unfurling of media content.
            thread_ts (optional): Provide another message's `ts` value to make this message a reply. Avoid using a reply's `ts` value; use its parent instead.
            reply_broadcast (optional): Used in conjunction with `thread_ts` and indicates whether reply should be made visible to everyone in the channel or conversation. Defaults to `false`.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_scheduleMessage`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if text is not None:
            kwargs_api['text'] = text
        if post_at is not None:
            kwargs_api['post_at'] = post_at
        if parse is not None:
            kwargs_api['parse'] = parse
        if as_user is not None:
            kwargs_api['as_user'] = as_user
        if link_names is not None:
            kwargs_api['link_names'] = link_names
        if attachments is not None:
            kwargs_api['attachments'] = attachments
        if blocks is not None:
            kwargs_api['blocks'] = blocks
        if unfurl_links is not None:
            kwargs_api['unfurl_links'] = unfurl_links
        if unfurl_media is not None:
            kwargs_api['unfurl_media'] = unfurl_media
        if thread_ts is not None:
            kwargs_api['thread_ts'] = thread_ts
        if reply_broadcast is not None:
            kwargs_api['reply_broadcast'] = reply_broadcast
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_scheduleMessage') or not callable(getattr(self.client, 'chat_scheduleMessage')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_scheduleMessage"
            )

        try:
            response = getattr(self.client, 'chat_scheduleMessage')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_scheduled_messages_list(self,
        *,
        channel: Optional[str] = None,
        latest: Optional[str] = None,
        oldest: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_scheduledMessages_list

        Slack method: `chat_scheduledMessages_list`  (HTTP GET /chat.scheduledMessages.list)
        Requires scope: `none`
        Args:
            channel (optional): The channel of the scheduled messages
            latest (optional): A UNIX timestamp of the latest value in the time range
            oldest (optional): A UNIX timestamp of the oldest value in the time range
            limit (optional): Maximum number of original entries to return.
            cursor (optional): For pagination purposes, this is the `cursor` value returned from a previous call to `chat.scheduledmessages.list` indicating where you want to start this call from.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_scheduledMessages_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if latest is not None:
            kwargs_api['latest'] = latest
        if oldest is not None:
            kwargs_api['oldest'] = oldest
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_scheduledMessages_list') or not callable(getattr(self.client, 'chat_scheduledMessages_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_scheduledMessages_list"
            )

        try:
            response = getattr(self.client, 'chat_scheduledMessages_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_unfurl(self,
        *,
        channel: str,
        ts: str,
        unfurls: Optional[Dict[str, Any]] = None,
        user_auth_message: Optional[str] = None,
        user_auth_required: Optional[bool] = None,
        user_auth_url: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_unfurl

        Slack method: `chat_unfurl`  (HTTP POST /chat.unfurl)
        Requires scope: `links:write`
        Args:
            channel (required): Channel ID of the message
            ts (required): Timestamp of the message to add unfurl behavior to.
            unfurls (optional): URL-encoded JSON map with keys set to URLs featured in the the message, pointing to their unfurl blocks or message attachments.
            user_auth_message (optional): Provide a simply-formatted string to send as an ephemeral message to the user as invitation to authenticate further and enable full unfurling behavior
            user_auth_required (optional): Set to `true` or `1` to indicate the user must install your Slack app to trigger unfurls for this domain
            user_auth_url (optional): Send users to this custom URL where they will complete authentication in your app to fully trigger unfurling. Value should be properly URL-encoded.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_unfurl`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if ts is not None:
            kwargs_api['ts'] = ts
        if unfurls is not None:
            kwargs_api['unfurls'] = unfurls
        if user_auth_message is not None:
            kwargs_api['user_auth_message'] = user_auth_message
        if user_auth_required is not None:
            kwargs_api['user_auth_required'] = user_auth_required
        if user_auth_url is not None:
            kwargs_api['user_auth_url'] = user_auth_url
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_unfurl') or not callable(getattr(self.client, 'chat_unfurl')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_unfurl"
            )

        try:
            response = getattr(self.client, 'chat_unfurl')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def chat_update(self,
        *,
        channel: str,
        ts: str,
        as_user: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        blocks: Optional[List[Dict[str, Any]]] = None,
        link_names: Optional[bool] = None,
        parse: Optional[str] = None,
        text: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """chat_update

        Slack method: `chat_update`  (HTTP POST /chat.update)
        Requires scope: `chat:write`
        Args:
            as_user (optional): Pass true to update the message as the authed user. [Bot users](/bot-users) in this context are considered authed users.
            attachments (optional): A JSON-based array of structured attachments, presented as a URL-encoded string. This field is required when not presenting `text`. If you don't include this field, the message's previous `attachments` will be retained. To remove previous `attachments`, include an empty array for this field.
            blocks (optional): A JSON-based array of [structured blocks](/block-kit/building), presented as a URL-encoded string. If you don't include this field, the message's previous `blocks` will be retained. To remove previous `blocks`, include an empty array for this field.
            channel (required): Channel containing the message to be updated.
            link_names (optional): Find and link channel names and usernames. Defaults to `none`. If you do not specify a value for this field, the original value set for the message will be overwritten with the default, `none`.
            parse (optional): Change how messages are treated. Defaults to `client`, unlike `chat.postMessage`. Accepts either `none` or `full`. If you do not specify a value for this field, the original value set for the message will be overwritten with the default, `client`.
            text (optional): New text for the message, using the [default formatting rules](/reference/surfaces/formatting). It's not required when presenting `blocks` or `attachments`.
            ts (required): Timestamp of the message to be updated.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.chat_update`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if as_user is not None:
            kwargs_api['as_user'] = as_user
        if attachments is not None:
            kwargs_api['attachments'] = attachments
        if blocks is not None:
            kwargs_api['blocks'] = blocks
        if channel is not None:
            kwargs_api['channel'] = channel
        if link_names is not None:
            kwargs_api['link_names'] = link_names
        if parse is not None:
            kwargs_api['parse'] = parse
        if text is not None:
            kwargs_api['text'] = text
        if ts is not None:
            kwargs_api['ts'] = ts
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'chat_update') or not callable(getattr(self.client, 'chat_update')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: chat_update"
            )

        try:
            response = getattr(self.client, 'chat_update')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_archive(self,
        *,
        channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_archive

        Slack method: `conversations_archive`  (HTTP POST /conversations.archive)
        Requires scope: `conversations:write`
        Args:
            channel (optional): ID of conversation to archive

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_archive`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_archive') or not callable(getattr(self.client, 'conversations_archive')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_archive"
            )

        try:
            response = getattr(self.client, 'conversations_archive')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_close(self,
        *,
        channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_close

        Slack method: `conversations_close`  (HTTP POST /conversations.close)
        Requires scope: `conversations:write`
        Args:
            channel (optional): Conversation to close.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_close`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_close') or not callable(getattr(self.client, 'conversations_close')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_close"
            )

        try:
            response = getattr(self.client, 'conversations_close')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_create(self,
        *,

        name: Optional[str] = None,
        is_private: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_create

        Slack method: `conversations_create`  (HTTP POST /conversations.create)
        Requires scope: `conversations:write`
        Args:
            name (optional): Name of the public or private channel to create
            is_private (optional): Create a private channel instead of a public one

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_create`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if name is not None:
            kwargs_api['name'] = name
        if is_private is not None:
            kwargs_api['is_private'] = is_private
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_create') or not callable(getattr(self.client, 'conversations_create')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_create"
            )

        try:
            response = getattr(self.client, 'conversations_create')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_history(self,
        *,
        channel: str,
        latest: Optional[str] = None,
        oldest: Optional[str] = None,
        inclusive: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_history

        Slack method: `conversations_history`  (HTTP GET /conversations.history)
        Requires scope: `conversations:history`
        Args:
            channel (optional): Conversation ID to fetch history for.
            latest (optional): End of time range of messages to include in results.
            oldest (optional): Start of time range of messages to include in results.
            inclusive (optional): Include messages with latest or oldest timestamp in results only when either timestamp is specified.
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_history`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if latest is not None:
            kwargs_api['latest'] = latest
        if oldest is not None:
            kwargs_api['oldest'] = oldest
        if inclusive is not None:
            kwargs_api['inclusive'] = inclusive
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_history') or not callable(getattr(self.client, 'conversations_history')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_history"
            )

        try:
            response = getattr(self.client, 'conversations_history')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_info(self,
        *,
        channel: Optional[str] = None,
        include_locale: Optional[bool] = None,
        include_num_members: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_info

        Slack method: `conversations_info`  (HTTP GET /conversations.info)
        Requires scope: `conversations:read`
        Args:
            channel (optional): Conversation ID to learn more about
            include_locale (optional): Set this to `true` to receive the locale for this conversation. Defaults to `false`
            include_num_members (optional): Set to `true` to include the member count for the specified conversation. Defaults to `false`

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if include_locale is not None:
            kwargs_api['include_locale'] = include_locale
        if include_num_members is not None:
            kwargs_api['include_num_members'] = include_num_members
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_info') or not callable(getattr(self.client, 'conversations_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_info"
            )

        try:
            response = getattr(self.client, 'conversations_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_invite(self,
        *,
        channel: Optional[str] = None,
        users: Optional[List[str]] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_invite

        Slack method: `conversations_invite`  (HTTP POST /conversations.invite)
        Requires scope: `conversations:write`
        Args:
            channel (optional): The ID of the public or private channel to invite user(s) to.
            users (optional): A comma separated list of user IDs. Up to 1000 users may be listed.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_invite`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if users is not None:
            kwargs_api['users'] = users
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_invite') or not callable(getattr(self.client, 'conversations_invite')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_invite"
            )

        try:
            response = getattr(self.client, 'conversations_invite')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_join(self,
        *,
        channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_join

        Slack method: `conversations_join`  (HTTP POST /conversations.join)
        Requires scope: `channels:write`
        Args:
            channel (optional): ID of conversation to join

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_join`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_join') or not callable(getattr(self.client, 'conversations_join')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_join"
            )

        try:
            response = getattr(self.client, 'conversations_join')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_kick(self,
        *,
        channel: Optional[str] = None,
        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_kick

        Slack method: `conversations_kick`  (HTTP POST /conversations.kick)
        Requires scope: `conversations:write`
        Args:
            channel (optional): ID of conversation to remove user from.
            user (optional): User ID to be removed.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_kick`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_kick') or not callable(getattr(self.client, 'conversations_kick')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_kick"
            )

        try:
            response = getattr(self.client, 'conversations_kick')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_leave(self,
        *,
        channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_leave

        Slack method: `conversations_leave`  (HTTP POST /conversations.leave)
        Requires scope: `conversations:write`
        Args:
            channel (optional): Conversation to leave

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_leave`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_leave') or not callable(getattr(self.client, 'conversations_leave')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_leave"
            )

        try:
            response = getattr(self.client, 'conversations_leave')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_list(self,
        *,
        exclude_archived: Optional[bool] = None,
        types: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_list

        Slack method: `conversations_list`  (HTTP GET /conversations.list)
        Requires scope: `conversations:read`
        Args:
            exclude_archived (optional): Set to `true` to exclude archived channels from the list
            types (optional): Mix and match channel types by providing a comma-separated list of any combination of `public_channel`, `private_channel`, `mpim`, `im`
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached. Must be an integer no larger than 1000.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if exclude_archived is not None:
            kwargs_api['exclude_archived'] = exclude_archived
        if types is not None:
            kwargs_api['types'] = types
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_list') or not callable(getattr(self.client, 'conversations_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_list"
            )

        try:
            response = getattr(self.client, 'conversations_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_mark(self,
        *,
        channel: Optional[str] = None,
        ts: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_mark

        Slack method: `conversations_mark`  (HTTP POST /conversations.mark)
        Requires scope: `conversations:write`
        Args:
            channel (optional): Channel or conversation to set the read cursor for.
            ts (optional): Unique identifier of message you want marked as most recently seen in this conversation.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_mark`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if ts is not None:
            kwargs_api['ts'] = ts
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_mark') or not callable(getattr(self.client, 'conversations_mark')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_mark"
            )

        try:
            response = getattr(self.client, 'conversations_mark')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_members(self,
        *,

        channel: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_members

        Slack method: `conversations_members`  (HTTP GET /conversations.members)
        Requires scope: `conversations:read`
        Args:
            channel (optional): ID of the conversation to retrieve members for
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_members`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_members') or not callable(getattr(self.client, 'conversations_members')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_members"
            )

        try:
            response = getattr(self.client, 'conversations_members')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_open(self,
        *,
        channel: Optional[str] = None,
        users: Optional[List[str]] = None,
        return_im: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_open

        Slack method: `conversations_open`  (HTTP POST /conversations.open)
        Requires scope: `conversations:write`
        Args:
            channel (optional): Resume a conversation by supplying an `im` or `mpim`'s ID. Or provide the `users` field instead.
            users (optional): Comma separated lists of users. If only one user is included, this creates a 1:1 DM.  The ordering of the users is preserved whenever a multi-person direct message is returned. Supply a `channel` when not supplying `users`.
            return_im (optional): Boolean, indicates you want the full IM channel definition in the response.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_open`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if users is not None:
            kwargs_api['users'] = users
        if return_im is not None:
            kwargs_api['return_im'] = return_im
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_open') or not callable(getattr(self.client, 'conversations_open')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_open"
            )

        try:
            response = getattr(self.client, 'conversations_open')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_rename(self,
        *,

        channel: Optional[str] = None,
        name: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_rename

        Slack method: `conversations_rename`  (HTTP POST /conversations.rename)
        Requires scope: `conversations:write`
        Args:
            channel (optional): ID of conversation to rename
            name (optional): New name for conversation.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_rename`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if name is not None:
            kwargs_api['name'] = name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_rename') or not callable(getattr(self.client, 'conversations_rename')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_rename"
            )

        try:
            response = getattr(self.client, 'conversations_rename')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_replies(self,
        *,
        channel: Optional[str] = None,
        ts: Optional[str] = None,
        latest: Optional[str] = None,
        oldest: Optional[str] = None,
        inclusive: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_replies

        Slack method: `conversations_replies`  (HTTP GET /conversations.replies)
        Requires scope: `conversations:history`
        Args:
            channel (optional): Conversation ID to fetch thread from.
            ts (optional): Unique identifier of a thread's parent message. `ts` must be the timestamp of an existing message with 0 or more replies. If there are no replies then just the single message referenced by `ts` will return - it is just an ordinary, unthreaded message.
            latest (optional): End of time range of messages to include in results.
            oldest (optional): Start of time range of messages to include in results.
            inclusive (optional): Include messages with latest or oldest timestamp in results only when either timestamp is specified.
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_replies`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if ts is not None:
            kwargs_api['ts'] = ts
        if latest is not None:
            kwargs_api['latest'] = latest
        if oldest is not None:
            kwargs_api['oldest'] = oldest
        if inclusive is not None:
            kwargs_api['inclusive'] = inclusive
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_replies') or not callable(getattr(self.client, 'conversations_replies')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_replies"
            )

        try:
            response = getattr(self.client, 'conversations_replies')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_set_purpose(self,
        *,
        channel: Optional[str] = None,
        purpose: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_setPurpose

        Slack method: `conversations_setPurpose`  (HTTP POST /conversations.setPurpose)
        Requires scope: `conversations:write`
        Args:
            channel (optional): Conversation to set the purpose of
            purpose (optional): A new, specialer purpose

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_setPurpose`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if purpose is not None:
            kwargs_api['purpose'] = purpose
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_setPurpose') or not callable(getattr(self.client, 'conversations_setPurpose')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_setPurpose"
            )

        try:
            response = getattr(self.client, 'conversations_setPurpose')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_set_topic(self,
        *,

        channel: Optional[str] = None,
        topic: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_setTopic

        Slack method: `conversations_setTopic`  (HTTP POST /conversations.setTopic)
        Requires scope: `conversations:write`
        Args:
            channel (optional): Conversation to set the topic of
            topic (optional): The new topic string. Does not support formatting or linkification.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_setTopic`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if topic is not None:
            kwargs_api['topic'] = topic
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_setTopic') or not callable(getattr(self.client, 'conversations_setTopic')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_setTopic"
            )

        try:
            response = getattr(self.client, 'conversations_setTopic')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def conversations_unarchive(self,
        *,

        channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """conversations_unarchive

        Slack method: `conversations_unarchive`  (HTTP POST /conversations.unarchive)
        Requires scope: `conversations:write`
        Args:
            channel (optional): ID of conversation to unarchive

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.conversations_unarchive`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if channel is not None:
            kwargs_api['channel'] = channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'conversations_unarchive') or not callable(getattr(self.client, 'conversations_unarchive')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: conversations_unarchive"
            )

        try:
            response = getattr(self.client, 'conversations_unarchive')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def dialog_open(self,
        *,

        dialog: Dict[str, Any],
        trigger_id: str,
        **kwargs
    ) -> SlackResponse:
        """dialog_open

        Slack method: `dialog_open`  (HTTP GET /dialog.open)
        Requires scope: `none`
        Args:
            dialog (required): The dialog definition. This must be a JSON-encoded string.
            trigger_id (required): Exchange a trigger to post to the user.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.dialog_open`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}

        if dialog is not None:
            kwargs_api['dialog'] = dialog
        if trigger_id is not None:
            kwargs_api['trigger_id'] = trigger_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'dialog_open') or not callable(getattr(self.client, 'dialog_open')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: dialog_open"
            )

        try:
            response = getattr(self.client, 'dialog_open')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def dnd_end_dnd(self, **kwargs) -> SlackResponse:
        """dnd_endDnd

        Slack method: `dnd_endDnd`  (HTTP POST /dnd.endDnd)
        Requires scope: `dnd:write`
        Args:
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.dnd_endDnd`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'dnd_endDnd') or not callable(getattr(self.client, 'dnd_endDnd')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: dnd_endDnd"
            )

        try:
            response = getattr(self.client, 'dnd_endDnd')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def dnd_end_snooze(self, **kwargs) -> SlackResponse:
        """dnd_endSnooze

        Slack method: `dnd_endSnooze`  (HTTP POST /dnd.endSnooze)
        Requires scope: `dnd:write`
        Args:
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.dnd_endSnooze`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'dnd_endSnooze') or not callable(getattr(self.client, 'dnd_endSnooze')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: dnd_endSnooze"
            )

        try:
            response = getattr(self.client, 'dnd_endSnooze')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def dnd_info(self,
        *,
        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """dnd_info

        Slack method: `dnd_info`  (HTTP GET /dnd.info)
        Requires scope: `dnd:read`
        Args:
            user (optional): User to fetch status for (defaults to current user)

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.dnd_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'dnd_info') or not callable(getattr(self.client, 'dnd_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: dnd_info"
            )

        try:
            response = getattr(self.client, 'dnd_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def dnd_set_snooze(self,
        *,
        num_minutes: int,
        **kwargs
    ) -> SlackResponse:
        """dnd_setSnooze

        Slack method: `dnd_setSnooze`  (HTTP POST /dnd.setSnooze)
        Requires scope: `dnd:write`
        Args:
            num_minutes (required): Number of minutes, from now, to snooze until.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.dnd_setSnooze`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if num_minutes is not None:
            kwargs_api['num_minutes'] = num_minutes
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'dnd_setSnooze') or not callable(getattr(self.client, 'dnd_setSnooze')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: dnd_setSnooze"
            )

        try:
            response = getattr(self.client, 'dnd_setSnooze')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def dnd_team_info(self,
        *,
        users: Optional[List[str]] = None,
        **kwargs
    ) -> SlackResponse:
        """dnd_teamInfo

        Slack method: `dnd_teamInfo`  (HTTP GET /dnd.teamInfo)
        Requires scope: `dnd:read`
        Args:
            users (optional): Comma-separated list of users to fetch Do Not Disturb status for

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.dnd_teamInfo`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if users is not None:
            kwargs_api['users'] = users
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'dnd_teamInfo') or not callable(getattr(self.client, 'dnd_teamInfo')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: dnd_teamInfo"
            )

        try:
            response = getattr(self.client, 'dnd_teamInfo')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def emoji_list(self, **kwargs) -> SlackResponse:
        """emoji_list

        Slack method: `emoji_list`  (HTTP GET /emoji.list)
        Requires scope: `emoji:read`
        Args:
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.emoji_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'emoji_list') or not callable(getattr(self.client, 'emoji_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: emoji_list"
            )

        try:
            response = getattr(self.client, 'emoji_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_comments_delete(self,
        *,
        file: Optional[str] = None,
        id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_comments_delete

        Slack method: `files_comments_delete`  (HTTP POST /files.comments.delete)
        Requires scope: `files:write:user`
        Args:
            file (optional): File to delete a comment from.
            id (optional): The comment to delete.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_comments_delete`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if id is not None:
            kwargs_api['id'] = id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_comments_delete') or not callable(getattr(self.client, 'files_comments_delete')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_comments_delete"
            )

        try:
            response = getattr(self.client, 'files_comments_delete')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_delete(self,
        *,
        file: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_delete

        Slack method: `files_delete`  (HTTP POST /files.delete)
        Requires scope: `files:write:user`
        Args:
            file (optional): ID of file to delete.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_delete`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_delete') or not callable(getattr(self.client, 'files_delete')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_delete"
            )

        try:
            response = getattr(self.client, 'files_delete')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_info(self,
        *,
        file: Optional[str] = None,
        count: Optional[int] = None,
        page: Optional[int] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_info

        Slack method: `files_info`  (HTTP GET /files.info)
        Requires scope: `files:read`
        Args:
            file (optional): Specify a file by providing its ID.
            count (optional):
            page (optional):
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached.
            cursor (optional): Parameter for pagination. File comments are paginated for a single file. Set `cursor` equal to the `next_cursor` attribute returned by the previous request's `response_metadata`. This parameter is optional, but pagination is mandatory: the default value simply fetches the first "page" of the collection of comments. See [pagination](/docs/pagination) for more details.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if count is not None:
            kwargs_api['count'] = count
        if page is not None:
            kwargs_api['page'] = page
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_info') or not callable(getattr(self.client, 'files_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_info"
            )

        try:
            response = getattr(self.client, 'files_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_list(self,
        *,
        user: Optional[str] = None,
        channel: Optional[str] = None,
        ts_from: Optional[str] = None,
        ts_to: Optional[str] = None,
        types: Optional[str] = None,
        count: Optional[int] = None,
        page: Optional[int] = None,
        show_files_hidden_by_limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """files_list

        Slack method: `files_list`  (HTTP GET /files.list)
        Requires scope: `files:read`
        Args:
            user (optional): Filter files created by a single user.
            channel (optional): Filter files appearing in a specific channel, indicated by its ID.
            ts_from (optional): Filter files created after this timestamp (inclusive).
            ts_to (optional): Filter files created before this timestamp (inclusive).
            types (optional): Filter files by type ([see below](#file_types)). You can pass multiple values in the types argument, like `types=spaces,snippets`.The default value is `all`, which does not filter the list.
            count (optional):
            page (optional):
            show_files_hidden_by_limit (optional): Show truncated file info for files hidden due to being too old, and the team who owns the file being over the file limit.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user is not None:
            kwargs_api['user'] = user
        if channel is not None:
            kwargs_api['channel'] = channel
        if ts_from is not None:
            kwargs_api['ts_from'] = ts_from
        if ts_to is not None:
            kwargs_api['ts_to'] = ts_to
        if types is not None:
            kwargs_api['types'] = types
        if count is not None:
            kwargs_api['count'] = count
        if page is not None:
            kwargs_api['page'] = page
        if show_files_hidden_by_limit is not None:
            kwargs_api['show_files_hidden_by_limit'] = show_files_hidden_by_limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_list') or not callable(getattr(self.client, 'files_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_list"
            )

        try:
            response = getattr(self.client, 'files_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_remote_add(self,
        *,
        external_id: Optional[str] = None,
        title: Optional[str] = None,
        filetype: Optional[str] = None,
        external_url: Optional[str] = None,
        preview_image: Optional[str] = None,
        indexable_file_contents: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_remote_add

        Slack method: `files_remote_add`  (HTTP POST /files.remote.add)
        Requires scope: `remote_files:write`
        Args:
            external_id (optional): Creator defined GUID for the file.
            title (optional): Title of the file being shared.
            filetype (optional): type of file
            external_url (optional): URL of the remote file.
            preview_image (optional): Preview of the document via `multipart/form-data`.
            indexable_file_contents (optional): A text file (txt, pdf, doc, etc.) containing textual search terms that are used to improve discovery of the remote file.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_remote_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if external_id is not None:
            kwargs_api['external_id'] = external_id
        if title is not None:
            kwargs_api['title'] = title
        if filetype is not None:
            kwargs_api['filetype'] = filetype
        if external_url is not None:
            kwargs_api['external_url'] = external_url
        if preview_image is not None:
            kwargs_api['preview_image'] = preview_image
        if indexable_file_contents is not None:
            kwargs_api['indexable_file_contents'] = indexable_file_contents
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_remote_add') or not callable(getattr(self.client, 'files_remote_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_remote_add"
            )

        try:
            response = getattr(self.client, 'files_remote_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_remote_info(self,
        *,
        file: Optional[str] = None,
        external_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_remote_info

        Slack method: `files_remote_info`  (HTTP GET /files.remote.info)
        Requires scope: `remote_files:read`
        Args:
            file (optional): Specify a file by providing its ID.
            external_id (optional): Creator defined GUID for the file.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_remote_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if external_id is not None:
            kwargs_api['external_id'] = external_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_remote_info') or not callable(getattr(self.client, 'files_remote_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_remote_info"
            )

        try:
            response = getattr(self.client, 'files_remote_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_remote_list(self,
        *,
        channel: Optional[str] = None,
        ts_from: Optional[str] = None,
        ts_to: Optional[str] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_remote_list

        Slack method: `files_remote_list`  (HTTP GET /files.remote.list)
        Requires scope: `remote_files:read`
        Args:
            channel (optional): Filter files appearing in a specific channel, indicated by its ID.
            ts_from (optional): Filter files created after this timestamp (inclusive).
            ts_to (optional): Filter files created before this timestamp (inclusive).
            limit (optional): The maximum number of items to return.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_remote_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if ts_from is not None:
            kwargs_api['ts_from'] = ts_from
        if ts_to is not None:
            kwargs_api['ts_to'] = ts_to
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_remote_list') or not callable(getattr(self.client, 'files_remote_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_remote_list"
            )

        try:
            response = getattr(self.client, 'files_remote_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_remote_remove(self,
        *,
        file: Optional[str] = None,
        external_id: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_remote_remove

        Slack method: `files_remote_remove`  (HTTP POST /files.remote.remove)
        Requires scope: `remote_files:write`
        Args:
            file (optional): Specify a file by providing its ID.
            external_id (optional): Creator defined GUID for the file.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_remote_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if external_id is not None:
            kwargs_api['external_id'] = external_id
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_remote_remove') or not callable(getattr(self.client, 'files_remote_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_remote_remove"
            )

        try:
            response = getattr(self.client, 'files_remote_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_remote_share(self,
        *,
        file: Optional[str] = None,
        external_id: Optional[str] = None,
        channels: Optional[List[str]] = None,
        **kwargs
    ) -> SlackResponse:
        """files_remote_share

        Slack method: `files_remote_share`  (HTTP GET /files.remote.share)
        Requires scope: `remote_files:share`
        Args:
            file (optional): Specify a file registered with Slack by providing its ID. Either this field or `external_id` or both are required.
            external_id (optional): The globally unique identifier (GUID) for the file, as set by the app registering the file with Slack.  Either this field or `file` or both are required.
            channels (optional): Comma-separated list of channel IDs where the file will be shared.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_remote_share`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if external_id is not None:
            kwargs_api['external_id'] = external_id
        if channels is not None:
            kwargs_api['channels'] = channels
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_remote_share') or not callable(getattr(self.client, 'files_remote_share')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_remote_share"
            )

        try:
            response = getattr(self.client, 'files_remote_share')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_remote_update(self,
        *,
        file: Optional[str] = None,
        external_id: Optional[str] = None,
        title: Optional[str] = None,
        filetype: Optional[str] = None,
        external_url: Optional[str] = None,
        preview_image: Optional[str] = None,
        indexable_file_contents: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_remote_update

        Slack method: `files_remote_update`  (HTTP POST /files.remote.update)
        Requires scope: `remote_files:write`
        Args:
            file (optional): Specify a file by providing its ID.
            external_id (optional): Creator defined GUID for the file.
            title (optional): Title of the file being shared.
            filetype (optional): type of file
            external_url (optional): URL of the remote file.
            preview_image (optional): Preview of the document via `multipart/form-data`.
            indexable_file_contents (optional): File containing contents that can be used to improve searchability for the remote file.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_remote_update`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if external_id is not None:
            kwargs_api['external_id'] = external_id
        if title is not None:
            kwargs_api['title'] = title
        if filetype is not None:
            kwargs_api['filetype'] = filetype
        if external_url is not None:
            kwargs_api['external_url'] = external_url
        if preview_image is not None:
            kwargs_api['preview_image'] = preview_image
        if indexable_file_contents is not None:
            kwargs_api['indexable_file_contents'] = indexable_file_contents
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_remote_update') or not callable(getattr(self.client, 'files_remote_update')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_remote_update"
            )

        try:
            response = getattr(self.client, 'files_remote_update')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_revoke_public_url(self,
        *,
        file: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_revokePublicURL

        Slack method: `files_revokePublicURL`  (HTTP POST /files.revokePublicURL)
        Requires scope: `files:write:user`
        Args:
            file (optional): File to revoke

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_revokePublicURL`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_revokePublicURL') or not callable(getattr(self.client, 'files_revokePublicURL')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_revokePublicURL"
            )

        try:
            response = getattr(self.client, 'files_revokePublicURL')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_shared_public_url(self,
        *,
        file: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_sharedPublicURL

        Slack method: `files_sharedPublicURL`  (HTTP POST /files.sharedPublicURL)
        Requires scope: `files:write:user`
        Args:
            file (optional): File to share

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_sharedPublicURL`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_sharedPublicURL') or not callable(getattr(self.client, 'files_sharedPublicURL')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_sharedPublicURL"
            )

        try:
            response = getattr(self.client, 'files_sharedPublicURL')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def files_upload(self,
        *,
        file: Optional[str] = None,
        content: Optional[str] = None,
        filetype: Optional[str] = None,
        filename: Optional[str] = None,
        title: Optional[str] = None,
        initial_comment: Optional[str] = None,
        channels: Optional[List[str]] = None,
        thread_ts: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """files_upload

        Slack method: `files_upload`  (HTTP POST /files.upload)
        Requires scope: `files:write:user`
        Args:
            file (optional): File contents via `multipart/form-data`. If omitting this parameter, you must submit `content`.
            content (optional): File contents via a POST variable. If omitting this parameter, you must provide a `file`.
            filetype (optional): A [file type](/types/file#file_types) identifier.
            filename (optional): Filename of file.
            title (optional): Title of file.
            initial_comment (optional): The message text introducing the file in specified `channels`.
            channels (optional): Comma-separated list of channel names or IDs where the file will be shared.
            thread_ts (optional): Provide another message's `ts` value to upload this file as a reply. Never use a reply's `ts` value; use its parent instead.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.files_upload`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if file is not None:
            kwargs_api['file'] = file
        if content is not None:
            kwargs_api['content'] = content
        if filetype is not None:
            kwargs_api['filetype'] = filetype
        if filename is not None:
            kwargs_api['filename'] = filename
        if title is not None:
            kwargs_api['title'] = title
        if initial_comment is not None:
            kwargs_api['initial_comment'] = initial_comment
        if channels is not None:
            kwargs_api['channels'] = channels
        if thread_ts is not None:
            kwargs_api['thread_ts'] = thread_ts
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'files_upload') or not callable(getattr(self.client, 'files_upload')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: files_upload"
            )

        try:
            response = getattr(self.client, 'files_upload')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def migration_exchange(self,
        *,
        users: List[str],
        team_id: Optional[str] = None,
        to_old: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """migration_exchange

        Slack method: `migration_exchange`  (HTTP GET /migration.exchange)
        Requires scope: `tokens.basic`
        Args:
            users (required): A comma-separated list of user ids, up to 400 per request
            team_id (optional): Specify team_id starts with `T` in case of Org Token
            to_old (optional): Specify `true` to convert `W` global user IDs to workspace-specific `U` IDs. Defaults to `false`.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.migration_exchange`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if users is not None:
            kwargs_api['users'] = users
        if team_id is not None:
            kwargs_api['team_id'] = team_id
        if to_old is not None:
            kwargs_api['to_old'] = to_old
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'migration_exchange') or not callable(getattr(self.client, 'migration_exchange')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: migration_exchange"
            )

        try:
            response = getattr(self.client, 'migration_exchange')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def oauth_access(self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        code: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        single_channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """oauth_access

        Slack method: `oauth_access`  (HTTP GET /oauth.access)
        Requires scope: `none`
        Args:
            client_id (optional): Issued when you created your application.
            client_secret (optional): Issued when you created your application.
            code (optional): The `code` param returned via the OAuth callback.
            redirect_uri (optional): This must match the originally submitted URI (if one was sent).
            single_channel (optional): Request the user to add your app only to a single channel. Only valid with a [legacy workspace app](https://api.slack.com/legacy-workspace-apps).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.oauth_access`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if client_id is not None:
            kwargs_api['client_id'] = client_id
        if client_secret is not None:
            kwargs_api['client_secret'] = client_secret
        if code is not None:
            kwargs_api['code'] = code
        if redirect_uri is not None:
            kwargs_api['redirect_uri'] = redirect_uri
        if single_channel is not None:
            kwargs_api['single_channel'] = single_channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'oauth_access') or not callable(getattr(self.client, 'oauth_access')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: oauth_access"
            )

        try:
            response = getattr(self.client, 'oauth_access')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def oauth_token(self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        code: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        single_channel: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """oauth_token

        Slack method: `oauth_token`  (HTTP GET /oauth.token)
        Requires scope: `none`
        Args:
            client_id (optional): Issued when you created your application.
            client_secret (optional): Issued when you created your application.
            code (optional): The `code` param returned via the OAuth callback.
            redirect_uri (optional): This must match the originally submitted URI (if one was sent).
            single_channel (optional): Request the user to add your app only to a single channel.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.oauth_token`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if client_id is not None:
            kwargs_api['client_id'] = client_id
        if client_secret is not None:
            kwargs_api['client_secret'] = client_secret
        if code is not None:
            kwargs_api['code'] = code
        if redirect_uri is not None:
            kwargs_api['redirect_uri'] = redirect_uri
        if single_channel is not None:
            kwargs_api['single_channel'] = single_channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'oauth_token') or not callable(getattr(self.client, 'oauth_token')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: oauth_token"
            )

        try:
            response = getattr(self.client, 'oauth_token')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def oauth_v2_access(self,
        *,
        code: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """oauth_v2_access

        Slack method: `oauth_v2_access`  (HTTP GET /oauth.v2.access)
        Requires scope: `none`
        Args:
            client_id (optional): Issued when you created your application.
            client_secret (optional): Issued when you created your application.
            code (required): The `code` param returned via the OAuth callback.
            redirect_uri (optional): This must match the originally submitted URI (if one was sent).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.oauth_v2_access`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if client_id is not None:
            kwargs_api['client_id'] = client_id
        if client_secret is not None:
            kwargs_api['client_secret'] = client_secret
        if code is not None:
            kwargs_api['code'] = code
        if redirect_uri is not None:
            kwargs_api['redirect_uri'] = redirect_uri
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'oauth_v2_access') or not callable(getattr(self.client, 'oauth_v2_access')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: oauth_v2_access"
            )

        try:
            response = getattr(self.client, 'oauth_v2_access')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def pins_add(self,
        *,
        channel: str,
        timestamp: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """pins_add

        Slack method: `pins_add`  (HTTP POST /pins.add)
        Requires scope: `pins:write`
        Args:
            channel (required): Channel to pin the item in.
            timestamp (optional): Timestamp of the message to pin.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.pins_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'pins_add') or not callable(getattr(self.client, 'pins_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: pins_add"
            )

        try:
            response = getattr(self.client, 'pins_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def pins_list(self,
        *,
        channel: str,
        **kwargs
    ) -> SlackResponse:
        """pins_list

        Slack method: `pins_list`  (HTTP GET /pins.list)
        Requires scope: `pins:read`
        Args:
            channel (required): Channel to get pinned items for.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.pins_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'pins_list') or not callable(getattr(self.client, 'pins_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: pins_list"
            )

        try:
            response = getattr(self.client, 'pins_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def pins_remove(self,
        *,
        channel: str,
        timestamp: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """pins_remove

        Slack method: `pins_remove`  (HTTP POST /pins.remove)
        Requires scope: `pins:write`
        Args:
            channel (required): Channel where the item is pinned to.
            timestamp (optional): Timestamp of the message to un-pin.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.pins_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'pins_remove') or not callable(getattr(self.client, 'pins_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: pins_remove"
            )

        try:
            response = getattr(self.client, 'pins_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reactions_add(self,
        *,
        channel: str,
        name: str,
        timestamp: str,

        **kwargs
    ) -> SlackResponse:
        """reactions_add

        Slack method: `reactions_add`  (HTTP POST /reactions.add)
        Requires scope: `reactions:write`
        Args:
            channel (required): Channel where the message to add reaction to was posted.
            name (required): Reaction (emoji) name.
            timestamp (required): Timestamp of the message to add reaction to.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reactions_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if name is not None:
            kwargs_api['name'] = name
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reactions_add') or not callable(getattr(self.client, 'reactions_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reactions_add"
            )

        try:
            response = getattr(self.client, 'reactions_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reactions_get(self,
        *,

        channel: Optional[str] = None,
        file: Optional[str] = None,
        file_comment: Optional[str] = None,
        full: Optional[bool] = None,
        timestamp: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """reactions_get

        Slack method: `reactions_get`  (HTTP GET /reactions.get)
        Requires scope: `reactions:read`
        Args:
            channel (optional): Channel where the message to get reactions for was posted.
            file (optional): File to get reactions for.
            file_comment (optional): File comment to get reactions for.
            full (optional): If true always return the complete reaction list.
            timestamp (optional): Timestamp of the message to get reactions for.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reactions_get`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if file is not None:
            kwargs_api['file'] = file
        if file_comment is not None:
            kwargs_api['file_comment'] = file_comment
        if full is not None:
            kwargs_api['full'] = full
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reactions_get') or not callable(getattr(self.client, 'reactions_get')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reactions_get"
            )

        try:
            response = getattr(self.client, 'reactions_get')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reactions_list(self,
        *,
        user: Optional[str] = None,
        full: Optional[bool] = None,
        count: Optional[int] = None,
        page: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """reactions_list

        Slack method: `reactions_list`  (HTTP GET /reactions.list)
        Requires scope: `reactions:read`
        Args:
            user (optional): Show reactions made by this user. Defaults to the authed user.
            full (optional): If true always return the complete reaction list.
            count (optional):
            page (optional):
            cursor (optional): Parameter for pagination. Set `cursor` equal to the `next_cursor` attribute returned by the previous request's `response_metadata`. This parameter is optional, but pagination is mandatory: the default value simply fetches the first "page" of the collection. See [pagination](/docs/pagination) for more details.
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reactions_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user is not None:
            kwargs_api['user'] = user
        if full is not None:
            kwargs_api['full'] = full
        if count is not None:
            kwargs_api['count'] = count
        if page is not None:
            kwargs_api['page'] = page
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reactions_list') or not callable(getattr(self.client, 'reactions_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reactions_list"
            )

        try:
            response = getattr(self.client, 'reactions_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reactions_remove(self,
        *,
        name: str,
        file: Optional[str] = None,
        file_comment: Optional[str] = None,
        channel: Optional[str] = None,
        timestamp: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """reactions_remove

        Slack method: `reactions_remove`  (HTTP POST /reactions.remove)
        Requires scope: `reactions:write`
        Args:
            name (required): Reaction (emoji) name.
            file (optional): File to remove reaction from.
            file_comment (optional): File comment to remove reaction from.
            channel (optional): Channel where the message to remove reaction from was posted.
            timestamp (optional): Timestamp of the message to remove reaction from.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reactions_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if name is not None:
            kwargs_api['name'] = name
        if file is not None:
            kwargs_api['file'] = file
        if file_comment is not None:
            kwargs_api['file_comment'] = file_comment
        if channel is not None:
            kwargs_api['channel'] = channel
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reactions_remove') or not callable(getattr(self.client, 'reactions_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reactions_remove"
            )

        try:
            response = getattr(self.client, 'reactions_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reminders_add(self,
        *,
        text: str,
        time: str,
        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """reminders_add

        Slack method: `reminders_add`  (HTTP POST /reminders.add)
        Requires scope: `reminders:write`
        Args:
            text (required): The content of the reminder
            time (required): When this reminder should happen: the Unix timestamp (up to five years from now), the number of seconds until the reminder (if within 24 hours), or a natural language description (Ex. "in 15 minutes," or "every Thursday")
            user (optional): The user who will receive the reminder. If no user is specified, the reminder will go to user who created it.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reminders_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if text is not None:
            kwargs_api['text'] = text
        if time is not None:
            kwargs_api['time'] = time
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reminders_add') or not callable(getattr(self.client, 'reminders_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reminders_add"
            )

        try:
            response = getattr(self.client, 'reminders_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reminders_complete(self,
        *,
        reminder: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """reminders_complete

        Slack method: `reminders_complete`  (HTTP POST /reminders.complete)
        Requires scope: `reminders:write`
        Args:
            reminder (optional): The ID of the reminder to be marked as complete

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reminders_complete`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if reminder is not None:
            kwargs_api['reminder'] = reminder
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reminders_complete') or not callable(getattr(self.client, 'reminders_complete')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reminders_complete"
            )

        try:
            response = getattr(self.client, 'reminders_complete')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reminders_delete(self,
        *,
        reminder: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """reminders_delete

        Slack method: `reminders_delete`  (HTTP POST /reminders.delete)
        Requires scope: `reminders:write`
        Args:
            reminder (optional): The ID of the reminder

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reminders_delete`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if reminder is not None:
            kwargs_api['reminder'] = reminder
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reminders_delete') or not callable(getattr(self.client, 'reminders_delete')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reminders_delete"
            )

        try:
            response = getattr(self.client, 'reminders_delete')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reminders_info(self,
        *,
        reminder: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """reminders_info

        Slack method: `reminders_info`  (HTTP GET /reminders.info)
        Requires scope: `reminders:read`
        Args:
            reminder (optional): The ID of the reminder

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reminders_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if reminder is not None:
            kwargs_api['reminder'] = reminder
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reminders_info') or not callable(getattr(self.client, 'reminders_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reminders_info"
            )

        try:
            response = getattr(self.client, 'reminders_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def reminders_list(self, **kwargs) -> SlackResponse:
        """reminders_list

        Slack method: `reminders_list`  (HTTP GET /reminders.list)
        Requires scope: `reminders:read`
        Args:

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.reminders_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'reminders_list') or not callable(getattr(self.client, 'reminders_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: reminders_list"
            )

        try:
            response = getattr(self.client, 'reminders_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def rtm_connect(self,
        *,
        batch_presence_aware: Optional[bool] = None,
        presence_sub: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """rtm_connect

        Slack method: `rtm_connect`  (HTTP GET /rtm.connect)
        Requires scope: `rtm:stream`
        Args:
            batch_presence_aware (optional): Batch presence deliveries via subscription. Enabling changes the shape of `presence_change` events. See [batch presence](/docs/presence-and-status#batching).
            presence_sub (optional): Only deliver presence events when requested by subscription. See [presence subscriptions](/docs/presence-and-status#subscriptions).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.rtm_connect`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if batch_presence_aware is not None:
            kwargs_api['batch_presence_aware'] = batch_presence_aware
        if presence_sub is not None:
            kwargs_api['presence_sub'] = presence_sub
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'rtm_connect') or not callable(getattr(self.client, 'rtm_connect')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: rtm_connect"
            )

        try:
            response = getattr(self.client, 'rtm_connect')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def search_messages(self,
        *,
        query: str,
        count: Optional[int] = None,
        highlight: Optional[str] = None,
        page: Optional[int] = None,
        sort: Optional[str] = None,
        sort_dir: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """search_messages
        Slack method: `search_messages`  (HTTP GET /search.messages)
        Requires scope: `search:read`
        Args:
            count (optional): Pass the number of results you want per "page". Maximum of `100`.
            highlight (optional): Pass a value of `true` to enable query highlight markers (see below).
            page (optional):
            query (required): Search query.
            sort (optional): Return matches sorted by either `score` or `timestamp`.
            sort_dir (optional): Change sort direction to ascending (`asc`) or descending (`desc`).

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.search_messages`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if count is not None:
            kwargs_api['count'] = count
        if highlight is not None:
            kwargs_api['highlight'] = highlight
        if page is not None:
            kwargs_api['page'] = page
        if query is not None:
            kwargs_api['query'] = query
        if sort is not None:
            kwargs_api['sort'] = sort
        if sort_dir is not None:
            kwargs_api['sort_dir'] = sort_dir
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'search_messages') or not callable(getattr(self.client, 'search_messages')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: search_messages"
            )

        try:
            response = getattr(self.client, 'search_messages')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def stars_add(self,
        *,

        channel: Optional[str] = None,
        file: Optional[str] = None,
        file_comment: Optional[str] = None,
        timestamp: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """stars_add

        Slack method: `stars_add`  (HTTP POST /stars.add)
        Requires scope: `stars:write`
        Args:
            channel (optional): Channel to add star to, or channel where the message to add star to was posted (used with `timestamp`).
            file (optional): File to add star to.
            file_comment (optional): File comment to add star to.
            timestamp (optional): Timestamp of the message to add star to.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.stars_add`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if file is not None:
            kwargs_api['file'] = file
        if file_comment is not None:
            kwargs_api['file_comment'] = file_comment
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'stars_add') or not callable(getattr(self.client, 'stars_add')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: stars_add"
            )

        try:
            response = getattr(self.client, 'stars_add')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def stars_list(self,
        *,
        count: Optional[int] = None,
        page: Optional[int] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """stars_list

        Slack method: `stars_list`  (HTTP GET /stars.list)
        Requires scope: `stars:read`
        Args:
            count (optional):
            page (optional):
            cursor (optional): Parameter for pagination. Set `cursor` equal to the `next_cursor` attribute returned by the previous request's `response_metadata`. This parameter is optional, but pagination is mandatory: the default value simply fetches the first "page" of the collection. See [pagination](/docs/pagination) for more details.
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.stars_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if count is not None:
            kwargs_api['count'] = count
        if page is not None:
            kwargs_api['page'] = page
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if limit is not None:
            kwargs_api['limit'] = limit
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'stars_list') or not callable(getattr(self.client, 'stars_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: stars_list"
            )

        try:
            response = getattr(self.client, 'stars_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def stars_remove(self,
        *,
        channel: Optional[str] = None,
        file: Optional[str] = None,
        file_comment: Optional[str] = None,
        timestamp: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """stars_remove

        Slack method: `stars_remove`  (HTTP POST /stars.remove)
        Requires scope: `stars:write`
        Args:
            channel (optional): Channel to remove star from, or channel where the message to remove star from was posted (used with `timestamp`).
            file (optional): File to remove star from.
            file_comment (optional): File comment to remove star from.
            timestamp (optional): Timestamp of the message to remove star from.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.stars_remove`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channel is not None:
            kwargs_api['channel'] = channel
        if file is not None:
            kwargs_api['file'] = file
        if file_comment is not None:
            kwargs_api['file_comment'] = file_comment
        if timestamp is not None:
            kwargs_api['timestamp'] = timestamp
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'stars_remove') or not callable(getattr(self.client, 'stars_remove')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: stars_remove"
            )

        try:
            response = getattr(self.client, 'stars_remove')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def team_access_logs(self,
        *,

        before: Optional[str] = None,
        count: Optional[int] = None,
        page: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """team_accessLogs

        Slack method: `team_accessLogs`  (HTTP GET /team.accessLogs)
        Requires scope: `admin`
        Args:
            before (optional): End of time range of logs to include in results (inclusive).
            count (optional):
            page (optional):
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.team_accessLogs`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if before is not None:
            kwargs_api['before'] = before
        if count is not None:
            kwargs_api['count'] = count
        if page is not None:
            kwargs_api['page'] = page
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'team_accessLogs') or not callable(getattr(self.client, 'team_accessLogs')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: team_accessLogs"
            )

        try:
            response = getattr(self.client, 'team_accessLogs')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def team_billable_info(self,
        *,

        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """team_billableInfo

        Slack method: `team_billableInfo`  (HTTP GET /team.billableInfo)
        Requires scope: `admin`
        Args:
            user (optional): A user to retrieve the billable information for. Defaults to all users.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.team_billableInfo`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'team_billableInfo') or not callable(getattr(self.client, 'team_billableInfo')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: team_billableInfo"
            )

        try:
            response = getattr(self.client, 'team_billableInfo')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def team_info(self,
        *,

        team: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """team_info

        Slack method: `team_info`  (HTTP GET /team.info)
        Requires scope: `team:read`
        Args:
            team (optional): Team to get info on, if omitted, will return information about the current team. Will only return team that the authenticated token is allowed to see through external shared channels

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.team_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if team is not None:
            kwargs_api['team'] = team
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'team_info') or not callable(getattr(self.client, 'team_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: team_info"
            )

        try:
            response = getattr(self.client, 'team_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def team_integration_logs(self,
        *,

        app_id: Optional[str] = None,
        change_type: Optional[str] = None,
        count: Optional[int] = None,
        page: Optional[int] = None,
        service_id: Optional[str] = None,
        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """team_integrationLogs

        Slack method: `team_integrationLogs`  (HTTP GET /team.integrationLogs)
        Requires scope: `admin`
        Args:
            app_id (optional): Filter logs to this Slack app. Defaults to all logs.
            change_type (optional): Filter logs with this change type. Defaults to all logs.
            count (optional):
            page (optional):
            service_id (optional): Filter logs to this service. Defaults to all logs.
            user (optional): Filter logs generated by this user’s actions. Defaults to all logs.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.team_integrationLogs`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if app_id is not None:
            kwargs_api['app_id'] = app_id
        if change_type is not None:
            kwargs_api['change_type'] = change_type
        if count is not None:
            kwargs_api['count'] = count
        if page is not None:
            kwargs_api['page'] = page
        if service_id is not None:
            kwargs_api['service_id'] = service_id
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'team_integrationLogs') or not callable(getattr(self.client, 'team_integrationLogs')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: team_integrationLogs"
            )

        try:
            response = getattr(self.client, 'team_integrationLogs')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def team_profile_get(self,
        *,

        visibility: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """team_profile_get

        Slack method: `team_profile_get`  (HTTP GET /team.profile.get)
        Requires scope: `users.profile:read`
        Args:
            visibility (optional): Filter by visibility.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.team_profile_get`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if visibility is not None:
            kwargs_api['visibility'] = visibility
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'team_profile_get') or not callable(getattr(self.client, 'team_profile_get')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: team_profile_get"
            )

        try:
            response = getattr(self.client, 'team_profile_get')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_create(self,
        *,

        name: str,
        channels: Optional[List[str]] = None,
        description: Optional[str] = None,
        handle: Optional[str] = None,
        include_count: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_create

        Slack method: `usergroups_create`  (HTTP POST /usergroups.create)
        Requires scope: `usergroups:write`
        Args:
            channels (optional): A comma separated string of encoded channel IDs for which the User Group uses as a default.
            description (optional): A short description of the User Group.
            handle (optional): A mention handle. Must be unique among channels, users and User Groups.
            include_count (optional): Include the number of users in each User Group.
            name (required): A name for the User Group. Must be unique among User Groups.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_create`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if channels is not None:
            kwargs_api['channels'] = channels
        if description is not None:
            kwargs_api['description'] = description
        if handle is not None:
            kwargs_api['handle'] = handle
        if include_count is not None:
            kwargs_api['include_count'] = include_count
        if name is not None:
            kwargs_api['name'] = name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_create') or not callable(getattr(self.client, 'usergroups_create')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_create"
            )

        try:
            response = getattr(self.client, 'usergroups_create')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_disable(self,
        *,
        usergroup: str,
        include_count: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_disable

        Slack method: `usergroups_disable`  (HTTP POST /usergroups.disable)
        Requires scope: `usergroups:write`
        Args:
            include_count (optional): Include the number of users in the User Group.
            usergroup (required): The encoded ID of the User Group to disable.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_disable`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_count is not None:
            kwargs_api['include_count'] = include_count
        if usergroup is not None:
            kwargs_api['usergroup'] = usergroup
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_disable') or not callable(getattr(self.client, 'usergroups_disable')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_disable"
            )

        try:
            response = getattr(self.client, 'usergroups_disable')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_enable(self,
        *,

        usergroup: str,
        include_count: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_enable

        Slack method: `usergroups_enable`  (HTTP POST /usergroups.enable)
        Requires scope: `usergroups:write`
        Args:
            include_count (optional): Include the number of users in the User Group.
            usergroup (required): The encoded ID of the User Group to enable.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_enable`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_count is not None:
            kwargs_api['include_count'] = include_count
        if usergroup is not None:
            kwargs_api['usergroup'] = usergroup
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_enable') or not callable(getattr(self.client, 'usergroups_enable')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_enable"
            )

        try:
            response = getattr(self.client, 'usergroups_enable')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_list(self,
        *,

        include_users: Optional[bool] = None,
        include_count: Optional[int] = None,
        include_disabled: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_list

        Slack method: `usergroups_list`  (HTTP GET /usergroups.list)
        Requires scope: `usergroups:read`
        Args:
            include_users (optional): Include the list of users for each User Group.
            include_count (optional): Include the number of users in each User Group.
            include_disabled (optional): Include disabled User Groups.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_users is not None:
            kwargs_api['include_users'] = include_users
        if include_count is not None:
            kwargs_api['include_count'] = include_count
        if include_disabled is not None:
            kwargs_api['include_disabled'] = include_disabled
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_list') or not callable(getattr(self.client, 'usergroups_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_list"
            )

        try:
            response = getattr(self.client, 'usergroups_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_update(self,
        *,

        usergroup: str,
        handle: Optional[str] = None,
        description: Optional[str] = None,
        channels: Optional[List[str]] = None,
        include_count: Optional[int] = None,
        name: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_update

        Slack method: `usergroups_update`  (HTTP POST /usergroups.update)
        Requires scope: `usergroups:write`
        Args:
            handle (optional): A mention handle. Must be unique among channels, users and User Groups.
            description (optional): A short description of the User Group.
            channels (optional): A comma separated string of encoded channel IDs for which the User Group uses as a default.
            include_count (optional): Include the number of users in the User Group.
            usergroup (required): The encoded ID of the User Group to update.
            name (optional): A name for the User Group. Must be unique among User Groups.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_update`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if handle is not None:
            kwargs_api['handle'] = handle
        if description is not None:
            kwargs_api['description'] = description
        if channels is not None:
            kwargs_api['channels'] = channels
        if include_count is not None:
            kwargs_api['include_count'] = include_count
        if usergroup is not None:
            kwargs_api['usergroup'] = usergroup
        if name is not None:
            kwargs_api['name'] = name
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_update') or not callable(getattr(self.client, 'usergroups_update')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_update"
            )

        try:
            response = getattr(self.client, 'usergroups_update')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_users_list(self,
        *,

        usergroup: str,
        include_disabled: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_users_list

        Slack method: `usergroups_users_list`  (HTTP GET /usergroups.users.list)
        Requires scope: `usergroups:read`
        Args:
            include_disabled (optional): Allow results that involve disabled User Groups.
            usergroup (required): The encoded ID of the User Group to update.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_users_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_disabled is not None:
            kwargs_api['include_disabled'] = include_disabled
        if usergroup is not None:
            kwargs_api['usergroup'] = usergroup
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_users_list') or not callable(getattr(self.client, 'usergroups_users_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_users_list"
            )

        try:
            response = getattr(self.client, 'usergroups_users_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def usergroups_users_update(self,
        *,

        usergroup: str,
        users: List[str],
        include_count: Optional[int] = None,
        **kwargs
    ) -> SlackResponse:
        """usergroups_users_update

        Slack method: `usergroups_users_update`  (HTTP POST /usergroups.users.update)
        Requires scope: `usergroups:write`
        Args:
            include_count (optional): Include the number of users in the User Group.
            usergroup (required): The encoded ID of the User Group to update.
            users (required): A comma separated string of encoded user IDs that represent the entire list of users for the User Group.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.usergroups_users_update`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_count is not None:
            kwargs_api['include_count'] = include_count
        if usergroup is not None:
            kwargs_api['usergroup'] = usergroup
        if users is not None:
            kwargs_api['users'] = users
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'usergroups_users_update') or not callable(getattr(self.client, 'usergroups_users_update')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: usergroups_users_update"
            )

        try:
            response = getattr(self.client, 'usergroups_users_update')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_conversations(self,
        *,
        user: Optional[str] = None,
        types: Optional[str] = None,
        exclude_archived: Optional[bool] = None,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """users_conversations

        Slack method: `users_conversations`  (HTTP GET /users.conversations)
        Requires scope: `conversations:read`
        Args:
            user (optional): Browse conversations by a specific user ID's membership. Non-public channels are restricted to those where the calling user shares membership.
            types (optional): Mix and match channel types by providing a comma-separated list of any combination of `public_channel`, `private_channel`, `mpim`, `im`
            exclude_archived (optional): Set to `true` to exclude archived channels from the list
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the list hasn't been reached. Must be an integer no larger than 1000.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_conversations`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user is not None:
            kwargs_api['user'] = user
        if types is not None:
            kwargs_api['types'] = types
        if exclude_archived is not None:
            kwargs_api['exclude_archived'] = exclude_archived
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_conversations') or not callable(getattr(self.client, 'users_conversations')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_conversations"
            )

        try:
            response = getattr(self.client, 'users_conversations')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_delete_photo(self, **kwargs) -> SlackResponse:
        """users_deletePhoto

        Slack method: `users_deletePhoto`  (HTTP POST /users.deletePhoto)
        Requires scope: `users:write`
        Args:
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_deletePhoto`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_deletePhoto') or not callable(getattr(self.client, 'users_deletePhoto')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_deletePhoto"
            )

        try:
            response = getattr(self.client, 'users_deletePhoto')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_get_presence(self,
        *,

        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """users_getPresence

        Slack method: `users_getPresence`  (HTTP GET /users.getPresence)
        Requires scope: `users:read`
        Args:
            user (optional): User to get presence info on. Defaults to the authed user.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_getPresence`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_getPresence') or not callable(getattr(self.client, 'users_getPresence')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_getPresence"
            )

        try:
            response = getattr(self.client, 'users_getPresence')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_identity(self, **kwargs) -> SlackResponse:
        """users_identity

        Slack method: `users_identity`  (HTTP GET /users.identity)
        Requires scope: `identity.basic`
        Args:

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_identity`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_identity') or not callable(getattr(self.client, 'users_identity')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_identity"
            )

        try:
            response = getattr(self.client, 'users_identity')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_info(self,
        *,
        include_locale: Optional[bool] = None,
        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """users_info

        Slack method: `users_info`  (HTTP GET /users.info)
        Requires scope: `users:read`
        Args:
            include_locale (optional): Set this to `true` to receive the locale for this user. Defaults to `false`
            user (optional): User to get info on

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_info`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_locale is not None:
            kwargs_api['include_locale'] = include_locale
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_info') or not callable(getattr(self.client, 'users_info')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_info"
            )

        try:
            response = getattr(self.client, 'users_info')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_list(self,
        *,
        limit: Optional[int] = None,
        cursor: Optional[str] = None,
        include_locale: Optional[bool] = None,
        **kwargs
    ) -> SlackResponse:
        """users_list

        Slack method: `users_list`  (HTTP GET /users.list)
        Requires scope: `users:read`
        Args:
            limit (optional): The maximum number of items to return. Fewer than the requested number of items may be returned, even if the end of the users list hasn't been reached. Providing no `limit` value will result in Slack attempting to deliver you the entire result set. If the collection is too large you may experience `limit_required` or HTTP 500 errors.
            cursor (optional): Paginate through collections of data by setting the `cursor` parameter to a `next_cursor` attribute returned by a previous request's `response_metadata`. Default value fetches the first "page" of the collection. See [pagination](/docs/pagination) for more detail.
            include_locale (optional): Set this to `true` to receive the locale for users. Defaults to `false`

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_list`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if limit is not None:
            kwargs_api['limit'] = limit
        if cursor is not None:
            kwargs_api['cursor'] = cursor
        if include_locale is not None:
            kwargs_api['include_locale'] = include_locale
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_list') or not callable(getattr(self.client, 'users_list')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_list"
            )

        try:
            response = getattr(self.client, 'users_list')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_lookup_by_email(self,
        *,
        email: str,
        **kwargs
    ) -> SlackResponse:
        """users_lookupByEmail

        Slack method: `users_lookupByEmail`  (HTTP GET /users.lookupByEmail)
        Requires scope: `users:read.email`
        Args:
            email (required): An email address belonging to a user in the workspace

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_lookupByEmail`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if email is not None:
            kwargs_api['email'] = email
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_lookupByEmail') or not callable(getattr(self.client, 'users_lookupByEmail')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_lookupByEmail"
            )

        try:
            response = getattr(self.client, 'users_lookupByEmail')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_profile_get(self,
        *,
        include_labels: Optional[bool] = None,
        user: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """users_profile_get

        Slack method: `users_profile_get`  (HTTP GET /users.profile.get)
        Requires scope: `users.profile:read`
        Args:
            include_labels (optional): Include labels for each ID in custom profile fields
            user (optional): User to retrieve profile info for

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_profile_get`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if include_labels is not None:
            kwargs_api['include_labels'] = include_labels
        if user is not None:
            kwargs_api['user'] = user
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_profile_get') or not callable(getattr(self.client, 'users_profile_get')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_profile_get"
            )

        try:
            response = getattr(self.client, 'users_profile_get')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_profile_set(self,
        *,
        name: Optional[str] = None,
        profile: Optional[str] = None,
        user: Optional[str] = None,
        value: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """users_profile_set

        Slack method: `users_profile_set`  (HTTP POST /users.profile.set)
        Requires scope: `users.profile:write`
        Args:
            name (optional): Name of a single key to set. Usable only if `profile` is not passed.
            profile (optional): Collection of key:value pairs presented as a URL-encoded JSON hash. At most 50 fields may be set. Each field name is limited to 255 characters.
            user (optional): ID of user to change. This argument may only be specified by team admins on paid teams.
            value (optional): Value to set a single key to. Usable only if `profile` is not passed.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_profile_set`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if name is not None:
            kwargs_api['name'] = name
        if profile is not None:
            kwargs_api['profile'] = profile
        if user is not None:
            kwargs_api['user'] = user
        if value is not None:
            kwargs_api['value'] = value
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_profile_set') or not callable(getattr(self.client, 'users_profile_set')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_profile_set"
            )

        try:
            response = getattr(self.client, 'users_profile_set')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_set_active(self, **kwargs) -> SlackResponse:
        """users_setActive
        Slack method: `users_setActive`  (HTTP POST /users.setActive)
        Requires scope: `users:write`
        Args:
        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_setActive`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_setActive') or not callable(getattr(self.client, 'users_setActive')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_setActive"
            )

        try:
            response = getattr(self.client, 'users_setActive')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_set_photo(self,
        *,
        crop_w: Optional[int] = None,
        crop_x: Optional[int] = None,
        crop_y: Optional[int] = None,
        image: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """users_setPhoto

        Slack method: `users_setPhoto`  (HTTP POST /users.setPhoto)
        Requires scope: `users.profile:write`
        Args:
            crop_w (optional): Width/height of crop box (always square)
            crop_x (optional): X coordinate of top-left corner of crop box
            crop_y (optional): Y coordinate of top-left corner of crop box
            image (optional): File contents via `multipart/form-data`.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_setPhoto`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if crop_w is not None:
            kwargs_api['crop_w'] = crop_w
        if crop_x is not None:
            kwargs_api['crop_x'] = crop_x
        if crop_y is not None:
            kwargs_api['crop_y'] = crop_y
        if image is not None:
            kwargs_api['image'] = image
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_setPhoto') or not callable(getattr(self.client, 'users_setPhoto')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_setPhoto"
            )

        try:
            response = getattr(self.client, 'users_setPhoto')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def users_set_presence(self,
        *,
        presence: str,
        **kwargs
    ) -> SlackResponse:
        """users_setPresence

        Slack method: `users_setPresence`  (HTTP POST /users.setPresence)
        Requires scope: `users:write`
        Args:
            presence (required): Either `auto` or `away`

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.users_setPresence`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if presence is not None:
            kwargs_api['presence'] = presence
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'users_setPresence') or not callable(getattr(self.client, 'users_setPresence')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: users_setPresence"
            )

        try:
            response = getattr(self.client, 'users_setPresence')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def views_open(self,
        *,
        trigger_id: str,
        view: Dict[str, Any],
        **kwargs
    ) -> SlackResponse:
        """views_open

        Slack method: `views_open`  (HTTP GET /views.open)
        Requires scope: `none`
        Args:
            trigger_id (required): Exchange a trigger to post to the user.
            view (required): A [view payload](/reference/surfaces/views). This must be a JSON-encoded string.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.views_open`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if trigger_id is not None:
            kwargs_api['trigger_id'] = trigger_id
        if view is not None:
            kwargs_api['view'] = view
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'views_open') or not callable(getattr(self.client, 'views_open')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: views_open"
            )

        try:
            response = getattr(self.client, 'views_open')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def views_publish(self,
        *,
        user_id: str,
        view: Dict[str, Any],
        hash: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """views_publish

        Slack method: `views_publish`  (HTTP GET /views.publish)
        Requires scope: `none`
        Args:
            user_id (required): `id` of the user you want publish a view to.
            view (required): A [view payload](/reference/surfaces/views). This must be a JSON-encoded string.
            hash (optional): A string that represents view state to protect against possible race conditions.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.views_publish`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if user_id is not None:
            kwargs_api['user_id'] = user_id
        if view is not None:
            kwargs_api['view'] = view
        if hash is not None:
            kwargs_api['hash'] = hash
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'views_publish') or not callable(getattr(self.client, 'views_publish')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: views_publish"
            )

        try:
            response = getattr(self.client, 'views_publish')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def views_push(self,
        *,
        trigger_id: str,
        view: Dict[str, Any],
        **kwargs
    ) -> SlackResponse:
        """views_push

        Slack method: `views_push`  (HTTP GET /views.push)
        Requires scope: `none`
        Args:
            trigger_id (required): Exchange a trigger to post to the user.
            view (required): A [view payload](/reference/surfaces/views). This must be a JSON-encoded string.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.views_push`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if trigger_id is not None:
            kwargs_api['trigger_id'] = trigger_id
        if view is not None:
            kwargs_api['view'] = view
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'views_push') or not callable(getattr(self.client, 'views_push')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: views_push"
            )

        try:
            response = getattr(self.client, 'views_push')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def views_update(self,
        *,
        view_id: Optional[str] = None,
        external_id: Optional[str] = None,
        view: Optional[Dict[str, Any]] = None,
        hash: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """views_update

        Slack method: `views_update`  (HTTP GET /views.update)
        Requires scope: `none`
        Args:
            view_id (optional): A unique identifier of the view to be updated. Either `view_id` or `external_id` is required.
            external_id (optional): A unique identifier of the view set by the developer. Must be unique for all views on a team. Max length of 255 characters. Either `view_id` or `external_id` is required.
            view (optional): A [view object](/reference/surfaces/views). This must be a JSON-encoded string.
            hash (optional): A string that represents view state to protect against possible race conditions.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.views_update`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if view_id is not None:
            kwargs_api['view_id'] = view_id
        if external_id is not None:
            kwargs_api['external_id'] = external_id
        if view is not None:
            kwargs_api['view'] = view
        if hash is not None:
            kwargs_api['hash'] = hash
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'views_update') or not callable(getattr(self.client, 'views_update')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: views_update"
            )

        try:
            response = getattr(self.client, 'views_update')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def workflows_step_completed(self,
        *,
        workflow_step_execute_id: str,
        outputs: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> SlackResponse:
        """workflows_stepCompleted

        Slack method: `workflows_stepCompleted`  (HTTP GET /workflows.stepCompleted)
        Requires scope: `workflow.steps:execute`
        Args:
            workflow_step_execute_id (required): Context identifier that maps to the correct workflow step execution.
            outputs (optional): Key-value object of outputs from your step. Keys of this object reflect the configured `key` properties of your [`outputs`](/reference/workflows/workflow_step#output) array from your `workflow_step` object.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.workflows_stepCompleted`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if workflow_step_execute_id is not None:
            kwargs_api['workflow_step_execute_id'] = workflow_step_execute_id
        if outputs is not None:
            kwargs_api['outputs'] = outputs
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'workflows_stepCompleted') or not callable(getattr(self.client, 'workflows_stepCompleted')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: workflows_stepCompleted"
            )

        try:
            response = getattr(self.client, 'workflows_stepCompleted')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def workflows_step_failed(self,
        *,
        workflow_step_execute_id: str,
        error: Dict[str, Any],
        **kwargs
    ) -> SlackResponse:
        """workflows_stepFailed

        Slack method: `workflows_stepFailed`  (HTTP GET /workflows.stepFailed)
        Requires scope: `workflow.steps:execute`
        Args:
            workflow_step_execute_id (required): Context identifier that maps to the correct workflow step execution.
            error (required): A JSON-based object with a `message` property that should contain a human readable error message.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.workflows_stepFailed`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if workflow_step_execute_id is not None:
            kwargs_api['workflow_step_execute_id'] = workflow_step_execute_id
        if error is not None:
            kwargs_api['error'] = error
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'workflows_stepFailed') or not callable(getattr(self.client, 'workflows_stepFailed')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: workflows_stepFailed"
            )

        try:
            response = getattr(self.client, 'workflows_stepFailed')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)

    async def workflows_update_step(self,
        *,
        workflow_step_edit_id: str,
        inputs: Optional[Dict[str, Any]] = None,
        outputs: Optional[List[Dict[str, Any]]] = None,
        step_name: Optional[str] = None,
        step_image_url: Optional[str] = None,
        **kwargs
    ) -> SlackResponse:
        """workflows_updateStep

        Slack method: `workflows_updateStep`  (HTTP GET /workflows.updateStep)
        Requires scope: `workflow.steps:execute`
        Args:
            workflow_step_edit_id (required): A context identifier provided with `view_submission` payloads used to call back to `workflows.updateStep`.
            inputs (optional): A JSON key-value map of inputs required from a user during configuration. This is the data your app expects to receive when the workflow step starts. **Please note**: the embedded variable format is set and replaced by the workflow system. You cannot create custom variables that will be replaced at runtime. [Read more about variables in workflow steps here](/workflows/steps#variables).
            outputs (optional): An JSON array of output objects used during step execution. This is the data your app agrees to provide when your workflow step was executed.
            step_name (optional): An optional field that can be used to override the step name that is shown in the Workflow Builder.
            step_image_url (optional): An optional field that can be used to override app image that is shown in the Workflow Builder.

        Returns:
            SlackResponse: Standardized response wrapper with success/data/error

        Notes:
            Auto-generated from Slack's OpenAPI. Calls `SlackClient.workflows_updateStep`.
            No `api_call` fallback is used; if the alias is missing, a NotImplementedError is raised.
        """
        kwargs_api: Dict[str, Any] = {}
        if workflow_step_edit_id is not None:
            kwargs_api['workflow_step_edit_id'] = workflow_step_edit_id
        if inputs is not None:
            kwargs_api['inputs'] = inputs
        if outputs is not None:
            kwargs_api['outputs'] = outputs
        if step_name is not None:
            kwargs_api['step_name'] = step_name
        if step_image_url is not None:
            kwargs_api['step_image_url'] = step_image_url
        if kwargs:
            kwargs_api.update(kwargs)

        if not hasattr(self.client, 'workflows_updateStep') or not callable(getattr(self.client, 'workflows_updateStep')):
            return SlackResponse(
                success=False,
                error="Slack client is missing required method alias: workflows_updateStep"
            )

        try:
            response = getattr(self.client, 'workflows_updateStep')(**kwargs_api)
            return await self._handle_slack_response(response)
        except Exception as e:
            return await self._handle_slack_error(e)
