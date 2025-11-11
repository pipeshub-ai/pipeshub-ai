import asyncio
from app.sources.external.zoom.zoom import ZoomDataSource

async def main():
    access_token = "ZOOM Access Token"
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
