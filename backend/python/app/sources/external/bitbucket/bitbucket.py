# ruff: noqa
"""
Bitbucket API DataSource
Auto-generated from OpenAPI Specification.
"""

from typing import Any, Dict, List, Optional, Union
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.bitbucket.bitbucket import BitbucketClient
HTTP_ERROR_THRESHOLD = 400

class BitbucketResponse:
    """Standardized response wrapper."""
    def __init__(self, success: bool, data: object = None, error: str = None, message: str = None):
        self.success = success
        self.data = data
        self.error = error
        self.message = message

class BitbucketDataSource:
    """
    Bitbucket API Data Source.
    """
    def __init__(self, client: BitbucketClient):
        self.client = client.get_client()
        self.base_url = client.get_base_url()

    async def delete_addon(self) -> BitbucketResponse:
        """Delete an app
        Deletes the application for the user.

This endpoint is intended to be used by Bitbucket Connect apps
and only supports JWT authentication -- that is how Bitbucket
identifies the particular installati...

        Args:

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_addon(self) -> BitbucketResponse:
        """Update an installed app
        Updates the application installation for the user.

This endpoint is intended to be used by Bitbucket Connect apps
and only supports JWT authentication -- that is how Bitbucket
identifies the particul...

        Args:

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_addon_linkers(self) -> BitbucketResponse:
        """List linkers for an app
        Gets a list of all [linkers](/cloud/bitbucket/modules/linker/)
for the authenticated application.

        Args:

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_addon_linkers_linker_key(self, linker_key: str) -> BitbucketResponse:
        """Get a linker for an app
        Gets a [linker](/cloud/bitbucket/modules/linker/) specified by `linker_key`
for the authenticated application.

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_addon_linkers_linker_key_values(self, linker_key: str) -> BitbucketResponse:
        """Delete all linker values
        Delete all [linker](/cloud/bitbucket/modules/linker/) values for the
specified linker of the authenticated application.

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}/values"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_addon_linkers_linker_key_values(self, linker_key: str) -> BitbucketResponse:
        """List linker values for a linker
        Gets a list of all [linker](/cloud/bitbucket/modules/linker/) values for the
specified linker of the authenticated application.

A linker value lets applications supply values to modify its regular ex...

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}/values"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_addon_linkers_linker_key_values(self, linker_key: str) -> BitbucketResponse:
        """Create a linker value
        Creates a [linker](/cloud/bitbucket/modules/linker/) value for the specified
linker of authenticated application.

A linker value lets applications supply values to modify its regular expression.

The...

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}/values"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_addon_linkers_linker_key_values(self, linker_key: str) -> BitbucketResponse:
        """Update a linker value
        Bulk update [linker](/cloud/bitbucket/modules/linker/) values for the specified
linker of the authenticated application.

A linker value lets applications supply values to modify its regular expressio...

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}/values"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_addon_linkers_linker_key_values_value_id(self, linker_key: str, value_id: int) -> BitbucketResponse:
        """Delete a linker value
        Delete a single [linker](/cloud/bitbucket/modules/linker/) value
of the authenticated application.

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...
            value_id: The numeric ID of the linker value.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}/values/{value_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_addon_linkers_linker_key_values_value_id(self, linker_key: str, value_id: int) -> BitbucketResponse:
        """Get a linker value
        Get a single [linker](/cloud/bitbucket/modules/linker/) value
of the authenticated application.

        Args:
            linker_key: The unique key of a [linker module](/cloud/bitbucket/modules/linker/) as defined in an application d...
            value_id: The numeric ID of the linker value.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/addon/linkers/{linker_key}/values/{value_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_hook_events(self) -> BitbucketResponse:
        """Get a webhook resource
        Returns the webhook resource or subject types on which webhooks can
be registered.

Each resource/subject type contains an `events` link that returns the
paginated list of specific events each individ...

        Args:

        Returns:
            BitbucketResponse: API response
        """
        path = f"/hook_events"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_hook_events_subject_type(self, subject_type: str) -> BitbucketResponse:
        """List subscribable webhook types
        Returns a paginated list of all valid webhook events for the
specified entity.
**The team and user webhooks are deprecated, and you should use workspace instead.
For more information, see [the announc...

        Args:
            subject_type: A resource or subject type.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/hook_events/{subject_type}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories(self, after: Optional[str] = None, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List public repositories
        Returns a paginated list of all public repositories.

This endpoint also supports filtering and sorting of the results. See
[filtering and sorting](/cloud/bitbucket/rest/intro/#filtering) for more det...

        Args:
            after: Filter the results to include only repositories created on or after this [ISO-8601](https://en.wikip...
            role: Filters the result based on the authenticated user's role on each repository.  * **member**: returns...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories"
        params = {}
        if after is not None:
            params["after"] = after
        if role is not None:
            params["role"] = role
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace(self, workspace: str, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List repositories in a workspace
        Returns a paginated list of all repositories owned by the specified
workspace.

The result can be narrowed down based on the authenticated user's role.

E.g. with `?role=contributor`, only those repos...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            role: Filters the result based on the authenticated user's role on each repository.  * **member**: returns...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}"
        params = {}
        if role is not None:
            params["role"] = role
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug(self, repo_slug: str, workspace: str, redirect_to: Optional[str] = None) -> BitbucketResponse:
        """Delete a repository
        Deletes the repository. This is an irreversible operation.

This does not affect its forks.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            redirect_to: If a repository has been moved to a new location, use this parameter to show users a friendly messag...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}"
        params = {}
        if redirect_to is not None:
            params["redirect_to"] = redirect_to
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a repository
        Returns the object describing this repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a repository
        Creates a new repository.

Note: In order to set the project for the newly created repository,
pass in either the project key or the project UUID as part of the
request body as shown in the examples b...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a repository
        Since this endpoint can be used to both update and to create a
repository, the request body depends on the intent.

#### Creation

See the POST documentation for the repository endpoint for an example...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_branch_restrictions(self, repo_slug: str, workspace: str, kind: Optional[str] = None, pattern: Optional[str] = None) -> BitbucketResponse:
        """List branch restrictions
        Returns a paginated list of all branch restrictions on the
repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            kind: Branch restrictions of this type
            pattern: Branch restrictions applied to branches of this pattern

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branch-restrictions"
        params = {}
        if kind is not None:
            params["kind"] = kind
        if pattern is not None:
            params["pattern"] = pattern
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_branch_restrictions(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a branch restriction rule
        Creates a new branch restriction rule for a repository.

`kind` describes what will be restricted. Allowed values include:
`push`, `force`, `delete`, `restrict_merges`, `require_tasks_to_be_completed`...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branch-restrictions"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_branch_restrictions_id(self, id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a branch restriction rule
        Deletes an existing branch restriction rule.

        Args:
            id: The restriction rule's id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_branch_restrictions_id(self, id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a branch restriction rule
        Returns a specific branch restriction rule.

        Args:
            id: The restriction rule's id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_branch_restrictions_id(self, id: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a branch restriction rule
        Updates an existing branch restriction rule.

Fields not present in the request body are ignored.

See [`POST`](/cloud/bitbucket/rest/api-group-branch-restrictions/#api-repositories-workspace-repo-slu...

        Args:
            id: The restriction rule's id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branch-restrictions/{id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_branching_model(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get the branching model for a repository
        Return the branching model as applied to the repository. This view is
read-only. The branching model settings can be changed using the
[settings](#api-repositories-workspace-repo-slug-branching-model-...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branching-model"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_branching_model_settings(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get the branching model config for a repository
        Return the branching model configuration for a repository. The returned
object:

1. Always has a `development` property for the development branch.
2. Always a `production` property for the production...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branching-model/settings"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_branching_model_settings(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Update the branching model config for a repository
        Update the branching model configuration for a repository.

The `development` branch can be configured to a specific branch or to
track the main branch. When set to a specific branch it must
currently...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/branching-model/settings"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commit_commit(self, commit: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a commit
        Returns the specified commit.

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_commit_commit_approve(self, commit: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Unapprove a commit
        Redact the authenticated user's approval of the specified commit.

This operation is only available to users that have explicit access to
the repository. In contrast, just the fact that a repository i...

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/approve"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_commit_commit_approve(self, commit: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Approve a commit
        Approve the specified commit as the authenticated user.

This operation is only available to users that have explicit access to
the repository. In contrast, just the fact that a repository is
publicly...

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/approve"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commit_commit_comments(self, commit: str, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List a commit's comments
        Returns the commit's comments.

This includes both global and inline comments.

The default sorting is oldest to newest and can be overridden with
the `sort` query parameter.

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/comments"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_commit_commit_comments(self, commit: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create comment for a commit
        Creates new comment on the specified commit.

To post a reply to an existing comment, include the `parent.id` field:

```
$ curl https://api.bitbucket.org/2.0/repositories/atlassian/prlinks/commit/db9...

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/comments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_commit_commit_comments_comment_id(self, comment_id: int, commit: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a commit comment
        Deletes the specified commit comment.

Note that deleting comments that have visible replies that point to
them will not really delete the resource. This is to retain the integrity
of the original com...

        Args:
            comment_id: The id of the comment.
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commit_commit_comments_comment_id(self, comment_id: int, commit: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a commit comment
        Returns the specified commit comment.

        Args:
            comment_id: The id of the comment.
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_commit_commit_comments_comment_id(self, comment_id: int, commit: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a commit comment
        Used to update the contents of a comment. Only the content of the comment can be updated.

```
$ curl https://api.bitbucket.org/2.0/repositories/atlassian/prlinks/commit/7f71b5/comments/5728901 \
  -X...

        Args:
            comment_id: The id of the comment.
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_commit_hosted_property_value(self, workspace: str, repo_slug: str, commit: str, app_key: str, property_name: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a commit application property
        Update an [application property](/cloud/bitbucket/application-properties/) value stored against a commit.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            commit: The commit.
            app_key: The key of the Connect app.
            property_name: The name of the property.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_commit_hosted_property_value(self, workspace: str, repo_slug: str, commit: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Delete a commit application property
        Delete an [application property](/cloud/bitbucket/application-properties/) value stored against a commit.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            commit: The commit.
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_commit_hosted_property_value(self, workspace: str, repo_slug: str, commit: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Get a commit application property
        Retrieve an [application property](/cloud/bitbucket/application-properties/) value stored against a commit.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            commit: The commit.
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pullrequests_for_commit(self, workspace: str, repo_slug: str, commit: str, page: Optional[int] = None, pagelen: Optional[int] = None) -> BitbucketResponse:
        """List pull requests that contain a commit
        Returns a paginated list of all pull requests as part of which this commit was reviewed. Pull Request Commit Links app must be installed first before using this API; installation automatically occurs ...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces
            repo_slug: The repository; either the UUID in curly braces, or the slug
            commit: The SHA1 of the commit
            page: Which page to retrieve
            pagelen: How many pull requests to retrieve per page

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/pullrequests"
        params = {}
        if page is not None:
            params["page"] = page
        if pagelen is not None:
            params["pagelen"] = pagelen
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_reports_for_commit(self, workspace: str, repo_slug: str, commit: str) -> BitbucketResponse:
        """List reports
        Returns a paginated list of Reports linked to this commit.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit for which to retrieve reports.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_or_update_report(self, workspace: str, repo_slug: str, commit: str, reportId: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create or update a report
        Creates or updates a report for the specified commit.
To upload a report, make sure to generate an ID that is unique across all reports for that commit. If you want to use an existing id from your own...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit the report belongs to.
            reportId: Either the uuid or external-id of the report.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_report(self, workspace: str, repo_slug: str, commit: str, reportId: str) -> BitbucketResponse:
        """Get a report
        Returns a single Report matching the provided ID.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit the report belongs to.
            reportId: Either the uuid or external-id of the report.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_report(self, workspace: str, repo_slug: str, commit: str, reportId: str) -> BitbucketResponse:
        """Delete a report
        Deletes a single Report matching the provided ID.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit the report belongs to.
            reportId: Either the uuid or external-id of the report.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_annotations_for_report(self, workspace: str, repo_slug: str, commit: str, reportId: str) -> BitbucketResponse:
        """List annotations
        Returns a paginated list of Annotations for a specified report.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit for which to retrieve reports.
            reportId: Uuid or external-if of the report for which to get annotations for.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}/annotations"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def bulk_create_or_update_annotations(self, workspace: str, repo_slug: str, commit: str, reportId: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Bulk create or update annotations
        Bulk upload of annotations.
Annotations are individual findings that have been identified as part of a report, for example, a line of code that represents a vulnerability. These annotations can be att...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit for which to retrieve reports.
            reportId: Uuid or external-if of the report for which to get annotations for.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}/annotations"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_annotation(self, workspace: str, repo_slug: str, commit: str, reportId: str, annotationId: str) -> BitbucketResponse:
        """Get an annotation
        Returns a single Annotation matching the provided ID.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit the report belongs to.
            reportId: Either the uuid or external-id of the report.
            annotationId: Either the uuid or external-id of the annotation.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}/annotations/{annotationId}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_or_update_annotation(self, workspace: str, repo_slug: str, commit: str, reportId: str, annotationId: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create or update an annotation
        Creates or updates an individual annotation for the specified report.
Annotations are individual findings that have been identified as part of a report, for example, a line of code that represents a v...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit the report belongs to.
            reportId: Either the uuid or external-id of the report.
            annotationId: Either the uuid or external-id of the annotation.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}/annotations/{annotationId}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_annotation(self, workspace: str, repo_slug: str, commit: str, reportId: str, annotationId: str) -> BitbucketResponse:
        """Delete an annotation
        Deletes a single Annotation matching the provided ID.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            commit: The commit the annotation belongs to.
            reportId: Either the uuid or external-id of the annotation.
            annotationId: Either the uuid or external-id of the annotation.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/reports/{reportId}/annotations/{annotationId}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commit_commit_statuses(self, commit: str, repo_slug: str, workspace: str, refname: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List commit statuses for a commit
        Returns all statuses (e.g. build results) for a specific commit.

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            refname: If specified, only return commit status objects that were either created without a refname, or were ...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses"
        params = {}
        if refname is not None:
            params["refname"] = refname
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_commit_commit_statuses_build(self, commit: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a build status for a commit
        Creates a new build status against the specified commit.

If the specified key already exists, the existing status object will
be overwritten.

Example:

```
curl https://api.bitbucket.org/2.0/reposit...

        Args:
            commit: The commit's SHA1.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commit_commit_statuses_build_key(self, commit: str, key: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a build status for a commit
        Returns the specified build status for a commit.

        Args:
            commit: The commit's SHA1.
            key: The build status' unique key
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build/{key}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_commit_commit_statuses_build_key(self, commit: str, key: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a build status for a commit
        Used to update the current status of a build status object on the
specific commit.

This operation can also be used to change other properties of the
build status:

* `state`
* `name`
* `description`
...

        Args:
            commit: The commit's SHA1.
            key: The build status' unique key
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commit/{commit}/statuses/build/{key}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commits(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List commits
        These are the repository's commits. They are paginated and returned
in reverse chronological order, similar to the output of `git log`.
Like these tools, the DAG can be filtered.

#### GET /repositori...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commits"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_commits(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List commits with include/exclude
        Identical to `GET /repositories/{workspace}/{repo_slug}/commits`,
except that POST allows clients to place the include and exclude
parameters in the request body to avoid URL length issues.

**Note th...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commits"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_commits_revision(self, repo_slug: str, revision: str, workspace: str) -> BitbucketResponse:
        """List commits for revision
        These are the repository's commits. They are paginated and returned
in reverse chronological order, similar to the output of `git log`.
Like these tools, the DAG can be filtered.

#### GET /repositori...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            revision: A commit SHA1 or ref name.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commits/{revision}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_commits_revision(self, repo_slug: str, revision: str, workspace: str) -> BitbucketResponse:
        """List commits for revision using include/exclude
        Identical to `GET /repositories/{workspace}/{repo_slug}/commits/{revision}`,
except that POST allows clients to place the include and exclude
parameters in the request body to avoid URL length issues....

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            revision: A commit SHA1 or ref name.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/commits/{revision}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_components(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List components
        Returns the components that have been defined in the issue tracker.

This resource is only available on repositories that have the issue
tracker enabled.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/components"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_components_component_id(self, component_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a component for issues
        Returns the specified issue tracker component object.

        Args:
            component_id: The component's id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/components/{component_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_default_reviewers(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List default reviewers
        Returns the repository's default reviewers.

These are the users that are automatically added as reviewers on every
new pull request that is created. To obtain the repository's default reviewers
as we...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/default-reviewers"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_default_reviewers_target_username(self, repo_slug: str, target_username: str, workspace: str) -> BitbucketResponse:
        """Remove a user from the default reviewers
        Removes a default reviewer from the repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            target_username: This can either be the username or the UUID of the default reviewer, surrounded by curly-braces, for...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_default_reviewers_target_username(self, repo_slug: str, target_username: str, workspace: str) -> BitbucketResponse:
        """Get a default reviewer
        Returns the specified reviewer.

This can be used to test whether a user is among the repository's
default reviewers list. A 404 indicates that that specified user is not
a default reviewer.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            target_username: This can either be the username or the UUID of the default reviewer, surrounded by curly-braces, for...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_default_reviewers_target_username(self, repo_slug: str, target_username: str, workspace: str) -> BitbucketResponse:
        """Add a user to the default reviewers
        Adds the specified user to the repository's list of default
reviewers.

This method is idempotent. Adding a user a second time has no effect.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            target_username: This can either be the username or the UUID of the default reviewer, surrounded by curly-braces, for...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/default-reviewers/{target_username}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_deploy_keys(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List repository deploy keys
        Returns all deploy-keys belonging to a repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deploy-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_deploy_keys(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Add a repository deploy key
        Create a new deploy key in a repository. Note: If authenticating a deploy key
with an OAuth consumer, any changes to the OAuth consumer will subsequently
invalidate the deploy key.


Example:
```
$ cu...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deploy-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_deploy_keys_key_id(self, key_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a repository deploy key
        This deletes a deploy key from a repository.

        Args:
            key_id: The key ID matching the deploy key.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deploy-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_deploy_keys_key_id(self, key_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a repository deploy key
        Returns the deploy key belonging to a specific key.

        Args:
            key_id: The key ID matching the deploy key.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deploy-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_deploy_keys_key_id(self, key_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Update a repository deploy key
        Create a new deploy key in a repository.

The same key needs to be passed in but the comment and label can change.

Example:
```
$ curl -X PUT \
-H 'Authorization <auth header>' \
-H 'Content-type: ap...

        Args:
            key_id: The key ID matching the deploy key.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deploy-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_deployments_for_repository(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """List deployments
        Find deployments

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deployments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_deployment_for_repository(self, workspace: str, repo_slug: str, deployment_uuid: str) -> BitbucketResponse:
        """Get a deployment
        Retrieve a deployment

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            deployment_uuid: The deployment UUID.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deployments/{deployment_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_deployment_variables(self, workspace: str, repo_slug: str, environment_uuid: str) -> BitbucketResponse:
        """List variables for an environment
        Find deployment environment level variables.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deployments_config/environments/{environment_uuid}/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_deployment_variable(self, workspace: str, repo_slug: str, environment_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a variable for an environment
        Create a deployment environment level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deployments_config/environments/{environment_uuid}/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_deployment_variable(self, workspace: str, repo_slug: str, environment_uuid: str, variable_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a variable for an environment
        Update a deployment environment level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment.
            variable_uuid: The UUID of the variable to update.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deployments_config/environments/{environment_uuid}/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_deployment_variable(self, workspace: str, repo_slug: str, environment_uuid: str, variable_uuid: str) -> BitbucketResponse:
        """Delete a variable for an environment
        Delete a deployment environment level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment.
            variable_uuid: The UUID of the variable to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/deployments_config/environments/{environment_uuid}/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_diff_spec(self, repo_slug: str, spec: str, workspace: str, context: Optional[int] = None, path: Optional[str] = None, ignore_whitespace: Optional[bool] = None, binary: Optional[bool] = None, renames: Optional[bool] = None, merge: Optional[bool] = None, topic: Optional[bool] = None) -> BitbucketResponse:
        """Compare two commits
        Produces a raw git-style diff.

#### Single commit spec

If the `spec` argument to this API is a single commit, the diff is
produced against the first parent of the specified commit.

#### Two commit ...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            spec: A commit SHA (e.g. `3a8b42`) or a commit range using double dot notation (e.g. `3a8b42..9ff173`).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            context: Generate diffs with <n> lines of context instead of the usual three.
            path: Limit the diff to a particular file (this parameter can be repeated for multiple paths).
            ignore_whitespace: Generate diffs that ignore whitespace.
            binary: Generate diffs that include binary files, true if omitted.
            renames: Whether to perform rename detection, true if omitted.
            merge: This parameter is deprecated. The 'topic' parameter should be used instead. The 'merge' and 'topic' ...
            topic: If true, returns 2-way 'three-dot' diff. This is a diff between the source commit and the merge base...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/diff/{spec}"
        params = {}
        if context is not None:
            params["context"] = context
        if path is not None:
            params["path"] = path
        if ignore_whitespace is not None:
            params["ignore_whitespace"] = ignore_whitespace
        if binary is not None:
            params["binary"] = binary
        if renames is not None:
            params["renames"] = renames
        if merge is not None:
            params["merge"] = merge
        if topic is not None:
            params["topic"] = topic
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_diffstat_spec(self, repo_slug: str, spec: str, workspace: str, ignore_whitespace: Optional[bool] = None, merge: Optional[bool] = None, path: Optional[str] = None, renames: Optional[bool] = None, topic: Optional[bool] = None) -> BitbucketResponse:
        """Compare two commit diff stats
        Produces a response in JSON format with a record for every path
modified, including information on the type of the change and the
number of lines added and removed.

#### Single commit spec

If the `s...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            spec: A commit SHA (e.g. `3a8b42`) or a commit range using double dot notation (e.g. `3a8b42..9ff173`).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            ignore_whitespace: Generate diffs that ignore whitespace
            merge: This parameter is deprecated. The 'topic' parameter should be used instead. The 'merge' and 'topic' ...
            path: Limit the diffstat to a particular file (this parameter can be repeated for multiple paths).
            renames: Whether to perform rename detection, true if omitted.
            topic: If true, returns 2-way 'three-dot' diff. This is a diff between the source commit and the merge base...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/diffstat/{spec}"
        params = {}
        if ignore_whitespace is not None:
            params["ignore_whitespace"] = ignore_whitespace
        if merge is not None:
            params["merge"] = merge
        if path is not None:
            params["path"] = path
        if renames is not None:
            params["renames"] = renames
        if topic is not None:
            params["topic"] = topic
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_downloads(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List download artifacts
        Returns a list of download links associated with the repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/downloads"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_downloads(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Upload a download artifact
        Upload new download artifacts.

To upload files, perform a `multipart/form-data` POST containing one
or more `files` fields:

    $ echo Hello World > hello.txt
    $ curl -s -u evzijst -X POST https:...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/downloads"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_downloads_filename(self, filename: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a download artifact
        Deletes the specified download artifact from the repository.

        Args:
            filename: Name of the file.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/downloads/{filename}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_downloads_filename(self, filename: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a download artifact link
        Return a redirect to the contents of a download artifact.

This endpoint returns the actual file contents and not the artifact's
metadata.

    $ curl -s -L https://api.bitbucket.org/2.0/repositories/...

        Args:
            filename: Name of the file.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/downloads/{filename}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_effective_branching_model(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get the effective, or currently applied, branching model for a repository

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/effective-branching-model"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_effective_default_reviewers(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List effective default reviewers
        Returns the repository's effective default reviewers. This includes both default
reviewers defined at the repository level as well as those inherited from its project.

These are the users that are au...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/effective-default-reviewers"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_environments_for_repository(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """List environments
        Find environments

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/environments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_environment(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create an environment
        Create an environment.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/environments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_environment_for_repository(self, workspace: str, repo_slug: str, environment_uuid: str) -> BitbucketResponse:
        """Get an environment
        Retrieve an environment

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment UUID.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_environment_for_repository(self, workspace: str, repo_slug: str, environment_uuid: str) -> BitbucketResponse:
        """Delete an environment

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment UUID.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_environment_for_repository(self, workspace: str, repo_slug: str, environment_uuid: str) -> BitbucketResponse:
        """Update an environment

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            environment_uuid: The environment UUID.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/environments/{environment_uuid}/changes"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_filehistory_commit_path(self, commit: str, path: str, repo_slug: str, workspace: str, renames: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List commits that modified a file
        Returns a paginated list of commits that modified the specified file.

Commits are returned in reverse chronological order. This is roughly
equivalent to the following commands:

    $ git log --follo...

        Args:
            commit: The commit's SHA1.
            path: Path to the file.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            renames: When `true`, Bitbucket will follow the history of the file across renames (this is the default behav...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Name of a response property sort the result by as per [filtering and sorting](/cloud/bitbucket/rest/...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/filehistory/{commit}/{path}"
        params = {}
        if renames is not None:
            params["renames"] = renames
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_forks(self, repo_slug: str, workspace: str, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List repository forks
        Returns a paginated list of all the forks of the specified
repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            role: Filters the result based on the authenticated user's role on each repository.  * **member**: returns...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/forks"
        params = {}
        if role is not None:
            params["role"] = role
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_forks(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Fork a repository
        Creates a new fork of the specified repository.

#### Forking a repository

To create a fork, specify the workspace explicitly as part of the
request body:

```
$ curl -X POST -u jdoe https://api.bitb...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/forks"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_hooks(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List webhooks for a repository
        Returns a paginated list of webhooks installed on this repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/hooks"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_hooks(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Create a webhook for a repository
        Creates a new webhook on the specified repository.

Example:

```
$ curl -X POST -u credentials -H 'Content-Type: application/json'
  https://api.bitbucket.org/2.0/repositories/my-workspace/my-repo-sl...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/hooks"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_hooks_uid(self, repo_slug: str, uid: str, workspace: str) -> BitbucketResponse:
        """Delete a webhook for a repository
        Deletes the specified webhook subscription from the given
repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            uid: Installed webhook's ID
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/hooks/{uid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_hooks_uid(self, repo_slug: str, uid: str, workspace: str) -> BitbucketResponse:
        """Get a webhook for a repository
        Returns the webhook with the specified id installed on the specified
repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            uid: Installed webhook's ID
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/hooks/{uid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_hooks_uid(self, repo_slug: str, uid: str, workspace: str) -> BitbucketResponse:
        """Update a webhook for a repository
        Updates the specified webhook subscription.

The following properties can be mutated:

* `description`
* `url`
* `secret`
* `active`
* `events`

The hook's secret is used as a key to generate the HMAC...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            uid: Installed webhook's ID
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/hooks/{uid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List issues
        Returns the issues in the issue tracker.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_issues(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create an issue
        Creates a new issue.

This call requires authentication. Private repositories or private
issue trackers require the caller to authenticate with an account that
has appropriate authorization.

The auth...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_issues_export(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Export issues
        A POST request to this endpoint initiates a new background celery task that archives the repo's issues.

When the job has been accepted, it will return a 202 (Accepted) along with a unique url to this...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/export"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_export_repo_name_issues_task_id_zip(self, repo_name: str, repo_slug: str, task_id: str, workspace: str) -> BitbucketResponse:
        """Check issue export status
        This endpoint is used to poll for the progress of an issue export
job and return the zip file after the job is complete.
As long as the job is running, this will return a 202 response
with in the resp...

        Args:
            repo_name: The name of the repo
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            task_id: The ID of the export task
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/export/{repo_name}-issues-{task_id}.zip"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_import(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Check issue import status
        When using GET, this endpoint reports the status of the current import task.

After the job has been scheduled, but before it starts executing, the endpoint
returns a 202 response with status `ACCEPTE...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/import"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_issues_import(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Import issues
        A POST request to this endpoint will import the zip file given by the archive parameter into the repository. All
existing issues will be deleted and replaced by the contents of the imported zip file.
...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/import"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_issues_issue_id(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete an issue
        Deletes the specified issue. This requires write access to the
repository.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get an issue
        Returns the specified issue.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_issues_issue_id(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Update an issue
        Modifies the issue.

```
$ curl https://api.bitbucket.org/2.0/repostories/evzijst/dogslow/issues/123 \
  -u evzijst -s -X PUT -H 'Content-Type: application/json' \
  -d '{
  'title': 'Updated title',
...

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_attachments(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List attachments for an issue
        Returns all attachments for this issue.

This returns the files' meta data. This does not return the files'
actual contents.

The files are always ordered by their upload date.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_issues_issue_id_attachments(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Upload an attachment to an issue
        Upload new issue attachments.

To upload files, perform a `multipart/form-data` POST containing one
or more file fields.

When a file is uploaded with the same name as an existing attachment,
then the...

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_issues_issue_id_attachments_path(self, issue_id: str, path: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete an attachment for an issue
        Deletes an attachment.

        Args:
            issue_id: The issue id
            path: Path to the file.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments/{path}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_attachments_path(self, issue_id: str, path: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get attachment for an issue
        Returns the contents of the specified file attachment.

Note that this endpoint does not return a JSON response, but instead
returns a redirect pointing to the actual file that in turn will return
the...

        Args:
            issue_id: The issue id
            path: Path to the file.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/attachments/{path}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_changes(self, issue_id: str, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List changes on an issue
        Returns the list of all changes that have been made to the specified
issue. Changes are returned in chronological order with the oldest
change first.

Each time an issue is edited in the UI or through...

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response. See [filtering and sorting](/cloud/bitbucket/rest/intro/#f...
            sort: Name of a response property to sort results. See [filtering and sorting](/cloud/bitbucket/rest/intro...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/changes"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_issues_issue_id_changes(self, issue_id: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Modify the state of an issue
        Makes a change to the specified issue.

For example, to change an issue's state and assignee, create a new
change object that modifies these fields:

```
curl https://api.bitbucket.org/2.0/site/master...

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/changes"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_changes_change_id(self, change_id: str, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get issue change object
        Returns the specified issue change object.

This resource is only available on repositories that have the issue
tracker enabled.

        Args:
            change_id: The issue change id
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/changes/{change_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_comments(self, issue_id: str, repo_slug: str, workspace: str, q: Optional[str] = None) -> BitbucketResponse:
        """List comments on an issue
        Returns a paginated list of all comments that were made on the
specified issue.

The default sorting is oldest to newest and can be overridden with
the `sort` query parameter.

This endpoint also supp...

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments"
        params = {}
        if q is not None:
            params["q"] = q
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_issues_issue_id_comments(self, issue_id: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a comment on an issue
        Creates a new issue comment.

```
$ curl https://api.bitbucket.org/2.0/repositories/atlassian/prlinks/issues/42/comments/ \
  -X POST -u evzijst \
  -H 'Content-Type: application/json' \
  -d '{'conte...

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_issues_issue_id_comments_comment_id(self, comment_id: int, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a comment on an issue
        Deletes the specified comment.

        Args:
            comment_id: The id of the comment.
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_comments_comment_id(self, comment_id: int, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a comment on an issue
        Returns the specified issue comment object.

        Args:
            comment_id: The id of the comment.
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_issues_issue_id_comments_comment_id(self, comment_id: int, issue_id: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a comment on an issue
        Updates the content of the specified issue comment. Note that only
the `content.raw` field can be modified.

```
$ curl https://api.bitbucket.org/2.0/repositories/atlassian/prlinks/issues/42/comments/...

        Args:
            comment_id: The id of the comment.
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_issues_issue_id_vote(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Remove vote for an issue
        Retract your vote.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_vote(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Check if current user voted for an issue
        Check whether the authenticated user has voted for this issue.
A 204 status code indicates that the user has voted, while a 404
implies they haven't.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_issues_issue_id_vote(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Vote for an issue
        Vote for this issue.

To cast your vote, do an empty PUT. The 204 status code indicates that
the operation was successful.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/vote"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_issues_issue_id_watch(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Stop watching an issue
        Stop watching this issue.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_issues_issue_id_watch(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Check if current user is watching a issue
        Indicated whether or not the authenticated user is watching this
issue.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_issues_issue_id_watch(self, issue_id: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Watch an issue
        Start watching this issue.

To start watching this issue, do an empty PUT. The 204 status code
indicates that the operation was successful.

        Args:
            issue_id: The issue id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/issues/{issue_id}/watch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_merge_base_revspec(self, repo_slug: str, revspec: str, workspace: str) -> BitbucketResponse:
        """Get the common ancestor between two commits
        Returns the best common ancestor between two commits, specified in a revspec
of 2 commits (e.g. 3a8b42..9ff173).

If more than one best common ancestor exists, only one will be returned. It is
unspeci...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            revspec: A commit range using double dot notation (e.g. `3a8b42..9ff173`).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/merge-base/{revspec}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_milestones(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List milestones
        Returns the milestones that have been defined in the issue tracker.

This resource is only available on repositories that have the issue
tracker enabled.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/milestones"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_milestones_milestone_id(self, milestone_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a milestone
        Returns the specified issue tracker milestone object.

        Args:
            milestone_id: The milestone's id
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/milestones/{milestone_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_override_settings(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Retrieve the inheritance state for repository settings

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/override-settings"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_override_settings(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Set the inheritance state for repository settings                 

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/override-settings"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_patch_spec(self, repo_slug: str, spec: str, workspace: str) -> BitbucketResponse:
        """Get a patch for two commits
        Produces a raw patch for a single commit (diffed against its first
parent), or a patch-series for a revspec of 2 commits (e.g.
`3a8b42..9ff173` where the first commit represents the source and the
sec...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            spec: A commit SHA (e.g. `3a8b42`) or a commit range using double dot notation (e.g. `3a8b42..9ff173`).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/patch/{spec}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_permissions_config_groups(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List explicit group permissions for a repository
        Returns a paginated list of explicit group permissions for the given repository.
This endpoint does not support BBQL features.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/groups"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_permissions_config_groups_group_slug(self, group_slug: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete an explicit group permission for a repository
        Deletes the repository group permission between the requested repository and group, if one exists.

Only users with admin permission for the repository may access this resource.

The only authenticati...

        Args:
            group_slug: Slug of the requested group.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_permissions_config_groups_group_slug(self, group_slug: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get an explicit group permission for a repository
        Returns the group permission for a given group slug and repository

Only users with admin permission for the repository may access this resource.

Permissions can be:

* `admin`
* `write`
* `read`
* `...

        Args:
            group_slug: Slug of the requested group.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_permissions_config_groups_group_slug(self, group_slug: str, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update an explicit group permission for a repository
        Updates the group permission, or grants a new permission if one does not already exist.

Only users with admin permission for the repository may access this resource.

The only authentication method s...

        Args:
            group_slug: Slug of the requested group.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/groups/{group_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_permissions_config_users(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List explicit user permissions for a repository
        Returns a paginated list of explicit user permissions for the given repository.
This endpoint does not support BBQL features.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/users"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_permissions_config_users_selected_user_id(self, repo_slug: str, selected_user_id: str, workspace: str) -> BitbucketResponse:
        """Delete an explicit user permission for a repository
        Deletes the repository user permission between the requested repository and user, if one exists.

Only users with admin permission for the repository may access this resource.

The only authentication...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            selected_user_id: This can either be the UUID of the account, surrounded by curly-braces, for example: `{account UUID}...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_permissions_config_users_selected_user_id(self, repo_slug: str, selected_user_id: str, workspace: str) -> BitbucketResponse:
        """Get an explicit user permission for a repository
        Returns the explicit user permission for a given user and repository.

Only users with admin permission for the repository may access this resource.

Permissions can be:

* `admin`
* `write`
* `read`
...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            selected_user_id: This can either be the UUID of the account, surrounded by curly-braces, for example: `{account UUID}...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_permissions_config_users_selected_user_id(self, repo_slug: str, selected_user_id: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update an explicit user permission for a repository
        Updates the explicit user permission for a given user and repository. The selected user must be a member of
the workspace, and cannot be the workspace owner.
Only users with admin permission for the r...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            selected_user_id: This can either be the UUID of the account, surrounded by curly-braces, for example: `{account UUID}...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/permissions-config/users/{selected_user_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipelines_for_repository(self, workspace: str, repo_slug: str, creator_uuid: Optional[str] = None, target_ref_type: Optional[str] = None, target_ref_name: Optional[str] = None, target_branch: Optional[str] = None, target_commit_hash: Optional[str] = None, target_selector_pattern: Optional[str] = None, target_selector_type: Optional[str] = None, created_on: Optional[str] = None, trigger_type: Optional[str] = None, status: Optional[str] = None, sort: Optional[str] = None, page: Optional[int] = None, pagelen: Optional[int] = None) -> BitbucketResponse:
        """List pipelines
        Find pipelines in a repository.

Note that unlike other endpoints in the Bitbucket API, this endpoint utilizes query parameters to allow filtering
and sorting of returned results. See [query parameter...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            creator_uuid: The UUID of the creator of the pipeline to filter by.
            target_ref_type: The type of the reference to filter by.
            target_ref_name: The reference name to filter by.
            target_branch: The name of the branch to filter by.
            target_commit_hash: The revision to filter by.
            target_selector_pattern: The pipeline pattern to filter by.
            target_selector_type: The type of pipeline to filter by.
            created_on: The creation date to filter by.
            trigger_type: The trigger type to filter by.
            status: The pipeline status to filter by.
            sort: The attribute name to sort on.
            page: The page number of elements to retrieve.
            pagelen: The maximum number of results to return.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines"
        params = {}
        if creator_uuid is not None:
            params["creator.uuid"] = creator_uuid
        if target_ref_type is not None:
            params["target.ref_type"] = target_ref_type
        if target_ref_name is not None:
            params["target.ref_name"] = target_ref_name
        if target_branch is not None:
            params["target.branch"] = target_branch
        if target_commit_hash is not None:
            params["target.commit.hash"] = target_commit_hash
        if target_selector_pattern is not None:
            params["target.selector.pattern"] = target_selector_pattern
        if target_selector_type is not None:
            params["target.selector.type"] = target_selector_type
        if created_on is not None:
            params["created_on"] = created_on
        if trigger_type is not None:
            params["trigger_type"] = trigger_type
        if status is not None:
            params["status"] = status
        if sort is not None:
            params["sort"] = sort
        if page is not None:
            params["page"] = page
        if pagelen is not None:
            params["pagelen"] = pagelen
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_pipeline_for_repository(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Run a pipeline
        Endpoint to create and initiate a pipeline.
There are a couple of different options to initiate a pipeline, where the payload of the request will determine which type of pipeline will be instantiated....

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_caches(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """List caches
        Retrieve the repository pipelines caches.

        Args:
            workspace: The account.
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines-config/caches"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_pipeline_caches(self, workspace: str, repo_slug: str, name: str) -> BitbucketResponse:
        """Delete caches
        Delete repository cache versions by name.

        Args:
            workspace: The account.
            repo_slug: The repository.
            name: The cache name.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines-config/caches"
        params = {}
        if name is not None:
            params["name"] = name
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_pipeline_cache(self, workspace: str, repo_slug: str, cache_uuid: str) -> BitbucketResponse:
        """Delete a cache
        Delete a repository cache.

        Args:
            workspace: The account.
            repo_slug: The repository.
            cache_uuid: The UUID of the cache to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines-config/caches/{cache_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_cache_content_uri(self, workspace: str, repo_slug: str, cache_uuid: str) -> BitbucketResponse:
        """Get cache content URI
        Retrieve the URI of the content of the specified cache.

        Args:
            workspace: The account.
            repo_slug: The repository.
            cache_uuid: The UUID of the cache.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines-config/caches/{cache_uuid}/content-uri"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_for_repository(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> BitbucketResponse:
        """Get a pipeline
        Retrieve a specified pipeline

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The pipeline UUID.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_steps_for_repository(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> BitbucketResponse:
        """List steps for a pipeline
        Find steps for the given pipeline.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_step_for_repository(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str) -> BitbucketResponse:
        """Get a step of a pipeline
        Retrieve a given step of a pipeline.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.
            step_uuid: The UUID of the step.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_step_log_for_repository(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str) -> BitbucketResponse:
        """Get log file for a step
        Retrieve the log file for a given step of a pipeline.

This endpoint supports (and encourages!) the use of [HTTP Range requests](https://tools.ietf.org/html/rfc7233) to deal with potentially very larg...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.
            step_uuid: The UUID of the step.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/log"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_container_log(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str, log_uuid: str) -> BitbucketResponse:
        """Get the logs for the build container or a service container for a given step of a pipeline.
        Retrieve the log file for a build container or service container.

This endpoint supports (and encourages!) the use of [HTTP Range requests](https://tools.ietf.org/html/rfc7233) to deal with potential...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.
            step_uuid: The UUID of the step.
            log_uuid: For the main build container specify the step UUID; for a service container specify the service cont...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/logs/{log_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_test_reports(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str) -> BitbucketResponse:
        """Get a summary of test reports for a given step of a pipeline.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.
            step_uuid: The UUID of the step.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/test_reports"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_test_report_test_cases(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str) -> BitbucketResponse:
        """Get test cases for a given step of a pipeline.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.
            step_uuid: The UUID of the step.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/test_reports/test_cases"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_test_report_test_case_reasons(self, workspace: str, repo_slug: str, pipeline_uuid: str, step_uuid: str, test_case_uuid: str) -> BitbucketResponse:
        """Get test case reasons (output) for a given test case in a step of a pipeline.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.
            step_uuid: The UUID of the step.
            test_case_uuid: The UUID of the test case.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/steps/{step_uuid}/test_reports/test_cases/{test_case_uuid}/test_case_reasons"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def stop_pipeline(self, workspace: str, repo_slug: str, pipeline_uuid: str) -> BitbucketResponse:
        """Stop a pipeline
        Signal the stop of a pipeline and all of its steps that not have completed yet.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            pipeline_uuid: The UUID of the pipeline.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines/{pipeline_uuid}/stopPipeline"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_config(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Get configuration
        Retrieve the repository pipelines configuration.

        Args:
            workspace: The account.
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_pipeline_config(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update configuration
        Update the pipelines configuration for a repository.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_build_number(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update the next build number
        Update the next build number that should be assigned to a pipeline. The next build number that will be configured has to be strictly higher than the current latest build number for this repository.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/build_number"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_repository_pipeline_schedule(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a schedule
        Create a schedule for the given repository.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/schedules"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_schedules(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """List schedules
        Retrieve the configured schedules for the given repository.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/schedules"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_schedule(self, workspace: str, repo_slug: str, schedule_uuid: str) -> BitbucketResponse:
        """Get a schedule
        Retrieve a schedule by its UUID.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            schedule_uuid: The uuid of the schedule.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/schedules/{schedule_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_pipeline_schedule(self, workspace: str, repo_slug: str, schedule_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a schedule
        Update a schedule.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            schedule_uuid: The uuid of the schedule.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/schedules/{schedule_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_pipeline_schedule(self, workspace: str, repo_slug: str, schedule_uuid: str) -> BitbucketResponse:
        """Delete a schedule
        Delete a schedule.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            schedule_uuid: The uuid of the schedule.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/schedules/{schedule_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_schedule_executions(self, workspace: str, repo_slug: str, schedule_uuid: str) -> BitbucketResponse:
        """List executions of a schedule
        Retrieve the executions of a given schedule.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            schedule_uuid: The uuid of the schedule.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/schedules/{schedule_uuid}/executions"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_ssh_key_pair(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Get SSH key pair
        Retrieve the repository SSH key pair excluding the SSH private key. The private key is a write only field and will never be exposed in the logs or the REST API.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/key_pair"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_pipeline_key_pair(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update SSH key pair
        Create or update the repository SSH key pair. The private key will be set as a default SSH identity in your build container.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/key_pair"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_pipeline_key_pair(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """Delete SSH key pair
        Delete the repository SSH key pair.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/key_pair"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_known_hosts(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """List known hosts
        Find repository level known hosts.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/known_hosts"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_repository_pipeline_known_host(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a known host
        Create a repository level known host.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/known_hosts"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_known_host(self, workspace: str, repo_slug: str, known_host_uuid: str) -> BitbucketResponse:
        """Get a known host
        Retrieve a repository level known host.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            known_host_uuid: The UUID of the known host to retrieve.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/known_hosts/{known_host_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_pipeline_known_host(self, workspace: str, repo_slug: str, known_host_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a known host
        Update a repository level known host.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            known_host_uuid: The UUID of the known host to update.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/known_hosts/{known_host_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_pipeline_known_host(self, workspace: str, repo_slug: str, known_host_uuid: str) -> BitbucketResponse:
        """Delete a known host
        Delete a repository level known host.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            known_host_uuid: The UUID of the known host to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/ssh/known_hosts/{known_host_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_variables(self, workspace: str, repo_slug: str) -> BitbucketResponse:
        """List variables for a repository
        Find repository level variables.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_repository_pipeline_variable(self, workspace: str, repo_slug: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a variable for a repository
        Create a repository level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_pipeline_variable(self, workspace: str, repo_slug: str, variable_uuid: str) -> BitbucketResponse:
        """Get a variable for a repository
        Retrieve a repository level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            variable_uuid: The UUID of the variable to retrieve.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_pipeline_variable(self, workspace: str, repo_slug: str, variable_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a variable for a repository
        Update a repository level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            variable_uuid: The UUID of the variable to update.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_pipeline_variable(self, workspace: str, repo_slug: str, variable_uuid: str) -> BitbucketResponse:
        """Delete a variable for a repository
        Delete a repository level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            repo_slug: The repository.
            variable_uuid: The UUID of the variable to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_repository_hosted_property_value(self, workspace: str, repo_slug: str, app_key: str, property_name: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a repository application property
        Update an [application property](/cloud/bitbucket/application-properties/) value stored against a repository.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            app_key: The key of the Connect app.
            property_name: The name of the property.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repository_hosted_property_value(self, workspace: str, repo_slug: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Delete a repository application property
        Delete an [application property](/cloud/bitbucket/application-properties/) value stored against a repository.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repository_hosted_property_value(self, workspace: str, repo_slug: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Get a repository application property
        Retrieve an [application property](/cloud/bitbucket/application-properties/) value stored against a repository.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests(self, repo_slug: str, workspace: str, state: Optional[str] = None) -> BitbucketResponse:
        """List pull requests
        Returns all pull requests on the specified repository.

By default only open pull requests are returned. This can be controlled
using the `state` query parameter. To retrieve pull requests that are
in...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            state: Only return pull requests that are in this state. This parameter can be repeated.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests"
        params = {}
        if state is not None:
            params["state"] = state
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a pull request
        Creates a new pull request where the destination repository is
this repository and the author is the authenticated user.

The minimum required fields to create a pull request are `title` and
`source`,...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_activity(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List a pull request activity log
        Returns a paginated list of the pull request's activity log.

This handler serves both a v20 and internal endpoint. The v20 endpoint
returns reviewer comments, updates, approvals and request changes. ...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/activity"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a pull request
        Returns the specified pull request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_pullrequests_pull_request_id(self, pull_request_id: int, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a pull request
        Mutates the specified pull request.

This can be used to change the pull request's branches or description.

Only open pull requests can be mutated.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_activity(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List a pull request activity log
        Returns a paginated list of the pull request's activity log.

This handler serves both a v20 and internal endpoint. The v20 endpoint
returns reviewer comments, updates, approvals and request changes. ...

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/activity"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_pullrequests_pull_request_id_approve(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Unapprove a pull request
        Redact the authenticated user's approval of the specified pull
request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/approve"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_approve(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Approve a pull request
        Approve the specified pull request as the authenticated user.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/approve"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List comments on a pull request
        Returns a paginated list of the pull request's comments.

This includes both global, inline comments and replies.

The default sorting is oldest to newest and can be overridden with
the `sort` query p...

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments(self, pull_request_id: int, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a comment on a pull request
        Creates a new pull request comment.

Returns the newly created pull request comment.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments_comment_id(self, comment_id: int, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a comment on a pull request
        Deletes a specific pull request comment.

        Args:
            comment_id: The id of the comment.
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments_comment_id(self, comment_id: int, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a comment on a pull request
        Returns a specific pull request comment.

        Args:
            comment_id: The id of the comment.
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments_comment_id(self, comment_id: int, pull_request_id: int, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a comment on a pull request
        Updates a specific pull request comment.

        Args:
            comment_id: The id of the comment.
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments_comment_id_resolve(self, comment_id: int, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Reopen a comment thread

        Args:
            comment_id: The id of the comment.
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}/resolve"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_comments_comment_id_resolve(self, comment_id: int, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Resolve a comment thread

        Args:
            comment_id: The id of the comment.
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/comments/{comment_id}/resolve"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_commits(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List commits on a pull request
        Returns a paginated list of the pull request's commits.

These are the commits that are being merged into the destination
branch when the pull requests gets accepted.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/commits"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_decline(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Decline a pull request
        Declines the pull request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/decline"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_diff(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List changes in a pull request
        Redirects to the [repository diff](/cloud/bitbucket/rest/api-group-commits/#api-repositories-workspace-repo-slug-diff-spec-get)
with the revspec that corresponds to the pull request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/diff"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_diffstat(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get the diff stat for a pull request
        Redirects to the [repository diffstat](/cloud/bitbucket/rest/api-group-commits/#api-repositories-workspace-repo-slug-diffstat-spec-get)
with the revspec that corresponds to the pull request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/diffstat"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_merge(self, pull_request_id: int, repo_slug: str, workspace: str, body: Dict[str, Any], async_param: Optional[bool] = None) -> BitbucketResponse:
        """Merge a pull request
        Merges the pull request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload
            async_param: Default value is false.   When set to true, runs merge asynchronously and immediately returns a 202 ...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/merge"
        params = {}
        if async_param is not None:
            params["async"] = async_param
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                query_params=params,
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_merge_task_status_task_id(self, pull_request_id: int, repo_slug: str, task_id: str, workspace: str) -> BitbucketResponse:
        """Get the merge task status for a pull request
        When merging a pull request takes too long, the client receives a
task ID along with a 202 status code. The task ID can be used in a call
to this endpoint to check the status of a merge task.

```
cur...

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            task_id: ID of the merge task
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/merge/task-status/{task_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_patch(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get the patch for a pull request
        Redirects to the [repository patch](/cloud/bitbucket/rest/api-group-commits/#api-repositories-workspace-repo-slug-patch-spec-get)
with the revspec that corresponds to pull request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/patch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_pullrequests_pull_request_id_request_changes(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Remove change request for a pull request

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/request-changes"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_request_changes(self, pull_request_id: int, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Request changes for a pull request

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/request-changes"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_statuses(self, pull_request_id: int, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List commit statuses for a pull request
        Returns all statuses (e.g. build results) for the given pull
request.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/statuses"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_tasks(self, pull_request_id: int, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None, pagelen: Optional[int] = None) -> BitbucketResponse:
        """List tasks on a pull request
        Returns a paginated list of the pull request's tasks.

This endpoint supports filtering and sorting of the results by the 'task' field.
See [filtering and sorting](/cloud/bitbucket/rest/intro/#filteri...

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response. See [filtering and sorting](/cloud/bitbucket/rest/intro/#f...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...
            pagelen: Current number of objects on the existing page. The default value is 10 with 100 being the maximum a...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/tasks"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        if pagelen is not None:
            params["pagelen"] = pagelen
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_pullrequests_pull_request_id_tasks(self, pull_request_id: int, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a task on a pull request
        Creates a new pull request task.

Returns the newly created pull request task.

Tasks can optionally be created in relation to a comment specified by the comment's ID which
will cause the task to appe...

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/tasks"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_pullrequests_pull_request_id_tasks_task_id(self, pull_request_id: int, repo_slug: str, task_id: int, workspace: str) -> BitbucketResponse:
        """Delete a task on a pull request
        Deletes a specific pull request task.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            task_id: The ID of the task.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/tasks/{task_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_pullrequests_pull_request_id_tasks_task_id(self, pull_request_id: int, repo_slug: str, task_id: int, workspace: str) -> BitbucketResponse:
        """Get a task on a pull request
        Returns a specific pull request task.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            task_id: The ID of the task.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/tasks/{task_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_repositories_workspace_repo_slug_pullrequests_pull_request_id_tasks_task_id(self, pull_request_id: int, repo_slug: str, task_id: int, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a task on a pull request
        Updates a specific pull request task.

        Args:
            pull_request_id: The id of the pull request.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            task_id: The ID of the task.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pull_request_id}/tasks/{task_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_pull_request_hosted_property_value(self, workspace: str, repo_slug: str, pullrequest_id: str, app_key: str, property_name: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a pull request application property
        Update an [application property](/cloud/bitbucket/application-properties/) value stored against a pull request.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            pullrequest_id: The pull request ID.
            app_key: The key of the Connect app.
            property_name: The name of the property.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pullrequest_id}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_pull_request_hosted_property_value(self, workspace: str, repo_slug: str, pullrequest_id: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Delete a pull request application property
        Delete an [application property](/cloud/bitbucket/application-properties/) value stored against a pull request.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            pullrequest_id: The pull request ID.
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pullrequest_id}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pull_request_hosted_property_value(self, workspace: str, repo_slug: str, pullrequest_id: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Get a pull request application property
        Retrieve an [application property](/cloud/bitbucket/application-properties/) value stored against a pull request.

        Args:
            workspace: The repository container; either the workspace slug or the UUID in curly braces.
            repo_slug: The repository.
            pullrequest_id: The pull request ID.
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/pullrequests/{pullrequest_id}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_refs(self, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List branches and tags
        Returns the branches and tags in the repository.

By default, results will be in the order the underlying source control system returns them and identical to
the ordering one sees when running '$ git ...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_refs_branches(self, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List open branches
        Returns a list of all open branches within the specified repository.
Results will be in the order the source control manager returns them.

Branches support [filtering and sorting](/cloud/bitbucket/re...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/branches"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_refs_branches(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Create a branch
        Creates a new branch in the specified repository.

The payload of the POST should consist of a JSON document that
contains the name of the tag and the target hash.

```
curl https://api.bitbucket.org/...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/branches"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_refs_branches_name(self, name: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a branch
        Delete a branch in the specified repository.

The main branch is not allowed to be deleted and will return a 400
response.

The branch name should not include any prefixes (e.g.
refs/heads).

        Args:
            name: The name of the branch.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/branches/{name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_refs_branches_name(self, name: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a branch
        Returns a branch object within the specified repository.

This call requires authentication. Private repositories require the
caller to authenticate with an account that has appropriate
authorization....

        Args:
            name: The name of the branch.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/branches/{name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_refs_tags(self, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List tags
        Returns the tags in the repository.

By default, results will be in the order the underlying source control system returns them and identical to
the ordering one sees when running '$ git tag --list'. ...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Field by which the results should be sorted as per [filtering and sorting](/cloud/bitbucket/rest/int...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/tags"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_refs_tags(self, repo_slug: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a tag
        Creates a new tag in the specified repository.

The payload of the POST should consist of a JSON document that
contains the name of the tag and the target hash.

```
curl https://api.bitbucket.org/2.0...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/tags"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_repositories_workspace_repo_slug_refs_tags_name(self, name: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Delete a tag
        Delete a tag in the specified repository.

The tag name should not include any prefixes (e.g. refs/tags).

        Args:
            name: The name of the tag.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/tags/{name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_refs_tags_name(self, name: str, repo_slug: str, workspace: str) -> BitbucketResponse:
        """Get a tag
        Returns the specified tag.

```
$ curl -s https://api.bitbucket.org/2.0/repositories/seanfarley/hg/refs/tags/3.8 -G | jq .
{
  'name': '3.8',
  'links': {
    'commits': {
      'href': 'https://api.b...

        Args:
            name: The name of the tag.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/refs/tags/{name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_src(self, repo_slug: str, workspace: str, format: Optional[str] = None) -> BitbucketResponse:
        """Get the root directory of the main branch
        This endpoint redirects the client to the directory listing of the
root directory on the main branch.

This is equivalent to directly hitting
[/2.0/repositories/{username}/{repo_slug}/src/{commit}/{pa...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            format: Instead of returning the file's contents, return the (json) meta data for it.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/src"
        params = {}
        if format is not None:
            params["format"] = format
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_repositories_workspace_repo_slug_src(self, repo_slug: str, workspace: str, message: Optional[str] = None, author: Optional[str] = None, parents: Optional[str] = None, files: Optional[str] = None, branch: Optional[str] = None) -> BitbucketResponse:
        """Create a commit by uploading a file
        This endpoint is used to create new commits in the repository by
uploading files.

To add a new file to a repository:

```
$ curl https://api.bitbucket.org/2.0/repositories/username/slug/src \
  -F /r...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            message: The commit message. When omitted, Bitbucket uses a canned string.
            author: The raw string to be used as the new commit's author. This string follows the format `Erik van Zijst...
            parents: #### Deprecation Notice: Support for specifying multiple parent commits is deprecated and will be re...
            files: Optional field that declares the files that the request is manipulating. When adding a new file to a...
            branch: The name of the branch that the new commit should be created on. When omitted, the commit will be cr...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/src"
        params = {}
        if message is not None:
            params["message"] = message
        if author is not None:
            params["author"] = author
        if parents is not None:
            params["parents"] = parents
        if files is not None:
            params["files"] = files
        if branch is not None:
            params["branch"] = branch
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_src_commit_path(self, commit: str, path: str, repo_slug: str, workspace: str, format: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None, max_depth: Optional[int] = None) -> BitbucketResponse:
        """Get file or directory contents
        This endpoints is used to retrieve the contents of a single file,
or the contents of a directory at a specified revision.

#### Raw file contents

When `path` points to a file, this endpoint returns t...

        Args:
            commit: The commit's SHA1.
            path: Path to the file.
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            format: If 'meta' is provided, returns the (json) meta data for the contents of the file.  If 'rendered' is ...
            q: Optional filter expression as per [filtering and sorting](/cloud/bitbucket/rest/intro/#filtering).
            sort: Optional sorting parameter as per [filtering and sorting](/cloud/bitbucket/rest/intro/#sorting-query...
            max_depth: If provided, returns the contents of the repository and its subdirectories recursively until the spe...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/src/{commit}/{path}"
        params = {}
        if format is not None:
            params["format"] = format
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        if max_depth is not None:
            params["max_depth"] = max_depth
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_versions(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List defined versions for issues
        Returns the versions that have been defined in the issue tracker.

This resource is only available on repositories that have the issue
tracker enabled.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/versions"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_versions_version_id(self, repo_slug: str, version_id: int, workspace: str) -> BitbucketResponse:
        """Get a defined version for issues
        Returns the specified issue tracker version object.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            version_id: The version's id
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/versions/{version_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_repositories_workspace_repo_slug_watchers(self, repo_slug: str, workspace: str) -> BitbucketResponse:
        """List repositories watchers
        Returns a paginated list of all the watchers on the specified
repository.

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/repositories/{workspace}/{repo_slug}/watchers"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets(self, role: Optional[str] = None) -> BitbucketResponse:
        """List snippets
        Returns all snippets. Like pull requests, repositories and workspaces, the
full set of snippets is defined by what the current user has access to.

This includes all snippets owned by any of the works...

        Args:
            role: Filter down the result based on the authenticated user's role (`owner`, `contributor`, or `member`).

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets"
        params = {}
        if role is not None:
            params["role"] = role
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_snippets(self, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a snippet
        Creates a new snippet under the authenticated user's account.

Snippets can contain multiple files. Both text and binary files are
supported.

The simplest way to create a new snippet from a local fil...

        Args:
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace(self, workspace: str, role: Optional[str] = None) -> BitbucketResponse:
        """List snippets in a workspace
        Identical to [`/snippets`](/cloud/bitbucket/rest/api-group-snippets/#api-snippets-get), except that the result is further filtered
by the snippet owner and only those that are owned by `{workspace}` a...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            role: Filter down the result based on the authenticated user's role (`owner`, `contributor`, or `member`).

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}"
        params = {}
        if role is not None:
            params["role"] = role
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_snippets_workspace(self, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a snippet for a workspace
        Identical to [`/snippets`](/cloud/bitbucket/rest/api-group-snippets/#api-snippets-post), except that the new snippet will be
created under the workspace specified in the path parameter
`{workspace}`.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_snippets_workspace_encoded_id(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Delete a snippet
        Deletes a snippet and returns an empty response.

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Get a snippet
        Retrieves a single snippet.

Snippets support multiple content types:

* application/json
* multipart/related
* multipart/form-data


application/json
----------------

The default content type of the...

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_snippets_workspace_encoded_id(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Update a snippet
        Used to update a snippet. Use this to add and delete files and to
change a snippet's title.

To update a snippet, one can either PUT a full snapshot, or only the
parts that need to be changed.

The co...

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_comments(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """List comments on a snippet
        Used to retrieve a paginated list of all comments for a specific
snippet.

This resource works identical to commit and pull request comments.

The default sorting is oldest to newest and can be overri...

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/comments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_snippets_workspace_encoded_id_comments(self, encoded_id: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a comment on a snippet
        Creates a new comment.

The only required field in the body is `content.raw`.

To create a threaded reply to an existing comment, include `parent.id`.

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/comments"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_snippets_workspace_encoded_id_comments_comment_id(self, comment_id: int, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Delete a comment on a snippet
        Deletes a snippet comment.

Comments can only be removed by the comment author, snippet creator, or workspace admin.

        Args:
            comment_id: The id of the comment.
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_comments_comment_id(self, comment_id: int, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Get a comment on a snippet
        Returns the specific snippet comment.

        Args:
            comment_id: The id of the comment.
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_snippets_workspace_encoded_id_comments_comment_id(self, comment_id: int, encoded_id: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a comment on a snippet
        Updates a comment.

The only required field in the body is `content.raw`.

Comments can only be updated by their author.

        Args:
            comment_id: The id of the comment.
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/comments/{comment_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_commits(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """List snippet changes
        Returns the changes (commits) made on this snippet.

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/commits"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_commits_revision(self, encoded_id: str, revision: str, workspace: str) -> BitbucketResponse:
        """Get a previous snippet change
        Returns the changes made on this snippet in this commit.

        Args:
            encoded_id: The snippet id.
            revision: The commit's SHA1.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/commits/{revision}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_files_path(self, encoded_id: str, path: str, workspace: str) -> BitbucketResponse:
        """Get a snippet's raw file at HEAD
        Convenience resource for getting to a snippet's raw files without the
need for first having to retrieve the snippet itself and having to pull
out the versioned file links.

        Args:
            encoded_id: The snippet id.
            path: Path to the file.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/files/{path}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_snippets_workspace_encoded_id_watch(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Stop watching a snippet
        Used to stop watching a specific snippet. Returns 204 (No Content)
to indicate success.

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/watch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_watch(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Check if the current user is watching a snippet
        Used to check if the current user is watching a specific snippet.

Returns 204 (No Content) if the user is watching the snippet and 404 if
not.

Hitting this endpoint anonymously always returns a 404.

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/watch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_snippets_workspace_encoded_id_watch(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """Watch a snippet
        Used to start watching a specific snippet. Returns 204 (No Content).

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/watch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_watchers(self, encoded_id: str, workspace: str) -> BitbucketResponse:
        """List users watching a snippet
        Returns a paginated list of all users watching a specific snippet.

        Args:
            encoded_id: The snippet id.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/watchers"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_snippets_workspace_encoded_id_node_id(self, encoded_id: str, node_id: str, workspace: str) -> BitbucketResponse:
        """Delete a previous revision of a snippet
        Deletes the snippet.

Note that this only works for versioned URLs that point to the latest
commit of the snippet. Pointing to an older commit results in a 405
status code.

To delete a snippet, regar...

        Args:
            encoded_id: The snippet id.
            node_id: A commit revision (SHA1).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/{node_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_node_id(self, encoded_id: str, node_id: str, workspace: str) -> BitbucketResponse:
        """Get a previous revision of a snippet
        Identical to `GET /snippets/encoded_id`, except that this endpoint
can be used to retrieve the contents of the snippet as it was at an
older revision, while `/snippets/encoded_id` always returns the
s...

        Args:
            encoded_id: The snippet id.
            node_id: A commit revision (SHA1).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/{node_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_snippets_workspace_encoded_id_node_id(self, encoded_id: str, node_id: str, workspace: str) -> BitbucketResponse:
        """Update a previous revision of a snippet
        Identical to `UPDATE /snippets/encoded_id`, except that this endpoint
takes an explicit commit revision. Only the snippet's 'HEAD'/'tip'
(most recent) version can be updated and requests on all other,...

        Args:
            encoded_id: The snippet id.
            node_id: A commit revision (SHA1).
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/{node_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_node_id_files_path(self, encoded_id: str, node_id: str, path: str, workspace: str) -> BitbucketResponse:
        """Get a snippet's raw file
        Retrieves the raw contents of a specific file in the snippet. The
`Content-Disposition` header will be 'attachment' to avoid issues with
malevolent executable files.

The file's mime type is derived f...

        Args:
            encoded_id: The snippet id.
            node_id: A commit revision (SHA1).
            path: Path to the file.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/{node_id}/files/{path}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_revision_diff(self, encoded_id: str, revision: str, workspace: str, path: Optional[str] = None) -> BitbucketResponse:
        """Get snippet changes between versions
        Returns the diff of the specified commit against its first parent.

Note that this resource is different in functionality from the `patch`
resource.

The differences between a diff and a patch are:

*...

        Args:
            encoded_id: The snippet id.
            revision: A revspec expression. This can simply be a commit SHA1, a ref name, or a compare expression like `st...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            path: When used, only one the diff of the specified file will be returned.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/{revision}/diff"
        params = {}
        if path is not None:
            params["path"] = path
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_snippets_workspace_encoded_id_revision_patch(self, encoded_id: str, revision: str, workspace: str) -> BitbucketResponse:
        """Get snippet patch between versions
        Returns the patch of the specified commit against its first
parent.

Note that this resource is different in functionality from the `diff`
resource.

The differences between a diff and a patch are:

*...

        Args:
            encoded_id: The snippet id.
            revision: A revspec expression. This can simply be a commit SHA1, a ref name, or a compare expression like `st...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/snippets/{workspace}/{encoded_id}/{revision}/patch"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_variables_for_team(self, username: str) -> BitbucketResponse:
        """List variables for an account
        Find account level variables.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bitb...

        Args:
            username: The account.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/teams/{username}/pipelines_config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_pipeline_variable_for_team(self, username: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a variable for a user
        Create an account level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/...

        Args:
            username: The account.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/teams/{username}/pipelines_config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_variable_for_team(self, username: str, variable_uuid: str) -> BitbucketResponse:
        """Get a variable for a team
        Retrieve a team level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bi...

        Args:
            username: The account.
            variable_uuid: The UUID of the variable to retrieve.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/teams/{username}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_pipeline_variable_for_team(self, username: str, variable_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a variable for a team
        Update a team level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bitb...

        Args:
            username: The account.
            variable_uuid: The UUID of the variable.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/teams/{username}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_pipeline_variable_for_team(self, username: str, variable_uuid: str) -> BitbucketResponse:
        """Delete a variable for a team
        Delete a team level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bitb...

        Args:
            username: The account.
            variable_uuid: The UUID of the variable to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/teams/{username}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def search_team(self, username: str, search_query: str, page: Optional[int] = None, pagelen: Optional[int] = None) -> BitbucketResponse:
        """Search for code in a team's repositories
        Search for code in the repositories of the specified team.

Note that searches can match in the file's text (`content_matches`),
the path (`path_matches`), or both.

You can use the same syntax for th...

        Args:
            username: The account to search in; either the username or the UUID in curly braces
            search_query: The search query
            page: Which page of the search results to retrieve
            pagelen: How many search results to retrieve per page

        Returns:
            BitbucketResponse: API response
        """
        path = f"/teams/{username}/search/code"
        params = {}
        if search_query is not None:
            params["search_query"] = search_query
        if page is not None:
            params["page"] = page
        if pagelen is not None:
            params["pagelen"] = pagelen
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_user(self) -> BitbucketResponse:
        """Get current user
        Returns the currently logged in user.

        Args:

        Returns:
            BitbucketResponse: API response
        """
        path = f"/user"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_user_emails(self) -> BitbucketResponse:
        """List email addresses for current user
        Returns all the authenticated user's email addresses. Both
confirmed and unconfirmed.

        Args:

        Returns:
            BitbucketResponse: API response
        """
        path = f"/user/emails"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_user_emails_email(self, email: str) -> BitbucketResponse:
        """Get an email address for current user
        Returns details about a specific one of the authenticated user's
email addresses.

Details describe whether the address has been confirmed by the user and
whether it is the user's primary address or n...

        Args:
            email: Email address of the user.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/user/emails/{email}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_user_permissions_repositories(self, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List repository permissions for a user
        Returns an object for each repository the caller has explicit access
to and their effective permission — the highest level of permission the
caller has. This does not return public repositories that t...

        Args:
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Name of a response property sort the result by as per [filtering and sorting](/cloud/bitbucket/rest/...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/user/permissions/repositories"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_user_permissions_workspaces(self, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List workspaces for the current user
        Returns an object for each workspace the caller is a member of, and
their effective role - the highest level of privilege the caller has.
If a user is a member of multiple groups with distinct roles, ...

        Args:
            q: Query string to narrow down the response. See [filtering and sorting](/cloud/bitbucket/rest/intro/#f...
            sort: Name of a response property to sort results. See [filtering and sorting](/cloud/bitbucket/rest/intro...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/user/permissions/workspaces"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_users_selected_user(self, selected_user: str) -> BitbucketResponse:
        """Get a user
        Gets the public information associated with a user account.

If the user's profile is private, `location`, `website` and
`created_on` elements are omitted.

Note that the user object returned by this ...

        Args:
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_users_selected_user_gpg_keys(self, selected_user: str) -> BitbucketResponse:
        """List GPG keys
        Returns a paginated list of the user's GPG public keys.
The `key` and `subkeys` fields can also be requested from the endpoint.
See [Partial Responses](/cloud/bitbucket/rest/intro/#partial-response) f...

        Args:
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/gpg-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_users_selected_user_gpg_keys(self, selected_user: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Add a new GPG key
        Adds a new GPG public key to the specified user account and returns the resulting key.

Example:

```
$ curl -X POST -H 'Content-Type: application/json' -d
'{'key': '<insert GPG Key>'}'
https://api.bi...

        Args:
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/gpg-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_users_selected_user_gpg_keys_fingerprint(self, fingerprint: str, selected_user: str) -> BitbucketResponse:
        """Delete a GPG key
        Deletes a specific GPG public key from a user's account.

        Args:
            fingerprint: A GPG key fingerprint.
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/gpg-keys/{fingerprint}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_users_selected_user_gpg_keys_fingerprint(self, fingerprint: str, selected_user: str) -> BitbucketResponse:
        """Get a GPG key
        Returns a specific GPG public key belonging to a user.
The `key` and `subkeys` fields can also be requested from the endpoint.
See [Partial Responses](/cloud/bitbucket/rest/intro/#partial-response) fo...

        Args:
            fingerprint: A GPG key fingerprint.
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/gpg-keys/{fingerprint}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_variables_for_user(self, selected_user: str) -> BitbucketResponse:
        """List variables for a user
        Find user level variables.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bitbuck...

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/pipelines_config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_pipeline_variable_for_user(self, selected_user: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a variable for a user
        Create a user level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bitb...

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/pipelines_config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_variable_for_user(self, selected_user: str, variable_uuid: str) -> BitbucketResponse:
        """Get a variable for a user
        Retrieve a user level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bi...

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            variable_uuid: The UUID of the variable to retrieve.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_pipeline_variable_for_user(self, selected_user: str, variable_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a variable for a user
        Update a user level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/bitb...

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            variable_uuid: The UUID of the variable.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_pipeline_variable_for_user(self, selected_user: str, variable_uuid: str) -> BitbucketResponse:
        """Delete a variable for a user
        Delete an account level variable.
This endpoint has been deprecated, and you should use the new workspaces endpoint. For more information, see [the announcement](https://developer.atlassian.com/cloud/...

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            variable_uuid: The UUID of the variable to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/pipelines_config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_user_hosted_property_value(self, selected_user: str, app_key: str, property_name: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a user application property
        Update an [application property](/cloud/bitbucket/application-properties/) value stored against a user.

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            app_key: The key of the Connect app.
            property_name: The name of the property.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_user_hosted_property_value(self, selected_user: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Delete a user application property
        Delete an [application property](/cloud/bitbucket/application-properties/) value stored against a user.

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def retrieve_user_hosted_property_value(self, selected_user: str, app_key: str, property_name: str) -> BitbucketResponse:
        """Get a user application property
        Retrieve an [application property](/cloud/bitbucket/application-properties/) value stored against a user.

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            app_key: The key of the Connect app.
            property_name: The name of the property.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/properties/{app_key}/{property_name}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def search_account(self, selected_user: str, search_query: str, page: Optional[int] = None, pagelen: Optional[int] = None) -> BitbucketResponse:
        """Search for code in a user's repositories
        Search for code in the repositories of the specified user.

Note that searches can match in the file's text (`content_matches`),
the path (`path_matches`), or both.

You can use the same syntax for th...

        Args:
            selected_user: Either the UUID of the account surrounded by curly-braces, for example `{account UUID}`, OR an Atlas...
            search_query: The search query
            page: Which page of the search results to retrieve
            pagelen: How many search results to retrieve per page

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/search/code"
        params = {}
        if search_query is not None:
            params["search_query"] = search_query
        if page is not None:
            params["page"] = page
        if pagelen is not None:
            params["pagelen"] = pagelen
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_users_selected_user_ssh_keys(self, selected_user: str) -> BitbucketResponse:
        """List SSH keys
        Returns a paginated list of the user's SSH public keys.

        Args:
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/ssh-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_users_selected_user_ssh_keys(self, selected_user: str, body: Dict[str, Any], expires_on: Optional[str] = None) -> BitbucketResponse:
        """Add a new SSH key
        Adds a new SSH public key to the specified user account and returns the resulting key.

Example:

```
$ curl -X POST -H 'Content-Type: application/json' -d '{'key': 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AA...

        Args:
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...
            body: Request body payload
            expires_on: The date or date-time of when the key will expire, in [ISO-8601](https://en.wikipedia.org/wiki/ISO_8...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/ssh-keys"
        params = {}
        if expires_on is not None:
            params["expires_on"] = expires_on
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                query_params=params,
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_users_selected_user_ssh_keys_key_id(self, key_id: str, selected_user: str) -> BitbucketResponse:
        """Delete a SSH key
        Deletes a specific SSH public key from a user's account.

        Args:
            key_id: The SSH key's UUID value.
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/ssh-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_users_selected_user_ssh_keys_key_id(self, key_id: str, selected_user: str) -> BitbucketResponse:
        """Get a SSH key
        Returns a specific SSH public key belonging to a user.

        Args:
            key_id: The SSH key's UUID value.
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/ssh-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_users_selected_user_ssh_keys_key_id(self, key_id: str, selected_user: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a SSH key
        Updates a specific SSH public key on a user's account

Note: Only the 'comment' field can be updated using this API. To modify the key or comment values, you must delete and add the key again.

Exampl...

        Args:
            key_id: The SSH key's UUID value.
            selected_user: This can either be an Atlassian Account ID OR the UUID of the account, surrounded by curly-braces, f...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/users/{selected_user}/ssh-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces(self, role: Optional[str] = None, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List workspaces for user
        Returns a list of workspaces accessible by the authenticated user.

Results may be further [filtered or sorted](/cloud/bitbucket/rest/intro/#filtering) by
workspace or permission by adding the followi...

        Args:
            role: Filters the workspaces based on the authenticated user's role on each workspace.              * **me...
            q: Query string to narrow down the response. See [filtering and sorting](/cloud/bitbucket/rest/intro/#f...
            sort: Name of a response property to sort results. See [filtering and sorting](/cloud/bitbucket/rest/intro...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces"
        params = {}
        if role is not None:
            params["role"] = role
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace(self, workspace: str) -> BitbucketResponse:
        """Get a workspace
        Returns the requested workspace.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_hooks(self, workspace: str) -> BitbucketResponse:
        """List webhooks for a workspace
        Returns a paginated list of webhooks installed on this workspace.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/hooks"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_workspaces_workspace_hooks(self, workspace: str) -> BitbucketResponse:
        """Create a webhook for a workspace
        Creates a new webhook on the specified workspace.

Workspace webhooks are fired for events from all repositories contained
by that workspace.

Example:

```
$ curl -X POST -u credentials -H 'Content-T...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/hooks"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_workspaces_workspace_hooks_uid(self, uid: str, workspace: str) -> BitbucketResponse:
        """Delete a webhook for a workspace
        Deletes the specified webhook subscription from the given workspace.

        Args:
            uid: Installed webhook's ID
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/hooks/{uid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_hooks_uid(self, uid: str, workspace: str) -> BitbucketResponse:
        """Get a webhook for a workspace
        Returns the webhook with the specified id installed on the given
workspace.

        Args:
            uid: Installed webhook's ID
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/hooks/{uid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_workspaces_workspace_hooks_uid(self, uid: str, workspace: str) -> BitbucketResponse:
        """Update a webhook for a workspace
        Updates the specified webhook subscription.

The following properties can be mutated:

* `description`
* `url`
* `secret`
* `active`
* `events`

The hook's secret is used as a key to generate the HMAC...

        Args:
            uid: Installed webhook's ID
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/hooks/{uid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_members(self, workspace: str) -> BitbucketResponse:
        """List users in a workspace
        Returns all members of the requested workspace.

This endpoint additionally supports [filtering](/cloud/bitbucket/rest/intro/#filtering) by
email address, if called by a workspace administrator, integ...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/members"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_members_member(self, member: str, workspace: str) -> BitbucketResponse:
        """Get user membership for a workspace
        Returns the workspace membership, which includes
a `User` object for the member and a `Workspace` object
for the requested workspace.

        Args:
            member: Member's UUID or Atlassian ID.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/members/{member}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_permissions(self, workspace: str, q: Optional[str] = None) -> BitbucketResponse:
        """List user permissions in a workspace
        Returns the list of members in a workspace
and their permission levels.
Permission can be:
* `owner`
* `collaborator`
* `member`

**The `collaborator` role is being removed from the Bitbucket Cloud AP...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/permissions"
        params = {}
        if q is not None:
            params["q"] = q
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_permissions_repositories(self, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List all repository permissions for a workspace
        Returns an object for each repository permission for all of a
workspace's repositories.

Permissions returned are effective permissions: the highest level of
permission the user has. This does not dis...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Name of a response property sort the result by as per [filtering and sorting](/cloud/bitbucket/rest/...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/permissions/repositories"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_permissions_repositories_repo_slug(self, repo_slug: str, workspace: str, q: Optional[str] = None, sort: Optional[str] = None) -> BitbucketResponse:
        """List a repository permissions for a workspace
        Returns an object for the repository permission of each user in the
requested repository.

Permissions returned are effective permissions: the highest level of
permission the user has. This does not d...

        Args:
            repo_slug: This can either be the repository slug or the UUID of the repository, surrounded by curly-braces, fo...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            q: Query string to narrow down the response as per [filtering and sorting](/cloud/bitbucket/rest/intro/...
            sort: Name of a response property sort the result by as per [filtering and sorting](/cloud/bitbucket/rest/...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/permissions/repositories/{repo_slug}"
        params = {}
        if q is not None:
            params["q"] = q
        if sort is not None:
            params["sort"] = sort
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_oidc_configuration(self, workspace: str) -> BitbucketResponse:
        """Get OpenID configuration for OIDC in Pipelines
        This is part of OpenID Connect for Pipelines, see https://support.atlassian.com/bitbucket-cloud/docs/integrate-pipelines-with-resource-servers-using-oidc/

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/identity/oidc/.well-known/openid-configuration"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_oidc_keys(self, workspace: str) -> BitbucketResponse:
        """Get keys for OIDC in Pipelines
        This is part of OpenID Connect for Pipelines, see https://support.atlassian.com/bitbucket-cloud/docs/integrate-pipelines-with-resource-servers-using-oidc/

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/identity/oidc/keys.json"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_variables_for_workspace(self, workspace: str) -> BitbucketResponse:
        """List variables for a workspace
        Find workspace level variables.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def create_pipeline_variable_for_workspace(self, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a variable for a workspace
        Create a workspace level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/variables"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_pipeline_variable_for_workspace(self, workspace: str, variable_uuid: str) -> BitbucketResponse:
        """Get variable for a workspace
        Retrieve a workspace level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            variable_uuid: The UUID of the variable to retrieve.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def update_pipeline_variable_for_workspace(self, workspace: str, variable_uuid: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update variable for a workspace
        Update a workspace level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            variable_uuid: The UUID of the variable.
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_pipeline_variable_for_workspace(self, workspace: str, variable_uuid: str) -> BitbucketResponse:
        """Delete a variable for a workspace
        Delete a workspace level variable.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            variable_uuid: The UUID of the variable to delete.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pipelines-config/variables/{variable_uuid}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects(self, workspace: str) -> BitbucketResponse:
        """List projects in a workspace
        Returns the list of projects in this workspace.

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_workspaces_workspace_projects(self, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Create a project in a workspace
        Creates a new project.

Note that the avatar has to be embedded as either a data-url
or a URL to an external image as shown in the examples below:

```
$ body=$(cat << EOF
{
    'name': 'Mars Project'...

        Args:
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_workspaces_workspace_projects_project_key(self, project_key: str, workspace: str) -> BitbucketResponse:
        """Delete a project for a workspace
        Deletes this project. This is an irreversible operation.

You cannot delete a project that still contains repositories.
To delete the project, [delete](/cloud/bitbucket/rest/api-group-repositories/#ap...

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key(self, project_key: str, workspace: str) -> BitbucketResponse:
        """Get a project for a workspace
        Returns the requested project.

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_workspaces_workspace_projects_project_key(self, project_key: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update a project for a workspace
        Since this endpoint can be used to both update and to create a
project, the request body depends on the intent.

#### Creation

See the POST documentation for the project collection for an
example of ...

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_branching_model(self, project_key: str, workspace: str) -> BitbucketResponse:
        """Get the branching model for a project
        Return the branching model set at the project level. This view is
read-only. The branching model settings can be changed using the
[settings](#api-workspaces-workspace-projects-project-key-branching-m...

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/branching-model"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_branching_model_settings(self, project_key: str, workspace: str) -> BitbucketResponse:
        """Get the branching model config for a project
        Return the branching model configuration for a project. The returned
object:

1. Always has a `development` property for the development branch.
2. Always a `production` property for the production br...

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/branching-model/settings"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_workspaces_workspace_projects_project_key_branching_model_settings(self, project_key: str, workspace: str) -> BitbucketResponse:
        """Update the branching model config for a project
        Update the branching model configuration for a project.

The `development` branch can be configured to a specific branch or to
track the main branch. Any branch name can be supplied, but will only
suc...

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/branching-model/settings"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_default_reviewers(self, project_key: str, workspace: str) -> BitbucketResponse:
        """List the default reviewers in a project
        Return a list of all default reviewers for a project. This is a list of users that will be added as default
reviewers to pull requests for any repository within the project.

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/default-reviewers"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_workspaces_workspace_projects_project_key_default_reviewers_selected_user(self, project_key: str, selected_user: str, workspace: str) -> BitbucketResponse:
        """Remove the specific user from the project's default reviewers
        Removes a default reviewer from the project.

Example:
```
$ curl https://api.bitbucket.org/2.0/.../default-reviewers/%7Bf0e0e8e9-66c1-4b85-a784-44a9eb9ef1a6%7D

HTTP/1.1 204
```

        Args:
            project_key: The project in question. This can either be the actual `key` assigned to the project or the `UUID` (...
            selected_user: This can either be the username or the UUID of the default reviewer, surrounded by curly-braces, for...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/default-reviewers/{selected_user}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_default_reviewers_selected_user(self, project_key: str, selected_user: str, workspace: str) -> BitbucketResponse:
        """Get a default reviewer
        Returns the specified default reviewer.

        Args:
            project_key: The project in question. This can either be the actual `key` assigned to the project or the `UUID` (...
            selected_user: This can either be the username or the UUID of the default reviewer, surrounded by curly-braces, for...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/default-reviewers/{selected_user}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_workspaces_workspace_projects_project_key_default_reviewers_selected_user(self, project_key: str, selected_user: str, workspace: str) -> BitbucketResponse:
        """Add the specific user as a default reviewer for the project
        Adds the specified user to the project's list of default reviewers. The method is
idempotent. Accepts an optional body containing the `uuid` of the user to be added.

        Args:
            project_key: The project in question. This can either be the actual `key` assigned to the project or the `UUID` (...
            selected_user: This can either be the username or the UUID of the default reviewer, surrounded by curly-braces, for...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/default-reviewers/{selected_user}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_deploy_keys(self, project_key: str, workspace: str) -> BitbucketResponse:
        """List project deploy keys
        Returns all deploy keys belonging to a project.

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/deploy-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def post_workspaces_workspace_projects_project_key_deploy_keys(self, project_key: str, workspace: str) -> BitbucketResponse:
        """Create a project deploy key
        Create a new deploy key in a project.

Example:
```
$ curl -X POST \
-H 'Authorization <auth header>' \
-H 'Content-type: application/json' \
https://api.bitbucket.org/2.0/workspaces/standard/projects...

        Args:
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/deploy-keys"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_workspaces_workspace_projects_project_key_deploy_keys_key_id(self, key_id: str, project_key: str, workspace: str) -> BitbucketResponse:
        """Delete a deploy key from a project
        This deletes a deploy key from a project.

        Args:
            key_id: The key ID matching the project deploy key.
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/deploy-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_deploy_keys_key_id(self, key_id: str, project_key: str, workspace: str) -> BitbucketResponse:
        """Get a project deploy key
        Returns the deploy key belonging to a specific key ID.

        Args:
            key_id: The key ID matching the project deploy key.
            project_key: The project in question. This is the actual `key` assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/deploy-keys/{key_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_permissions_config_groups(self, project_key: str, workspace: str) -> BitbucketResponse:
        """List explicit group permissions for a project
        Returns a paginated list of explicit group permissions for the given project.
This endpoint does not support BBQL features.

        Args:
            project_key: The project in question. This is the actual key assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/groups"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_workspaces_workspace_projects_project_key_permissions_config_groups_group_slug(self, group_slug: str, project_key: str, workspace: str) -> BitbucketResponse:
        """Delete an explicit group permission for a project
        Deletes the project group permission between the requested project and group, if one exists.

Only users with admin permission for the project may access this resource.

        Args:
            group_slug: Slug of the requested group.
            project_key: The project in question. This is the actual key assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/groups/{group_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_permissions_config_groups_group_slug(self, group_slug: str, project_key: str, workspace: str) -> BitbucketResponse:
        """Get an explicit group permission for a project
        Returns the group permission for a given group and project.

Only users with admin permission for the project may access this resource.

Permissions can be:

* `admin`
* `create-repo`
* `write`
* `rea...

        Args:
            group_slug: Slug of the requested group.
            project_key: The project in question. This is the actual key assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/groups/{group_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_workspaces_workspace_projects_project_key_permissions_config_groups_group_slug(self, group_slug: str, project_key: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update an explicit group permission for a project
        Updates the group permission, or grants a new permission if one does not already exist.

Only users with admin permission for the project may access this resource.

Due to security concerns, the JWT a...

        Args:
            group_slug: Slug of the requested group.
            project_key: The project in question. This is the actual key assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/groups/{group_slug}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_permissions_config_users(self, project_key: str, workspace: str) -> BitbucketResponse:
        """List explicit user permissions for a project
        Returns a paginated list of explicit user permissions for the given project.
This endpoint does not support BBQL features.

        Args:
            project_key: The project in question. This is the actual key assigned to the project.
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/users"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def delete_workspaces_workspace_projects_project_key_permissions_config_users_selected_user_id(self, project_key: str, selected_user_id: str, workspace: str) -> BitbucketResponse:
        """Delete an explicit user permission for a project
        Deletes the project user permission between the requested project and user, if one exists.

Only users with admin permission for the project may access this resource.

Due to security concerns, the JW...

        Args:
            project_key: The project in question. This is the actual key assigned to the project.
            selected_user_id: This can either be the username, the user's UUID surrounded by curly-braces, for example: {account U...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/users/{selected_user_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="DELETE",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_projects_project_key_permissions_config_users_selected_user_id(self, project_key: str, selected_user_id: str, workspace: str) -> BitbucketResponse:
        """Get an explicit user permission for a project
        Returns the explicit user permission for a given user and project.

Only users with admin permission for the project may access this resource.

Permissions can be:

* `admin`
* `create-repo`
* `write`...

        Args:
            project_key: The project in question. This is the actual key assigned to the project.
            selected_user_id: This can either be the username, the user's UUID surrounded by curly-braces, for example: {account U...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/users/{selected_user_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def put_workspaces_workspace_projects_project_key_permissions_config_users_selected_user_id(self, project_key: str, selected_user_id: str, workspace: str, body: Dict[str, Any]) -> BitbucketResponse:
        """Update an explicit user permission for a project
        Updates the explicit user permission for a given user and project. The selected
user must be a member of the workspace, and cannot be the workspace owner.

Only users with admin permission for the pro...

        Args:
            project_key: The project in question. This is the actual key assigned to the project.
            selected_user_id: This can either be the username, the user's UUID surrounded by curly-braces, for example: {account U...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            body: Request body payload

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/projects/{project_key}/permissions-config/users/{selected_user_id}"
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="PUT",
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def get_workspaces_workspace_pullrequests_selected_user(self, selected_user: str, workspace: str, state: Optional[str] = None) -> BitbucketResponse:
        """List workspace pull requests for a user
        Returns all workspace pull requests authored by the specified user.

By default only open pull requests are returned. This can be controlled
using the `state` query parameter. To retrieve pull request...

        Args:
            selected_user: This can either be the username of the pull request author, the author's UUID surrounded by curly-br...
            workspace: This can either be the workspace ID (slug) or the workspace UUID surrounded by curly-braces, for exa...
            state: Only return pull requests that are in this state. This parameter can be repeated.

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/pullrequests/{selected_user}"
        params = {}
        if state is not None:
            params["state"] = state
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))

    async def search_workspace(self, workspace: str, search_query: str, page: Optional[int] = None, pagelen: Optional[int] = None) -> BitbucketResponse:
        """Search for code in a workspace
        Search for code in the repositories of the specified workspace.

Note that searches can match in the file's text (`content_matches`),
the path (`path_matches`), or both.

You can use the same syntax f...

        Args:
            workspace: The workspace to search in; either the slug or the UUID in curly braces
            search_query: The search query
            page: Which page of the search results to retrieve
            pagelen: How many search results to retrieve per page

        Returns:
            BitbucketResponse: API response
        """
        path = f"/workspaces/{workspace}/search/code"
        params = {}
        if search_query is not None:
            params["search_query"] = search_query
        if page is not None:
            params["page"] = page
        if pagelen is not None:
            params["pagelen"] = pagelen
        
        try:
            request = HTTPRequest(
                url=self.base_url + path,
                method="GET",
                headers={"Content-Type": "application/json"},
                query_params=params,
            )
            response = await self.client.execute(request)
            return BitbucketResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response.json() if response.text() else {},
                message=f"Request finished with status {response.status}"
            )
        except Exception as e:
            return BitbucketResponse(success=False, error=str(e))
