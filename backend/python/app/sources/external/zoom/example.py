import asyncio
import os

from app.sources.external.zoom.zoom import ZoomDataSource


async def main():
    """Example script demonstrating Zoom API integration"""

    # Load access token from environment variable
    access_token = os.getenv("ZOOM_ACCESS_TOKEN")
    if not access_token:
        raise ValueError(
            "Environment variable ZOOM_ACCESS_TOKEN not set. "
            "Please export it before running:\n"
            "export ZOOM_ACCESS_TOKEN='your_token_here'"
        )

    zoom_ds = ZoomDataSource(access_token)
    user_id = "me"

    print("Fetching user info:")
    print(await zoom_ds.get_user_info(user_id))

    print("\nListing meetings:")
    print(await zoom_ds.list_meetings(user_id))

    print("\nCreating test meeting:")
    meeting_data = {
        "topic": "Demo Meeting",
        "type": 1,
        "settings": {"host_video": True, "participant_video": True},
    }
    print(await zoom_ds.create_meeting(user_id, meeting_data))


if __name__ == "__main__":
    asyncio.run(main())
