# ruff: noqa
"""
Bitbucket Cloud API 2.0 DataSource
===================================

Generated comprehensive datasource for Bitbucket Cloud REST API.
Covers all major API groups with proper typing and error handling.

Total endpoints: 154

API Documentation: https://developer.atlassian.com/cloud/bitbucket/rest/
"""

from typing import Dict, List, Optional, Any
from app.sources.client.bitbucket.bitbucket import (
    BitbucketClient,
    BitbucketRESTClientViaToken,
    BitbucketRESTClientViaOAuth,
    BitbucketResponse,
)
from app.sources.client.http.http_request import HTTPRequest


class BitbucketDataSource:
    """Comprehensive Bitbucket Cloud API DataSource.

    Provides async wrapper methods for all Bitbucket Cloud REST API operations.
    All methods return standardized BitbucketResponse objects.

    Usage:
        ```python
        from app.sources.client.bitbucket.bitbucket import BitbucketClient, BitbucketTokenConfig
        from app.sources.external.bitbucket.bitbucket_data_source import BitbucketDataSource

        # Create client
        config = BitbucketTokenConfig(token="your_token")
        client = BitbucketClient.build_with_config(config)

        # Create datasource
        datasource = BitbucketDataSource(client)

        # Use it
        response = await datasource.list_workspaces()
        if response.success:
            print(response.data)
        ```

    API Groups Covered:
    - Workspaces (members, projects, webhooks, permissions)
    - Repositories (CRUD, forks, watchers, webhooks)
    - Commits (history, statuses, approvals, comments)
    - Pull Requests (CRUD, approvals, comments, merge, decline)
    - Branches & Tags (CRUD, restrictions)
    - Pipelines (run, stop, steps, variables, config)
    - Projects (CRUD operations)
    - Issues (CRUD, comments, attachments, votes, watch)
    - Source/Files (browse, read, history)
    - Users (profile, repositories, permissions, emails)
    - Snippets (CRUD, comments, files)
    - Deployments (environments, variables)
    - Downloads (upload, download, delete)
    - Permissions (users, groups, repository access)
    - SSH Keys (CRUD operations)
    - Reports (code quality, test results, annotations)
    - Default Reviewers (CRUD operations)

    Generated methods: 154
    """

    def __init__(self, bitbucket_client: BitbucketClient) -> None:
        """Initialize Bitbucket DataSource.

        Args:
            bitbucket_client: BitbucketClient instance (supports both Token and OAuth)
        """
        self.client = bitbucket_client.get_client()
        self._bitbucket_client = bitbucket_client

    def get_client(self) -> BitbucketClient:
        """Get the underlying BitbucketClient."""
        return self._bitbucket_client

    async def list_workspaces(self, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10, page: Optional[int] = None) -> BitbucketResponse:
        """Returns a paginated list of workspaces accessible by the authenticated user.

        Args:
            role: Filter by role (member, collaborator, owner)
            q: BBQL query string for filtering
            sort: Field to sort by
            pagelen: Number of items per page
            page: Page number

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: workspace
        """
        try:
            params = {}
            if role is not None:
                params["role"] = role
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen
            if page is not None:
                params["page"] = page

            url = f"{self.client.get_base_url()}/workspaces"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_workspaces: {str(e)}"
            )

    async def get_workspace(self, workspace: str) -> BitbucketResponse:
        """Returns the requested workspace.

        Args:
            workspace: Workspace slug or UUID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: workspace
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_workspace: {str(e)}"
            )

    async def list_workspace_members(self, workspace: str, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns all members of the requested workspace.

        Args:
            workspace: Workspace slug or UUID
            q: BBQL query for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: workspace
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/workspaces/{workspace}/members"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_workspace_members: {str(e)}"
            )

    async def get_workspace_member(self, workspace: str, member: str) -> BitbucketResponse:
        """Returns the workspace membership of a specific user.

        Args:
            workspace: Workspace slug or UUID
            member: Member UUID or username

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: workspace
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/members/{member}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_workspace_member: {str(e)}"
            )

    async def list_workspace_projects(self, workspace: str, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns the list of projects in this workspace.

        Args:
            workspace: Workspace slug or UUID
            q: BBQL query for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: project
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/workspaces/{workspace}/projects"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_workspace_projects: {str(e)}"
            )

    async def list_workspace_webhooks(self, workspace: str, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of webhooks installed on this workspace.

        Args:
            workspace: Workspace slug or UUID
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/workspaces/{workspace}/hooks"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_workspace_webhooks: {str(e)}"
            )

    async def create_workspace_webhook(self, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new webhook on the specified workspace.

        Args:
            workspace: Workspace slug or UUID
            body: Webhook configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/hooks"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_workspace_webhook: {str(e)}"
            )

    async def get_workspace_webhook(self, workspace: str, uid: str) -> BitbucketResponse:
        """Returns the webhook with the specified id.

        Args:
            workspace: Workspace slug or UUID
            uid: Webhook UUID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/hooks/{uid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_workspace_webhook: {str(e)}"
            )

    async def update_workspace_webhook(self, workspace: str, uid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified webhook.

        Args:
            workspace: Workspace slug or UUID
            uid: Webhook UUID
            body: Updated webhook configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/hooks/{uid}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_workspace_webhook: {str(e)}"
            )

    async def delete_workspace_webhook(self, workspace: str, uid: str) -> BitbucketResponse:
        """Deletes the specified webhook.

        Args:
            workspace: Workspace slug or UUID
            uid: Webhook UUID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/hooks/{uid}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_workspace_webhook: {str(e)}"
            )

    async def list_workspace_permissions(self, workspace: str, q: Optional[str] = None) -> BitbucketResponse:
        """Returns the list of members in a workspace and their permission levels.

        Args:
            workspace: Workspace slug or UUID
            q: BBQL query for filtering

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: workspace
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q

            url = f"{self.client.get_base_url()}/workspaces/{workspace}/permissions"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_workspace_permissions: {str(e)}"
            )

    async def list_repositories(self, workspace: str, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of all repositories owned by the specified workspace.

        Args:
            workspace: Workspace slug or UUID
            role: Filter by role
            q: BBQL query string for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if role is not None:
                params["role"] = role
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_repositories: {str(e)}"
            )

    async def get_repository(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns the object describing this repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_repository: {str(e)}"
            )

    async def create_repository(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Repository configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_repository: {str(e)}"
            )

    async def update_repository(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Updated repository configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_repository: {str(e)}"
            )

    async def delete_repository(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Deletes the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:delete
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_repository: {str(e)}"
            )

    async def list_repository_watchers(self, workspace: str, repo_slug: str, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of all the watchers on the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/watchers"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_repository_watchers: {str(e)}"
            )

    async def list_repository_forks(self, workspace: str, repo_slug: str, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of all the forks of the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/forks"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_repository_forks: {str(e)}"
            )

    async def fork_repository(self, workspace: str, repo_slug: str, body: Optional[Dict[str, Any]] = None) -> BitbucketResponse:
        """Creates a new fork of the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Fork configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/forks"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to fork_repository: {str(e)}"
            )

    async def list_repository_webhooks(self, workspace: str, repo_slug: str, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of webhooks installed on this repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/hooks"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_repository_webhooks: {str(e)}"
            )

    async def create_repository_webhook(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new webhook on the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Webhook configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/hooks"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_repository_webhook: {str(e)}"
            )

    async def get_repository_webhook(self, workspace: str, repo_slug: str, uid: str) -> BitbucketResponse:
        """Returns the webhook with the specified id.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            uid: Webhook UUID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/hooks/{uid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_repository_webhook: {str(e)}"
            )

    async def update_repository_webhook(self, workspace: str, repo_slug: str, uid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified webhook.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            uid: Webhook UUID
            body: Updated webhook configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/hooks/{uid}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_repository_webhook: {str(e)}"
            )

    async def delete_repository_webhook(self, workspace: str, repo_slug: str, uid: str) -> BitbucketResponse:
        """Deletes the specified webhook.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            uid: Webhook UUID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: webhook
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/hooks/{uid}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_repository_webhook: {str(e)}"
            )

    async def list_commits(self, workspace: str, repo_slug: str, include: Optional[str] = None, exclude: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns all commits in the repository that are reachable from specified commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            include: Branch/tag/commit to start from
            exclude: Branch/tag/commit to exclude
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if include is not None:
                params["include"] = include
            if exclude is not None:
                params["exclude"] = exclude
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commits"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_commits: {str(e)}"
            )

    async def get_commit(self, workspace: str, repo_slug: str, commit: str) -> BitbucketResponse:
        """Returns the specified commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commits/{commit}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_commit: {str(e)}"
            )

    async def list_commit_comments(self, workspace: str, repo_slug: str, commit: str, pagelen: int = 10) -> BitbucketResponse:
        """Returns the commit's comments.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commits/{commit}/comments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_commit_comments: {str(e)}"
            )

    async def create_commit_comment(self, workspace: str, repo_slug: str, commit: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a comment on the specified commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            body: Comment content

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commits/{commit}/comments"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_commit_comment: {str(e)}"
            )

    async def list_commit_statuses(self, workspace: str, repo_slug: str, commit: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns all statuses for a given commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_commit_statuses: {str(e)}"
            )

    async def create_commit_build_status(self, workspace: str, repo_slug: str, commit: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new build status on the specified commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            body: Build status data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_commit_build_status: {str(e)}"
            )

    async def get_commit_build_status(self, workspace: str, repo_slug: str, commit: str, key: str) -> BitbucketResponse:
        """Returns the specified build status for a commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            key: Build status key

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build/{key}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_commit_build_status: {str(e)}"
            )

    async def update_commit_build_status(self, workspace: str, repo_slug: str, commit: str, key: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified build status for a commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            key: Build status key
            body: Updated build status data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build/{key}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_commit_build_status: {str(e)}"
            )

    async def approve_commit(self, workspace: str, repo_slug: str, commit: str) -> BitbucketResponse:
        """Approve the specified commit as the authenticated user.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/approve"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to approve_commit: {str(e)}"
            )

    async def unapprove_commit(self, workspace: str, repo_slug: str, commit: str) -> BitbucketResponse:
        """Revoke approval for the specified commit as the authenticated user.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/approve"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to unapprove_commit: {str(e)}"
            )

    async def list_pull_requests(self, workspace: str, repo_slug: str, state: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns all pull requests on the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            state: Filter by state: OPEN, MERGED, DECLINED, SUPERSEDED
            q: BBQL query for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            params = {}
            if state is not None:
                params["state"] = state
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pull_requests: {str(e)}"
            )

    async def create_pull_request(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Pull request data (title, source, destination, etc.)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_pull_request: {str(e)}"
            )

    async def get_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Returns the specified pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pull_request: {str(e)}"
            )

    async def update_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Mutates the specified pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            body: Updated pull request data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_pull_request: {str(e)}"
            )

    async def get_pull_request_activity(self, workspace: str, repo_slug: str, pull_request_id: int, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of the pull request's activity log.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/activity"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pull_request_activity: {str(e)}"
            )

    async def list_pull_request_comments(self, workspace: str, repo_slug: str, pull_request_id: int, q: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of the pull request's comments.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            q: BBQL query for filtering
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pull_request_comments: {str(e)}"
            )

    async def create_pull_request_comment(self, workspace: str, repo_slug: str, pull_request_id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new pull request comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            body: Comment content

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_pull_request_comment: {str(e)}"
            )

    async def get_pull_request_comment(self, workspace: str, repo_slug: str, pull_request_id: int, comment_id: int) -> BitbucketResponse:
        """Returns a specific pull request comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            comment_id: Comment ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pull_request_comment: {str(e)}"
            )

    async def update_pull_request_comment(self, workspace: str, repo_slug: str, pull_request_id: int, comment_id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates a specific pull request comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            comment_id: Comment ID
            body: Updated comment content

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_pull_request_comment: {str(e)}"
            )

    async def delete_pull_request_comment(self, workspace: str, repo_slug: str, pull_request_id: int, comment_id: int) -> BitbucketResponse:
        """Deletes a specific pull request comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            comment_id: Comment ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_pull_request_comment: {str(e)}"
            )

    async def list_pull_request_commits(self, workspace: str, repo_slug: str, pull_request_id: int, pagelen: int = 10) -> BitbucketResponse:
        """Returns all commits on the specified pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/commits"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pull_request_commits: {str(e)}"
            )

    async def approve_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Approve the specified pull request as the authenticated user.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/approve"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to approve_pull_request: {str(e)}"
            )

    async def unapprove_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Revoke the authenticated user's approval of the specified pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/approve"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to unapprove_pull_request: {str(e)}"
            )

    async def get_pull_request_diff(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Returns the diff for the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/diff"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pull_request_diff: {str(e)}"
            )

    async def get_pull_request_diffstat(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Returns the diffstat for the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/diffstat"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pull_request_diffstat: {str(e)}"
            )

    async def merge_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int, body: Optional[Dict[str, Any]] = None) -> BitbucketResponse:
        """Merges the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            body: Merge options

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/merge"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to merge_pull_request: {str(e)}"
            )

    async def decline_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Declines the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/decline"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to decline_pull_request: {str(e)}"
            )

    async def list_pull_request_statuses(self, workspace: str, repo_slug: str, pull_request_id: int, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns all statuses for the specified pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/statuses"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pull_request_statuses: {str(e)}"
            )

    async def get_pull_request_patch(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Returns the patch for the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/patch"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pull_request_patch: {str(e)}"
            )

    async def request_changes_on_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Request changes on the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/request-changes"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to request_changes_on_pull_request: {str(e)}"
            )

    async def unrequest_changes_on_pull_request(self, workspace: str, repo_slug: str, pull_request_id: int) -> BitbucketResponse:
        """Remove request for changes on the pull request.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pull_request_id: Pull request ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pullrequest:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/request-changes"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to unrequest_changes_on_pull_request: {str(e)}"
            )

    async def list_branches(self, workspace: str, repo_slug: str, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of branches for the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            q: BBQL query for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/branches"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_branches: {str(e)}"
            )

    async def create_branch(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new branch in the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Branch data (name, target)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/branches"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_branch: {str(e)}"
            )

    async def get_branch(self, workspace: str, repo_slug: str, name: str) -> BitbucketResponse:
        """Returns a branch object within the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            name: Branch name

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/branches/{name}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_branch: {str(e)}"
            )

    async def delete_branch(self, workspace: str, repo_slug: str, name: str) -> BitbucketResponse:
        """Deletes a branch in the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            name: Branch name

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/branches/{name}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_branch: {str(e)}"
            )

    async def list_branch_restrictions(self, workspace: str, repo_slug: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns a paginated list of all branch restrictions on the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/branch-restrictions"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_branch_restrictions: {str(e)}"
            )

    async def create_branch_restriction(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new branch restriction rule.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Branch restriction data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/branch-restrictions"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_branch_restriction: {str(e)}"
            )

    async def get_branch_restriction(self, workspace: str, repo_slug: str, id: int) -> BitbucketResponse:
        """Returns a specific branch restriction rule.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            id: Branch restriction ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_branch_restriction: {str(e)}"
            )

    async def update_branch_restriction(self, workspace: str, repo_slug: str, id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates an existing branch restriction rule.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            id: Branch restriction ID
            body: Updated branch restriction data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_branch_restriction: {str(e)}"
            )

    async def delete_branch_restriction(self, workspace: str, repo_slug: str, id: int) -> BitbucketResponse:
        """Deletes an existing branch restriction rule.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            id: Branch restriction ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_branch_restriction: {str(e)}"
            )

    async def list_tags(self, workspace: str, repo_slug: str, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of tags for the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            q: BBQL query for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/tags"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_tags: {str(e)}"
            )

    async def create_tag(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new tag in the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Tag data (name, target)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/tags"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_tag: {str(e)}"
            )

    async def get_tag(self, workspace: str, repo_slug: str, name: str) -> BitbucketResponse:
        """Returns a tag object within the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            name: Tag name

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/tags/{name}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_tag: {str(e)}"
            )

    async def delete_tag(self, workspace: str, repo_slug: str, name: str) -> BitbucketResponse:
        """Deletes a tag in the specified repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            name: Tag name

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/refs/tags/{name}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_tag: {str(e)}"
            )

    async def list_pipelines(self, workspace: str, repo_slug: str, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns a paginated list of pipelines.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline
        """
        try:
            params = {}
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pipelines: {str(e)}"
            )

    async def create_pipeline(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Triggers a new pipeline.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Pipeline configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_pipeline: {str(e)}"
            )

    async def get_pipeline(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> BitbucketResponse:
        """Returns a pipeline for the given pipeline UUID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pipeline_uuid: Pipeline UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pipeline: {str(e)}"
            )

    async def stop_pipeline(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> BitbucketResponse:
        """Stops a pipeline.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pipeline_uuid: Pipeline UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/stopPipeline"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to stop_pipeline: {str(e)}"
            )

    async def list_pipeline_steps(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> BitbucketResponse:
        """Returns the steps of a pipeline.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pipeline_uuid: Pipeline UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pipeline_steps: {str(e)}"
            )

    async def get_pipeline_step(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str) -> BitbucketResponse:
        """Returns a given step of a pipeline.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pipeline_uuid: Pipeline UUID with curly braces
            step_uuid: Step UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pipeline_step: {str(e)}"
            )

    async def get_pipeline_step_log(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str) -> BitbucketResponse:
        """Returns the log of a given step of a pipeline.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            pipeline_uuid: Pipeline UUID with curly braces
            step_uuid: Step UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/log"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pipeline_step_log: {str(e)}"
            )

    async def get_pipeline_config(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns the configuration for the repository's pipelines.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pipeline_config: {str(e)}"
            )

    async def update_pipeline_config(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the configuration for the repository's pipelines.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Pipeline configuration

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_pipeline_config: {str(e)}"
            )

    async def list_pipeline_variables(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns a list of pipeline variables for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:variable
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config/variables"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_pipeline_variables: {str(e)}"
            )

    async def create_pipeline_variable(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new pipeline variable.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Variable data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:variable:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config/variables"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_pipeline_variable: {str(e)}"
            )

    async def get_pipeline_variable(self, workspace: str, repo_slug: str, variable_uuid: str) -> BitbucketResponse:
        """Returns a pipeline variable by UUID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            variable_uuid: Variable UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:variable
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_pipeline_variable: {str(e)}"
            )

    async def update_pipeline_variable(self, workspace: str, repo_slug: str, variable_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates a pipeline variable.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            variable_uuid: Variable UUID with curly braces
            body: Updated variable data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:variable:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_pipeline_variable: {str(e)}"
            )

    async def delete_pipeline_variable(self, workspace: str, repo_slug: str, variable_uuid: str) -> BitbucketResponse:
        """Deletes a pipeline variable.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            variable_uuid: Variable UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: pipeline:variable:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_pipeline_variable: {str(e)}"
            )

    async def get_project(self, workspace: str, project_key: str) -> BitbucketResponse:
        """Returns the requested project.

        Args:
            workspace: Workspace slug or UUID
            project_key: Project key

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: project
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/projects/{project_key}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_project: {str(e)}"
            )

    async def update_project(self, workspace: str, project_key: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates an existing project.

        Args:
            workspace: Workspace slug or UUID
            project_key: Project key
            body: Updated project data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: project:write
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/projects/{project_key}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_project: {str(e)}"
            )

    async def delete_project(self, workspace: str, project_key: str) -> BitbucketResponse:
        """Deletes the specified project.

        Args:
            workspace: Workspace slug or UUID
            project_key: Project key

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: project:admin
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/projects/{project_key}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_project: {str(e)}"
            )

    async def create_project(self, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new project.

        Args:
            workspace: Workspace slug or UUID
            body: Project data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: project:write
        """
        try:
            url = f"{self.client.get_base_url()}/workspaces/{workspace}/projects"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_project: {str(e)}"
            )

    async def list_issues(self, workspace: str, repo_slug: str, q: Optional[str] = None, sort: Optional[str] = None, pagelen: int = 10) -> BitbucketResponse:
        """Returns the issues in the issue tracker.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            q: BBQL query for filtering
            sort: Field to sort by
            pagelen: Number of items per page

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort
            if pagelen is not None:
                params["pagelen"] = pagelen

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_issues: {str(e)}"
            )

    async def create_issue(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Issue data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_issue: {str(e)}"
            )

    async def get_issue(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Returns the specified issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_issue: {str(e)}"
            )

    async def update_issue(self, workspace: str, repo_slug: str, issue_id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            body: Updated issue data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_issue: {str(e)}"
            )

    async def delete_issue(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Deletes the specified issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_issue: {str(e)}"
            )

    async def list_issue_comments(self, workspace: str, repo_slug: str, issue_id: int, q: Optional[str] = None) -> BitbucketResponse:
        """Returns a paginated list of issue comments.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            q: BBQL query for filtering

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_issue_comments: {str(e)}"
            )

    async def create_issue_comment(self, workspace: str, repo_slug: str, issue_id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new issue comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            body: Comment content

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_issue_comment: {str(e)}"
            )

    async def get_issue_comment(self, workspace: str, repo_slug: str, issue_id: int, comment_id: int) -> BitbucketResponse:
        """Returns the specified issue comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            comment_id: Comment ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_issue_comment: {str(e)}"
            )

    async def update_issue_comment(self, workspace: str, repo_slug: str, issue_id: int, comment_id: int, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified issue comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            comment_id: Comment ID
            body: Updated comment content

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_issue_comment: {str(e)}"
            )

    async def delete_issue_comment(self, workspace: str, repo_slug: str, issue_id: int, comment_id: int) -> BitbucketResponse:
        """Deletes the specified issue comment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            comment_id: Comment ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_issue_comment: {str(e)}"
            )

    async def list_issue_attachments(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Returns all attachments for the specified issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_issue_attachments: {str(e)}"
            )

    async def upload_issue_attachment(self, workspace: str, repo_slug: str, issue_id: int, files: Dict[str, Any]) -> BitbucketResponse:
        """Uploads a file as an attachment to an issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            files: File data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=files
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to upload_issue_attachment: {str(e)}"
            )

    async def get_issue_attachment(self, workspace: str, repo_slug: str, issue_id: int, path: str) -> BitbucketResponse:
        """Returns the specified attachment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            path: Attachment path

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments/{path}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_issue_attachment: {str(e)}"
            )

    async def delete_issue_attachment(self, workspace: str, repo_slug: str, issue_id: int, path: str) -> BitbucketResponse:
        """Deletes the specified attachment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID
            path: Attachment path

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments/{path}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_issue_attachment: {str(e)}"
            )

    async def vote_for_issue(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Vote for this issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to vote_for_issue: {str(e)}"
            )

    async def unvote_for_issue(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Remove your vote from this issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to unvote_for_issue: {str(e)}"
            )

    async def watch_issue(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Start watching this issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to watch_issue: {str(e)}"
            )

    async def unwatch_issue(self, workspace: str, repo_slug: str, issue_id: int) -> BitbucketResponse:
        """Stop watching this issue.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            issue_id: Issue ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: issue:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to unwatch_issue: {str(e)}"
            )

    async def get_file_content(self, workspace: str, repo_slug: str, commit: str, path: str, format: Optional[str] = None) -> BitbucketResponse:
        """Returns the contents of the specified file.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash or branch/tag name
            path: File path
            format: Response format (meta for metadata)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if format is not None:
                params["format"] = format

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/src/{commit}/{path}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_file_content: {str(e)}"
            )

    async def browse_directory(self, workspace: str, repo_slug: str, commit: str, path: Optional[str] = None) -> BitbucketResponse:
        """Returns the contents of a directory in the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash or branch/tag name
            path: Directory path (empty for root)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if path is not None:
                params["path"] = path

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/src/{commit}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to browse_directory: {str(e)}"
            )

    async def get_file_history(self, workspace: str, repo_slug: str, commit: str, path: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns the file's commit history.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash or branch/tag name
            path: File path
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/filehistory/{commit}/{path}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_file_history: {str(e)}"
            )

    async def get_current_user(self) -> BitbucketResponse:
        """Returns the currently logged in user.

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account
        """
        try:
            url = f"{self.client.get_base_url()}/user"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_current_user: {str(e)}"
            )

    async def list_user_emails(self) -> BitbucketResponse:
        """Returns all email addresses associated with the current user.

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: email
        """
        try:
            url = f"{self.client.get_base_url()}/user/emails"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_user_emails: {str(e)}"
            )

    async def get_user_email(self, email: str) -> BitbucketResponse:
        """Returns details about the specified email address.

        Args:
            email: Email address

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: email
        """
        try:
            url = f"{self.client.get_base_url()}/user/emails/{email}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_user_email: {str(e)}"
            )

    async def get_user(self, selected_user: str) -> BitbucketResponse:
        """Returns the profile for the specified user.

        Args:
            selected_user: User UUID or username

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account
        """
        try:
            url = f"{self.client.get_base_url()}/users/{selected_user}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_user: {str(e)}"
            )

    async def list_user_repositories(self, selected_user: str, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns all repositories owned by the specified user.

        Args:
            selected_user: User UUID or username
            role: Filter by role
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            params = {}
            if role is not None:
                params["role"] = role
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/users/{selected_user}/repositories"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_user_repositories: {str(e)}"
            )

    async def list_user_workspace_permissions(self, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns an object for each workspace the caller is a member of.

        Args:
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/user/permissions/workspaces"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_user_workspace_permissions: {str(e)}"
            )

    async def list_user_repository_permissions(self, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns an object for each repository the caller has explicit access to.

        Args:
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/user/permissions/repositories"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_user_repository_permissions: {str(e)}"
            )

    async def list_snippets(self, workspace: str, role: Optional[str] = None) -> BitbucketResponse:
        """Returns all snippets for the workspace.

        Args:
            workspace: Workspace slug or UUID
            role: Filter by role (owner, contributor, member)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet
        """
        try:
            params = {}
            if role is not None:
                params["role"] = role

            url = f"{self.client.get_base_url()}/snippets/{workspace}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_snippets: {str(e)}"
            )

    async def create_snippet(self, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new snippet.

        Args:
            workspace: Workspace slug or UUID
            body: Snippet data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet:write
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_snippet: {str(e)}"
            )

    async def get_snippet(self, workspace: str, encoded_id: str) -> BitbucketResponse:
        """Returns the specified snippet.

        Args:
            workspace: Workspace slug or UUID
            encoded_id: Snippet ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}/{encoded_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_snippet: {str(e)}"
            )

    async def update_snippet(self, workspace: str, encoded_id: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified snippet.

        Args:
            workspace: Workspace slug or UUID
            encoded_id: Snippet ID
            body: Updated snippet data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet:write
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}/{encoded_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_snippet: {str(e)}"
            )

    async def delete_snippet(self, workspace: str, encoded_id: str) -> BitbucketResponse:
        """Deletes the specified snippet.

        Args:
            workspace: Workspace slug or UUID
            encoded_id: Snippet ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet:write
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}/{encoded_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_snippet: {str(e)}"
            )

    async def list_snippet_comments(self, workspace: str, encoded_id: str) -> BitbucketResponse:
        """Returns all comments on the specified snippet.

        Args:
            workspace: Workspace slug or UUID
            encoded_id: Snippet ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}/{encoded_id}/comments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_snippet_comments: {str(e)}"
            )

    async def create_snippet_comment(self, workspace: str, encoded_id: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new snippet comment.

        Args:
            workspace: Workspace slug or UUID
            encoded_id: Snippet ID
            body: Comment content

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}/{encoded_id}/comments"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_snippet_comment: {str(e)}"
            )

    async def get_snippet_file(self, workspace: str, encoded_id: str, node_id: str) -> BitbucketResponse:
        """Returns the raw contents of the specified snippet file.

        Args:
            workspace: Workspace slug or UUID
            encoded_id: Snippet ID
            node_id: Node ID (commit hash)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: snippet
        """
        try:
            url = f"{self.client.get_base_url()}/snippets/{workspace}/{encoded_id}/{node_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_snippet_file: {str(e)}"
            )

    async def list_deployments(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns a paginated list of deployments for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: deployment
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/deployments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_deployments: {str(e)}"
            )

    async def get_deployment(self, workspace: str, repo_slug: str, deployment_uuid: str) -> BitbucketResponse:
        """Returns the deployment with the specified UUID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            deployment_uuid: Deployment UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: deployment
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/deployments/{deployment_uuid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_deployment: {str(e)}"
            )

    async def list_deployment_environments(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns a paginated list of deployment environments.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/environments"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_deployment_environments: {str(e)}"
            )

    async def create_deployment_environment(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates a new deployment environment.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            body: Environment data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/environments"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_deployment_environment: {str(e)}"
            )

    async def get_deployment_environment(self, workspace: str, repo_slug: str, environment_uuid: str) -> BitbucketResponse:
        """Returns the deployment environment with the specified UUID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            environment_uuid: Environment UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_deployment_environment: {str(e)}"
            )

    async def delete_deployment_environment(self, workspace: str, repo_slug: str, environment_uuid: str) -> BitbucketResponse:
        """Deletes the deployment environment with the specified UUID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            environment_uuid: Environment UUID with curly braces

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_deployment_environment: {str(e)}"
            )

    async def update_deployment_environment(self, workspace: str, repo_slug: str, environment_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the deployment environment with the specified UUID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            environment_uuid: Environment UUID with curly braces
            body: Updated environment data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}/changes"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_deployment_environment: {str(e)}"
            )

    async def list_downloads(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns a paginated list of downloads for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/downloads"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_downloads: {str(e)}"
            )

    async def upload_download(self, workspace: str, repo_slug: str, files: Dict[str, Any]) -> BitbucketResponse:
        """Uploads a download artifact.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            files: File data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/downloads"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={},
                query_params={},
                body=files
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to upload_download: {str(e)}"
            )

    async def get_download(self, workspace: str, repo_slug: str, filename: str) -> BitbucketResponse:
        """Returns the download with the specified filename.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            filename: Download filename

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/downloads/{filename}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_download: {str(e)}"
            )

    async def delete_download(self, workspace: str, repo_slug: str, filename: str) -> BitbucketResponse:
        """Deletes the download with the specified filename.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            filename: Download filename

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/downloads/{filename}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_download: {str(e)}"
            )

    async def list_repository_user_permissions(self, workspace: str, repo_slug: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns a paginated list of all explicit user permissions for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/permissions-config/users"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_repository_user_permissions: {str(e)}"
            )

    async def update_repository_user_permission(self, workspace: str, repo_slug: str, selected_user_id: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the explicit user permission for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            selected_user_id: User UUID
            body: Permission data (permission level)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_repository_user_permission: {str(e)}"
            )

    async def delete_repository_user_permission(self, workspace: str, repo_slug: str, selected_user_id: str) -> BitbucketResponse:
        """Deletes the explicit user permission for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            selected_user_id: User UUID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_repository_user_permission: {str(e)}"
            )

    async def list_repository_group_permissions(self, workspace: str, repo_slug: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """Returns a paginated list of all explicit group permissions for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            q: BBQL query for filtering
            sort: Field to sort by

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            params = {}
            if q is not None:
                params["q"] = q
            if sort is not None:
                params["sort"] = sort

            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/permissions-config/groups"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params=params,
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_repository_group_permissions: {str(e)}"
            )

    async def update_repository_group_permission(self, workspace: str, repo_slug: str, group_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the explicit group permission for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            group_slug: Group slug
            body: Permission data (permission level)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_repository_group_permission: {str(e)}"
            )

    async def delete_repository_group_permission(self, workspace: str, repo_slug: str, group_slug: str) -> BitbucketResponse:
        """Deletes the explicit group permission for the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            group_slug: Group slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_repository_group_permission: {str(e)}"
            )

    async def list_user_ssh_keys(self) -> BitbucketResponse:
        """Returns a paginated list of the user's SSH public keys.

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account
        """
        try:
            url = f"{self.client.get_base_url()}/user/ssh-keys"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_user_ssh_keys: {str(e)}"
            )

    async def create_user_ssh_key(self, body: Dict[str, Any]) -> BitbucketResponse:
        """Adds a new SSH public key to the account.

        Args:
            body: SSH key data (key, label)

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account:write
        """
        try:
            url = f"{self.client.get_base_url()}/user/ssh-keys"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_user_ssh_key: {str(e)}"
            )

    async def get_user_ssh_key(self, key_id: str) -> BitbucketResponse:
        """Returns the SSH key with the specified key ID.

        Args:
            key_id: SSH key ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account
        """
        try:
            url = f"{self.client.get_base_url()}/user/ssh-keys/{key_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_user_ssh_key: {str(e)}"
            )

    async def update_user_ssh_key(self, key_id: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Updates the specified SSH key.

        Args:
            key_id: SSH key ID
            body: Updated SSH key data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account:write
        """
        try:
            url = f"{self.client.get_base_url()}/user/ssh-keys/{key_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to update_user_ssh_key: {str(e)}"
            )

    async def delete_user_ssh_key(self, key_id: str) -> BitbucketResponse:
        """Deletes the specified SSH key.

        Args:
            key_id: SSH key ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: account:write
        """
        try:
            url = f"{self.client.get_base_url()}/user/ssh-keys/{key_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_user_ssh_key: {str(e)}"
            )

    async def list_commit_reports(self, workspace: str, repo_slug: str, commit: str) -> BitbucketResponse:
        """Returns a paginated list of reports for the commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/reports"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_commit_reports: {str(e)}"
            )

    async def get_commit_report(self, workspace: str, repo_slug: str, commit: str, report_id: str) -> BitbucketResponse:
        """Returns the report with the specified ID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            report_id: Report ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_commit_report: {str(e)}"
            )

    async def create_or_update_commit_report(self, workspace: str, repo_slug: str, commit: str, report_id: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Creates or updates a report for the commit.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            report_id: Report ID
            body: Report data

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_or_update_commit_report: {str(e)}"
            )

    async def delete_commit_report(self, workspace: str, repo_slug: str, commit: str, report_id: str) -> BitbucketResponse:
        """Deletes the report with the specified ID.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            report_id: Report ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to delete_commit_report: {str(e)}"
            )

    async def list_commit_report_annotations(self, workspace: str, repo_slug: str, commit: str, report_id: str) -> BitbucketResponse:
        """Returns a paginated list of annotations for the report.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            report_id: Report ID

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}/annotations"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_commit_report_annotations: {str(e)}"
            )

    async def create_commit_report_annotations(self, workspace: str, repo_slug: str, commit: str, report_id: str, body: List[Dict[str, Any]]) -> BitbucketResponse:
        """Creates annotations for the report.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            commit: Commit hash
            report_id: Report ID
            body: List of annotations

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:write
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{report_id}/annotations"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                query_params={},
                body=body
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to create_commit_report_annotations: {str(e)}"
            )

    async def list_default_reviewers(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Returns the repository's default reviewers.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/default-reviewers"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to list_default_reviewers: {str(e)}"
            )

    async def get_default_reviewer(self, workspace: str, repo_slug: str, target_username: str) -> BitbucketResponse:
        """Returns the specified default reviewer.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            target_username: Reviewer username

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to get_default_reviewer: {str(e)}"
            )

    async def add_default_reviewer(self, workspace: str, repo_slug: str, target_username: str) -> BitbucketResponse:
        """Adds a default reviewer to the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            target_username: Reviewer username

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to add_default_reviewer: {str(e)}"
            )

    async def remove_default_reviewer(self, workspace: str, repo_slug: str, target_username: str) -> BitbucketResponse:
        """Removes a default reviewer from the repository.

        Args:
            workspace: Workspace slug or UUID
            repo_slug: Repository slug
            target_username: Reviewer username

        Returns:
            BitbucketResponse: Response containing data or error information

        Required OAuth scopes: repository:admin
        """
        try:
            url = f"{self.client.get_base_url()}/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={},
                query_params={},
                body=None
            )

            response = await self.client.execute(request)

            if response.status >= 400:
                return BitbucketResponse(
                    success=False,
                    error=f"Request failed with status {response.status}",
                    message=response.text()
                )

            # Handle empty responses (e.g., 204 No Content)
            if response.status == 204 or not response.text():
                return BitbucketResponse(success=True, data={})

            data = response.json() if response.text() else {}
            return BitbucketResponse(success=True, data=data)

        except Exception as e:
            return BitbucketResponse(
                success=False,
                error=str(e),
                message=f"Failed to remove_default_reviewer: {str(e)}"
            )

