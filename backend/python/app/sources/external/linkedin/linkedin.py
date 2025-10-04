import logging
from typing import Any, Dict

from app.sources.client.linkedin.linkedin import LinkedInClient, LinkedInResponse

# Set up logger
logger = logging.getLogger(__name__)

class LinkedInDataSource:
    """Auto-generated LinkedIn REST API client wrapper.
    - Uses OAuth 2.0 authentication
    - Snake_case method names
    - All responses wrapped in standardized LinkedInResponse format
    """
    def __init__(self, client: LinkedInClient) -> None:
        self.client = client


    async def _handle_linkedin_response(self, response: Any) -> LinkedInResponse:  # noqa: ANN401
        """Handle LinkedIn API response and convert to standardized format"""
        try:
            if not response:
                return LinkedInResponse(success=False, error="Empty response from LinkedIn API")

            # LinkedIn responses are typically JSON
            if isinstance(response, dict):
                data = response
                success = True
                error_msg = None

                # Check for error indicators
                if 'error' in data:
                    success = False
                    error_msg = data.get('message') or str(data.get('error'))
                elif 'status' in data and data['status'] >= 400:
                    success = False
                    error_msg = data.get('message') or f"HTTP {data['status']}"

                return LinkedInResponse(
                    success=success,
                    data=data,
                    error=error_msg
                )
            else:
                return LinkedInResponse(
                    success=True,
                    data={"raw_response": str(response)}
                )
        except Exception as e:
            logger.error(f"Error handling LinkedIn response: {e}")
            return LinkedInResponse(success=False, error=str(e))

    async def _handle_linkedin_error(self, error: Exception) -> LinkedInResponse:
        """Handle LinkedIn API errors and convert to standardized format"""
        error_msg = str(error)
        logger.error(f"LinkedIn API error: {error_msg}")
        return LinkedInResponse(success=False, error=error_msg)


    async def get_profile(self, **kwargs) -> LinkedInResponse:
        """Get current user's profile

        LinkedIn method: `get_profile` (HTTP GET /me)
        Required scopes: r_liteprofile, r_basicprofile

        Args:
            (no parameters)

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        # No parameters
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/me',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_profile_by_id(self, *, id: Any, **kwargs) -> LinkedInResponse:
        """Get profile by ID

        LinkedIn method: `get_profile_by_id` (HTTP GET /people/{id})
        Required scopes: r_liteprofile

        Args:
            id (required): LinkedIn member ID or 'me' for current user

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if id is not None:
            kwargs_api['id'] = id
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/people/{id}',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_organization(self, *, id: Any, **kwargs) -> LinkedInResponse:
        """Get organization details

        LinkedIn method: `get_organization` (HTTP GET /organizations/{id})
        Required scopes: r_organization_social

        Args:
            id (required): Organization ID

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if id is not None:
            kwargs_api['id'] = id
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/organizations/{id}',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_organization_brand(self, *, id: Any, **kwargs) -> LinkedInResponse:
        """Get organization brand

        LinkedIn method: `get_organization_brand` (HTTP GET /organizationBrands/{id})
        Required scopes: r_organization_social

        Args:
            id (required): Organization brand ID

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if id is not None:
            kwargs_api['id'] = id
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/organizationBrands/{id}',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def create_share(self,
        *,
        author: Any,
        specificContent: Any,
        visibility: Any,
        lifecycleState: Any = None,
        **kwargs
    ) -> LinkedInResponse:
        """Create a post/share

        LinkedIn method: `create_share` (HTTP POST /ugcPosts)
        Required scopes: w_member_social

        Args:
            author (required): URN of the author (person or organization)
            lifecycleState (optional): Lifecycle state of the share
            specificContent (required): Content specific to the share
            visibility (required): Visibility settings for the share

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if author is not None:
            kwargs_api['author'] = author
        if lifecycleState is not None:
            kwargs_api['lifecycleState'] = lifecycleState
        if specificContent is not None:
            kwargs_api['specificContent'] = specificContent
        if visibility is not None:
            kwargs_api['visibility'] = visibility
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='POST',
                path='/ugcPosts',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_shares(self,
        *,
        q: Any,
        authors: Any = None,
        count: Any = None,
        start: Any = None,
        **kwargs
    ) -> LinkedInResponse:
        """Get shares

        LinkedIn method: `get_shares` (HTTP GET /ugcPosts)
        Required scopes: r_member_social

        Args:
            q (required): Query parameter (e.g., 'authors')
            authors (optional): List of author URNs
            count (optional): Number of results to return
            start (optional): Starting position

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if q is not None:
            kwargs_api['q'] = q
        if authors is not None:
            kwargs_api['authors'] = authors
        if count is not None:
            kwargs_api['count'] = count
        if start is not None:
            kwargs_api['start'] = start
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/ugcPosts',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_share_statistics(self,
        *,
        q: Any,
        organizationalEntity: Any,
        **kwargs
    ) -> LinkedInResponse:
        """Get share statistics

        LinkedIn method: `get_share_statistics` (HTTP GET /organizationalEntityShareStatistics)
        Required scopes: r_organization_social

        Args:
            q (required): Query type (e.g., 'organizationalEntity')
            organizationalEntity (required): Organization URN

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if q is not None:
            kwargs_api['q'] = q
        if organizationalEntity is not None:
            kwargs_api['organizationalEntity'] = organizationalEntity
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/organizationalEntityShareStatistics',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_connections(self,
        *,
        q: Any = None,
        start: Any = None,
        count: Any = None,
        **kwargs
    ) -> LinkedInResponse:
        """Get connections

        LinkedIn method: `get_connections` (HTTP GET /connections)
        Required scopes: r_basicprofile

        Args:
            q (optional): Query parameter
            start (optional): Starting position
            count (optional): Number of results

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if q is not None:
            kwargs_api['q'] = q
        if start is not None:
            kwargs_api['start'] = start
        if count is not None:
            kwargs_api['count'] = count
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/connections',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_organization_follower_statistics(self,
        *,
        q: Any,
        organizationalEntity: Any,
        **kwargs
    ) -> LinkedInResponse:
        """Get follower statistics

        LinkedIn method: `get_organization_follower_statistics` (HTTP GET /organizationalEntityFollowerStatistics)
        Required scopes: r_organization_social

        Args:
            q (required): Query type
            organizationalEntity (required): Organization URN

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if q is not None:
            kwargs_api['q'] = q
        if organizationalEntity is not None:
            kwargs_api['organizationalEntity'] = organizationalEntity
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/organizationalEntityFollowerStatistics',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_page_statistics(self, *, organization: Any, **kwargs) -> LinkedInResponse:
        """Get page statistics

        LinkedIn method: `get_page_statistics` (HTTP GET /organizationPageStatistics/{organization})
        Required scopes: r_organization_social

        Args:
            organization (required): Organization ID

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if organization is not None:
            kwargs_api['organization'] = organization
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/organizationPageStatistics/{organization}',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def search_companies(self,
        *,
        q: Any,
        start: Any = None,
        count: Any = None,
        **kwargs
    ) -> LinkedInResponse:
        """Search companies

        LinkedIn method: `search_companies` (HTTP GET /search/companies)
        Required scopes: r_basicprofile

        Args:
            q (required): Search query
            start (optional): Starting position
            count (optional): Number of results

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if q is not None:
            kwargs_api['q'] = q
        if start is not None:
            kwargs_api['start'] = start
        if count is not None:
            kwargs_api['count'] = count
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/search/companies',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_member_companies(self,
        *,
        id: Any,
        count: Any = None,
        start: Any = None,
        **kwargs
    ) -> LinkedInResponse:
        """Get member company updates

        LinkedIn method: `get_member_companies` (HTTP GET /people/{id}/network/company-updates)
        Required scopes: r_network

        Args:
            id (required): Member ID
            count (optional): Number of results
            start (optional): Starting position

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if id is not None:
            kwargs_api['id'] = id
        if count is not None:
            kwargs_api['count'] = count
        if start is not None:
            kwargs_api['start'] = start
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/people/{id}/network/company-updates',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def create_comment(self,
        *,
        shareUrn: Any,
        message: Any,
        **kwargs
    ) -> LinkedInResponse:
        """Create a comment

        LinkedIn method: `create_comment` (HTTP POST /socialActions/{shareUrn}/comments)
        Required scopes: w_member_social

        Args:
            shareUrn (required): URN of the share to comment on
            message (required): Comment message

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if shareUrn is not None:
            kwargs_api['shareUrn'] = shareUrn
        if message is not None:
            kwargs_api['message'] = message
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='POST',
                path='/socialActions/{shareUrn}/comments',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def get_comments(self,
        *,
        shareUrn: Any,
        count: Any = None,
        start: Any = None,
        **kwargs
    ) -> LinkedInResponse:
        """Get comments

        LinkedIn method: `get_comments` (HTTP GET /socialActions/{shareUrn}/comments)
        Required scopes: r_member_social

        Args:
            shareUrn (required): URN of the share
            count (optional): Number of results
            start (optional): Starting position

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if shareUrn is not None:
            kwargs_api['shareUrn'] = shareUrn
        if count is not None:
            kwargs_api['count'] = count
        if start is not None:
            kwargs_api['start'] = start
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='GET',
                path='/socialActions/{shareUrn}/comments',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def create_like(self, *, shareUrn: Any, **kwargs) -> LinkedInResponse:
        """Like a post

        LinkedIn method: `create_like` (HTTP POST /socialActions/{shareUrn}/likes)
        Required scopes: w_member_social

        Args:
            shareUrn (required): URN of the share to like

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if shareUrn is not None:
            kwargs_api['shareUrn'] = shareUrn
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='POST',
                path='/socialActions/{shareUrn}/likes',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)

    async def delete_like(self,
        *,
        shareUrn: Any,
        actor: Any,
        **kwargs
    ) -> LinkedInResponse:
        """Unlike a post

        LinkedIn method: `delete_like` (HTTP DELETE /socialActions/{shareUrn}/likes/{actor})
        Required scopes: w_member_social

        Args:
            shareUrn (required): URN of the share
            actor (required): Actor URN (person or organization)

        Returns:
            LinkedInResponse: Standardized response wrapper with success/data/error
        """
        kwargs_api: Dict[str, Any] = {}
        if shareUrn is not None:
            kwargs_api['shareUrn'] = shareUrn
        if actor is not None:
            kwargs_api['actor'] = actor
        if kwargs:
            kwargs_api.update(kwargs)

        try:
            response = await self.client._execute_request(
                method='DELETE',
                path='/socialActions/{shareUrn}/likes/{actor}',
                **kwargs_api
            )
            return await self._handle_linkedin_response(response)
        except Exception as e:
            return await self._handle_linkedin_error(e)


__all__ = ['LinkedInDataSource', 'LinkedInResponse']
