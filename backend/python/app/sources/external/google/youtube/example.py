# ruff: noqa
"""
Example script to demonstrate how to use the Google Youtube API
"""
import asyncio
import logging

from app.sources.client.google.google import GoogleClient
from app.services.graph_db.graph_db_factory import GraphDBFactory
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.config.configuration_service import ConfigurationService
from app.sources.external.google.youtube.youtube import YouTubeDataSource


async def main() -> None:
    # create configuration service client
    etcd3_encrypted_key_value_store = Etcd3EncryptedKeyValueStore(logger=logging.getLogger(__name__))

    # create configuration service
    config_service = ConfigurationService(logger=logging.getLogger(__name__), key_value_store=etcd3_encrypted_key_value_store)
    # create graph db service
    graph_db_service = await GraphDBFactory.create_service("arango", logger=logging.getLogger(__name__), config_service=config_service)
    if not graph_db_service:
        raise Exception("Graph DB service not found")
    await graph_db_service.connect()

    youtube_google_client = await GoogleClient.build_from_services(
        service_name="youtube",
        version="v3",
        logger=logging.getLogger(__name__),
        config_service=config_service,
        graph_db_service=graph_db_service,
        scopes=[
            "https://www.googleapis.com/auth/youtube",
            "https://www.googleapis.com/auth/youtube.readonly",
            "https://www.googleapis.com/auth/youtube.channel-memberships.creator",
        ],
    )

    google_youtube_data_source = YouTubeDataSource(youtube_google_client.get_client())
    # 1) Get the channel by handle (use forHandle, not id)
    ch = await google_youtube_data_source.channels_list(
        part="id,contentDetails",
        forHandle="@PipesHub-GenAI",
    )
    if not ch["items"]:
        raise RuntimeError("Channel not found for handle @PipesHub-GenAI")

    channel = ch["items"][0]
    channel_id = channel["id"]
    uploads_pl = channel["contentDetails"]["relatedPlaylists"]["uploads"]

    # 2) Page through the uploads playlist to collect video IDs
    video_ids = []
    page_token = None
    while True:
        pl_page = await google_youtube_data_source.playlist_items_list(
            part="snippet,contentDetails",
            playlistId=uploads_pl,
            maxResults=50,
            pageToken=page_token,
        )
        for it in pl_page.get("items", []):
            # Prefer contentDetails.videoId when present; snippet.resourceId is a fallback
            vid = it.get("contentDetails", {}).get("videoId") \
                or it.get("snippet", {}).get("resourceId", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = pl_page.get("nextPageToken")
        if not page_token:
            break

    print(f"Collected {len(video_ids)} video IDs from channel {channel_id}")

    # 3) (Optional) Get details/stats for those videos in batches of 50
    details = []
    
    for i in range(0, len(video_ids), 50):
        batch_ids = ",".join(video_ids[i:i+50])
        vres = await google_youtube_data_source.videos_list(
            part="snippet,contentDetails,statistics",
            id=batch_ids,
        )
        details.extend(vres.get("items", []))

    print("Videos (details) count:", len(details))
    for video in details:
        snippet = video.get("snippet", {})
        title = snippet.get("title", "No Title")
        desc = snippet.get("description", "No Description")
        print(f"▶️ {title}\n{desc}\n{'-'*60}")




if __name__ == "__main__":
    asyncio.run(main())
