"""
Egnyte REST API DataSource - Auto-generated API wrapper

Generated from Egnyte Public API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.egnyte.egnyte import EgnyteClient, EgnyteResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class EgnyteDataSource:
    """Egnyte REST API DataSource

    Provides async wrapper methods for Egnyte Public API v1 operations:
    - File system operations (metadata, content, folders)
    - Links management
    - User and group management
    - Audit operations (files, logins, permissions)
    - Search
    - Permissions management

    The base URL is determined by the EgnyteClient's configured domain.

    All methods return EgnyteResponse objects.
    """

    def __init__(self, client: EgnyteClient) -> None:
        """Initialize with EgnyteClient.

        Args:
            client: EgnyteClient instance with configured authentication and domain
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'EgnyteDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> EgnyteClient:
        """Return the underlying EgnyteClient."""
        return self._client

    async def get_file_or_folder_metadata(
        self,
        path: str,
        *,
        list_content: bool | None = None,
        allowed_link_types: bool | None = None,
        count: int | None = None,
        offset: int | None = None,
        sort_by: str | None = None,
        sort_direction: str | None = None
    ) -> EgnyteResponse:
        """Get file or folder metadata at the given path

        Args:
            path: File or folder path (e.g. 'Shared/Documents')
            list_content: If true and path is a folder, list contents
            allowed_link_types: Include allowed link types info
            count: Number of items to return (for folder listing)
            offset: Offset for pagination (for folder listing)
            sort_by: Sort field (name, last_modified, size)
            sort_direction: Sort direction (asc, desc)

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if list_content is not None:
            query_params['list_content'] = str(list_content).lower()
        if allowed_link_types is not None:
            query_params['allowed_link_types'] = str(allowed_link_types).lower()
        if count is not None:
            query_params['count'] = str(count)
        if offset is not None:
            query_params['offset'] = str(offset)
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if sort_direction is not None:
            query_params['sort_direction'] = sort_direction

        url = self.base_url + "/fs/{path}".format(path=path)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_file_or_folder_metadata" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute get_file_or_folder_metadata")

    async def create_folder(
        self,
        path: str,
        action: str
    ) -> EgnyteResponse:
        """Create a folder at the given path

        Args:
            path: Folder path to create
            action: Action type (must be 'add_folder')

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/fs/{path}".format(path=path)

        body: dict[str, Any] = {}
        body['action'] = action

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute create_folder")

    async def delete_file_or_folder(
        self,
        path: str
    ) -> EgnyteResponse:
        """Delete a file or folder at the given path

        Args:
            path: File or folder path to delete

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/fs/{path}".format(path=path)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_file_or_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute delete_file_or_folder")

    async def move_file_or_folder(
        self,
        path: str,
        action: str,
        destination: str
    ) -> EgnyteResponse:
        """Move or copy a file or folder

        Args:
            path: Source file or folder path
            action: Action type ('move' or 'copy')
            destination: Destination path

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/fs/{path}".format(path=path)

        body: dict[str, Any] = {}
        body['action'] = action
        body['destination'] = destination

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed move_file_or_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute move_file_or_folder")

    async def download_file(
        self,
        path: str,
        *,
        entry_id: str | None = None
    ) -> EgnyteResponse:
        """Download file content at the given path

        Args:
            path: File path to download
            entry_id: Specific version entry ID

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if entry_id is not None:
            query_params['entry_id'] = entry_id

        url = self.base_url + "/fs-content/{path}".format(path=path)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed download_file" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute download_file")

    async def upload_file(
        self,
        path: str
    ) -> EgnyteResponse:
        """Upload file content to the given path

        Args:
            path: File path for upload

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/fs-content/{path}".format(path=path)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_file" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute upload_file")

    async def list_links(
        self,
        *,
        path: str | None = None,
        type_: str | None = None,
        accessibility: str | None = None,
        count: int | None = None,
        offset: int | None = None
    ) -> EgnyteResponse:
        """List shared links

        Args:
            path: Filter by path
            type_: Link type (file or folder)
            accessibility: Accessibility (anyone, password, domain, recipients)
            count: Number of links to return
            offset: Offset for pagination

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if path is not None:
            query_params['path'] = path
        if type_ is not None:
            query_params['type'] = type_
        if accessibility is not None:
            query_params['accessibility'] = accessibility
        if count is not None:
            query_params['count'] = str(count)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/links"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_links" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute list_links")

    async def create_link(
        self,
        path: str,
        type_: str,
        accessibility: str,
        *,
        send_email: bool | None = None,
        recipients: list[str] | None = None,
        message: str | None = None,
        copy_me: bool | None = None,
        notify: bool | None = None,
        link_to_current: bool | None = None,
        expiry_date: str | None = None,
        expiry_clicks: int | None = None,
        add_file_name: bool | None = None
    ) -> EgnyteResponse:
        """Create a shared link

        Args:
            path: Path to the file or folder
            type_: Link type (file or folder)
            accessibility: Accessibility (anyone, password, domain, recipients)
            send_email: Send email notification
            recipients: List of recipient email addresses
            message: Email message body
            copy_me: Send copy to creator
            notify: Notify on access
            link_to_current: Link to current version only
            expiry_date: Expiry date (YYYY-MM-DD)
            expiry_clicks: Number of clicks before expiry
            add_file_name: Add file name to link

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/links"

        body: dict[str, Any] = {}
        body['path'] = path
        body['type'] = type_
        body['accessibility'] = accessibility
        if send_email is not None:
            body['send_email'] = send_email
        if recipients is not None:
            body['recipients'] = recipients
        if message is not None:
            body['message'] = message
        if copy_me is not None:
            body['copy_me'] = copy_me
        if notify is not None:
            body['notify'] = notify
        if link_to_current is not None:
            body['link_to_current'] = link_to_current
        if expiry_date is not None:
            body['expiry_date'] = expiry_date
        if expiry_clicks is not None:
            body['expiry_clicks'] = expiry_clicks
        if add_file_name is not None:
            body['add_file_name'] = add_file_name

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_link" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute create_link")

    async def get_link(
        self,
        link_id: str
    ) -> EgnyteResponse:
        """Get a specific shared link

        Args:
            link_id: The link ID

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/links/{link_id}".format(link_id=link_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_link" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute get_link")

    async def delete_link(
        self,
        link_id: str
    ) -> EgnyteResponse:
        """Delete a shared link

        Args:
            link_id: The link ID

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/links/{link_id}".format(link_id=link_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_link" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute delete_link")

    async def get_current_user(
        self
    ) -> EgnyteResponse:
        """Get current authenticated user info

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/userinfo"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_users(
        self,
        *,
        startIndex: int | None = None,
        count: int | None = None
    ) -> EgnyteResponse:
        """List users in the domain

        Args:
            startIndex: Start index for pagination (1-based)
            count: Number of users to return (max 100)

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if startIndex is not None:
            query_params['startIndex'] = str(startIndex)
        if count is not None:
            query_params['count'] = str(count)

        url = self.base_url + "/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> EgnyteResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID

        Returns:
            EgnyteResponse with operation result
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
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def create_user(
        self,
        userName: str,
        externalId: str,
        email: str,
        name: dict[str, str],
        *,
        active: bool | None = None,
        sendInvite: bool | None = None,
        authType: str | None = None,
        userType: str | None = None,
        role: str | None = None
    ) -> EgnyteResponse:
        """Create a new user

        Args:
            userName: Username (email)
            externalId: External ID
            email: User email address
            name: User name object with familyName and givenName
            active: Whether user is active
            sendInvite: Send invite email
            authType: Authentication type
            userType: User type (power, standard, etc.)
            role: User role

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/users"

        body: dict[str, Any] = {}
        body['userName'] = userName
        body['externalId'] = externalId
        body['email'] = email
        body['name'] = name
        if active is not None:
            body['active'] = active
        if sendInvite is not None:
            body['sendInvite'] = sendInvite
        if authType is not None:
            body['authType'] = authType
        if userType is not None:
            body['userType'] = userType
        if role is not None:
            body['role'] = role

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute create_user")

    async def update_user(
        self,
        user_id: str,
        *,
        userName: str | None = None,
        email: str | None = None,
        name: dict[str, str] | None = None,
        active: bool | None = None,
        userType: str | None = None,
        role: str | None = None
    ) -> EgnyteResponse:
        """Update an existing user

        Args:
            user_id: The user ID
            userName: Username (email)
            email: User email address
            name: User name object
            active: Whether user is active
            userType: User type
            role: User role

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)

        body: dict[str, Any] = {}
        if userName is not None:
            body['userName'] = userName
        if email is not None:
            body['email'] = email
        if name is not None:
            body['name'] = name
        if active is not None:
            body['active'] = active
        if userType is not None:
            body['userType'] = userType
        if role is not None:
            body['role'] = role

        try:
            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute update_user")

    async def delete_user(
        self,
        user_id: str
    ) -> EgnyteResponse:
        """Delete a user

        Args:
            user_id: The user ID

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute delete_user")

    async def list_groups(
        self
    ) -> EgnyteResponse:
        """List all groups

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/groups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute list_groups")

    async def get_group(
        self,
        group_id: str
    ) -> EgnyteResponse:
        """Get a specific group by ID

        Args:
            group_id: The group ID

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/groups/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute get_group")

    async def create_group(
        self,
        displayName: str,
        *,
        members: list[dict[str, str]] | None = None
    ) -> EgnyteResponse:
        """Create a new group

        Args:
            displayName: Group display name
            members: List of member objects with 'value' (user ID)

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/groups"

        body: dict[str, Any] = {}
        body['displayName'] = displayName
        if members is not None:
            body['members'] = members

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute create_group")

    async def update_group(
        self,
        group_id: str,
        *,
        displayName: str | None = None,
        members: list[dict[str, str]] | None = None
    ) -> EgnyteResponse:
        """Update a group

        Args:
            group_id: The group ID
            displayName: Group display name
            members: List of member objects

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/groups/{group_id}".format(group_id=group_id)

        body: dict[str, Any] = {}
        if displayName is not None:
            body['displayName'] = displayName
        if members is not None:
            body['members'] = members

        try:
            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute update_group")

    async def delete_group(
        self,
        group_id: str
    ) -> EgnyteResponse:
        """Delete a group

        Args:
            group_id: The group ID

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/groups/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute delete_group")

    async def audit_files(
        self,
        startdate: str,
        enddate: str,
        *,
        count: int | None = None,
        offset: int | None = None,
        folder: str | None = None,
        file: str | None = None,
        users: str | None = None,
        transaction_type: str | None = None
    ) -> EgnyteResponse:
        """Audit file activity (access, uploads, downloads, etc.)

        Args:
            startdate: Start date (YYYY-MM-DD)
            enddate: End date (YYYY-MM-DD)
            count: Number of records to return
            offset: Offset for pagination
            folder: Filter by folder path
            file: Filter by file path
            users: Filter by username
            transaction_type: Transaction type filter

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['startdate'] = startdate
        query_params['enddate'] = enddate
        if count is not None:
            query_params['count'] = str(count)
        if offset is not None:
            query_params['offset'] = str(offset)
        if folder is not None:
            query_params['folder'] = folder
        if file is not None:
            query_params['file'] = file
        if users is not None:
            query_params['users'] = users
        if transaction_type is not None:
            query_params['transaction_type'] = transaction_type

        url = self.base_url + "/audit/files"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed audit_files" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute audit_files")

    async def audit_logins(
        self,
        startdate: str,
        enddate: str,
        *,
        count: int | None = None,
        offset: int | None = None,
        users: str | None = None,
        events: str | None = None,
        access_points: str | None = None
    ) -> EgnyteResponse:
        """Audit login activity

        Args:
            startdate: Start date (YYYY-MM-DD)
            enddate: End date (YYYY-MM-DD)
            count: Number of records to return
            offset: Offset for pagination
            users: Filter by username
            events: Filter by event type
            access_points: Filter by access point

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['startdate'] = startdate
        query_params['enddate'] = enddate
        if count is not None:
            query_params['count'] = str(count)
        if offset is not None:
            query_params['offset'] = str(offset)
        if users is not None:
            query_params['users'] = users
        if events is not None:
            query_params['events'] = events
        if access_points is not None:
            query_params['access_points'] = access_points

        url = self.base_url + "/audit/logins"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed audit_logins" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute audit_logins")

    async def audit_permissions(
        self,
        startdate: str,
        enddate: str,
        *,
        count: int | None = None,
        offset: int | None = None,
        folder: str | None = None,
        users: str | None = None
    ) -> EgnyteResponse:
        """Audit permissions changes

        Args:
            startdate: Start date (YYYY-MM-DD)
            enddate: End date (YYYY-MM-DD)
            count: Number of records to return
            offset: Offset for pagination
            folder: Filter by folder path
            users: Filter by username

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['startdate'] = startdate
        query_params['enddate'] = enddate
        if count is not None:
            query_params['count'] = str(count)
        if offset is not None:
            query_params['offset'] = str(offset)
        if folder is not None:
            query_params['folder'] = folder
        if users is not None:
            query_params['users'] = users

        url = self.base_url + "/audit/permissions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed audit_permissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute audit_permissions")

    async def search(
        self,
        query: str,
        *,
        offset: int | None = None,
        count: int | None = None,
        folder: str | None = None,
        modified_before: str | None = None,
        modified_after: str | None = None,
        type_: str | None = None
    ) -> EgnyteResponse:
        """Search for files and folders

        Args:
            query: Search query string
            offset: Offset for pagination
            count: Number of results to return
            folder: Restrict search to folder path
            modified_before: Filter modified before (ISO 8601)
            modified_after: Filter modified after (ISO 8601)
            type_: Filter by type (file, folder)

        Returns:
            EgnyteResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['query'] = query
        if offset is not None:
            query_params['offset'] = str(offset)
        if count is not None:
            query_params['count'] = str(count)
        if folder is not None:
            query_params['folder'] = folder
        if modified_before is not None:
            query_params['modified_before'] = modified_before
        if modified_after is not None:
            query_params['modified_after'] = modified_after
        if type_ is not None:
            query_params['type'] = type_

        url = self.base_url + "/search"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute search")

    async def get_folder_permissions(
        self,
        path: str
    ) -> EgnyteResponse:
        """Get permissions for a folder

        Args:
            path: Folder path

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/perms/{path}".format(path=path)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder_permissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute get_folder_permissions")

    async def set_folder_permissions(
        self,
        path: str,
        *,
        userPerms: dict[str, str] | None = None,
        groupPerms: dict[str, str] | None = None,
        inheritsPermissions: bool | None = None
    ) -> EgnyteResponse:
        """Set permissions for a folder

        Args:
            path: Folder path
            userPerms: User permissions mapping
            groupPerms: Group permissions mapping
            inheritsPermissions: Whether folder inherits parent permissions

        Returns:
            EgnyteResponse with operation result
        """
        url = self.base_url + "/perms/{path}".format(path=path)

        body: dict[str, Any] = {}
        if userPerms is not None:
            body['userPerms'] = userPerms
        if groupPerms is not None:
            body['groupPerms'] = groupPerms
        if inheritsPermissions is not None:
            body['inheritsPermissions'] = inheritsPermissions

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return EgnyteResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed set_folder_permissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return EgnyteResponse(success=False, error=str(e), message="Failed to execute set_folder_permissions")
