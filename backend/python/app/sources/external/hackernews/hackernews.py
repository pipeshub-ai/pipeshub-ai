"""
HackerNews API DataSource

Auto-generated HackerNews (Firebase v0) API client wrapper.
Covers the full official HackerNews API with explicit type hints.

Generated from HackerNews API documentation at:
https://github.com/HackerNews/API
"""

from app.sources.client.hackernews.hackernews import (
    HackerNewsClient,
    HackerNewsResponse,
)
from app.sources.client.http.http_request import HTTPRequest


class HackerNewsDataSource:
    """Comprehensive HackerNews API client wrapper.
    
    Provides async methods for the full official HackerNews v0 API:
    
    ITEMS & USERS:
    - get_item, get_user
    
    LIVE DATA:
    - get_max_item_id, get_top_stories, get_new_stories, get_best_stories,
      get_ask_stories, get_show_stories, get_job_stories, get_updates
    
    All methods return HackerNewsResponse objects with a standardized
    success/data/error shape. No Any types — all parameters are
    explicitly typed. The API is public and read-only: no auth is sent.
    """

    def __init__(self, client: HackerNewsClient) -> None:
        """Initialize with HackerNewsClient.
        
        Args:
            client: HackerNewsClient instance
        """
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError('HTTP client is not initialized')
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'HackerNewsDataSource':
        """Return the data source instance."""
        return self

    async def get_item(
        self,
        item_id: int
    ) -> HackerNewsResponse:
        """Get an item (story, comment, job, poll, or poll option) by id.

        Args:
            item_id: The item's unique id (required)

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/item/{item_id}.json".format(item_id=item_id)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_user(
        self,
        username: str
    ) -> HackerNewsResponse:
        """Get a user profile by username (HackerNews usernames are case-sensitive).

        Args:
            username: The user's unique username (required)

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/user/{username}.json".format(username=username)

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_max_item_id(
        self
    ) -> HackerNewsResponse:
        """Get the current largest item id. Poll this to walk every item sequentially.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/maxitem.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_top_stories(
        self
    ) -> HackerNewsResponse:
        """Get up to 500 of the current top story ids, best rank first.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/topstories.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_new_stories(
        self
    ) -> HackerNewsResponse:
        """Get up to 500 of the newest story ids, most recent first.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/newstories.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_best_stories(
        self
    ) -> HackerNewsResponse:
        """Get up to 500 of the current best story ids.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/beststories.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_ask_stories(
        self
    ) -> HackerNewsResponse:
        """Get up to 200 of the latest Ask HN story ids.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/askstories.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_show_stories(
        self
    ) -> HackerNewsResponse:
        """Get up to 200 of the latest Show HN story ids.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/showstories.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_job_stories(
        self
    ) -> HackerNewsResponse:
        """Get up to 200 of the latest Job story ids.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/jobstories.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_updates(
        self
    ) -> HackerNewsResponse:
        """Get recently changed items and profiles, as {"items": [...ids], "profiles": [...usernames]}.

        Returns:
            HackerNewsResponse: Response object with success status and data/error
        """
        url = self.base_url + "/updates.json"

        headers = dict(self.http.headers)

        request = HTTPRequest(
            method="GET",
            url=url,
            headers=headers,
            query_params={},
            body=None
        )

        try:
            response = await self.http.execute(request)
            if response.status >= 400:
                return HackerNewsResponse(
                    success=False,
                    error=f"HTTP {response.status}: {response.text()}",
                )
            return HackerNewsResponse(success=True, data=response.json())
        except Exception as e:
            return HackerNewsResponse(success=False, error=str(e))

    async def get_api_info(self) -> HackerNewsResponse:
        """Get information about the HackerNews API client.
        
        Returns:
            HackerNewsResponse: Information about available API methods
        """
        info = {
            'total_methods': 10,
            'base_url': self.base_url,
            'api_categories': [
                'Items & Users (2 methods)',
                'Live data: max item id, story lists, updates (8 methods)',
            ]
        }
        return HackerNewsResponse(success=True, data=info)
