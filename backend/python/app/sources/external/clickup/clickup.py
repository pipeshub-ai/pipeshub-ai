# ruff: noqa: FBT001
"""
ClickUp REST API DataSource - Auto-generated API wrapper

Generated from ClickUp REST API v2/v3 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.clickup.clickup import ClickUpClient, ClickUpResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class ClickUpDataSource:
    """ClickUp REST API DataSource

    Provides async wrapper methods for ClickUp REST API operations:
    - Workspace / Team management
    - Space, Folder, List CRUD
    - Task CRUD and management
    - Comments, Members, Tags
    - Goals, Time tracking
    - Views, Webhooks, Custom Fields
    - Checklists, Dependencies, Guests

    The base URL is determined by the ClickUpClient's configured version
    (v2 or v3). Create a client with the desired version and pass it here.

    All methods return ClickUpResponse objects.
    """

    def __init__(self, client: ClickUpClient) -> None:
        """Initialize with ClickUpClient.

        Args:
            client: ClickUpClient instance with configured authentication and version
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'ClickUpDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> ClickUpClient:
        """Return the underlying ClickUpClient."""
        return self._client

    async def get_authorized_user(
        self
    ) -> ClickUpResponse:
        """Get the authorized user details (API v2)

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/user"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_authorized_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_authorized_user")

    async def get_authorized_teams_workspaces(
        self
    ) -> ClickUpResponse:
        """Get the authorized teams (Workspaces) for the authenticated user (API v2)

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_authorized_teams_workspaces" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_authorized_teams_workspaces")

    async def get_spaces(
        self,
        team_id: str,
        *,
        archived: bool | None = None
    ) -> ClickUpResponse:
        """Get all Spaces in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            archived: Include archived spaces

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if archived is not None:
            query_params['archived'] = str(archived).lower()

        url = self.base_url + "/team/{team_id}/space".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_spaces" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_spaces")

    async def create_space(
        self,
        team_id: str,
        name: str,
        *,
        multiple_assignees: bool | None = None,
        features: dict[str, Any] | None = None
    ) -> ClickUpResponse:
        """Create a new Space in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            name: The name of the Space
            multiple_assignees: Enable multiple assignees
            features: Space features configuration

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/space".format(team_id=team_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if multiple_assignees is not None:
            body['multiple_assignees'] = multiple_assignees
        if features is not None:
            body['features'] = features

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_space" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_space")

    async def get_space(
        self,
        space_id: str
    ) -> ClickUpResponse:
        """Get a specific Space (API v2)

        Args:
            space_id: The Space ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}".format(space_id=space_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_space" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_space")

    async def update_space(
        self,
        space_id: str,
        *,
        name: str | None = None,
        color: str | None = None,
        private: bool | None = None,
        admin_can_manage: bool | None = None,
        multiple_assignees: bool | None = None,
        features: dict[str, Any] | None = None
    ) -> ClickUpResponse:
        """Update a Space (API v2)

        Args:
            space_id: The Space ID
            name: New name for the Space
            color: Space color hex code
            private: Make Space private
            admin_can_manage: Allow admin to manage
            multiple_assignees: Enable multiple assignees
            features: Space features configuration

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}".format(space_id=space_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if color is not None:
            body['color'] = color
        if private is not None:
            body['private'] = private
        if admin_can_manage is not None:
            body['admin_can_manage'] = admin_can_manage
        if multiple_assignees is not None:
            body['multiple_assignees'] = multiple_assignees
        if features is not None:
            body['features'] = features

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_space" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_space")

    async def delete_space(
        self,
        space_id: str
    ) -> ClickUpResponse:
        """Delete a Space (API v2)

        Args:
            space_id: The Space ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}".format(space_id=space_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_space" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_space")

    async def get_folders(
        self,
        space_id: str,
        *,
        archived: bool | None = None
    ) -> ClickUpResponse:
        """Get all Folders in a Space (API v2)

        Args:
            space_id: The Space ID
            archived: Include archived folders

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if archived is not None:
            query_params['archived'] = str(archived).lower()

        url = self.base_url + "/space/{space_id}/folder".format(space_id=space_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_folders")

    async def create_folder(
        self,
        space_id: str,
        name: str
    ) -> ClickUpResponse:
        """Create a Folder in a Space (API v2)

        Args:
            space_id: The Space ID
            name: The name of the Folder

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}/folder".format(space_id=space_id)

        body: dict[str, Any] = {}
        body['name'] = name

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_folder")

    async def get_folder(
        self,
        folder_id: str
    ) -> ClickUpResponse:
        """Get a specific Folder (API v2)

        Args:
            folder_id: The Folder ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/folder/{folder_id}".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_folder")

    async def update_folder(
        self,
        folder_id: str,
        name: str
    ) -> ClickUpResponse:
        """Update a Folder (API v2)

        Args:
            folder_id: The Folder ID
            name: New name for the Folder

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/folder/{folder_id}".format(folder_id=folder_id)

        body: dict[str, Any] = {}
        body['name'] = name

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_folder")

    async def delete_folder(
        self,
        folder_id: str
    ) -> ClickUpResponse:
        """Delete a Folder (API v2)

        Args:
            folder_id: The Folder ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/folder/{folder_id}".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_folder")

    async def get_lists(
        self,
        folder_id: str,
        *,
        archived: bool | None = None
    ) -> ClickUpResponse:
        """Get all Lists in a Folder (API v2)

        Args:
            folder_id: The Folder ID
            archived: Include archived lists

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if archived is not None:
            query_params['archived'] = str(archived).lower()

        url = self.base_url + "/folder/{folder_id}/list".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_lists" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_lists")

    async def create_list(
        self,
        folder_id: str,
        name: str,
        *,
        content: str | None = None,
        due_date: int | None = None,
        due_date_time: bool | None = None,
        priority: int | None = None,
        assignee: int | None = None,
        status: str | None = None
    ) -> ClickUpResponse:
        """Create a List in a Folder (API v2)

        Args:
            folder_id: The Folder ID
            name: The name of the List
            content: List description
            due_date: Due date as Unix timestamp (ms)
            due_date_time: Include time in due date
            priority: Priority level (1=Urgent, 2=High, 3=Normal, 4=Low)
            assignee: Assignee user ID
            status: Status name

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/folder/{folder_id}/list".format(folder_id=folder_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if content is not None:
            body['content'] = content
        if due_date is not None:
            body['due_date'] = due_date
        if due_date_time is not None:
            body['due_date_time'] = due_date_time
        if priority is not None:
            body['priority'] = priority
        if assignee is not None:
            body['assignee'] = assignee
        if status is not None:
            body['status'] = status

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_list")

    async def get_folderless_lists(
        self,
        space_id: str,
        *,
        archived: bool | None = None
    ) -> ClickUpResponse:
        """Get Lists that are not in a Folder (folderless Lists) (API v2)

        Args:
            space_id: The Space ID
            archived: Include archived lists

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if archived is not None:
            query_params['archived'] = str(archived).lower()

        url = self.base_url + "/space/{space_id}/list".format(space_id=space_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folderless_lists" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_folderless_lists")

    async def create_folderless_list(
        self,
        space_id: str,
        name: str,
        *,
        content: str | None = None,
        due_date: int | None = None,
        due_date_time: bool | None = None,
        priority: int | None = None,
        assignee: int | None = None,
        status: str | None = None
    ) -> ClickUpResponse:
        """Create a folderless List in a Space (API v2)

        Args:
            space_id: The Space ID
            name: The name of the List
            content: List description
            due_date: Due date as Unix timestamp (ms)
            due_date_time: Include time in due date
            priority: Priority level
            assignee: Assignee user ID
            status: Status name

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}/list".format(space_id=space_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if content is not None:
            body['content'] = content
        if due_date is not None:
            body['due_date'] = due_date
        if due_date_time is not None:
            body['due_date_time'] = due_date_time
        if priority is not None:
            body['priority'] = priority
        if assignee is not None:
            body['assignee'] = assignee
        if status is not None:
            body['status'] = status

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_folderless_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_folderless_list")

    async def get_list(
        self,
        list_id: str
    ) -> ClickUpResponse:
        """Get a specific List (API v2)

        Args:
            list_id: The List ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_list")

    async def update_list(
        self,
        list_id: str,
        *,
        name: str | None = None,
        content: str | None = None,
        due_date: int | None = None,
        due_date_time: bool | None = None,
        priority: int | None = None,
        assignee_add: int | None = None,
        assignee_rem: int | None = None,
        unset_status: bool | None = None
    ) -> ClickUpResponse:
        """Update a List (API v2)

        Args:
            list_id: The List ID
            name: New name for the List
            content: List description
            due_date: Due date as Unix timestamp (ms)
            due_date_time: Include time in due date
            priority: Priority level
            assignee_add: Add assignee by user ID
            assignee_rem: Remove assignee by user ID
            unset_status: Remove the status field

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}".format(list_id=list_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if content is not None:
            body['content'] = content
        if due_date is not None:
            body['due_date'] = due_date
        if due_date_time is not None:
            body['due_date_time'] = due_date_time
        if priority is not None:
            body['priority'] = priority
        if assignee_add is not None:
            body['assignee_add'] = assignee_add
        if assignee_rem is not None:
            body['assignee_rem'] = assignee_rem
        if unset_status is not None:
            body['unset_status'] = unset_status

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_list")

    async def delete_list(
        self,
        list_id: str
    ) -> ClickUpResponse:
        """Delete a List (API v2)

        Args:
            list_id: The List ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_list")

    async def get_tasks(
        self,
        list_id: str,
        *,
        archived: bool | None = None,
        include_markdown_description: bool | None = None,
        page: int | None = None,
        order_by: str | None = None,
        reverse: bool | None = None,
        subtasks: bool | None = None,
        statuses: list[str] | None = None,
        include_closed: bool | None = None,
        assignees: list[str] | None = None,
        tags: list[str] | None = None,
        due_date_gt: int | None = None,
        due_date_lt: int | None = None,
        date_created_gt: int | None = None,
        date_created_lt: int | None = None,
        date_updated_gt: int | None = None,
        date_updated_lt: int | None = None,
        custom_fields: list[dict[str, Any]] | None = None
    ) -> ClickUpResponse:
        """Get Tasks in a List (API v2)

        Args:
            list_id: The List ID
            archived: Include archived tasks
            include_markdown_description: Return description in markdown
            page: Page number (starts at 0)
            order_by: Order by field (id, created, updated, due_date)
            reverse: Reverse sort order
            subtasks: Include subtasks
            statuses: Filter by status names
            include_closed: Include closed tasks
            assignees: Filter by assignee IDs
            tags: Filter by tag names
            due_date_gt: Filter tasks due after timestamp (ms)
            due_date_lt: Filter tasks due before timestamp (ms)
            date_created_gt: Filter tasks created after timestamp (ms)
            date_created_lt: Filter tasks created before timestamp (ms)
            date_updated_gt: Filter tasks updated after timestamp (ms)
            date_updated_lt: Filter tasks updated before timestamp (ms)
            custom_fields: Filter by custom field values

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if archived is not None:
            query_params['archived'] = str(archived).lower()
        if include_markdown_description is not None:
            query_params['include_markdown_description'] = str(include_markdown_description).lower()
        if page is not None:
            query_params['page'] = str(page)
        if order_by is not None:
            query_params['order_by'] = order_by
        if reverse is not None:
            query_params['reverse'] = str(reverse).lower()
        if subtasks is not None:
            query_params['subtasks'] = str(subtasks).lower()
        if statuses is not None:
            query_params['statuses[]'] = statuses
        if include_closed is not None:
            query_params['include_closed'] = str(include_closed).lower()
        if assignees is not None:
            query_params['assignees[]'] = assignees
        if tags is not None:
            query_params['tags[]'] = tags
        if due_date_gt is not None:
            query_params['due_date_gt'] = str(due_date_gt)
        if due_date_lt is not None:
            query_params['due_date_lt'] = str(due_date_lt)
        if date_created_gt is not None:
            query_params['date_created_gt'] = str(date_created_gt)
        if date_created_lt is not None:
            query_params['date_created_lt'] = str(date_created_lt)
        if date_updated_gt is not None:
            query_params['date_updated_gt'] = str(date_updated_gt)
        if date_updated_lt is not None:
            query_params['date_updated_lt'] = str(date_updated_lt)
        if custom_fields is not None:
            query_params['custom_fields[]'] = custom_fields

        url = self.base_url + "/list/{list_id}/task".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_tasks")

    async def create_task(
        self,
        list_id: str,
        name: str,
        *,
        description: str | None = None,
        markdown_description: str | None = None,
        assignees: list[int] | None = None,
        tags: list[str] | None = None,
        status: str | None = None,
        priority: int | None = None,
        due_date: int | None = None,
        due_date_time: bool | None = None,
        time_estimate: int | None = None,
        start_date: int | None = None,
        start_date_time: bool | None = None,
        notify_all: bool | None = None,
        parent: str | None = None,
        links_to: str | None = None,
        check_required_custom_fields: bool | None = None,
        custom_fields: list[dict[str, Any]] | None = None
    ) -> ClickUpResponse:
        """Create a Task in a List (API v2)

        Args:
            list_id: The List ID
            name: The name of the Task
            description: Task description (plain text)
            markdown_description: Task description (markdown)
            assignees: Assignee user IDs
            tags: Tag names
            status: Status name
            priority: Priority (1=Urgent, 2=High, 3=Normal, 4=Low)
            due_date: Due date as Unix timestamp (ms)
            due_date_time: Include time in due date
            time_estimate: Time estimate in milliseconds
            start_date: Start date as Unix timestamp (ms)
            start_date_time: Include time in start date
            notify_all: Notify all assignees
            parent: Parent task ID for subtasks
            links_to: Task ID to link to
            check_required_custom_fields: Validate required custom fields
            custom_fields: Custom field values

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}/task".format(list_id=list_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if description is not None:
            body['description'] = description
        if markdown_description is not None:
            body['markdown_description'] = markdown_description
        if assignees is not None:
            body['assignees'] = assignees
        if tags is not None:
            body['tags'] = tags
        if status is not None:
            body['status'] = status
        if priority is not None:
            body['priority'] = priority
        if due_date is not None:
            body['due_date'] = due_date
        if due_date_time is not None:
            body['due_date_time'] = due_date_time
        if time_estimate is not None:
            body['time_estimate'] = time_estimate
        if start_date is not None:
            body['start_date'] = start_date
        if start_date_time is not None:
            body['start_date_time'] = start_date_time
        if notify_all is not None:
            body['notify_all'] = notify_all
        if parent is not None:
            body['parent'] = parent
        if links_to is not None:
            body['links_to'] = links_to
        if check_required_custom_fields is not None:
            body['check_required_custom_fields'] = check_required_custom_fields
        if custom_fields is not None:
            body['custom_fields'] = custom_fields

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_task")

    async def get_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None,
        include_subtasks: bool | None = None,
        include_markdown_description: bool | None = None
    ) -> ClickUpResponse:
        """Get a specific Task (API v2)

        Args:
            task_id: The Task ID
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)
            include_subtasks: Include subtasks
            include_markdown_description: Return description in markdown

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id
        if include_subtasks is not None:
            query_params['include_subtasks'] = str(include_subtasks).lower()
        if include_markdown_description is not None:
            query_params['include_markdown_description'] = str(include_markdown_description).lower()

        url = self.base_url + "/task/{task_id}".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_task")

    async def update_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None,
        name: str | None = None,
        description: str | None = None,
        markdown_description: str | None = None,
        status: str | None = None,
        priority: int | None = None,
        due_date: int | None = None,
        due_date_time: bool | None = None,
        time_estimate: int | None = None,
        start_date: int | None = None,
        start_date_time: bool | None = None,
        assignees_add: list[int] | None = None,
        assignees_rem: list[int] | None = None,
        archived: bool | None = None
    ) -> ClickUpResponse:
        """Update a Task (API v2)

        Args:
            task_id: The Task ID
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)
            name: New task name
            description: Task description (plain text)
            markdown_description: Task description (markdown)
            status: Status name
            priority: Priority (1=Urgent, 2=High, 3=Normal, 4=Low)
            due_date: Due date as Unix timestamp (ms)
            due_date_time: Include time in due date
            time_estimate: Time estimate in milliseconds
            start_date: Start date as Unix timestamp (ms)
            start_date_time: Include time in start date
            assignees_add: Add assignees by user ID
            assignees_rem: Remove assignees by user ID
            archived: Archive or unarchive the task

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}".format(task_id=task_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if description is not None:
            body['description'] = description
        if markdown_description is not None:
            body['markdown_description'] = markdown_description
        if status is not None:
            body['status'] = status
        if priority is not None:
            body['priority'] = priority
        if due_date is not None:
            body['due_date'] = due_date
        if due_date_time is not None:
            body['due_date_time'] = due_date_time
        if time_estimate is not None:
            body['time_estimate'] = time_estimate
        if start_date is not None:
            body['start_date'] = start_date
        if start_date_time is not None:
            body['start_date_time'] = start_date_time
        if assignees_add is not None:
            body['assignees_add'] = assignees_add
        if assignees_rem is not None:
            body['assignees_rem'] = assignees_rem
        if archived is not None:
            body['archived'] = archived

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_task")

    async def delete_task(
        self,
        task_id: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Delete a Task (API v2)

        Args:
            task_id: The Task ID
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_task")

    async def get_filtered_team_tasks(
        self,
        team_id: str,
        *,
        page: int | None = None,
        order_by: str | None = None,
        reverse: bool | None = None,
        subtasks: bool | None = None,
        statuses: list[str] | None = None,
        include_closed: bool | None = None,
        assignees: list[str] | None = None,
        tags: list[str] | None = None,
        due_date_gt: int | None = None,
        due_date_lt: int | None = None,
        date_created_gt: int | None = None,
        date_created_lt: int | None = None,
        date_updated_gt: int | None = None,
        date_updated_lt: int | None = None,
        space_ids: list[str] | None = None,
        project_ids: list[str] | None = None,
        list_ids: list[str] | None = None
    ) -> ClickUpResponse:
        """Get filtered Tasks across an entire Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            page: Page number (starts at 0)
            order_by: Order by field
            reverse: Reverse sort order
            subtasks: Include subtasks
            statuses: Filter by status names
            include_closed: Include closed tasks
            assignees: Filter by assignee IDs
            tags: Filter by tag names
            due_date_gt: Filter tasks due after timestamp (ms)
            due_date_lt: Filter tasks due before timestamp (ms)
            date_created_gt: Filter tasks created after timestamp
            date_created_lt: Filter tasks created before timestamp
            date_updated_gt: Filter tasks updated after timestamp
            date_updated_lt: Filter tasks updated before timestamp
            space_ids: Filter by Space IDs
            project_ids: Filter by project (Folder) IDs
            list_ids: Filter by List IDs

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if order_by is not None:
            query_params['order_by'] = order_by
        if reverse is not None:
            query_params['reverse'] = str(reverse).lower()
        if subtasks is not None:
            query_params['subtasks'] = str(subtasks).lower()
        if statuses is not None:
            query_params['statuses[]'] = statuses
        if include_closed is not None:
            query_params['include_closed'] = str(include_closed).lower()
        if assignees is not None:
            query_params['assignees[]'] = assignees
        if tags is not None:
            query_params['tags[]'] = tags
        if due_date_gt is not None:
            query_params['due_date_gt'] = str(due_date_gt)
        if due_date_lt is not None:
            query_params['due_date_lt'] = str(due_date_lt)
        if date_created_gt is not None:
            query_params['date_created_gt'] = str(date_created_gt)
        if date_created_lt is not None:
            query_params['date_created_lt'] = str(date_created_lt)
        if date_updated_gt is not None:
            query_params['date_updated_gt'] = str(date_updated_gt)
        if date_updated_lt is not None:
            query_params['date_updated_lt'] = str(date_updated_lt)
        if space_ids is not None:
            query_params['space_ids[]'] = space_ids
        if project_ids is not None:
            query_params['project_ids[]'] = project_ids
        if list_ids is not None:
            query_params['list_ids[]'] = list_ids

        url = self.base_url + "/team/{team_id}/task".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_filtered_team_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_filtered_team_tasks")

    async def get_task_comments(
        self,
        task_id: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None,
        start: int | None = None,
        start_id: str | None = None
    ) -> ClickUpResponse:
        """Get comments on a Task (API v2)

        Args:
            task_id: The Task ID
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)
            start: Start timestamp for comments
            start_id: Start comment ID for pagination

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id
        if start is not None:
            query_params['start'] = str(start)
        if start_id is not None:
            query_params['start_id'] = start_id

        url = self.base_url + "/task/{task_id}/comment".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_task_comments")

    async def create_task_comment(
        self,
        task_id: str,
        comment_text: str,
        *,
        assignee: int | None = None,
        notify_all: bool | None = None,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Create a comment on a Task (API v2)

        Args:
            task_id: The Task ID
            comment_text: The comment text (plain text)
            assignee: Assign the comment to a user ID
            notify_all: Notify all assignees
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/comment".format(task_id=task_id)

        body: dict[str, Any] = {}
        body['comment_text'] = comment_text
        if assignee is not None:
            body['assignee'] = assignee
        if notify_all is not None:
            body['notify_all'] = notify_all

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_task_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_task_comment")

    async def get_list_comments(
        self,
        list_id: str,
        start: int | None = None,
        start_id: str | None = None
    ) -> ClickUpResponse:
        """Get comments on a List (API v2)

        Args:
            list_id: The List ID
            start: Start timestamp for comments
            start_id: Start comment ID for pagination

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if start_id is not None:
            query_params['start_id'] = start_id

        url = self.base_url + "/list/{list_id}/comment".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_list_comments")

    async def create_list_comment(
        self,
        list_id: str,
        comment_text: str,
        *,
        assignee: int | None = None,
        notify_all: bool | None = None
    ) -> ClickUpResponse:
        """Create a comment on a List (API v2)

        Args:
            list_id: The List ID
            comment_text: The comment text (plain text)
            assignee: Assign the comment to a user ID
            notify_all: Notify all assignees

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}/comment".format(list_id=list_id)

        body: dict[str, Any] = {}
        body['comment_text'] = comment_text
        if assignee is not None:
            body['assignee'] = assignee
        if notify_all is not None:
            body['notify_all'] = notify_all

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_list_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_list_comment")

    async def update_comment(
        self,
        comment_id: str,
        comment_text: str,
        *,
        assignee: int | None = None,
        resolved: bool | None = None
    ) -> ClickUpResponse:
        """Update a comment (API v2)

        Args:
            comment_id: The Comment ID
            comment_text: Updated comment text
            assignee: Reassign the comment
            resolved: Resolve or unresolve the comment

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/comment/{comment_id}".format(comment_id=comment_id)

        body: dict[str, Any] = {}
        body['comment_text'] = comment_text
        if assignee is not None:
            body['assignee'] = assignee
        if resolved is not None:
            body['resolved'] = resolved

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_comment")

    async def delete_comment(
        self,
        comment_id: str
    ) -> ClickUpResponse:
        """Delete a comment (API v2)

        Args:
            comment_id: The Comment ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/comment/{comment_id}".format(comment_id=comment_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_comment")

    async def get_task_members(
        self,
        task_id: str
    ) -> ClickUpResponse:
        """Get members assigned to a Task (API v2)

        Args:
            task_id: The Task ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/task/{task_id}/member".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_task_members")

    async def get_list_members(
        self,
        list_id: str
    ) -> ClickUpResponse:
        """Get members of a List (API v2)

        Args:
            list_id: The List ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}/member".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_list_members")

    async def get_space_tags(
        self,
        space_id: str
    ) -> ClickUpResponse:
        """Get all Tags in a Space (API v2)

        Args:
            space_id: The Space ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}/tag".format(space_id=space_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_space_tags" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_space_tags")

    async def create_space_tag(
        self,
        space_id: str,
        name: str,
        tag_fg: str | None = None,
        tag_bg: str | None = None
    ) -> ClickUpResponse:
        """Create a Tag in a Space (API v2)

        Args:
            space_id: The Space ID
            name: Tag name
            tag_fg: Foreground color hex
            tag_bg: Background color hex

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}/tag".format(space_id=space_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if tag_fg is not None:
            body['tag_fg'] = tag_fg
        if tag_bg is not None:
            body['tag_bg'] = tag_bg

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_space_tag" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_space_tag")

    async def add_tag_to_task(
        self,
        task_id: str,
        tag_name: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Add a Tag to a Task (API v2)

        Args:
            task_id: The Task ID
            tag_name: The Tag name
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/tag/{tag_name}".format(task_id=task_id, tag_name=tag_name)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_tag_to_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute add_tag_to_task")

    async def remove_tag_from_task(
        self,
        task_id: str,
        tag_name: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Remove a Tag from a Task (API v2)

        Args:
            task_id: The Task ID
            tag_name: The Tag name
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/tag/{tag_name}".format(task_id=task_id, tag_name=tag_name)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed remove_tag_from_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute remove_tag_from_task")

    async def get_goals(
        self,
        team_id: str
    ) -> ClickUpResponse:
        """Get all Goals in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/goal".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_goals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_goals")

    async def create_goal(
        self,
        team_id: str,
        name: str,
        *,
        due_date: int | None = None,
        description: str | None = None,
        multiple_owners: bool | None = None,
        owners: list[int] | None = None,
        color: str | None = None
    ) -> ClickUpResponse:
        """Create a Goal in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            name: Goal name
            due_date: Due date as Unix timestamp (ms)
            description: Goal description
            multiple_owners: Allow multiple owners
            owners: Owner user IDs
            color: Goal color hex code

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/goal".format(team_id=team_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if due_date is not None:
            body['due_date'] = due_date
        if description is not None:
            body['description'] = description
        if multiple_owners is not None:
            body['multiple_owners'] = multiple_owners
        if owners is not None:
            body['owners'] = owners
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
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_goal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_goal")

    async def get_goal(
        self,
        goal_id: str
    ) -> ClickUpResponse:
        """Get a specific Goal (API v2)

        Args:
            goal_id: The Goal ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/goal/{goal_id}".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_goal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_goal")

    async def update_goal(
        self,
        goal_id: str,
        name: str | None = None,
        due_date: int | None = None,
        description: str | None = None,
        color: str | None = None,
        add_owners: list[int] | None = None,
        rem_owners: list[int] | None = None
    ) -> ClickUpResponse:
        """Update a Goal (API v2)

        Args:
            goal_id: The Goal ID
            name: Goal name
            due_date: Due date as Unix timestamp (ms)
            description: Goal description
            color: Goal color hex code
            add_owners: Add owner user IDs
            rem_owners: Remove owner user IDs

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/goal/{goal_id}".format(goal_id=goal_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if due_date is not None:
            body['due_date'] = due_date
        if description is not None:
            body['description'] = description
        if color is not None:
            body['color'] = color
        if add_owners is not None:
            body['add_owners'] = add_owners
        if rem_owners is not None:
            body['rem_owners'] = rem_owners

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_goal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_goal")

    async def delete_goal(
        self,
        goal_id: str
    ) -> ClickUpResponse:
        """Delete a Goal (API v2)

        Args:
            goal_id: The Goal ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/goal/{goal_id}".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_goal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_goal")

    async def get_time_entries_in_range(
        self,
        team_id: str,
        *,
        start_date: int | None = None,
        end_date: int | None = None,
        assignee: str | None = None,
        include_task_tags: bool | None = None,
        include_location_names: bool | None = None
    ) -> ClickUpResponse:
        """Get time entries within a date range for a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            start_date: Start date as Unix timestamp (ms)
            end_date: End date as Unix timestamp (ms)
            assignee: Filter by user ID
            include_task_tags: Include task tag info
            include_location_names: Include Space, Folder, List names

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start_date is not None:
            query_params['start_date'] = str(start_date)
        if end_date is not None:
            query_params['end_date'] = str(end_date)
        if assignee is not None:
            query_params['assignee'] = assignee
        if include_task_tags is not None:
            query_params['include_task_tags'] = str(include_task_tags).lower()
        if include_location_names is not None:
            query_params['include_location_names'] = str(include_location_names).lower()

        url = self.base_url + "/team/{team_id}/time_entries".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_time_entries_in_range" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_time_entries_in_range")

    async def get_task_time_entries(
        self,
        task_id: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Get tracked time entries for a Task (API v2)

        Args:
            task_id: The Task ID
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/time".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task_time_entries" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_task_time_entries")

    async def create_time_entry(
        self,
        team_id: str,
        start: int,
        duration: int,
        *,
        description: str | None = None,
        end: int | None = None,
        assignee: int | None = None,
        tid: str | None = None,
        billable: bool | None = None,
        tags: list[dict[str, str]] | None = None
    ) -> ClickUpResponse:
        """Create a time entry (API v2)

        Args:
            team_id: The Workspace (Team) ID
            description: Time entry description
            start: Start time as Unix timestamp (ms)
            end: End time as Unix timestamp (ms)
            duration: Duration in milliseconds
            assignee: User ID to assign the time entry
            tid: Task ID to associate with
            billable: Mark as billable
            tags: Tags for the time entry

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/time_entries".format(team_id=team_id)

        body: dict[str, Any] = {}
        if description is not None:
            body['description'] = description
        body['start'] = start
        if end is not None:
            body['end'] = end
        body['duration'] = duration
        if assignee is not None:
            body['assignee'] = assignee
        if tid is not None:
            body['tid'] = tid
        if billable is not None:
            body['billable'] = billable
        if tags is not None:
            body['tags'] = tags

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_time_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_time_entry")

    async def delete_time_entry(
        self,
        team_id: str,
        timer_id: str
    ) -> ClickUpResponse:
        """Delete a time entry (API v2)

        Args:
            team_id: The Workspace (Team) ID
            timer_id: The time entry ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/time_entries/{timer_id}".format(team_id=team_id, timer_id=timer_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_time_entry" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_time_entry")

    async def get_team_views(
        self,
        team_id: str
    ) -> ClickUpResponse:
        """Get all Views at Workspace level (API v2)

        Args:
            team_id: The Workspace (Team) ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/view".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_team_views" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_team_views")

    async def get_space_views(
        self,
        space_id: str
    ) -> ClickUpResponse:
        """Get all Views in a Space (API v2)

        Args:
            space_id: The Space ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/space/{space_id}/view".format(space_id=space_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_space_views" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_space_views")

    async def get_folder_views(
        self,
        folder_id: str
    ) -> ClickUpResponse:
        """Get all Views in a Folder (API v2)

        Args:
            folder_id: The Folder ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/folder/{folder_id}/view".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder_views" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_folder_views")

    async def get_list_views(
        self,
        list_id: str
    ) -> ClickUpResponse:
        """Get all Views for a List (API v2)

        Args:
            list_id: The List ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}/view".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list_views" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_list_views")

    async def get_view(
        self,
        view_id: str
    ) -> ClickUpResponse:
        """Get a specific View (API v2)

        Args:
            view_id: The View ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/view/{view_id}".format(view_id=view_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_view" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_view")

    async def get_view_tasks(
        self,
        view_id: str,
        page: int | None = None
    ) -> ClickUpResponse:
        """Get Tasks from a View (API v2)

        Args:
            view_id: The View ID
            page: Page number (starts at 0)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/view/{view_id}/task".format(view_id=view_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_view_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_view_tasks")

    async def get_accessible_custom_fields(
        self,
        list_id: str
    ) -> ClickUpResponse:
        """Get all accessible Custom Fields for a List (API v2)

        Args:
            list_id: The List ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/list/{list_id}/field".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_accessible_custom_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_accessible_custom_fields")

    async def set_custom_field_value(
        self,
        task_id: str,
        field_id: str,
        value: str | int | float | bool | list[Any] | dict[str, Any],
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Set a Custom Field value on a Task (API v2)

        Args:
            task_id: The Task ID
            field_id: The Custom Field ID
            value: The value to set
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/field/{field_id}".format(task_id=task_id, field_id=field_id)

        body: dict[str, Any] = {}
        body['value'] = value

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed set_custom_field_value" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute set_custom_field_value")

    async def remove_custom_field_value(
        self,
        task_id: str,
        field_id: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Remove a Custom Field value from a Task (API v2)

        Args:
            task_id: The Task ID
            field_id: The Custom Field ID
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/field/{field_id}".format(task_id=task_id, field_id=field_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed remove_custom_field_value" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute remove_custom_field_value")

    async def get_webhooks(
        self,
        team_id: str
    ) -> ClickUpResponse:
        """Get all Webhooks in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/webhook".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_webhooks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_webhooks")

    async def create_webhook(
        self,
        team_id: str,
        endpoint: str,
        events: list[str],
        space_id: str | None = None,
        folder_id: str | None = None,
        list_id: str | None = None,
        task_id: str | None = None
    ) -> ClickUpResponse:
        """Create a Webhook in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            endpoint: Webhook endpoint URL
            events: List of event types to subscribe to
            space_id: Filter to a specific Space
            folder_id: Filter to a specific Folder
            list_id: Filter to a specific List
            task_id: Filter to a specific Task

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/webhook".format(team_id=team_id)

        body: dict[str, Any] = {}
        body['endpoint'] = endpoint
        body['events'] = events
        if space_id is not None:
            body['space_id'] = space_id
        if folder_id is not None:
            body['folder_id'] = folder_id
        if list_id is not None:
            body['list_id'] = list_id
        if task_id is not None:
            body['task_id'] = task_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_webhook")

    async def update_webhook(
        self,
        webhook_id: str,
        endpoint: str | None = None,
        events: list[str] | None = None,
        status: str | None = None
    ) -> ClickUpResponse:
        """Update a Webhook (API v2)

        Args:
            webhook_id: The Webhook ID
            endpoint: New endpoint URL
            events: Updated event types
            status: Webhook status (active/inactive)

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/webhook/{webhook_id}".format(webhook_id=webhook_id)

        body: dict[str, Any] = {}
        if endpoint is not None:
            body['endpoint'] = endpoint
        if events is not None:
            body['events'] = events
        if status is not None:
            body['status'] = status

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_webhook")

    async def delete_webhook(
        self,
        webhook_id: str
    ) -> ClickUpResponse:
        """Delete a Webhook (API v2)

        Args:
            webhook_id: The Webhook ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/webhook/{webhook_id}".format(webhook_id=webhook_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_webhook")

    async def create_checklist(
        self,
        task_id: str,
        name: str,
        *,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Create a Checklist in a Task (API v2)

        Args:
            task_id: The Task ID
            name: Checklist name
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/checklist".format(task_id=task_id)

        body: dict[str, Any] = {}
        body['name'] = name

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_checklist" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_checklist")

    async def update_checklist(
        self,
        checklist_id: str,
        name: str | None = None,
        position: int | None = None
    ) -> ClickUpResponse:
        """Update a Checklist (API v2)

        Args:
            checklist_id: The Checklist ID
            name: New checklist name
            position: Position of the checklist

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/checklist/{checklist_id}".format(checklist_id=checklist_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if position is not None:
            body['position'] = position

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_checklist" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_checklist")

    async def delete_checklist(
        self,
        checklist_id: str
    ) -> ClickUpResponse:
        """Delete a Checklist (API v2)

        Args:
            checklist_id: The Checklist ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/checklist/{checklist_id}".format(checklist_id=checklist_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_checklist" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_checklist")

    async def create_checklist_item(
        self,
        checklist_id: str,
        name: str,
        assignee: int | None = None
    ) -> ClickUpResponse:
        """Create a Checklist Item (API v2)

        Args:
            checklist_id: The Checklist ID
            name: Checklist item name
            assignee: Assignee user ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/checklist/{checklist_id}/checklist_item".format(checklist_id=checklist_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if assignee is not None:
            body['assignee'] = assignee

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_checklist_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute create_checklist_item")

    async def update_checklist_item(
        self,
        checklist_id: str,
        checklist_item_id: str,
        *,
        name: str | None = None,
        assignee: int | None = None,
        resolved: bool | None = None,
        parent: str | None = None
    ) -> ClickUpResponse:
        """Update a Checklist Item (API v2)

        Args:
            checklist_id: The Checklist ID
            checklist_item_id: The Checklist Item ID
            name: New item name
            assignee: Assignee user ID
            resolved: Mark resolved/unresolved
            parent: Parent checklist item ID (for nesting)

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/checklist/{checklist_id}/checklist_item/{checklist_item_id}".format(checklist_id=checklist_id, checklist_item_id=checklist_item_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if assignee is not None:
            body['assignee'] = assignee
        if resolved is not None:
            body['resolved'] = resolved
        if parent is not None:
            body['parent'] = parent

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_checklist_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute update_checklist_item")

    async def delete_checklist_item(
        self,
        checklist_id: str,
        checklist_item_id: str
    ) -> ClickUpResponse:
        """Delete a Checklist Item (API v2)

        Args:
            checklist_id: The Checklist ID
            checklist_item_id: The Checklist Item ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/checklist/{checklist_id}/checklist_item/{checklist_item_id}".format(checklist_id=checklist_id, checklist_item_id=checklist_item_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_checklist_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_checklist_item")

    async def get_shared_hierarchy(
        self,
        team_id: str
    ) -> ClickUpResponse:
        """Get the shared hierarchy for a Workspace (items shared with the authenticated user) (API v2)

        Args:
            team_id: The Workspace (Team) ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/shared".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_shared_hierarchy" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_shared_hierarchy")

    async def add_task_dependency(
        self,
        task_id: str,
        *,
        depends_on: str | None = None,
        dependency_of: str | None = None,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Add a dependency relationship between Tasks (API v2)

        Args:
            task_id: The Task ID
            depends_on: Task ID this task depends on (waiting on)
            dependency_of: Task ID that depends on this task (blocking)
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/dependency".format(task_id=task_id)

        body: dict[str, Any] = {}
        if depends_on is not None:
            body['depends_on'] = depends_on
        if dependency_of is not None:
            body['dependency_of'] = dependency_of

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed add_task_dependency" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute add_task_dependency")

    async def delete_task_dependency(
        self,
        task_id: str,
        *,
        depends_on: str | None = None,
        dependency_of: str | None = None,
        custom_task_ids: bool | None = None,
        team_id: str | None = None
    ) -> ClickUpResponse:
        """Remove a dependency from a Task (API v2)

        Args:
            task_id: The Task ID
            depends_on: Task ID to remove as depends_on
            dependency_of: Task ID to remove as dependency_of
            custom_task_ids: Use custom task IDs
            team_id: Team ID (required with custom_task_ids)

        Returns:
            ClickUpResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if depends_on is not None:
            query_params['depends_on'] = depends_on
        if dependency_of is not None:
            query_params['dependency_of'] = dependency_of
        if custom_task_ids is not None:
            query_params['custom_task_ids'] = str(custom_task_ids).lower()
        if team_id is not None:
            query_params['team_id'] = team_id

        url = self.base_url + "/task/{task_id}/dependency".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_task_dependency" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute delete_task_dependency")

    async def invite_guest_to_workspace(
        self,
        team_id: str,
        email: str,
        *,
        can_edit_tags: bool | None = None,
        can_see_time_spent: bool | None = None,
        can_see_time_estimated: bool | None = None
    ) -> ClickUpResponse:
        """Invite a guest to a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            email: Guest email address
            can_edit_tags: Allow guest to edit tags
            can_see_time_spent: Allow guest to see time spent
            can_see_time_estimated: Allow guest to see time estimates

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/guest".format(team_id=team_id)

        body: dict[str, Any] = {}
        body['email'] = email
        if can_edit_tags is not None:
            body['can_edit_tags'] = can_edit_tags
        if can_see_time_spent is not None:
            body['can_see_time_spent'] = can_see_time_spent
        if can_see_time_estimated is not None:
            body['can_see_time_estimated'] = can_see_time_estimated

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed invite_guest_to_workspace" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute invite_guest_to_workspace")

    async def get_guest(
        self,
        team_id: str,
        guest_id: str
    ) -> ClickUpResponse:
        """Get a guest in a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            guest_id: The Guest ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/guest/{guest_id}".format(team_id=team_id, guest_id=guest_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_guest" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute get_guest")

    async def remove_guest_from_workspace(
        self,
        team_id: str,
        guest_id: str
    ) -> ClickUpResponse:
        """Remove a guest from a Workspace (API v2)

        Args:
            team_id: The Workspace (Team) ID
            guest_id: The Guest ID

        Returns:
            ClickUpResponse with operation result
        """
        url = self.base_url + "/team/{team_id}/guest/{guest_id}".format(team_id=team_id, guest_id=guest_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return ClickUpResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed remove_guest_from_workspace" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return ClickUpResponse(success=False, error=str(e), message="Failed to execute remove_guest_from_workspace")
