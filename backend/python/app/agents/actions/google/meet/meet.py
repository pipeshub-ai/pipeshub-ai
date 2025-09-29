import asyncio
import json
import logging
from typing import Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.external.google.meet.meet import GoogleMeetDataSource

logger = logging.getLogger(__name__)

class GoogleMeet:
    """Google Meet tool exposed to the agents using GoogleMeetDataSource"""
    def __init__(self, client: object) -> None:
        """Initialize the Google Meet tool"""
        """
        Args:
            client: Google Meet client
        Returns:
            None
        """
        self.client = GoogleMeetDataSource(client)

    def _run_async(self, coro):
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
        app_name="meet",
        tool_name="create_meeting_space",
        parameters=[
            ToolParameter(
                name="space_config",
                type=ParameterType.OBJECT,
                description="Optional space configuration body to pass to Meet API",
                required=False
            )
        ]
    )
    def create_meeting_space(self, space_config: Optional[dict] = None) -> tuple[bool, str]:
        """Create a new Google Meet space"""
        """
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Create a default space. The Meet API rejects unknown fields like 'title'/'description'.
            # If needed, follow-up updates should use spaces_patch with supported fields and updateMask.
            space = self._run_async(self.client.spaces_create())

            return True, json.dumps({
                "space_name": space.get("name", ""),
                "meeting_code": space.get("meetingCode", ""),
                "meeting_uri": space.get("meetingUri", ""),
                "space_config": space.get("spaceConfig", {}),
                "message": "Meeting space created successfully"
            })
        except Exception as e:
            logger.error(f"Failed to create meeting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_meeting_space",
        parameters=[
            ToolParameter(
                name="space_name",
                type=ParameterType.STRING,
                description="Resource name of the space (e.g., 'spaces/{space}' or 'spaces/{meetingCode}')",
                required=True
            )
        ]
    )
    def get_meeting_space(self, space_name: str) -> tuple[bool, str]:
        """Get details about a meeting space"""
        """
        Args:
            space_name: Resource name of the space
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            space = self._run_async(self.client.spaces_get(name=space_name))

            return True, json.dumps(space)
        except Exception as e:
            logger.error(f"Failed to get meeting space: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="end_active_conference",
        parameters=[
            ToolParameter(
                name="space_name",
                type=ParameterType.STRING,
                description="Resource name of the space",
                required=True
            )
        ]
    )
    def end_active_conference(self, space_name: str) -> tuple[bool, str]:
        """End an active conference in a meeting space"""
        """
        Args:
            space_name: Resource name of the space
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            result = self._run_async(self.client.spaces_end_active_conference(name=space_name))

            return True, json.dumps({
                "message": f"Active conference ended for space {space_name}",
                "result": result
            })
        except Exception as e:
            logger.error(f"Failed to end active conference: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_records",
        parameters=[
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of conference records to return",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter condition in EBNF format",
                required=False
            )
        ]
    )
    def get_conference_records(
        self,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        filter: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get list of conference records"""
        """
        Args:
            page_size: Maximum number of records to return
            page_token: Page token for pagination
            filter: Filter condition
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            records = self._run_async(self.client.conference_records_list(
                pageSize=page_size,
                pageToken=page_token,
                filter=filter
            ))

            return True, json.dumps(records)
        except Exception as e:
            logger.error(f"Failed to get conference records: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_participants",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of participants to return",
                required=False
            ),
            ToolParameter(
                name="page_token",
                type=ParameterType.STRING,
                description="Page token for pagination",
                required=False
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter condition for participants",
                required=False
            )
        ]
    )
    def get_conference_participants(
        self,
        conference_record: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None,
        filter: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get participants in a conference record"""
        """
        Args:
            conference_record: Conference record name
            page_size: Maximum number of participants
            page_token: Page token for pagination
            filter: Filter condition
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            participants = self._run_async(self.client.conference_records_participants_list(
                parent=conference_record,
                pageSize=page_size,
                pageToken=page_token,
                filter=filter
            ))

            return True, json.dumps(participants)
        except Exception as e:
            logger.error(f"Failed to get conference participants: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_recordings",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of recordings to return",
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
    def get_conference_recordings(
        self,
        conference_record: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get recordings from a conference record"""
        """
        Args:
            conference_record: Conference record name
            page_size: Maximum number of recordings
            page_token: Page token for pagination
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            recordings = self._run_async(self.client.conference_records_recordings_list(
                parent=conference_record,
                pageSize=page_size,
                pageToken=page_token
            ))

            return True, json.dumps(recordings)
        except Exception as e:
            logger.error(f"Failed to get conference recordings: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="meet",
        tool_name="get_conference_transcripts",
        parameters=[
            ToolParameter(
                name="conference_record",
                type=ParameterType.STRING,
                description="Conference record name (e.g., 'conferenceRecords/{conference_record}')",
                required=True
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of transcripts to return",
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
    def get_conference_transcripts(
        self,
        conference_record: str,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get transcripts from a conference record"""
        """
        Args:
            conference_record: Conference record name
            page_size: Maximum number of transcripts
            page_token: Page token for pagination
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleMeetDataSource method
            transcripts = self._run_async(self.client.conference_records_transcripts_list(
                parent=conference_record,
                pageSize=page_size,
                pageToken=page_token
            ))

            return True, json.dumps(transcripts)
        except Exception as e:
            logger.error(f"Failed to get conference transcripts: {e}")
            return False, json.dumps({"error": str(e)})
