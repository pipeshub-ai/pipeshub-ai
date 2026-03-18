"""
WordPress REST API DataSource - Auto-generated API wrapper

Generated from WordPress REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.wordpress.wordpress import WordPressClient, WordPressResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class WordPressDataSource:
    """WordPress REST API DataSource

    Provides async wrapper methods for WordPress REST API v2 operations:
    - Posts CRUD
    - Pages CRUD
    - Categories and Tags
    - Comments
    - Users
    - Media
    - Post Types, Statuses, Taxonomies
    - Search

    The base URL is determined by the WordPressClient's configured
    authentication method (WordPress.com OAuth or self-hosted).

    All methods return WordPressResponse objects.
    """

    def __init__(self, client: WordPressClient) -> None:
        """Initialize with WordPressClient.

        Args:
            client: WordPressClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'WordPressDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> WordPressClient:
        """Return the underlying WordPressClient."""
        return self._client

    async def list_posts(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        after: str | None = None,
        before: str | None = None,
        author: str | None = None,
        categories: str | None = None,
        tags: str | None = None,
        status: str | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all posts

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            after: Limit to posts published after a given ISO8601 date
            before: Limit to posts published before a given ISO8601 date
            author: Limit to posts by one or more author IDs (comma-separated)
            categories: Limit to posts in specific category IDs (comma-separated)
            tags: Limit to posts with specific tag IDs (comma-separated)
            status: Limit to posts with a specific status (publish, draft, pending, etc.)
            orderby: Sort by attribute (date, relevance, id, include, title, slug)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before
        if author is not None:
            query_params['author'] = author
        if categories is not None:
            query_params['categories'] = categories
        if tags is not None:
            query_params['tags'] = tags
        if status is not None:
            query_params['status'] = status
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/posts"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_posts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_posts")

    async def get_post(
        self,
        post_id: str
    ) -> WordPressResponse:
        """Get a specific post by ID

        Args:
            post_id: The post ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/posts/{post_id}".format(post_id=post_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_post" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_post")

    async def create_post(
        self,
        title: str,
        content: str | None = None,
        status: str | None = None,
        excerpt: str | None = None,
        author: int | None = None,
        categories: list[int] | None = None,
        tags: list[int] | None = None,
        format_: str | None = None,
        slug: str | None = None,
        comment_status: str | None = None,
        ping_status: str | None = None,
        featured_media: int | None = None
    ) -> WordPressResponse:
        """Create a new post

        Args:
            title: The title for the post
            content: The content for the post
            status: Post status (publish, draft, pending, private)
            excerpt: The excerpt for the post
            author: The ID of the author
            categories: Category IDs for the post
            tags: Tag IDs for the post
            format_: Post format (standard, aside, chat, gallery, link, image, quote, status, video, audio)
            slug: Alphanumeric identifier for the post
            comment_status: Whether comments are open (open or closed)
            ping_status: Whether pings are accepted (open or closed)
            featured_media: The ID of the featured media

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/posts"

        body: dict[str, Any] = {}
        body['title'] = title
        if content is not None:
            body['content'] = content
        if status is not None:
            body['status'] = status
        if excerpt is not None:
            body['excerpt'] = excerpt
        if author is not None:
            body['author'] = author
        if categories is not None:
            body['categories'] = categories
        if tags is not None:
            body['tags'] = tags
        if format_ is not None:
            body['format'] = format_
        if slug is not None:
            body['slug'] = slug
        if comment_status is not None:
            body['comment_status'] = comment_status
        if ping_status is not None:
            body['ping_status'] = ping_status
        if featured_media is not None:
            body['featured_media'] = featured_media

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_post" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute create_post")

    async def update_post(
        self,
        post_id: str,
        title: str | None = None,
        content: str | None = None,
        status: str | None = None,
        excerpt: str | None = None,
        author: int | None = None,
        categories: list[int] | None = None,
        tags: list[int] | None = None,
        slug: str | None = None,
        comment_status: str | None = None,
        featured_media: int | None = None
    ) -> WordPressResponse:
        """Update an existing post

        Args:
            post_id: The post ID
            title: The title for the post
            content: The content for the post
            status: Post status (publish, draft, pending, private)
            excerpt: The excerpt for the post
            author: The ID of the author
            categories: Category IDs for the post
            tags: Tag IDs for the post
            slug: Alphanumeric identifier for the post
            comment_status: Whether comments are open (open or closed)
            featured_media: The ID of the featured media

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/posts/{post_id}".format(post_id=post_id)

        body: dict[str, Any] = {}
        if title is not None:
            body['title'] = title
        if content is not None:
            body['content'] = content
        if status is not None:
            body['status'] = status
        if excerpt is not None:
            body['excerpt'] = excerpt
        if author is not None:
            body['author'] = author
        if categories is not None:
            body['categories'] = categories
        if tags is not None:
            body['tags'] = tags
        if slug is not None:
            body['slug'] = slug
        if comment_status is not None:
            body['comment_status'] = comment_status
        if featured_media is not None:
            body['featured_media'] = featured_media

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_post" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute update_post")

    async def delete_post(
        self,
        post_id: str
    ) -> WordPressResponse:
        """Delete a post

        Args:
            post_id: The post ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/posts/{post_id}".format(post_id=post_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_post" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute delete_post")

    async def list_pages(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        after: str | None = None,
        before: str | None = None,
        author: str | None = None,
        status: str | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all pages

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            after: Limit to pages published after a given ISO8601 date
            before: Limit to pages published before a given ISO8601 date
            author: Limit to pages by one or more author IDs (comma-separated)
            status: Limit to pages with a specific status
            orderby: Sort by attribute (date, relevance, id, include, title, slug, menu_order)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before
        if author is not None:
            query_params['author'] = author
        if status is not None:
            query_params['status'] = status
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/pages"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_pages")

    async def get_page(
        self,
        page_id: str
    ) -> WordPressResponse:
        """Get a specific page by ID

        Args:
            page_id: The page ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/pages/{page_id}".format(page_id=page_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_page")

    async def create_page(
        self,
        title: str,
        content: str | None = None,
        status: str | None = None,
        excerpt: str | None = None,
        author: int | None = None,
        parent: int | None = None,
        menu_order: int | None = None,
        slug: str | None = None,
        comment_status: str | None = None,
        featured_media: int | None = None
    ) -> WordPressResponse:
        """Create a new page

        Args:
            title: The title for the page
            content: The content for the page
            status: Page status (publish, draft, pending, private)
            excerpt: The excerpt for the page
            author: The ID of the author
            parent: Parent page ID
            menu_order: Page order in menu
            slug: Alphanumeric identifier for the page
            comment_status: Whether comments are open (open or closed)
            featured_media: The ID of the featured media

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/pages"

        body: dict[str, Any] = {}
        body['title'] = title
        if content is not None:
            body['content'] = content
        if status is not None:
            body['status'] = status
        if excerpt is not None:
            body['excerpt'] = excerpt
        if author is not None:
            body['author'] = author
        if parent is not None:
            body['parent'] = parent
        if menu_order is not None:
            body['menu_order'] = menu_order
        if slug is not None:
            body['slug'] = slug
        if comment_status is not None:
            body['comment_status'] = comment_status
        if featured_media is not None:
            body['featured_media'] = featured_media

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute create_page")

    async def update_page(
        self,
        page_id: str,
        title: str | None = None,
        content: str | None = None,
        status: str | None = None,
        excerpt: str | None = None,
        author: int | None = None,
        parent: int | None = None,
        menu_order: int | None = None,
        slug: str | None = None,
        featured_media: int | None = None
    ) -> WordPressResponse:
        """Update an existing page

        Args:
            page_id: The page ID
            title: The title for the page
            content: The content for the page
            status: Page status (publish, draft, pending, private)
            excerpt: The excerpt for the page
            author: The ID of the author
            parent: Parent page ID
            menu_order: Page order in menu
            slug: Alphanumeric identifier for the page
            featured_media: The ID of the featured media

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/pages/{page_id}".format(page_id=page_id)

        body: dict[str, Any] = {}
        if title is not None:
            body['title'] = title
        if content is not None:
            body['content'] = content
        if status is not None:
            body['status'] = status
        if excerpt is not None:
            body['excerpt'] = excerpt
        if author is not None:
            body['author'] = author
        if parent is not None:
            body['parent'] = parent
        if menu_order is not None:
            body['menu_order'] = menu_order
        if slug is not None:
            body['slug'] = slug
        if featured_media is not None:
            body['featured_media'] = featured_media

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute update_page")

    async def delete_page(
        self,
        page_id: str
    ) -> WordPressResponse:
        """Delete a page

        Args:
            page_id: The page ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/pages/{page_id}".format(page_id=page_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_page" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute delete_page")

    async def list_categories(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        parent: int | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all categories

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            parent: Limit to categories with a specific parent ID
            orderby: Sort by attribute (id, include, name, slug, count, description)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if parent is not None:
            query_params['parent'] = str(parent)
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/categories"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_categories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_categories")

    async def get_category(
        self,
        category_id: str
    ) -> WordPressResponse:
        """Get a specific category by ID

        Args:
            category_id: The category ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/categories/{category_id}".format(category_id=category_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_category" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_category")

    async def create_category(
        self,
        name: str,
        description: str | None = None,
        slug: str | None = None,
        parent: int | None = None
    ) -> WordPressResponse:
        """Create a new category

        Args:
            name: The name of the category
            description: Category description
            slug: Alphanumeric identifier for the category
            parent: Parent category ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/categories"

        body: dict[str, Any] = {}
        body['name'] = name
        if description is not None:
            body['description'] = description
        if slug is not None:
            body['slug'] = slug
        if parent is not None:
            body['parent'] = parent

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_category" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute create_category")

    async def update_category(
        self,
        category_id: str,
        name: str | None = None,
        description: str | None = None,
        slug: str | None = None,
        parent: int | None = None
    ) -> WordPressResponse:
        """Update a category

        Args:
            category_id: The category ID
            name: The name of the category
            description: Category description
            slug: Alphanumeric identifier for the category
            parent: Parent category ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/categories/{category_id}".format(category_id=category_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if description is not None:
            body['description'] = description
        if slug is not None:
            body['slug'] = slug
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
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_category" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute update_category")

    async def delete_category(
        self,
        category_id: str
    ) -> WordPressResponse:
        """Delete a category

        Args:
            category_id: The category ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/categories/{category_id}".format(category_id=category_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_category" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute delete_category")

    async def list_tags(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all tags

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            orderby: Sort by attribute (id, include, name, slug, count, description)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/tags"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tags" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_tags")

    async def get_tag(
        self,
        tag_id: str
    ) -> WordPressResponse:
        """Get a specific tag by ID

        Args:
            tag_id: The tag ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/tags/{tag_id}".format(tag_id=tag_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_tag" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_tag")

    async def create_tag(
        self,
        name: str,
        description: str | None = None,
        slug: str | None = None
    ) -> WordPressResponse:
        """Create a new tag

        Args:
            name: The name of the tag
            description: Tag description
            slug: Alphanumeric identifier for the tag

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/tags"

        body: dict[str, Any] = {}
        body['name'] = name
        if description is not None:
            body['description'] = description
        if slug is not None:
            body['slug'] = slug

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_tag" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute create_tag")

    async def update_tag(
        self,
        tag_id: str,
        name: str | None = None,
        description: str | None = None,
        slug: str | None = None
    ) -> WordPressResponse:
        """Update a tag

        Args:
            tag_id: The tag ID
            name: The name of the tag
            description: Tag description
            slug: Alphanumeric identifier for the tag

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/tags/{tag_id}".format(tag_id=tag_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if description is not None:
            body['description'] = description
        if slug is not None:
            body['slug'] = slug

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_tag" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute update_tag")

    async def delete_tag(
        self,
        tag_id: str
    ) -> WordPressResponse:
        """Delete a tag

        Args:
            tag_id: The tag ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/tags/{tag_id}".format(tag_id=tag_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_tag" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute delete_tag")

    async def list_comments(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        after: str | None = None,
        before: str | None = None,
        post: int | None = None,
        author: str | None = None,
        status: str | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all comments

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            after: Limit to comments published after a given ISO8601 date
            before: Limit to comments published before a given ISO8601 date
            post: Limit to comments for a specific post ID
            author: Limit to comments by a specific author ID
            status: Limit to comments with a specific status (approve, hold, spam, trash)
            orderby: Sort by attribute (date, date_gmt, id, include, post, parent, type)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before
        if post is not None:
            query_params['post'] = str(post)
        if author is not None:
            query_params['author'] = author
        if status is not None:
            query_params['status'] = status
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/comments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_comments")

    async def get_comment(
        self,
        comment_id: str
    ) -> WordPressResponse:
        """Get a specific comment by ID

        Args:
            comment_id: The comment ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/comments/{comment_id}".format(comment_id=comment_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_comment")

    async def create_comment(
        self,
        post: int,
        content: str,
        author: int | None = None,
        author_name: str | None = None,
        author_email: str | None = None,
        author_url: str | None = None,
        parent: int | None = None,
        status: str | None = None
    ) -> WordPressResponse:
        """Create a new comment

        Args:
            post: The ID of the post the comment is for
            content: The content of the comment
            author: The ID of the comment author
            author_name: Display name of the comment author
            author_email: Email of the comment author
            author_url: URL of the comment author
            parent: Parent comment ID for threaded comments
            status: Comment status (approve, hold, spam, trash)

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/comments"

        body: dict[str, Any] = {}
        body['post'] = post
        body['content'] = content
        if author is not None:
            body['author'] = author
        if author_name is not None:
            body['author_name'] = author_name
        if author_email is not None:
            body['author_email'] = author_email
        if author_url is not None:
            body['author_url'] = author_url
        if parent is not None:
            body['parent'] = parent
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
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute create_comment")

    async def update_comment(
        self,
        comment_id: str,
        content: str | None = None,
        status: str | None = None,
        author: int | None = None
    ) -> WordPressResponse:
        """Update a comment

        Args:
            comment_id: The comment ID
            content: The content of the comment
            status: Comment status (approve, hold, spam, trash)
            author: The ID of the comment author

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/comments/{comment_id}".format(comment_id=comment_id)

        body: dict[str, Any] = {}
        if content is not None:
            body['content'] = content
        if status is not None:
            body['status'] = status
        if author is not None:
            body['author'] = author

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute update_comment")

    async def delete_comment(
        self,
        comment_id: str
    ) -> WordPressResponse:
        """Delete a comment

        Args:
            comment_id: The comment ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/comments/{comment_id}".format(comment_id=comment_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute delete_comment")

    async def list_users(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        roles: str | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all users

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            roles: Limit to users with specific roles (comma-separated)
            orderby: Sort by attribute (id, include, name, registered_date, slug, email, url)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if roles is not None:
            query_params['roles'] = roles
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

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
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> WordPressResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID

        Returns:
            WordPressResponse with operation result
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
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def get_current_user(
        self
    ) -> WordPressResponse:
        """Get the current authenticated user

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/users/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_media(
        self,
        page: int | None = None,
        per_page: int | None = None,
        search: str | None = None,
        after: str | None = None,
        before: str | None = None,
        media_type: str | None = None,
        mime_type: str | None = None,
        orderby: str | None = None,
        order: str | None = None
    ) -> WordPressResponse:
        """List all media items

        Args:
            page: Current page of the collection (default 1)
            per_page: Maximum number of items per page (default 10, max 100)
            search: Limit results to those matching a search string
            after: Limit to media uploaded after a given ISO8601 date
            before: Limit to media uploaded before a given ISO8601 date
            media_type: Limit to a specific media type (image, video, text, application, audio)
            mime_type: Limit to a specific MIME type
            orderby: Sort by attribute (date, relevance, id, include, title, slug)
            order: Sort order (asc or desc)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if search is not None:
            query_params['search'] = search
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before
        if media_type is not None:
            query_params['media_type'] = media_type
        if mime_type is not None:
            query_params['mime_type'] = mime_type
        if orderby is not None:
            query_params['orderby'] = orderby
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/media"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_media" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_media")

    async def get_media_item(
        self,
        media_id: str
    ) -> WordPressResponse:
        """Get a specific media item by ID

        Args:
            media_id: The media item ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/media/{media_id}".format(media_id=media_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_media_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_media_item")

    async def delete_media_item(
        self,
        media_id: str
    ) -> WordPressResponse:
        """Delete a media item

        Args:
            media_id: The media item ID

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/media/{media_id}".format(media_id=media_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_media_item" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute delete_media_item")

    async def list_post_types(
        self
    ) -> WordPressResponse:
        """List all post types

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/types"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_post_types" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_post_types")

    async def get_post_type(
        self,
        type_slug: str
    ) -> WordPressResponse:
        """Get a specific post type by slug

        Args:
            type_slug: The post type slug (e.g., post, page)

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/types/{type_slug}".format(type_slug=type_slug)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_post_type" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_post_type")

    async def list_post_statuses(
        self
    ) -> WordPressResponse:
        """List all post statuses

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/statuses"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_post_statuses" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_post_statuses")

    async def get_post_status(
        self,
        status_slug: str
    ) -> WordPressResponse:
        """Get a specific post status by slug

        Args:
            status_slug: The status slug (e.g., publish, draft, pending)

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/statuses/{status_slug}".format(status_slug=status_slug)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_post_status" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_post_status")

    async def list_taxonomies(
        self
    ) -> WordPressResponse:
        """List all taxonomies

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/taxonomies"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_taxonomies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute list_taxonomies")

    async def get_taxonomy(
        self,
        taxonomy_slug: str
    ) -> WordPressResponse:
        """Get a specific taxonomy by slug

        Args:
            taxonomy_slug: The taxonomy slug (e.g., category, post_tag)

        Returns:
            WordPressResponse with operation result
        """
        url = self.base_url + "/taxonomies/{taxonomy_slug}".format(taxonomy_slug=taxonomy_slug)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_taxonomy" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute get_taxonomy")

    async def search_content(
        self,
        search: str,
        type_: str | None = None,
        subtype: str | None = None,
        per_page: int | None = None,
        page: int | None = None
    ) -> WordPressResponse:
        """Search site content across multiple types

        Args:
            search: The search term (required)
            type_: Limit to a specific object type (post, term, post-format)
            subtype: Limit to specific subtypes (post, page, category, tag, or any)
            per_page: Maximum number of items per page (default 10, max 100)
            page: Current page of the collection (default 1)

        Returns:
            WordPressResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['search'] = search
        if type_ is not None:
            query_params['type'] = type_
        if subtype is not None:
            query_params['subtype'] = subtype
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

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
            return WordPressResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WordPressResponse(success=False, error=str(e), message="Failed to execute search_content")
