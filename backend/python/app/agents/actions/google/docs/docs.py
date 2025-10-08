import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter
from app.sources.client.google.google import GoogleClient
from app.sources.client.http.http_response import HTTPResponse
from app.sources.external.google.docs.docs import GoogleDocsDataSource

logger = logging.getLogger(__name__)

class GoogleDocs:
    """Google Docs tool exposed to the agents using GoogleDocsDataSource"""
    def __init__(self, client: GoogleClient) -> None:
        """Initialize the Google Docs tool"""
        """
        Args:
            client: Google Docs client
        Returns:
            None
        """
        self.client = GoogleDocsDataSource(client)

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
        app_name="docs",
        tool_name="get_document",
        parameters=[
            ToolParameter(
                name="document_id",
                type=ParameterType.STRING,
                description="The ID of the document to retrieve",
                required=True
            ),
            ToolParameter(
                name="suggestions_view_mode",
                type=ParameterType.STRING,
                description="Mode for viewing suggestions (DEFAULT_FOR_CURRENT_ACCESS, SUGGESTIONS_INLINE)",
                required=False
            ),
            ToolParameter(
                name="include_tabs_content",
                type=ParameterType.BOOLEAN,
                description="Whether to include tabs content",
                required=False
            )
        ]
    )
    def get_document(
        self,
        document_id: str,
        suggestions_view_mode: Optional[str] = None,
        include_tabs_content: Optional[bool] = None
    ) -> tuple[bool, str]:
        """Get a Google Docs document"""
        """
        Args:
            document_id: The ID of the document
            suggestions_view_mode: Mode for viewing suggestions
            include_tabs_content: Whether to include tabs content
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Use GoogleDocsDataSource method
            document = self._run_async(self.client.documents_get(
                documentId=document_id,
                suggestionsViewMode=suggestions_view_mode,
                includeTabsContent=include_tabs_content
            ))

            return True, json.dumps(document)
        except Exception as e:
            logger.error(f"Failed to get document: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="docs",
        tool_name="create_document",
        parameters=[
            ToolParameter(
                name="title",
                type=ParameterType.STRING,
                description="Title of the document",
                required=False
            ),
            ToolParameter(
                name="document_id",
                type=ParameterType.STRING,
                description="Custom document ID (optional)",
                required=False
            )
        ]
    )
    def create_document(
        self,
        title: Optional[str] = None,
        document_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """Create a new Google Docs document"""
        """
        Args:
            title: Title of the document
            document_id: Custom document ID
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare document data
            document_data = {}
            if title:
                document_data["title"] = title
            if document_id:
                document_data["documentId"] = document_id

            # Use GoogleDocsDataSource method
            document = self._run_async(self.client.documents_create(
                documentId=document_id,
                title=title,
                body=document_data
            ))

            return True, json.dumps({
                "document_id": document.get("documentId", ""),
                "title": document.get("title", ""),
                "revision_id": document.get("revisionId", ""),
                "url": f"https://docs.google.com/document/d/{document.get('documentId', '')}/edit",
                "message": "Document created successfully"
            })
        except Exception as e:
            logger.error(f"Failed to create document: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="docs",
        tool_name="batch_update_document",
        parameters=[
            ToolParameter(
                name="document_id",
                type=ParameterType.STRING,
                description="The ID of the document to update",
                required=True
            ),
            ToolParameter(
                name="requests",
                type=ParameterType.ARRAY,
                description="List of update requests to apply",
                required=False,
                items={"type": "object"}
            ),
            ToolParameter(
                name="write_control",
                type=ParameterType.OBJECT,
                description="Write control settings",
                required=False
            )
        ]
    )
    def batch_update_document(
        self,
        document_id: str,
        requests: Optional[List[Dict[str, Any]]] = None,
        write_control: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str]:
        """Apply batch updates to a Google Docs document"""
        """
        Args:
            document_id: The ID of the document
            requests: List of update requests
            write_control: Write control settings
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare batch update data
            batch_update_data = {}
            if requests:
                batch_update_data["requests"] = requests
            if write_control:
                batch_update_data["writeControl"] = write_control

            # Use GoogleDocsDataSource method
            result = self._run_async(self.client.documents_batch_update(
                documentId=document_id,
                requests=requests,
                writeControl=write_control,
                body=batch_update_data
            ))

            return True, json.dumps({
                "document_id": document_id,
                "revision_id": result.get("revisionId", ""),
                "replies": result.get("replies", []),
                "message": "Document updated successfully"
            })
        except Exception as e:
            logger.error(f"Failed to batch update document: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="docs",
        tool_name="insert_text",
        parameters=[
            ToolParameter(
                name="document_id",
                type=ParameterType.STRING,
                description="The ID of the document",
                required=True
            ),
            ToolParameter(
                name="text",
                type=ParameterType.STRING,
                description="Text to insert",
                required=True
            ),
            ToolParameter(
                name="location_index",
                type=ParameterType.INTEGER,
                description="Index where to insert the text",
                required=False
            )
        ]
    )
    def insert_text(
        self,
        document_id: str,
        text: str,
        location_index: Optional[int] = None
    ) -> tuple[bool, str]:
        """Insert text into a Google Docs document"""
        """
        Args:
            document_id: The ID of the document
            text: Text to insert
            location_index: Index where to insert the text
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare insert text request
            if location_index is None:
                location_index = 1  # Default to beginning of document

            requests = [{
                "insertText": {
                    "location": {
                        "index": location_index
                    },
                    "text": text
                }
            }]

            # Use GoogleDocsDataSource method
            result = self._run_async(self.client.documents_batch_update(
                documentId=document_id,
                requests=requests,
                body={"requests": requests}
            ))

            return True, json.dumps({
                "document_id": document_id,
                "text_inserted": text,
                "location_index": location_index,
                "revision_id": result.get("revisionId", ""),
                "message": "Text inserted successfully"
            })
        except Exception as e:
            logger.error(f"Failed to insert text: {e}")
            return False, json.dumps({"error": str(e)})

    @tool(
        app_name="docs",
        tool_name="replace_text",
        parameters=[
            ToolParameter(
                name="document_id",
                type=ParameterType.STRING,
                description="The ID of the document",
                required=True
            ),
            ToolParameter(
                name="old_text",
                type=ParameterType.STRING,
                description="Text to replace",
                required=True
            ),
            ToolParameter(
                name="new_text",
                type=ParameterType.STRING,
                description="New text to replace with",
                required=True
            )
        ]
    )
    def replace_text(
        self,
        document_id: str,
        old_text: str,
        new_text: str
    ) -> tuple[bool, str]:
        """Replace text in a Google Docs document"""
        """
        Args:
            document_id: The ID of the document
            old_text: Text to replace
            new_text: New text to replace with
        Returns:
            tuple[bool, str]: True if successful, False otherwise
        """
        try:
            # Prepare replace text request
            requests = [{
                "replaceAllText": {
                    "containsText": {
                        "text": old_text
                    },
                    "replaceText": new_text
                }
            }]

            # Use GoogleDocsDataSource method
            result = self._run_async(self.client.documents_batch_update(
                documentId=document_id,
                requests=requests,
                body={"requests": requests}
            ))

            return True, json.dumps({
                "document_id": document_id,
                "old_text": old_text,
                "new_text": new_text,
                "occurrences_changed": result.get("replies", [{}])[0].get("replaceAllText", {}).get("occurrencesChanged", 0),
                "revision_id": result.get("revisionId", ""),
                "message": "Text replaced successfully"
            })
        except Exception as e:
            logger.error(f"Failed to replace text: {e}")
            return False, json.dumps({"error": str(e)})
