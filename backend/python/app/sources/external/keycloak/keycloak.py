# ruff: noqa
"""
Keycloak Admin REST API DataSource - Auto-generated API wrapper

Generated from Keycloak Admin REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_authenticated() is called before each
      request to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.keycloak.keycloak import KeycloakClient, KeycloakResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class KeycloakDataSource:
    """Keycloak Admin REST API DataSource

    Provides async wrapper methods for Keycloak Admin REST API operations:
    - Users management
    - Groups management
    - Clients management
    - Roles management
    - Events (login and admin)
    - Identity Providers
    - Authentication Flows

    All methods return KeycloakResponse objects.
    """

    def __init__(self, client: KeycloakClient) -> None:
        """Initialize with KeycloakClient.

        Args:
            client: KeycloakClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'KeycloakDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> KeycloakClient:
        """Return the underlying KeycloakClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        first: int | None = None,
        max: int | None = None,
        search: str | None = None,
        username: str | None = None,
        email: str | None = None,
        enabled: str | None = None
    ) -> KeycloakResponse:
        """List all users in the realm

        HTTP GET /users

        Args:
            first: Pagination offset
            max: Maximum results to return
            search: Search string for username, first/last name, or email
            username: Filter by username
            email: Filter by email
            enabled: Filter by enabled status (true/false)

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)
        if search is not None:
            query_params['search'] = str(search)
        if username is not None:
            query_params['username'] = str(username)
        if email is not None:
            query_params['email'] = str(email)
        if enabled is not None:
            query_params['enabled'] = str(enabled)

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
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_users")


    async def get_user(
        self,
        user_id: str
    ) -> KeycloakResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user id

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/users/{user_id}".format(user_id=user_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_user")


    async def get_users_count(
        self
    ) -> KeycloakResponse:
        """Get the total number of users in the realm

        HTTP GET /users/count

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/users/count"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_users_count" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_users_count")


    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def list_groups(
        self,
        *,
        first: int | None = None,
        max: int | None = None,
        search: str | None = None
    ) -> KeycloakResponse:
        """List all groups in the realm

        HTTP GET /groups

        Args:
            first: Pagination offset
            max: Maximum results to return
            search: Search string for group name

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)
        if search is not None:
            query_params['search'] = str(search)

        url = self.base_url + "/groups"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_groups")


    async def get_group(
        self,
        group_id: str
    ) -> KeycloakResponse:
        """Get a specific group by ID

        HTTP GET /groups/{group_id}

        Args:
            group_id: The group id

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/groups/{group_id}".format(group_id=group_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_group")


    async def get_group_members(
        self,
        group_id: str,
        *,
        first: int | None = None,
        max: int | None = None
    ) -> KeycloakResponse:
        """Get members of a specific group

        HTTP GET /groups/{group_id}/members

        Args:
            group_id: The group id
            first: Pagination offset
            max: Maximum results to return

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)

        url = self.base_url + "/groups/{group_id}/members".format(group_id=group_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_group_members")


    # -----------------------------------------------------------------------
    # Clients
    # -----------------------------------------------------------------------

    async def list_clients(
        self,
        *,
        first: int | None = None,
        max: int | None = None,
        search: str | None = None
    ) -> KeycloakResponse:
        """List all clients in the realm

        HTTP GET /clients

        Args:
            first: Pagination offset
            max: Maximum results to return
            search: Filter by client ID or name

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)
        if search is not None:
            query_params['search'] = str(search)

        url = self.base_url + "/clients"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_clients" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_clients")


    async def get_client(
        self,
        client_id: str
    ) -> KeycloakResponse:
        """Get a specific client by ID

        HTTP GET /clients/{client_id}

        Args:
            client_id: The client id

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/clients/{client_id}".format(client_id=client_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_client" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_client")


    # -----------------------------------------------------------------------
    # Roles
    # -----------------------------------------------------------------------

    async def list_roles(
        self,
        *,
        first: int | None = None,
        max: int | None = None,
        search: str | None = None
    ) -> KeycloakResponse:
        """List all realm-level roles

        HTTP GET /roles

        Args:
            first: Pagination offset
            max: Maximum results to return
            search: Filter by role name

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)
        if search is not None:
            query_params['search'] = str(search)

        url = self.base_url + "/roles"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_roles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_roles")


    async def get_role(
        self,
        role_name: str
    ) -> KeycloakResponse:
        """Get a specific role by name

        HTTP GET /roles/{role_name}

        Args:
            role_name: The role name

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/roles/{role_name}".format(role_name=role_name)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_role" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_role")


    async def get_role_users(
        self,
        role_name: str,
        *,
        first: int | None = None,
        max: int | None = None
    ) -> KeycloakResponse:
        """Get users assigned to a specific role

        HTTP GET /roles/{role_name}/users

        Args:
            role_name: The role name
            first: Pagination offset
            max: Maximum results to return

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)

        url = self.base_url + "/roles/{role_name}/users".format(role_name=role_name)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_role_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute get_role_users")


    # -----------------------------------------------------------------------
    # Events
    # -----------------------------------------------------------------------

    async def list_events(
        self,
        *,
        type: str | None = None,
        dateFrom: str | None = None,
        dateTo: str | None = None,
        first: int | None = None,
        max: int | None = None
    ) -> KeycloakResponse:
        """List login events in the realm

        HTTP GET /events

        Args:
            type: Event type filter
            dateFrom: Date range start (yyyy-MM-dd)
            dateTo: Date range end (yyyy-MM-dd)
            first: Pagination offset
            max: Maximum results to return

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if type is not None:
            query_params['type'] = str(type)
        if dateFrom is not None:
            query_params['dateFrom'] = str(dateFrom)
        if dateTo is not None:
            query_params['dateTo'] = str(dateTo)
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)

        url = self.base_url + "/events"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_events" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_events")


    async def list_admin_events(
        self,
        *,
        first: int | None = None,
        max: int | None = None
    ) -> KeycloakResponse:
        """List admin events in the realm

        HTTP GET /admin-events

        Args:
            first: Pagination offset
            max: Maximum results to return

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if first is not None:
            query_params['first'] = str(first)
        if max is not None:
            query_params['max'] = str(max)

        url = self.base_url + "/admin-events"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_admin_events" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_admin_events")


    # -----------------------------------------------------------------------
    # Identity Providers
    # -----------------------------------------------------------------------

    async def list_identity_providers(
        self
    ) -> KeycloakResponse:
        """List all identity provider instances

        HTTP GET /identity-provider/instances

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/identity-provider/instances"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_identity_providers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_identity_providers")


    # -----------------------------------------------------------------------
    # Authentication Flows
    # -----------------------------------------------------------------------

    async def list_authentication_flows(
        self
    ) -> KeycloakResponse:
        """List all authentication flows

        HTTP GET /authentication/flows

        Returns:
            KeycloakResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/authentication/flows"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return KeycloakResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_authentication_flows" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return KeycloakResponse(success=False, error=str(e), message="Failed to execute list_authentication_flows")


