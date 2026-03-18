# ruff: noqa: A002, FBT001
"""
Intercom REST API DataSource - Auto-generated API wrapper

Generated from Intercom REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.intercom.intercom import IntercomClient, IntercomResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class IntercomDataSource:
    """Intercom REST API DataSource

    Provides async wrapper methods for Intercom REST API operations:
    - Admin management
    - Contact CRUD and search
    - Conversation management
    - Company management
    - Article management
    - Teams, tags, segments, data attributes

    All methods return IntercomResponse objects.
    """

    def __init__(self, client: IntercomClient) -> None:
        """Initialize with IntercomClient.

        Args:
            client: IntercomClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'IntercomDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> IntercomClient:
        """Return the underlying IntercomClient."""
        return self._client

    async def get_me(
        self
    ) -> IntercomResponse:
        """Get the current admin

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_me" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute get_me")

    async def list_admins(
        self
    ) -> IntercomResponse:
        """List all admins

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/admins"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_admins" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_admins")

    async def get_admin(
        self,
        id: str
    ) -> IntercomResponse:
        """Get a specific admin by ID

        Args:
            id: Admin ID

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/admins/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_admin" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute get_admin")

    async def list_contacts(
        self,
        per_page: int | None = None,
        starting_after: str | None = None
    ) -> IntercomResponse:
        """List all contacts with optional pagination

        Args:
            per_page: Number of contacts per page
            starting_after: Cursor for pagination

        Returns:
            IntercomResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if starting_after is not None:
            query_params['starting_after'] = starting_after

        url = self.base_url + "/contacts"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_contacts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_contacts")

    async def get_contact(
        self,
        id: str
    ) -> IntercomResponse:
        """Get a specific contact by ID

        Args:
            id: Contact ID

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/contacts/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contact" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute get_contact")

    async def create_contact(
        self,
        role: str | None = None,
        external_id: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        avatar: str | None = None,
        signed_up_at: int | None = None,
        last_seen_at: int | None = None,
        owner_id: int | None = None,
        unsubscribed_from_emails: bool | None = None,
        custom_attributes: dict[str, Any] | None = None
    ) -> IntercomResponse:
        """Create a new contact

        Args:
            role: Role: lead or user
            external_id: External ID for the contact
            email: Email address
            phone: Phone number
            name: Full name
            avatar: Avatar URL
            signed_up_at: Signup timestamp (Unix)
            last_seen_at: Last seen timestamp (Unix)
            owner_id: Owner admin ID
            unsubscribed_from_emails: Unsubscribed from emails
            custom_attributes: Custom attributes

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/contacts"

        request_body: dict[str, Any] = {}
        if role is not None:
            request_body['role'] = role
        if external_id is not None:
            request_body['external_id'] = external_id
        if email is not None:
            request_body['email'] = email
        if phone is not None:
            request_body['phone'] = phone
        if name is not None:
            request_body['name'] = name
        if avatar is not None:
            request_body['avatar'] = avatar
        if signed_up_at is not None:
            request_body['signed_up_at'] = signed_up_at
        if last_seen_at is not None:
            request_body['last_seen_at'] = last_seen_at
        if owner_id is not None:
            request_body['owner_id'] = owner_id
        if unsubscribed_from_emails is not None:
            request_body['unsubscribed_from_emails'] = unsubscribed_from_emails
        if custom_attributes is not None:
            request_body['custom_attributes'] = custom_attributes

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_contact" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute create_contact")

    async def update_contact(
        self,
        id: str,
        role: str | None = None,
        external_id: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        name: str | None = None,
        avatar: str | None = None,
        signed_up_at: int | None = None,
        last_seen_at: int | None = None,
        owner_id: int | None = None,
        unsubscribed_from_emails: bool | None = None,
        custom_attributes: dict[str, Any] | None = None
    ) -> IntercomResponse:
        """Update an existing contact

        Args:
            id: Contact ID
            role: Role: lead or user
            external_id: External ID
            email: Email address
            phone: Phone number
            name: Full name
            avatar: Avatar URL
            signed_up_at: Signup timestamp (Unix)
            last_seen_at: Last seen timestamp (Unix)
            owner_id: Owner admin ID
            unsubscribed_from_emails: Unsubscribed from emails
            custom_attributes: Custom attributes

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/contacts/{id}".format(id=id)

        request_body: dict[str, Any] = {}
        if role is not None:
            request_body['role'] = role
        if external_id is not None:
            request_body['external_id'] = external_id
        if email is not None:
            request_body['email'] = email
        if phone is not None:
            request_body['phone'] = phone
        if name is not None:
            request_body['name'] = name
        if avatar is not None:
            request_body['avatar'] = avatar
        if signed_up_at is not None:
            request_body['signed_up_at'] = signed_up_at
        if last_seen_at is not None:
            request_body['last_seen_at'] = last_seen_at
        if owner_id is not None:
            request_body['owner_id'] = owner_id
        if unsubscribed_from_emails is not None:
            request_body['unsubscribed_from_emails'] = unsubscribed_from_emails
        if custom_attributes is not None:
            request_body['custom_attributes'] = custom_attributes

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_contact" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute update_contact")

    async def search_contacts(
        self,
        query_: dict[str, Any],
        pagination: dict[str, Any] | None = None,
        sort: dict[str, Any] | None = None
    ) -> IntercomResponse:
        """Search contacts with query filters

        Args:
            query_: Search query object with field, operator, and value
            pagination: Pagination options
            sort: Sort options

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/contacts/search"

        request_body: dict[str, Any] = {}
        request_body['query'] = query_
        if pagination is not None:
            request_body['pagination'] = pagination
        if sort is not None:
            request_body['sort'] = sort

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_contacts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute search_contacts")

    async def list_conversations(
        self,
        per_page: int | None = None,
        starting_after: str | None = None
    ) -> IntercomResponse:
        """List all conversations with optional pagination

        Args:
            per_page: Number of conversations per page
            starting_after: Cursor for pagination

        Returns:
            IntercomResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if starting_after is not None:
            query_params['starting_after'] = starting_after

        url = self.base_url + "/conversations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_conversations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_conversations")

    async def get_conversation(
        self,
        id: str
    ) -> IntercomResponse:
        """Get a specific conversation by ID

        Args:
            id: Conversation ID

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/conversations/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_conversation" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute get_conversation")

    async def list_companies(
        self,
        per_page: int | None = None,
        page: int | None = None
    ) -> IntercomResponse:
        """List all companies

        Args:
            per_page: Number of companies per page
            page: Page number

        Returns:
            IntercomResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/companies"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_companies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_companies")

    async def get_company(
        self,
        id: str
    ) -> IntercomResponse:
        """Get a specific company by ID

        Args:
            id: Company ID

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/companies/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_company" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute get_company")

    async def create_company(
        self,
        company_id: str | None = None,
        name: str | None = None,
        plan: str | None = None,
        monthly_spend: float | None = None,
        size: int | None = None,
        website: str | None = None,
        industry: str | None = None,
        remote_created_at: int | None = None,
        custom_attributes: dict[str, Any] | None = None
    ) -> IntercomResponse:
        """Create or update a company

        Args:
            company_id: External company ID
            name: Company name
            plan: Plan name
            monthly_spend: Monthly spend
            size: Number of employees
            website: Website URL
            industry: Industry
            remote_created_at: Creation timestamp (Unix)
            custom_attributes: Custom attributes

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/companies"

        request_body: dict[str, Any] = {}
        if company_id is not None:
            request_body['company_id'] = company_id
        if name is not None:
            request_body['name'] = name
        if plan is not None:
            request_body['plan'] = plan
        if monthly_spend is not None:
            request_body['monthly_spend'] = monthly_spend
        if size is not None:
            request_body['size'] = size
        if website is not None:
            request_body['website'] = website
        if industry is not None:
            request_body['industry'] = industry
        if remote_created_at is not None:
            request_body['remote_created_at'] = remote_created_at
        if custom_attributes is not None:
            request_body['custom_attributes'] = custom_attributes

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_company" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute create_company")

    async def list_articles(
        self,
        per_page: int | None = None,
        page: int | None = None
    ) -> IntercomResponse:
        """List all articles

        Args:
            per_page: Number of articles per page
            page: Page number

        Returns:
            IntercomResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if page is not None:
            query_params['page'] = str(page)

        url = self.base_url + "/articles"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_articles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_articles")

    async def get_article(
        self,
        id: str
    ) -> IntercomResponse:
        """Get a specific article by ID

        Args:
            id: Article ID

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/articles/{id}".format(id=id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_article" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute get_article")

    async def create_article(
        self,
        title: str,
        author_id: int,
        description: str | None = None,
        body: str | None = None,
        state: str | None = None,
        parent_id: int | None = None,
        parent_type: str | None = None,
        translated_content: dict[str, Any] | None = None
    ) -> IntercomResponse:
        """Create a new article

        Args:
            title: Article title
            author_id: Author admin ID
            description: Article description
            body: Article body (HTML)
            state: State: published or draft
            parent_id: Parent collection/section ID
            parent_type: Parent type: collection or section
            translated_content: Translated content by locale

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/articles"

        request_body: dict[str, Any] = {}
        request_body['title'] = title
        request_body['author_id'] = author_id
        if description is not None:
            request_body['description'] = description
        if body is not None:
            request_body['body'] = body
        if state is not None:
            request_body['state'] = state
        if parent_id is not None:
            request_body['parent_id'] = parent_id
        if parent_type is not None:
            request_body['parent_type'] = parent_type
        if translated_content is not None:
            request_body['translated_content'] = translated_content

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                body=request_body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_article" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute create_article")

    async def list_teams(
        self
    ) -> IntercomResponse:
        """List all teams

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/teams"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_teams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_teams")

    async def list_tags(
        self
    ) -> IntercomResponse:
        """List all tags

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/tags"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tags" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_tags")

    async def list_segments(
        self
    ) -> IntercomResponse:
        """List all segments

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/segments"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_segments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_segments")

    async def list_data_attributes(
        self
    ) -> IntercomResponse:
        """List all data attributes

        Returns:
            IntercomResponse with operation result
        """
        url = self.base_url + "/data_attributes"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return IntercomResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_data_attributes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return IntercomResponse(success=False, error=str(e), message="Failed to execute list_data_attributes")
