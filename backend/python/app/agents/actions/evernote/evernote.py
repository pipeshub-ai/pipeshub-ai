import asyncio
import json
import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.evernote.evernote import EvernoteClient, EvernoteResponse
from app.sources.external.evernote.evernote import EvernoteDataSource

logger = logging.getLogger(__name__)


class Evernote:
    """Evernote tools exposed to the agents using EvernoteDataSource"""

    def __init__(self, client: EvernoteClient) -> None:
        """Initialize the Evernote tool with a data source wrapper.
        Args:
            client: An initialized `EvernoteClient` instance
        """
        self.client = EvernoteDataSource(client)
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

    def _run_async(self, coro) -> EvernoteResponse:
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
            logger.warning(f"Evernote shutdown encountered an issue: {exc}")

    def _handle_response(
        self,
        response: EvernoteResponse,
        success_message: str
    ) -> Tuple[bool, str]:
        """Handle EvernoteResponse and return standardized tuple."""
        if response.success:
            return True, json.dumps({
                "message": success_message,
                "data": response.data or {}
            })
        return False, json.dumps({
            "error": response.error or "Unknown error"
        })

    @tool(
        app_name="evernote",
        tool_name="create_note",
        description="Create a new note in Evernote",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="The title of the note (required)"
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="The content of the note in ENML format (required)"
            ),
            ToolParameter(
                name="notebook_guid",
                type=ParameterType.STRING,
                description="The GUID of the notebook to create the note in",
                required=False
            ),
            ToolParameter(
                name="tag_guids",
                type=ParameterType.ARRAY,
                description="Array of tag GUIDs to assign to the note",
                required=False
            ),
            ToolParameter(
                name="resources",
                type=ParameterType.ARRAY,
                description="Array of resource objects (attachments)",
                required=False
            )
        ],
        returns="JSON with created note details"
    )
    def create_note(
        self,
        title: str,
        content: str,
        notebook_guid: Optional[str] = None,
        tag_guids: Optional[List[str]] = None,
        resources: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str]:
        try:
            note_data = {
                "title": title,
                "content": content,
                "notebook_guid": notebook_guid,
                "tag_guids": tag_guids,
                "resources": resources
            }
            # Remove None values
            note_data = {k: v for k, v in note_data.items() if v is not None}

            response = self._run_async(self.client.create_note(**note_data))
            return self._handle_response(response, "Note created successfully")
        except Exception as e:
            logger.error(f"Error creating note: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="get_note",
        description="Get details of a specific note",
        parameters=[
            ToolParameter(
                name="note_guid",
                type=ParameterType.STRING,
                description="The GUID of the note to retrieve (required)"
            ),
            ToolParameter(
                name="include_content",
                type=ParameterType.BOOLEAN,
                description="Whether to include the note content",
                required=False
            ),
            ToolParameter(
                name="include_resources_data",
                type=ParameterType.BOOLEAN,
                description="Whether to include resource data",
                required=False
            )
        ],
        returns="JSON with note details"
    )
    def get_note(
        self,
        note_guid: str,
        include_content: Optional[bool] = None,
        include_resources_data: Optional[bool] = None
    ) -> Tuple[bool, str]:
        try:
            get_params = {
                "note_guid": note_guid,
                "include_content": include_content,
                "include_resources_data": include_resources_data
            }
            # Remove None values
            get_params = {k: v for k, v in get_params.items() if v is not None}

            response = self._run_async(self.client.get_note(**get_params))
            return self._handle_response(response, "Note retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting note: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="update_note",
        description="Update an existing note",
        parameters=[
            ToolParameter(
                name="note_guid",
                type=ParameterType.STRING,
                description="The GUID of the note to update (required)"
            ),
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Updated title",
                required=False
            ),
            ToolParameter(
                name="content",
                type=ParameterType.STRING,
                description="Updated content in ENML format",
                required=False
            ),
            ToolParameter(
                name="notebook_guid",
                type=ParameterType.STRING,
                description="Updated notebook GUID",
                required=False
            ),
            ToolParameter(
                name="tag_guids",
                type=ParameterType.ARRAY,
                description="Updated array of tag GUIDs",
                required=False
            ),
            ToolParameter(
                name="resources",
                type=ParameterType.ARRAY,
                description="Updated array of resource objects",
                required=False
            )
        ],
        returns="JSON with updated note details"
    )
    def update_note(
        self,
        note_guid: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        notebook_guid: Optional[str] = None,
        tag_guids: Optional[List[str]] = None,
        resources: Optional[List[Dict[str, Any]]] = None
    ) -> Tuple[bool, str]:
        try:
            update_data = {
                "note_guid": note_guid,
                "title": title,
                "content": content,
                "notebook_guid": notebook_guid,
                "tag_guids": tag_guids,
                "resources": resources
            }
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}

            response = self._run_async(self.client.update_note(**update_data))
            return self._handle_response(response, "Note updated successfully")
        except Exception as e:
            logger.error(f"Error updating note: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="delete_note",
        description="Delete a note from Evernote",
        parameters=[
            ToolParameter(
                name="note_guid",
                type=ParameterType.STRING,
                description="The GUID of the note to delete (required)"
            )
        ],
        returns="JSON with deletion confirmation"
    )
    def delete_note(self, note_guid: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.delete_note(note_guid=note_guid))
            return self._handle_response(response, "Note deleted successfully")
        except Exception as e:
            logger.error(f"Error deleting note: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="create_notebook",
        description="Create a new notebook in Evernote",
        parameters=[
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="The name of the notebook (required)"
            ),
            ToolParameter(
                name="stack",
                type=ParameterType.STRING,
                description="The stack name to organize notebooks",
                required=False
            ),
            ToolParameter(
                name="default_notebook",
                type=ParameterType.BOOLEAN,
                description="Whether this should be the default notebook",
                required=False
            )
        ],
        returns="JSON with created notebook details"
    )
    def create_notebook(
        self,
        name: str,
        stack: Optional[str] = None,
        default_notebook: Optional[bool] = None
    ) -> Tuple[bool, str]:
        try:
            notebook_data = {
                "name": name,
                "stack": stack,
                "default_notebook": default_notebook
            }
            # Remove None values
            notebook_data = {k: v for k, v in notebook_data.items() if v is not None}

            response = self._run_async(self.client.create_notebook(**notebook_data))
            return self._handle_response(response, "Notebook created successfully")
        except Exception as e:
            logger.error(f"Error creating notebook: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="get_notebook",
        description="Get details of a specific notebook",
        parameters=[
            ToolParameter(
                name="notebook_guid",
                type=ParameterType.STRING,
                description="The GUID of the notebook to retrieve (required)"
            )
        ],
        returns="JSON with notebook details"
    )
    def get_notebook(self, notebook_guid: str) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_notebook(notebook_guid=notebook_guid))
            return self._handle_response(response, "Notebook retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting notebook: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="update_notebook",
        description="Update an existing notebook",
        parameters=[
            ToolParameter(
                name="notebook_guid",
                type=ParameterType.STRING,
                description="The GUID of the notebook to update (required)"
            ),
            ToolParameter(
                name="name",
                type=ParameterType.STRING,
                description="Updated notebook name",
                required=False
            ),
            ToolParameter(
                name="stack",
                type=ParameterType.STRING,
                description="Updated stack name",
                required=False
            ),
            ToolParameter(
                name="default_notebook",
                type=ParameterType.BOOLEAN,
                description="Updated default notebook setting",
                required=False
            )
        ],
        returns="JSON with updated notebook details"
    )
    def update_notebook(
        self,
        notebook_guid: str,
        name: Optional[str] = None,
        stack: Optional[str] = None,
        default_notebook: Optional[bool] = None
    ) -> Tuple[bool, str]:
        try:
            update_data = {
                "notebook_guid": notebook_guid,
                "name": name,
                "stack": stack,
                "default_notebook": default_notebook
            }
            # Remove None values
            update_data = {k: v for k, v in update_data.items() if v is not None}

            response = self._run_async(self.client.update_notebook(**update_data))
            return self._handle_response(response, "Notebook updated successfully")
        except Exception as e:
            logger.error(f"Error updating notebook: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="get_default_notebook",
        description="Get the default notebook for the user",
        parameters=[],
        returns="JSON with default notebook details"
    )
    def get_default_notebook(self) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_default_notebook())
            return self._handle_response(response, "Default notebook retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting default notebook: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="search_notes",
        description="Search for notes with various filters",
        parameters=[
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Search query string (required)"
            ),
            ToolParameter(
                name="notebook_guid",
                type=ParameterType.STRING,
                description="Filter by notebook GUID",
                required=False
            ),
            ToolParameter(
                name="tag_guids",
                type=ParameterType.ARRAY,
                description="Filter by tag GUIDs",
                required=False
            ),
            ToolParameter(
                name="max_results",
                type=ParameterType.NUMBER,
                description="Maximum number of results to return",
                required=False
            ),
            ToolParameter(
                name="order",
                type=ParameterType.NUMBER,
                description="Sort order (0=Created, 1=Updated, 2=Relevance, 3=UpdateSequenceNumber, 4=Title)",
                required=False
            )
        ],
        returns="JSON with search results"
    )
    def search_notes(
        self,
        query: str,
        notebook_guid: Optional[str] = None,
        tag_guids: Optional[List[str]] = None,
        max_results: Optional[int] = None,
        order: Optional[int] = None
    ) -> Tuple[bool, str]:
        try:
            search_params = {
                "query": query,
                "notebook_guid": notebook_guid,
                "tag_guids": tag_guids,
                "max_results": max_results,
                "order": order
            }
            # Remove None values
            search_params = {k: v for k, v in search_params.items() if v is not None}

            response = self._run_async(self.client.search_notes(**search_params))
            return self._handle_response(response, "Note search completed successfully")
        except Exception as e:
            logger.error(f"Error searching notes: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="evernote",
        tool_name="get_user_info",
        description="Get information about the authenticated user",
        parameters=[],
        returns="JSON with user information"
    )
    def get_user_info(self) -> Tuple[bool, str]:
        try:
            response = self._run_async(self.client.get_user_info())
            return self._handle_response(response, "User information retrieved successfully")
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            return False, json.dumps({"error": str(e)})
