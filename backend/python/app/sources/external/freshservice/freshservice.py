from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.freshservice.freshservice import (
    FreshServiceClient,
)


class FreshServiceDataSource:
    """Lightweight Freshservice API wrapper covering common endpoints.

    Uses the HTTP client with Basic auth via API key.
    Only a subset of endpoints are implemented initially, following the
    existing data sources style. You can extend this progressively.
    """

    def __init__(self, client: FreshServiceClient) -> None:
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self._client.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "FreshServiceDataSource":
        return self

    # Tickets
    async def list_tickets(
        self,
        requester_id: Optional[int] = None,
        requester_email: Optional[str] = None,
        agent_id: Optional[int] = None,
        company_id: Optional[int] = None,
        updated_since: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        include: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> HTTPResponse:
        """List tickets with optional filters.

        GET /tickets
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _query: Dict[str, Any] = {}
        if requester_id is not None:
            _query["requester_id"] = requester_id
        if requester_email is not None:
            _query["email"] = requester_email
        if agent_id is not None:
            _query["agent_id"] = agent_id
        if company_id is not None:
            _query["company_id"] = company_id
        if updated_since is not None:
            _query["updated_since"] = updated_since
        if page is not None:
            _query["page"] = page
        if per_page is not None:
            _query["per_page"] = per_page
        if include is not None:
            _query["include"] = include
        url = self.base_url + "/tickets"
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_headers,
            path_params={},
            query_params={k: str(v) for k, v in _query.items()},
            body=None,
        )
        return await self._client.execute(req)

    async def get_ticket(
        self,
        ticket_id: int,
        include: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> HTTPResponse:
        """Get a ticket by ID.

        GET /tickets/{id}
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _query: Dict[str, Any] = {}
        if include is not None:
            _query["include"] = include
        url = self.base_url + f"/tickets/{ticket_id}"
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_headers,
            path_params={},
            query_params={k: str(v) for k, v in _query.items()},
            body=None,
        )
        return await self._client.execute(req)

    async def create_ticket(
        self,
        email: Optional[str] = None,
        requester_id: Optional[int] = None,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[int] = None,
        priority: Optional[int] = None,
        cc_emails: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        group_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        company_id: Optional[int] = None,
        assets: Optional[List[int]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> HTTPResponse:
        """Create a ticket.

        POST /tickets
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _body: Dict[str, Any] = {}
        if email is not None:
            _body["email"] = email
        if requester_id is not None:
            _body["requester_id"] = requester_id
        if subject is not None:
            _body["subject"] = subject
        if description is not None:
            _body["description"] = description
        if status is not None:
            _body["status"] = status
        if priority is not None:
            _body["priority"] = priority
        if cc_emails is not None:
            _body["cc_emails"] = cc_emails
        if tags is not None:
            _body["tags"] = tags
        if group_id is not None:
            _body["group_id"] = group_id
        if agent_id is not None:
            _body["responder_id"] = agent_id
        if company_id is not None:
            _body["company_id"] = company_id
        if assets is not None:
            _body["assets"] = assets
        if custom_fields is not None:
            _body["custom_fields"] = custom_fields
        url = self.base_url + "/tickets"
        req = HTTPRequest(
            method="POST",
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=_body,
        )
        return await self._client.execute(req)

    async def update_ticket(
        self,
        ticket_id: int,
        subject: Optional[str] = None,
        description: Optional[str] = None,
        status: Optional[int] = None,
        priority: Optional[int] = None,
        cc_emails: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        group_id: Optional[int] = None,
        agent_id: Optional[int] = None,
        company_id: Optional[int] = None,
        assets: Optional[List[int]] = None,
        custom_fields: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> HTTPResponse:
        """Update a ticket.

        PUT /tickets/{id}
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _headers.setdefault("Content-Type", "application/json")
        _body: Dict[str, Any] = {}
        if subject is not None:
            _body["subject"] = subject
        if description is not None:
            _body["description"] = description
        if status is not None:
            _body["status"] = status
        if priority is not None:
            _body["priority"] = priority
        if cc_emails is not None:
            _body["cc_emails"] = cc_emails
        if tags is not None:
            _body["tags"] = tags
        if group_id is not None:
            _body["group_id"] = group_id
        if agent_id is not None:
            _body["responder_id"] = agent_id
        if company_id is not None:
            _body["company_id"] = company_id
        if assets is not None:
            _body["assets"] = assets
        if custom_fields is not None:
            _body["custom_fields"] = custom_fields
        url = self.base_url + f"/tickets/{ticket_id}"
        req = HTTPRequest(
            method="PUT",
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=_body,
        )
        return await self._client.execute(req)

    async def delete_ticket(
        self,
        ticket_id: int,
        headers: Optional[Dict[str, Any]] = None,
    ) -> HTTPResponse:
        """Delete a ticket.

        DELETE /tickets/{id}
        """
        _headers: Dict[str, Any] = dict(headers or {})
        url = self.base_url + f"/tickets/{ticket_id}"
        req = HTTPRequest(
            method="DELETE",
            url=url,
            headers=_headers,
            path_params={},
            query_params={},
            body=None,
        )
        return await self._client.execute(req)

    # Requesters (users)
    async def list_requesters(
        self,
        email: Optional[str] = None,
        mobile: Optional[str] = None,
        phone: Optional[str] = None,
        page: Optional[int] = None,
        per_page: Optional[int] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> HTTPResponse:
        """List requesters (users).

        GET /requesters
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _query: Dict[str, Any] = {}
        if email is not None:
            _query["email"] = email
        if mobile is not None:
            _query["mobile"] = mobile
        if phone is not None:
            _query["phone"] = phone
        if page is not None:
            _query["page"] = page
        if per_page is not None:
            _query["per_page"] = per_page
        url = self.base_url + "/requesters"
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_headers,
            path_params={},
            query_params={k: str(v) for k, v in _query.items()},
            body=None,
        )
        return await self._client.execute(req)

    async def get_requester(
        self, requester_id: int, headers: Optional[Dict[str, Any]] = None
    ) -> HTTPResponse:
        """Get a requester by ID.

        GET /requesters/{id}
        """
        url = self.base_url + f"/requesters/{requester_id}"
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=dict(headers or {}),
            path_params={},
            query_params={},
            body=None,
        )
        return await self._client.execute(req)
