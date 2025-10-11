import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.forms.forms import GoogleFormsDataSource

logger = logging.getLogger(__name__)

class GoogleForms:
    """Google Forms tool exposed to the agents using GoogleFormsDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Forms tool"""
        """
        Args:
            client: Google Forms client
        Returns:
            None
        """
        self.client = GoogleFormsDataSource(client)

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
        app_name="forms",
        tool_name="create_form",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the form",
                required=False
            ),
            ToolParameter(
                name="description",
                type=ParameterType.STRING,
                description="Description of the form",
                required=False
            )
        ]
    )
    def create_form(
        self,
        title: Optional[str] = None,
        description: Optional[str] = None
    ) -> tuple[bool, str]:
        """Create a new Google Form"""
        """
        Args:
            title: Title of the form
            description: Description of the form
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare form data
            form_data = {}
            if title or description:
                form_data["info"] = {}
                if title:
                    form_data["info"]["title"] = title
                if description:
                    form_data["info"]["description"] = description

            # Use GoogleFormsDataSource method
            form = self._run_async(self.client.forms_create(
                body=form_data
            ))

            return True, json.dumps({
                "form_id": form.get("formId", ""),
                "title": form.get("info", {}).get("title", ""),
                "description": form.get("info", {}).get("description", ""),
                "revision_id": form.get("revisionId", ""),
                "responder_uri": form.get("responderUri", ""),
                "message": "Form created successfully"
            })
        except Exception as e:
            logger.error(f"Failed to create form: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="forms",
        tool_name="get_form",
        parameters=[
            ToolParameter(
                name="form_id",
                type=ParameterType.STRING,
                description="The ID of the form to retrieve",
                required=True
            )
        ]
    )
    def get_form(self, form_id: str) -> tuple[bool, str]:
        """Get a Google Form"""
        """
        Args:
            form_id: The ID of the form
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleFormsDataSource method
            form = self._run_async(self.client.forms_get(
                formId=form_id
            ))

            return True, json.dumps(form)
        except Exception as e:
            logger.error(f"Failed to get form: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="forms",
        tool_name="batch_update_form",
        parameters=[
            ToolParameter(
                name="form_id",
                type=ParameterType.STRING,
                description="The ID of the form to update",
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
    def batch_update_form(
        self,
        form_id: str,
        requests: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[bool, str]:
        """Apply batch updates to a Google Form"""
        """
        Args:
            form_id: The ID of the form
            requests: List of update requests
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare batch update data
            batch_update_data = {}
            if requests:
                batch_update_data["requests"] = requests

            # Use GoogleFormsDataSource method
            result = self._run_async(self.client.forms_batch_update(
                formId=form_id,
                body=batch_update_data
            ))

            return True, json.dumps({
                "form_id": form_id,
                "revision_id": result.get("revisionId", ""),
                "replies": result.get("replies", []),
                "message": "Form updated successfully"
            })
        except Exception as e:
            logger.error(f"Failed to batch update form: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="forms",
        tool_name="get_form_responses",
        parameters=[
            ToolParameter(
                name="form_id",
                type=ParameterType.STRING,
                description="The ID of the form",
                required=True
            ),
            ToolParameter(
                name="filter",
                type=ParameterType.STRING,
                description="Filter for responses",
                required=False
            ),
            ToolParameter(
                name="page_size",
                type=ParameterType.INTEGER,
                description="Maximum number of responses to return",
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
    def get_form_responses(
        self,
        form_id: str,
        filter: Optional[str] = None,
        page_size: Optional[int] = None,
        page_token: Optional[str] = None
    ) -> tuple[bool, str]:
        """Get responses from a Google Form"""
        """
        Args:
            form_id: The ID of the form
            filter: Filter for responses
            page_size: Maximum number of responses
            page_token: Page token for pagination
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleFormsDataSource method
            responses = self._run_async(self.client.forms_responses_list(
                formId=form_id,
                filter=filter,
                pageSize=page_size,
                pageToken=page_token
            ))

            return True, json.dumps(responses)
        except Exception as e:
            logger.error(f"Failed to get form responses: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="forms",
        tool_name="get_form_response",
        parameters=[
            ToolParameter(
                name="form_id",
                type=ParameterType.STRING,
                description="The ID of the form",
                required=True
            ),
            ToolParameter(
                name="response_id",
                type=ParameterType.STRING,
                description="The ID of the response",
                required=True
            )
        ]
    )
    def get_form_response(
        self,
        form_id: str,
        response_id: str
    ) -> tuple[bool, str]:
        """Get a specific response from a Google Form"""
        """
        Args:
            form_id: The ID of the form
            response_id: The ID of the response
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleFormsDataSource method
            response = self._run_async(self.client.forms_responses_get(
                formId=form_id,
                responseId=response_id
            ))

            return True, json.dumps(response)
        except Exception as e:
            logger.error(f"Failed to get form response: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="forms",
        tool_name="set_publish_settings",
        parameters=[
            ToolParameter(
                name="form_id",
                type=ParameterType.STRING,
                description="The ID of the form",
                required=True
            ),
            ToolParameter(
                name="publish_settings",
                type=ParameterType.OBJECT,
                description="Publish settings for the form",
                required=False
            )
        ]
    )
    def set_publish_settings(
        self,
        form_id: str,
        publish_settings: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str]:
        """Set publish settings for a Google Form"""
        """
        Args:
            form_id: The ID of the form
            publish_settings: Publish settings
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare publish settings data
            settings_data = {}
            if publish_settings:
                settings_data = publish_settings

            # Use GoogleFormsDataSource method
            result = self._run_async(self.client.forms_set_publish_settings(
                formId=form_id,
                body=settings_data
            ))

            return True, json.dumps({
                "form_id": form_id,
                "revision_id": result.get("revisionId", ""),
                "message": "Publish settings updated successfully"
            })
        except Exception as e:
            logger.error(f"Failed to set publish settings: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="forms",
        tool_name="create_watch",
        parameters=[
            ToolParameter(
                name="form_id",
                type=ParameterType.STRING,
                description="The ID of the form",
                required=True
            ),
            ToolParameter(
                name="watch_settings",
                type=ParameterType.OBJECT,
                description="Watch settings for the form",
                required=False
            )
        ]
    )
    def create_watch(
        self,
        form_id: str,
        watch_settings: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str]:
        """Create a watch for form changes"""
        """
        Args:
            form_id: The ID of the form
            watch_settings: Watch settings
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare watch data
            watch_data = {}
            if watch_settings:
                watch_data = watch_settings

            # Use GoogleFormsDataSource method
            watch = self._run_async(self.client.forms_watches_create(
                formId=form_id,
                body=watch_data
            ))

            return True, json.dumps({
                "form_id": form_id,
                "watch_id": watch.get("id", ""),
                "expiration": watch.get("expiration", ""),
                "message": "Watch created successfully"
            })
        except Exception as e:
            logger.error(f"Failed to create watch: {e}")
            return False, json.dumps({"error": str(e)})
