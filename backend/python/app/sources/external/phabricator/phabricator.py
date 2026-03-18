# ruff: noqa
"""
Phabricator Conduit API DataSource - Auto-generated API wrapper

Generated from Phabricator Conduit API documentation.
Uses HTTP client for direct REST API interactions.
All Phabricator API calls are POST with form-encoded body including api.token.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.phabricator.phabricator import PhabricatorClient, PhabricatorResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PhabricatorDataSource:
    """Phabricator Conduit API DataSource

    Provides async wrapper methods for Phabricator Conduit API operations:
    - Maniphest (Tasks) search
    - Differential (Code Review) revision search
    - Project search
    - User search
    - Paste search
    - Diffusion (Repository) search
    - PHID lookup
    - Feed (Activity) query

    All Phabricator API calls are POST requests with form-encoded body.
    The api.token is automatically injected into each request body.

    All methods return PhabricatorResponse objects.
    """

    def __init__(self, client: PhabricatorClient) -> None:
        """Initialize with PhabricatorClient.

        Args:
            client: PhabricatorClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc
        self.api_token = self.http.get_api_token()

    def get_data_source(self) -> 'PhabricatorDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PhabricatorClient:
        """Return the underlying PhabricatorClient."""
        return self._client

    def _build_form_body(self, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Build form-encoded body with api.token included.

        Args:
            params: Additional parameters to include in the body

        Returns:
            Dict with api.token and all additional parameters
        """
        body: dict[str, Any] = {"api.token": self.api_token}
        if params:
            body.update(params)
        return body

    async def search_maniphest_tasks(
        self,
        *,
        constraints: dict[str, Any] | None = None,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> PhabricatorResponse:
        """Search Maniphest tasks (POST /maniphest.search)

        Args:
            constraints: Search constraints (e.g. {"statuses": ["open"]})
            limit: Maximum number of results to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination
            order: Result ordering

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/maniphest.search"

        params: dict[str, Any] = {}
        if constraints is not None:
            params['constraints'] = constraints
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before
        if order is not None:
            params['order'] = order

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_maniphest_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute search_maniphest_tasks")

    async def search_differential_revisions(
        self,
        *,
        constraints: dict[str, Any] | None = None,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> PhabricatorResponse:
        """Search Differential revisions (POST /differential.revision.search)

        Args:
            constraints: Search constraints (e.g. {"statuses": ["needs-review"]})
            limit: Maximum number of results to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination
            order: Result ordering

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/differential.revision.search"

        params: dict[str, Any] = {}
        if constraints is not None:
            params['constraints'] = constraints
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before
        if order is not None:
            params['order'] = order

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_differential_revisions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute search_differential_revisions")

    async def search_projects(
        self,
        *,
        constraints: dict[str, Any] | None = None,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> PhabricatorResponse:
        """Search projects (POST /project.search)

        Args:
            constraints: Search constraints (e.g. {"name": "Backend"})
            limit: Maximum number of results to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination
            order: Result ordering

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/project.search"

        params: dict[str, Any] = {}
        if constraints is not None:
            params['constraints'] = constraints
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before
        if order is not None:
            params['order'] = order

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute search_projects")

    async def search_users(
        self,
        *,
        constraints: dict[str, Any] | None = None,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> PhabricatorResponse:
        """Search users (POST /user.search)

        Args:
            constraints: Search constraints (e.g. {"usernames": ["admin"]})
            limit: Maximum number of results to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination
            order: Result ordering

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/user.search"

        params: dict[str, Any] = {}
        if constraints is not None:
            params['constraints'] = constraints
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before
        if order is not None:
            params['order'] = order

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute search_users")

    async def search_pastes(
        self,
        *,
        constraints: dict[str, Any] | None = None,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> PhabricatorResponse:
        """Search pastes (POST /paste.search)

        Args:
            constraints: Search constraints
            limit: Maximum number of results to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination
            order: Result ordering

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/paste.search"

        params: dict[str, Any] = {}
        if constraints is not None:
            params['constraints'] = constraints
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before
        if order is not None:
            params['order'] = order

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_pastes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute search_pastes")

    async def search_repositories(
        self,
        *,
        constraints: dict[str, Any] | None = None,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
        order: str | None = None,
    ) -> PhabricatorResponse:
        """Search Diffusion repositories (POST /diffusion.repository.search)

        Args:
            constraints: Search constraints
            limit: Maximum number of results to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination
            order: Result ordering

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/diffusion.repository.search"

        params: dict[str, Any] = {}
        if constraints is not None:
            params['constraints'] = constraints
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before
        if order is not None:
            params['order'] = order

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_repositories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute search_repositories")

    async def lookup_phids(
        self,
        names: list[str],
    ) -> PhabricatorResponse:
        """Look up PHIDs by name (POST /phid.lookup)

        Args:
            names: List of PHID names to look up (e.g. ["T123", "D456"])

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/phid.lookup"

        params: dict[str, Any] = {}
        for i, name in enumerate(names):
            params[f'names[{i}]'] = name

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed lookup_phids" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute lookup_phids")

    async def query_feed(
        self,
        *,
        limit: int | None = None,
        after: str | None = None,
        before: str | None = None,
    ) -> PhabricatorResponse:
        """Query the activity feed (POST /feed.query)

        Args:
            limit: Maximum number of feed items to return
            after: Cursor for forward pagination
            before: Cursor for backward pagination

        Returns:
            PhabricatorResponse with operation result
        """
        url = self.base_url + "/feed.query"

        params: dict[str, Any] = {}
        if limit is not None:
            params['limit'] = limit
        if after is not None:
            params['after'] = after
        if before is not None:
            params['before'] = before

        body = self._build_form_body(params)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PhabricatorResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed query_feed" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PhabricatorResponse(success=False, error=str(e), message="Failed to execute query_feed")
