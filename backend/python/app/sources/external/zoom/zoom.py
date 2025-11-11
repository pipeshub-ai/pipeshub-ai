from dataclasses import dataclass
from app.sources.client.zoom.zoom import ZoomClient, ZoomTokenConfig


class ZoomDataSource:
    """ZoomDataSource interacts with Zoom API via ZoomClient."""

    def __init__(self, token: str):
        config = ZoomTokenConfig(token=token)
        self.client = ZoomClient.build_with_config(config=config)

    async def get_user_info(self, user_id: str):
        """Fetch details of a user."""
        return await self.client.get_client().request("GET", f"/users/{user_id}")

    async def list_meetings(self, user_id: str):
        """Fetch list of meetings for a user."""
        return await self.client.get_client().request("GET", f"/users/{user_id}/meetings")

    async def create_meeting(self, user_id: str, data: dict):
        """Create a new meeting for the given user."""
        return await self.client.get_client().request("POST", f"/users/{user_id}/meetings", body=data)
