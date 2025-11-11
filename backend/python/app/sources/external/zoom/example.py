import asyncio
from app.sources.external.zoom.zoom import ZoomDataSource

async def main():
    access_token = "eyJzdiI6IjAwMDAwMiIsImFsZyI6IkhTNTEyIiwidiI6IjIuMCIsImtpZCI6Ijk2MWZlNjdjLTlhMDYtNGQ4Zi04NzQ3LTE0NTc5OGFiYzBlMSJ9.eyJhdWQiOiJodHRwczovL29hdXRoLnpvb20udXMiLCJ1aWQiOiItM2FRcHVWeFRTbWxtd1dfVEc1eHVRIiwidmVyIjoxMCwiYXVpZCI6IjYzMmQ2ODlkZWFiNjllOTQ5MTc4OGYyOWY2YjA4MTQ1ZTBhY2EwYTY3YTBmNGQ2ZTgxNTRmOTcyNmViMzc1MDEiLCJuYmYiOjE3NjI4ODU4NDgsImNvZGUiOiI4TWwzMnQ5aFJuT2lqb2tlWmE4WnRRT2Y4cmpodnFQUnEiLCJpc3MiOiJ6bTpjaWQ6azdvcWc0ZldTNHVEWVdQTm83MlI0ZyIsImdubyI6MCwiZXhwIjoxNzYyODg5NDQ4LCJ0eXBlIjozLCJpYXQiOjE3NjI4ODU4NDgsImFpZCI6IkFWclNDNG1QUVQyTnBnSms2RktCQ0EifQ.k91VjSyRsrxu84u188KVKtLgM0sELrsElRdigAKaFyJ14WyvzRRgwVWzaoRmKfgvgHXtdkTKPK3CNq8qL_qSUQ"
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
