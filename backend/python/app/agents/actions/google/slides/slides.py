import asyncio
import json
import logging
from typing import Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.slides.slides import GoogleSlidesDataSource

logger = logging.getLogger(__name__)

class GoogleSlides:
    """Google Slides tool exposed to the agents using GoogleSlidesDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Slides tool"""
        """
        Args:
            client: Google Slides client
        Returns:
            None
        """
        self.client = GoogleSlidesDataSource(client)

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
        app_name="slides",
        tool_name="get_presentation",
        parameters=[
            ToolParameter(
                name="presentation_id",
                type=ParameterType.STRING,
                description="The ID of the presentation to retrieve",
                required=True
            )
        ]
    )
    def get_presentation(self, presentation_id: str) -> tuple[bool, str]:
        """Get a Google Slides presentation"""
        """
        Args:
            presentation_id: The ID of the presentation
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSlidesDataSource method
            presentation = self._run_async(self.client.presentations_get(
                presentationId=presentation_id
            ))

            return True, json.dumps(presentation)
        except Exception as e:
            logger.error(f"Failed to get presentation: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="slides",
        tool_name="create_presentation",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the presentation",
                required=False
            )
        ]
    )
    def create_presentation(self, title: Optional[str] = None) -> tuple[bool, str]:
        """Create a new Google Slides presentation"""
        """
        Args:
            title: Title of the presentation
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare presentation data
            presentation_data = {}
            if title:
                presentation_data = {
                    "title": title
                }

            # Use GoogleSlidesDataSource method
            presentation = self._run_async(self.client.presentations_create(
                body=presentation_data
            ))

            return True, json.dumps({
                "presentation_id": presentation.get("presentationId", ""),
                "title": presentation.get("title", ""),
                "revision_id": presentation.get("revisionId", ""),
                "message": "Presentation created successfully"
            })
        except Exception as e:
            logger.error(f"Failed to create presentation: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="slides",
        tool_name="batch_update_presentation",
        parameters=[
            ToolParameter(
                name="presentation_id",
                type=ParameterType.STRING,
                description="The ID of the presentation to update",
                required=True
            ),
            ToolParameter(
                name="requests",
                type=ParameterType.ARRAY,
                description="List of update requests to apply",
                required=False,
                items={"type": "object"}
            )
        ]
    )
    def batch_update_presentation(
        self,
        presentation_id: str,
        requests: Optional[list] = None
    ) -> tuple[bool, str]:
        """Apply batch updates to a Google Slides presentation"""
        """
        Args:
            presentation_id: The ID of the presentation
            requests: List of update requests
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare batch update data
            batch_update_data = {}
            if requests:
                batch_update_data["requests"] = requests

            # Use GoogleSlidesDataSource method
            result = self._run_async(self.client.presentations_batch_update(
                presentationId=presentation_id,
                body=batch_update_data
            ))

            return True, json.dumps({
                "presentation_id": presentation_id,
                "revision_id": result.get("revisionId", ""),
                "replies": result.get("replies", []),
                "message": "Presentation updated successfully"
            })
        except Exception as e:
            logger.error(f"Failed to batch update presentation: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="slides",
        tool_name="get_slide_page",
        parameters=[
            ToolParameter(
                name="presentation_id",
                type=ParameterType.STRING,
                description="The ID of the presentation",
                required=True
            ),
            ToolParameter(
                name="page_object_id",
                type=ParameterType.STRING,
                description="The object ID of the slide page",
                required=True
            )
        ]
    )
    def get_slide_page(self, presentation_id: str, page_object_id: str) -> tuple[bool, str]:
        """Get a specific slide page from a presentation"""
        """
        Args:
            presentation_id: The ID of the presentation
            page_object_id: The object ID of the slide page
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSlidesDataSource method
            page = self._run_async(self.client.presentations_pages_get(
                presentationId=presentation_id,
                pageObjectId=page_object_id
            ))

            return True, json.dumps(page)
        except Exception as e:
            logger.error(f"Failed to get slide page: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="slides",
        tool_name="get_slide_thumbnail",
        parameters=[
            ToolParameter(
                name="presentation_id",
                type=ParameterType.STRING,
                description="The ID of the presentation",
                required=True
            ),
            ToolParameter(
                name="page_object_id",
                type=ParameterType.STRING,
                description="The object ID of the slide page",
                required=True
            ),
            ToolParameter(
                name="mime_type",
                type=ParameterType.STRING,
                description="MIME type of the thumbnail (e.g., 'image/png')",
                required=False
            ),
            ToolParameter(
                name="thumbnail_size",
                type=ParameterType.STRING,
                description="Size of the thumbnail (e.g., 'LARGE', 'MEDIUM', 'SMALL')",
                required=False
            )
        ]
    )
    def get_slide_thumbnail(
        self,
        presentation_id: str,
        page_object_id: str,
        mime_type: Optional[str] = None,
        thumbnail_size: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get a thumbnail of a slide page"""
        """
        Args:
            presentation_id: The ID of the presentation
            page_object_id: The object ID of the slide page
            mime_type: MIME type of the thumbnail
            thumbnail_size: Size of the thumbnail
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleSlidesDataSource method
            thumbnail = self._run_async(self.client.presentations_pages_get_thumbnail(
                presentationId=presentation_id,
                pageObjectId=page_object_id,
                thumbnailProperties_mimeType=mime_type,
                thumbnailProperties_thumbnailSize=thumbnail_size
            ))

            return True, json.dumps(thumbnail)
        except Exception as e:
            logger.error(f"Failed to get slide thumbnail: {e}")
            return False, json.dumps({"error": str(e)})
