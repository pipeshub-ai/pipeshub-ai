# ruff: noqa
"""
Ping Identity (PingOne) REST API DataSource - Auto-generated API wrapper

Generated from PingOne Platform API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.

Note: For OAuth clients, ensure_authenticated() is called before each
      request to auto-fetch a client_credentials OAuth token.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.pingidentity.pingidentity import PingIdentityClient, PingIdentityResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PingIdentityDataSource:
    """PingOne REST API DataSource

    Provides async wrapper methods for PingOne REST API operations:
    - Users management
    - Groups management
    - Populations management
    - Applications management
    - Sign-On Policies management
    - Schemas management
    - Password Policies management
    - Identity Providers management
    - Gateways management

    All methods return PingIdentityResponse objects.
    """

    def __init__(self, client: PingIdentityClient) -> None:
        """Initialize with PingIdentityClient.

        Args:
            client: PingIdentityClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PingIdentityDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PingIdentityClient:
        """Return the underlying PingIdentityClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        limit: int | None = None,
        filter: str | None = None
    ) -> PingIdentityResponse:
        """List all users in the environment

        HTTP GET /users

        Args:
            limit: Maximum number of results
            filter: SCIM filter expression

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if filter is not None:
            query_params['filter'] = str(filter)

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
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_users")


    async def get_user(
        self,
        user_id: str
    ) -> PingIdentityResponse:
        """Get a specific user by ID

        HTTP GET /users/{user_id}

        Args:
            user_id: The user id

        Returns:
            PingIdentityResponse with operation result
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
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute get_user")


    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def list_groups(
        self,
        *,
        limit: int | None = None,
        filter: str | None = None
    ) -> PingIdentityResponse:
        """List all groups in the environment

        HTTP GET /groups

        Args:
            limit: Maximum number of results
            filter: SCIM filter expression

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if filter is not None:
            query_params['filter'] = str(filter)

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
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_groups")


    async def get_group(
        self,
        group_id: str
    ) -> PingIdentityResponse:
        """Get a specific group by ID

        HTTP GET /groups/{group_id}

        Args:
            group_id: The group id

        Returns:
            PingIdentityResponse with operation result
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
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute get_group")


    # -----------------------------------------------------------------------
    # Populations
    # -----------------------------------------------------------------------

    async def list_populations(
        self,
        *,
        limit: int | None = None
    ) -> PingIdentityResponse:
        """List all populations in the environment

        HTTP GET /populations

        Args:
            limit: Maximum number of results

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/populations"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_populations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_populations")


    async def get_population(
        self,
        population_id: str
    ) -> PingIdentityResponse:
        """Get a specific population by ID

        HTTP GET /populations/{population_id}

        Args:
            population_id: The population id

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/populations/{population_id}".format(population_id=population_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_population" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute get_population")


    # -----------------------------------------------------------------------
    # Applications
    # -----------------------------------------------------------------------

    async def list_applications(
        self,
        *,
        limit: int | None = None
    ) -> PingIdentityResponse:
        """List all applications in the environment

        HTTP GET /applications

        Args:
            limit: Maximum number of results

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/applications"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_applications" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_applications")


    async def get_application(
        self,
        application_id: str
    ) -> PingIdentityResponse:
        """Get a specific application by ID

        HTTP GET /applications/{application_id}

        Args:
            application_id: The application id

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/applications/{application_id}".format(application_id=application_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_application" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute get_application")


    # -----------------------------------------------------------------------
    # Sign-On Policies
    # -----------------------------------------------------------------------

    async def list_sign_on_policies(
        self,
        *,
        limit: int | None = None
    ) -> PingIdentityResponse:
        """List all sign-on policies in the environment

        HTTP GET /signOnPolicies

        Args:
            limit: Maximum number of results

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/signOnPolicies"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_sign_on_policies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_sign_on_policies")


    async def get_sign_on_policy(
        self,
        policy_id: str
    ) -> PingIdentityResponse:
        """Get a specific sign-on policy by ID

        HTTP GET /signOnPolicies/{policy_id}

        Args:
            policy_id: The policy id

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/signOnPolicies/{policy_id}".format(policy_id=policy_id)


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_sign_on_policy" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute get_sign_on_policy")


    # -----------------------------------------------------------------------
    # Schemas
    # -----------------------------------------------------------------------

    async def list_schemas(
        self
    ) -> PingIdentityResponse:
        """List all schemas in the environment

        HTTP GET /schemas

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/schemas"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_schemas" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_schemas")


    # -----------------------------------------------------------------------
    # Password Policies
    # -----------------------------------------------------------------------

    async def list_password_policies(
        self
    ) -> PingIdentityResponse:
        """List all password policies in the environment

        HTTP GET /passwordPolicies

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/passwordPolicies"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_password_policies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_password_policies")


    # -----------------------------------------------------------------------
    # Identity Providers
    # -----------------------------------------------------------------------

    async def list_identity_providers(
        self
    ) -> PingIdentityResponse:
        """List all identity providers in the environment

        HTTP GET /identityProviders

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/identityProviders"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_identity_providers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_identity_providers")


    # -----------------------------------------------------------------------
    # Gateways
    # -----------------------------------------------------------------------

    async def list_gateways(
        self
    ) -> PingIdentityResponse:
        """List all gateways in the environment

        HTTP GET /gateways

        Returns:
            PingIdentityResponse with operation result
        """
        if hasattr(self.http, 'ensure_authenticated'):
            await self.http.ensure_authenticated()

        url = self.base_url + "/gateways"


        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PingIdentityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_gateways" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PingIdentityResponse(success=False, error=str(e), message="Failed to execute list_gateways")


