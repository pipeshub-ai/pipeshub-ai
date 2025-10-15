import asyncio
import json
import logging
import threading
from typing import Coroutine, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.posthog.posthog import PostHogClient
from app.sources.external.posthog.posthog import PostHogDataSource

logger = logging.getLogger(__name__)


class PostHog:
    """PostHog tools exposed to agents using PostHogDataSource.

    This provides CRUD operations for PostHog events and persons, plus search functionality,
    following the same pattern as Confluence and Google Meet tools.
    """

    def __init__(self, client: PostHogClient) -> None:
        """Initialize the PostHog tool with a data source wrapper.

        Args:
            client: An initialized `PostHogClient` instance
        """
        self.client = PostHogDataSource(client)
        # Dedicated background event loop for running coroutines from sync context
        self._bg_loop = asyncio.new_event_loop()
        self._bg_loop_thread = threading.Thread(
            target=self._start_background_loop,
            daemon=True
        )
        self._bg_loop_thread.start()

    def _start_background_loop(self) -> None:
        """Start the background event loop."""
        asyncio.set_event_loop(self._bg_loop)
        self._bg_loop.run_forever()

    def _run_async(self, coro: Coroutine[None, None, object]) -> object:
        """Run a coroutine safely from sync context via a dedicated loop."""
        future = asyncio.run_coroutine_threadsafe(coro, self._bg_loop)
        return future.result()

    def shutdown(self) -> None:
        """Gracefully stop the background event loop and thread."""
        try:
            if getattr(self, "_bg_loop", None) is not None and self._bg_loop.is_running():
                self._bg_loop.call_soon_threadsafe(self._bg_loop.stop)
            if getattr(self, "_bg_loop_thread", None) is not None:
                self._bg_loop_thread.join()
            if getattr(self, "_bg_loop", None) is not None:
                self._bg_loop.close()
        except Exception as exc:
            logger.warning(f"PostHog shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle PostHog response and return standardized format."""
        try:
            if hasattr(response, 'success') and response.success:
                return True, json.dumps({
                    "message": success_message,
                    "data": response.data or {}
                })
            else:
                error_msg = getattr(response, 'error', 'Unknown error')
                return False, json.dumps({"error": error_msg})
        except Exception as e:
            logger.error(f"Error handling response: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="posthog",
        tool_name="capture_event",
        description="Capture a new event in PostHog",
        parameters=[
            ToolParameter(
                name="event",
                type=ParameterType.STRING,
                description="Event name"
            ),
            ToolParameter(
                name="distinct_id",
                type=ParameterType.STRING,
                description="Distinct ID of the user"
            ),
            ToolParameter(
                name="properties",
                type=ParameterType.STRING,
                description="JSON object with event properties",
                required=False
            ),
            ToolParameter(
                name="timestamp",
                type=ParameterType.STRING,
                description="Event timestamp (ISO format)",
                required=False
            )
        ],
        returns="JSON with event capture result"
    )
    def capture_event(
        self,
        event: str,
        distinct_id: str,
        properties: Optional[str] = None,
        timestamp: Optional[str] = None
    ) -> Tuple[bool, str]:
        """Capture a new event in PostHog."""
        try:
            # Parse properties if provided
            properties_dict = None
            if properties:
                try:
                    properties_dict = json.loads(properties)
                    if not isinstance(properties_dict, dict):
                        raise ValueError("properties must be a JSON object")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for properties: {exc}"})

            response = self._run_async(
                self.client.capture_event(
                    event=event,
                    distinct_id=distinct_id,
                    properties=properties_dict,
                    timestamp=timestamp
                )
            )
            return self._handle_response(response, "Event captured successfully")
        except Exception as e:
            logger.error(f"Error capturing event: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="posthog",
        tool_name="get_event",
        description="Get a single event by ID from PostHog",
        parameters=[
            ToolParameter(
                name="event_id",
                type=ParameterType.STRING,
                description="Event ID"
            )
        ],
        returns="JSON with event data"
    )
    def get_event(self, event_id: str) -> Tuple[bool, str]:
        """Get a single event by ID from PostHog."""
        try:
            response = self._run_async(
                self.client.event(id=event_id)
            )
            return self._handle_response(response, "Event retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting event: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="posthog",
        tool_name="get_person",
        description="Get a person by ID from PostHog",
        parameters=[
            ToolParameter(
                name="person_id",
                type=ParameterType.STRING,
                description="Person ID"
            )
        ],
        returns="JSON with person data"
    )
    def get_person(self, person_id: str) -> Tuple[bool, str]:
        """Get a person by ID from PostHog."""
        try:
            response = self._run_async(
                self.client.person(id=person_id)
            )
            return self._handle_response(response, "Person retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting person: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="posthog",
        tool_name="update_person",
        description="Update person properties in PostHog",
        parameters=[
            ToolParameter(
                name="person_id",
                type=ParameterType.STRING,
                description="Person ID"
            ),
            ToolParameter(
                name="properties",
                type=ParameterType.STRING,
                description="JSON object with person properties to update"
            )
        ],
        returns="JSON with person update result"
    )
    def update_person(
        self,
        person_id: str,
        properties: str
    ) -> Tuple[bool, str]:
        """Update person properties in PostHog."""
        try:
            # Parse properties
            try:
                properties_dict = json.loads(properties)
                if not isinstance(properties_dict, dict):
                    raise ValueError("properties must be a JSON object")
            except json.JSONDecodeError as exc:
                return False, json.dumps({"error": f"Invalid JSON for properties: {exc}"})

            response = self._run_async(
                self.client.person_update(
                    id=person_id,
                    properties=properties_dict
                )
            )
            return self._handle_response(response, "Person updated successfully")
        except Exception as e:
            logger.error(f"Error updating person: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="posthog",
        tool_name="delete_person",
        description="Delete a person from PostHog",
        parameters=[
            ToolParameter(
                name="person_id",
                type=ParameterType.STRING,
                description="Person ID to delete"
            )
        ],
        returns="JSON with deletion result"
    )
    def delete_person(self, person_id: str) -> Tuple[bool, str]:
        """Delete a person from PostHog."""
        try:
            response = self._run_async(
                self.client.person_delete(id=person_id)
            )
            return self._handle_response(response, "Person deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting person: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="posthog",
        tool_name="search_events",
        description="Search for events in PostHog with filtering",
        parameters=[
            ToolParameter(
                name="after",
                type=ParameterType.STRING,
                description="Cursor for pagination (after)",
                required=False
            ),
            ToolParameter(
                name="before",
                type=ParameterType.STRING,
                description="Cursor for pagination (before)",
                required=False
            ),
            ToolParameter(
                name="distinct_id",
                type=ParameterType.STRING,
                description="Filter by distinct ID",
                required=False
            ),
            ToolParameter(
                name="event",
                type=ParameterType.STRING,
                description="Filter by event name",
                required=False
            ),
            ToolParameter(
                name="properties",
                type=ParameterType.STRING,
                description="JSON object with properties to filter by",
                required=False
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Maximum number of events to return",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_events(
        self,
        after: Optional[str] = None,
        before: Optional[str] = None,
        distinct_id: Optional[str] = None,
        event: Optional[str] = None,
        properties: Optional[str] = None,
        limit: Optional[int] = None
    ) -> Tuple[bool, str]:
        """Search for events in PostHog with filtering."""
        try:
            # Parse properties if provided
            properties_dict = None
            if properties:
                try:
                    properties_dict = json.loads(properties)
                    if not isinstance(properties_dict, dict):
                        raise ValueError("properties must be a JSON object")
                except json.JSONDecodeError as exc:
                    return False, json.dumps({"error": f"Invalid JSON for properties: {exc}"})

            response = self._run_async(
                self.client.events(
                    after=after,
                    before=before,
                    distinct_id=distinct_id,
                    event=event,
                    properties=properties_dict,
                    limit=limit
                )
            )
            return self._handle_response(response, "Events search completed successfully")
        except Exception as e:
            logger.error(f"Error searching events: {e}")
            return False, json.dumps({"error": str(e)})
