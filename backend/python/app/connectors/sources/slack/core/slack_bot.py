"""Slack Bot for handling Slack API interactions"""

import logging
from typing import Any, Dict, List, Optional

from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

logger = logging.getLogger(__name__)


class SlackBot:
    def __init__(self, token: str, signing_secret: str) -> None:
        """Initialize Slack bot with token and signing secret."""
        self.token = token
        self.signing_secret = signing_secret
        self.client = AsyncWebClient(token=self.token)
        self.workspace_id: Optional[str] = None
        self.workspace_info: Optional[Dict[str, Any]] = None

    async def initialize(self) -> bool:
        """Initialize bot and fetch workspace information."""
        try:
            # Test auth and get workspace info
            auth_response = await self.client.auth_test()
            if not auth_response["ok"]:
                logger.error("Slack authentication failed")
                return False

            self.workspace_id = auth_response["team_id"]
            self.workspace_info = {
                "id": auth_response["team_id"],
                "name": auth_response["team"],
                "url": auth_response["url"],
                "bot_id": auth_response["bot_id"],
                "user_id": auth_response["user_id"]
            }

            logger.info(f"Successfully initialized Slack bot for workspace: {self.workspace_info['name']}")
            return True

        except SlackApiError as e:
            logger.error(f"Slack authentication failed: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during Slack bot initialization: {str(e)}")
            return False

    async def verify_auth(self) -> bool:
        """Verify bot authentication."""
        if not self.workspace_id:
            return await self.initialize()
        return True

    @property
    def get_workspace_id(self) -> Optional[str]:
        """Get current workspace ID."""
        return self.workspace_id

    async def verify_workspace(self) -> Optional[str]:
        """Verify and return workspace ID."""
        if not self.workspace_id:
            await self.initialize()
        return self.workspace_id

    async def get_channel_info(self, channel_id: str) -> Optional[Dict[str, Any]]:
        """Get channel information."""
        try:
            response = await self.client.conversations_info(channel=channel_id)
            if response["ok"]:
                return response["channel"]
            return None
        except SlackApiError as e:
            logger.error(f"Error getting channel info: {str(e)}")
            return None

    async def users_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about a user"""
        try:
            response = await self.client.users_info(user=user_id)
            if not response or not response.get('ok'):
                logger.error(f"❌ Failed to get user info: {response.get('error', 'Unknown error')}")
                return None

            user_data = response.get('user')
            if not user_data or not isinstance(user_data, dict):
                logger.error("❌ Invalid user data format")
                return None

            return {
                'user': user_data
            }

        except Exception as e:
            logger.error(f"❌ Error getting user info: {str(e)}")
            return None

    async def get_channel_members(self, channel_id: str) -> List[str]:
        """Get list of member IDs for a channel with pagination"""
        try:
            all_members = []
            cursor = None

            while True:
                response = await self.client.conversations_members(
                    channel=channel_id,
                    cursor=cursor
                )

                if not response or not response.get('ok'):
                    logger.error(f"❌ Failed to get channel members: {response.get('error', 'Unknown error')}")
                    break

                members = response.get('members', [])
                if not isinstance(members, list):
                    logger.error("❌ Invalid members response format")
                    break

                all_members.extend(members)

                # Check for next page
                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break

            return all_members

        except Exception as e:
            logger.error(f"❌ Error getting channel members: {str(e)}")
            return []

    async def get_message_history(
        self,
        channel_id: str,
        latest: Optional[str] = None,
        oldest: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get channel message history."""
        try:
            response = await self.client.conversations_history(
                channel=channel_id,
                latest=latest,
                oldest=oldest,
                limit=limit
            )
            if response["ok"]:
                return response["messages"]
            return []
        except SlackApiError as e:
            logger.error(f"Error getting message history: {str(e)}")
            return []

    async def get_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get thread replies."""
        try:
            response = await self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=limit
            )
            if response["ok"]:
                return response["messages"]
            return []
        except SlackApiError as e:
            logger.error(f"Error getting thread replies: {str(e)}")
            return []

    async def join_channel(self, channel_id: str) -> bool:
        """Join a channel."""
        try:
            response = await self.client.conversations_join(channel=channel_id)
            return response["ok"]
        except SlackApiError as e:
            logger.error(f"Error joining channel: {str(e)}")
            return False

    async def get_channels_list(self, types: str = "public_channel,private_channel") -> List[Dict[str, Any]]:
        """Get list of all channels."""
        try:
            channels = []
            cursor = None
            while True:
                response = await self.client.conversations_list(
                    types=types,
                    cursor=cursor
                )
                if response["ok"]:
                    channels.extend(response["channels"])
                    cursor = response.get("response_metadata", {}).get("next_cursor")
                    if not cursor:
                        break
                else:
                    break
            return channels
        except SlackApiError as e:
            logger.error(f"Error getting channels list: {str(e)}")
            return []

    async def get_team_info(self) -> Optional[Dict[str, Any]]:
        """Get team/workspace information."""
        try:
            response = await self.client.team_info()
            if response["ok"]:
                return response
            return None
        except SlackApiError as e:
            logger.error(f"Error getting team info: {str(e)}")
            return None

    async def get_users_list(self, limit: int = 100, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get list of users in the workspace."""
        try:
            params = {"limit": limit}
            if cursor:
                params["cursor"] = cursor

            response = await self.client.users_list(**params)
            if response["ok"]:
                return response
            return None
        except SlackApiError as e:
            logger.error(f"Error getting users list: {str(e)}")
            return None

    async def conversations_members(self, channel: str, cursor: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get members of a conversation/channel with pagination support"""
        try:
            response = await self.client.conversations_members(
                channel=channel,
                cursor=cursor,
                limit=1000  # Maximum allowed by Slack API
            )

            if not response or not response.get('ok'):
                logger.error(f"❌ Failed to get conversation members: {response.get('error', 'Unknown error')}")
                return None

            return response

        except Exception as e:
            logger.error(f"❌ Error getting conversation members: {str(e)}")
            return None
