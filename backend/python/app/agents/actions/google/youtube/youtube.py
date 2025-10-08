import asyncio
import json
import logging
from typing import Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.youtube.youtube import YouTubeDataSource

logger = logging.getLogger(__name__)

class YouTube:
    """YouTube tool exposed to the agents using YouTubeDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the YouTube tool"""
        """
        Args:
            client: YouTube client
        Returns:
            None
        """
        self.client = YouTubeDataSource(client)

    def _run_async(self, coro) -> HTTPResponse: # type: ignore [valid method]
        """Helper method to run async operations in sync context"""
        try:
            asyncio.get_running_loop()
            # We're in an async context, use asyncio.run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            # No running loop, we can use asyncio.run
            return asyncio.run(coro)

    @tool(
        app_name="youtube",
        tool_name="search_videos",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query for videos",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of results to return",
                required=False
            ),
            ToolParameter(
                name="order",
                type=ParameterType.STRING,
                description="Sort order (relevance, date, rating, viewCount, title)",
                required=False
            ),
            ToolParameter(
                name="video_duration",
                type=ParameterType.STRING,
                description="Video duration filter (short, medium, long)",
                required=False
            ),
            ToolParameter(
                name="video_definition",
                type=ParameterType.STRING,
                description="Video definition filter (high, standard)",
                required=False
            )
        ]
    )
    def search_videos(
        self,
        query: str,
        max_results: Optional[int] = None,
        order: Optional[str] = None,
        video_duration: Optional[str] = None,
        video_definition: Optional[str] = None
    ) -> tuple[bool, str]:
        """Search for YouTube videos"""
        """
        Args:
            query: Search query
            max_results: Maximum number of results
            order: Sort order
            video_duration: Video duration filter
            video_definition: Video definition filter
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use YouTubeDataSource method
            results = self._run_async(self.client.search_list(
                part="snippet",
                q=query,
                type="video",
                maxResults=max_results,
                order=order,
                videoDuration=video_duration,
                videoDefinition=video_definition
            ))

            return True, json.dumps(results)
        except Exception as e:
            logger.error(f"Failed to search videos: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="youtube",
        tool_name="get_video_details",
        parameters=[
            ToolParameter(
                name="video_id",
                type=ParameterType.STRING,
                description="The ID of the video",
                required=True
            ),
            ToolParameter(
                name="part",
                type=ParameterType.STRING,
                description="Parts to retrieve (snippet, statistics, contentDetails, etc.)",
                required=False
            )
        ]
    )
    def get_video_details(
        self,
        video_id: str,
        part: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get details of a specific YouTube video"""
        """
        Args:
            video_id: The ID of the video
            part: Parts to retrieve
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            if part is None:
                part = "snippet,statistics,contentDetails"

            # Use YouTubeDataSource method
            video = self._run_async(self.client.videos_list(
                part=part,
                id=video_id
            ))

            return True, json.dumps(video)
        except Exception as e:
            logger.error(f"Failed to get video details: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="youtube",
        tool_name="get_channel_info",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.STRING,
                description="The ID of the channel",
                required=False
            ),
            ToolParameter(
                name="for_username",
                type=ParameterType.STRING,
                description="Username of the channel",
                required=False
            ),
            ToolParameter(
                name="mine",
                type=ParameterType.BOOLEAN,
                description="Whether to get current user's channel",
                required=False
            )
        ]
    )
    def get_channel_info(
        self,
        channel_id: Optional[str] = None,
        for_username: Optional[str] = None,
        mine: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get information about a YouTube channel"""
        """
        Args:
            channel_id: The ID of the channel
            for_username: Username of the channel
            mine: Whether to get current user's channel
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use YouTubeDataSource method
            channel = self._run_async(self.client.channels_list(
                part="snippet,statistics,contentDetails",
                id=channel_id,
                forUsername=for_username,
                mine=mine
            ))

            return True, json.dumps(channel)
        except Exception as e:
            logger.error(f"Failed to get channel info: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="youtube",
        tool_name="get_playlist_videos",
        parameters=[
            ToolParameter(
                name="playlist_id",
                type=ParameterType.STRING,
                description="The ID of the playlist",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of videos to return",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            )
        ]
    )
    def get_playlist_videos(
        self,
        playlist_id: str,
        max_results: Optional[int] = None,
        page_token: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get videos from a YouTube playlist"""
        """
        Args:
            playlist_id: The ID of the playlist
            max_results: Maximum number of videos
            page_token: Page token for pagination
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use YouTubeDataSource method
            videos = self._run_async(self.client.playlist_items_list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=max_results,
                pageToken=page_token
            ))

            return True, json.dumps(videos)
        except Exception as e:
            logger.error(f"Failed to get playlist videos: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="youtube",
        tool_name="get_user_playlists",
        parameters=[
            ToolParameter(
                name="channel_id",
                type=ParameterType.STRING,
                description="The ID of the channel",
                required=False
            ),
            ToolParameter(
                name="mine",
                type=ParameterType.BOOLEAN,
                description="Whether to get current user's playlists",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of playlists to return",
                required=False
            )
        ]
    )
    def get_user_playlists(
        self,
        channel_id: Optional[str] = None,
        mine: Optional[bool] = None,
        max_results: Optional[int] = None
    ) -> tuple[bool, str]:
        """Get playlists from a YouTube channel"""
        """
        Args:
            channel_id: The ID of the channel
            mine: Whether to get current user's playlists
            max_results: Maximum number of playlists
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use YouTubeDataSource method
            playlists = self._run_async(self.client.playlists_list(
                part="snippet,contentDetails",
                channelId=channel_id,
                mine=mine,
                maxResults=max_results
            ))

            return True, json.dumps(playlists)
        except Exception as e:
            logger.error(f"Failed to get user playlists: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="youtube",
        tool_name="get_video_comments",
        parameters=[
            ToolParameter(
                name="video_id",
                type=ParameterType.STRING,
                description="The ID of the video",
                required=True
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of comments to return",
                required=False
            ),
            ToolParameter(
                name="order",
                type=ParameterType.STRING,
                description="Sort order (time, relevance)",
                required=False
            )
        ]
    )
    def get_video_comments(
        self,
        video_id: str,
        max_results: Optional[int] = None,
        order: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get comments for a YouTube video"""
        """
        Args:
            video_id: The ID of the video
            max_results: Maximum number of comments
            order: Sort order
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use YouTubeDataSource method
            comments = self._run_async(self.client.comment_threads_list(
                part="snippet,replies",
                videoId=video_id,
                maxResults=max_results,
                order=order
            ))

            return True, json.dumps(comments)
        except Exception as e:
            logger.error(f"Failed to get video comments: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="youtube",
        tool_name="get_trending_videos",
        parameters=[
            ToolParameter(
                name="region_code",
                type=ParameterType.STRING,
                description="Region code for trending videos (e.g., 'US', 'GB')",
                required=False
            ),
            ToolParameter(
                name="category_id",
                type=ParameterType.STRING,
                description="Category ID for trending videos",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.INTEGER,
                description="Maximum number of videos to return",
                required=False
            )
        ]
    )
    def get_trending_videos(
        self,
        region_code: Optional[str] = None,
        category_id: Optional[str] = None,
        max_results: Optional[int] = None
    ) -> tuple[bool, str]:
        """Get trending YouTube videos"""
        """
        Args:
            region_code: Region code for trending videos
            category_id: Category ID for trending videos
            max_results: Maximum number of videos
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use YouTubeDataSource method
            videos = self._run_async(self.client.videos_list(
                part="snippet,statistics,contentDetails",
                chart="mostPopular",
                regionCode=region_code,
                videoCategoryId=category_id,
                maxResults=max_results
            ))

            return True, json.dumps(videos)
        except Exception as e:
            logger.error(f"Failed to get trending videos: {e}")
            return False, json.dumps({"error": str(e)})
