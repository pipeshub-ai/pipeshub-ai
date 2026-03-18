"""
Quip REST API DataSource - Auto-generated API wrapper

Generated from Quip Automation API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.quip.quip import QuipClient, QuipResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class QuipDataSource:
    """Quip REST API DataSource

    Provides async wrapper methods for Quip Automation API operations:
    - Users (current user, get user, contacts)
    - Threads/Documents (get, create, edit, search, recent)
    - Messages/Comments (get, create)
    - Folders (get, create, update, members)

    The base URL is https://platform.quip.com/1.

    All methods return QuipResponse objects.
    """

    def __init__(self, client: QuipClient) -> None:
        """Initialize with QuipClient.

        Args:
            client: QuipClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'QuipDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> QuipClient:
        """Return the underlying QuipClient."""
        return self._client

    async def get_current_user(
        self
    ) -> QuipResponse:
        """Get the authenticated user's information

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/users/current"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def get_user(
        self,
        user_id: str
    ) -> QuipResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def get_users(
        self,
        user_ids: str
    ) -> QuipResponse:
        """Get multiple users by IDs (comma-separated)

        Args:
            user_ids: Comma-separated user IDs

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/users/{user_ids}".format(user_ids=user_ids)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_users")

    async def get_contacts(
        self
    ) -> QuipResponse:
        """Get the authenticated user's contacts

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/users/contacts"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contacts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_contacts")

    async def get_thread(
        self,
        thread_id: str
    ) -> QuipResponse:
        """Get a specific thread (document) by ID

        Args:
            thread_id: The thread ID

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/{thread_id}".format(thread_id=thread_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_thread" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_thread")

    async def get_threads(
        self,
        thread_ids: str
    ) -> QuipResponse:
        """Get multiple threads by IDs (comma-separated)

        Args:
            thread_ids: Comma-separated thread IDs

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/{thread_ids}".format(thread_ids=thread_ids)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_threads" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_threads")

    async def get_recent_threads(
        self,
        *,
        count: int | None = None,
        max_updated_usec: int | None = None
    ) -> QuipResponse:
        """Get recently accessed threads for the authenticated user

        Args:
            count: Number of threads to return
            max_updated_usec: Max updated time in microseconds (for pagination)

        Returns:
            QuipResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if count is not None:
            query_params['count'] = str(count)
        if max_updated_usec is not None:
            query_params['max_updated_usec'] = str(max_updated_usec)

        url = self.base_url + "/threads/recent"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_recent_threads" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_recent_threads")

    async def search_threads(
        self,
        query: str,
        *,
        count: int | None = None,
        only_match_titles: bool | None = None
    ) -> QuipResponse:
        """Search for threads (documents)

        Args:
            query: Search query string
            count: Number of results to return
            only_match_titles: Only match thread titles

        Returns:
            QuipResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['query'] = query
        if count is not None:
            query_params['count'] = str(count)
        if only_match_titles is not None:
            query_params['only_match_titles'] = str(only_match_titles).lower()

        url = self.base_url + "/threads/search"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_threads" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute search_threads")

    async def create_document(
        self,
        content: str,
        *,
        title: str | None = None,
        content_format: str | None = None,
        member_ids: list[str] | None = None,
        thread_type: str | None = None
    ) -> QuipResponse:
        """Create a new document thread

        Args:
            content: HTML content of the document
            title: Document title
            content_format: Content format ('html' or 'markdown')
            member_ids: List of member IDs to add
            thread_type: Thread type (document, spreadsheet)

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/new-document"

        body: dict[str, Any] = {}
        body['content'] = content
        if title is not None:
            body['title'] = title
        if content_format is not None:
            body['format'] = content_format
        if member_ids is not None:
            body['member_ids'] = member_ids
        if thread_type is not None:
            body['type'] = thread_type

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_document" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute create_document")

    async def edit_document(
        self,
        thread_id: str,
        *,
        content: str | None = None,
        content_format: str | None = None,
        location: int | None = None,
        section_id: str | None = None
    ) -> QuipResponse:
        """Edit an existing document thread

        Args:
            thread_id: The thread ID to edit
            content: New HTML content
            content_format: Content format ('html' or 'markdown')
            location: Insert location (0=beginning, 1=end, 2=after_section, 3=before_section, 4=replace_section, 5=delete_section)
            section_id: Section ID for location-based edits

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/edit-document"

        body: dict[str, Any] = {}
        body['thread_id'] = thread_id
        if content is not None:
            body['content'] = content
        if content_format is not None:
            body['format'] = content_format
        if location is not None:
            body['location'] = location
        if section_id is not None:
            body['section_id'] = section_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed edit_document" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute edit_document")

    async def add_thread_members(
        self,
        thread_id: str,
        member_ids: list[str]
    ) -> QuipResponse:
        """Add members to a thread

        Args:
            thread_id: The thread ID
            member_ids: List of user IDs to add as members

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/add-members"

        body: dict[str, Any] = {}
        body['thread_id'] = thread_id
        body['member_ids'] = member_ids

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_thread_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute add_thread_members")

    async def remove_thread_members(
        self,
        thread_id: str,
        member_ids: list[str]
    ) -> QuipResponse:
        """Remove members from a thread

        Args:
            thread_id: The thread ID
            member_ids: List of user IDs to remove

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/remove-members"

        body: dict[str, Any] = {}
        body['thread_id'] = thread_id
        body['member_ids'] = member_ids

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed remove_thread_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute remove_thread_members")

    async def move_thread(
        self,
        thread_id: str,
        folder_id: str
    ) -> QuipResponse:
        """Move a thread to a different folder

        Args:
            thread_id: The thread ID to move
            folder_id: Destination folder ID

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/move"

        body: dict[str, Any] = {}
        body['thread_id'] = thread_id
        body['folder_id'] = folder_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed move_thread" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute move_thread")

    async def delete_thread(
        self,
        thread_id: str
    ) -> QuipResponse:
        """Delete (trash) a thread

        Args:
            thread_id: The thread ID to delete

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/threads/delete"

        body: dict[str, Any] = {}
        body['thread_id'] = thread_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_thread" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute delete_thread")

    async def get_thread_messages(
        self,
        thread_id: str,
        *,
        count: int | None = None,
        max_created_usec: int | None = None
    ) -> QuipResponse:
        """Get messages (comments) for a thread

        Args:
            thread_id: The thread ID
            count: Number of messages to return
            max_created_usec: Max created time in microseconds (for pagination)

        Returns:
            QuipResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if count is not None:
            query_params['count'] = str(count)
        if max_created_usec is not None:
            query_params['max_created_usec'] = str(max_created_usec)

        url = self.base_url + "/messages/{thread_id}".format(thread_id=thread_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_thread_messages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_thread_messages")

    async def create_message(
        self,
        thread_id: str,
        content: str,
        *,
        frame: str | None = None,
        section_id: str | None = None,
        annotation_id: str | None = None
    ) -> QuipResponse:
        """Create a new message (comment) on a thread

        Args:
            thread_id: The thread ID to comment on
            content: Message content (can contain HTML)
            frame: Frame type (bubble, card, line)
            section_id: Section ID to attach comment to
            annotation_id: Annotation ID for inline comments

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/messages/new"

        body: dict[str, Any] = {}
        body['thread_id'] = thread_id
        body['content'] = content
        if frame is not None:
            body['frame'] = frame
        if section_id is not None:
            body['section_id'] = section_id
        if annotation_id is not None:
            body['annotation_id'] = annotation_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_message" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute create_message")

    async def get_folder(
        self,
        folder_id: str
    ) -> QuipResponse:
        """Get a specific folder by ID

        Args:
            folder_id: The folder ID

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/{folder_id}".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_folder")

    async def get_folders(
        self,
        folder_ids: str
    ) -> QuipResponse:
        """Get multiple folders by IDs (comma-separated)

        Args:
            folder_ids: Comma-separated folder IDs

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/{folder_ids}".format(folder_ids=folder_ids)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute get_folders")

    async def create_folder(
        self,
        title: str,
        *,
        parent_id: str | None = None,
        color: str | None = None,
        member_ids: list[str] | None = None
    ) -> QuipResponse:
        """Create a new folder

        Args:
            title: Folder title
            parent_id: Parent folder ID
            color: Folder color (manila, red, orange, green, blue)
            member_ids: List of member IDs to add

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/new"

        body: dict[str, Any] = {}
        body['title'] = title
        if parent_id is not None:
            body['parent_id'] = parent_id
        if color is not None:
            body['color'] = color
        if member_ids is not None:
            body['member_ids'] = member_ids

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute create_folder")

    async def update_folder(
        self,
        folder_id: str,
        *,
        title: str | None = None,
        color: str | None = None
    ) -> QuipResponse:
        """Update a folder

        Args:
            folder_id: The folder ID to update
            title: New folder title
            color: New folder color

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/update"

        body: dict[str, Any] = {}
        body['folder_id'] = folder_id
        if title is not None:
            body['title'] = title
        if color is not None:
            body['color'] = color

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute update_folder")

    async def add_folder_members(
        self,
        folder_id: str,
        member_ids: list[str]
    ) -> QuipResponse:
        """Add members to a folder

        Args:
            folder_id: The folder ID
            member_ids: List of user IDs to add

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/add-members"

        body: dict[str, Any] = {}
        body['folder_id'] = folder_id
        body['member_ids'] = member_ids

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_folder_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute add_folder_members")

    async def remove_folder_members(
        self,
        folder_id: str,
        member_ids: list[str]
    ) -> QuipResponse:
        """Remove members from a folder

        Args:
            folder_id: The folder ID
            member_ids: List of user IDs to remove

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/remove-members"

        body: dict[str, Any] = {}
        body['folder_id'] = folder_id
        body['member_ids'] = member_ids

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed remove_folder_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute remove_folder_members")

    async def delete_folder(
        self,
        folder_id: str
    ) -> QuipResponse:
        """Delete (trash) a folder

        Args:
            folder_id: The folder ID to delete

        Returns:
            QuipResponse with operation result
        """
        url = self.base_url + "/folders/delete"

        body: dict[str, Any] = {}
        body['folder_id'] = folder_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return QuipResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return QuipResponse(success=False, error=str(e), message="Failed to execute delete_folder")
