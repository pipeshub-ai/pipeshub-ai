from typing import Any, Literal

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.zendesk.zendesk import ZendeskClient, ZendeskResponse

SUCCESS_CODE_IS_LESS_THAN = 400


class ZendeskDataSource:
    """Comprehensive Zendesk API client wrapper.
    Provides async methods for ALL Zendesk API endpoints across:
    SUPPORT API:
    - Tickets (CRUD, bulk operations, comments, merge, import)
    - Users (CRUD, identities, passwords, authentication)
    - Organizations (CRUD, memberships, search, autocomplete)
    - Groups (CRUD, memberships)
    - Search (unified search across all resources)
    - Requests (end-user perspective of tickets)
    - Custom Fields (tickets, users, organizations)
    - Comments, Attachments, Tags
    BUSINESS RULES:
    - Automations (time-based workflow rules)
    - Triggers (event-based workflow rules)
    - Macros (manual agent actions)
    - Views (filtered ticket lists)
    - SLA Policies (service level agreements)
    WEBHOOKS:
    - Webhook management (CRUD, testing, signing secrets)
    - Event subscriptions (tickets, users, organizations)
    - Authentication and security
    HELP CENTER API:
    - Articles (CRUD, translations, search)
    - Sections (CRUD, translations)
    - Categories (CRUD, translations)
    CUSTOM DATA:
    - Custom Objects (CRUD, records, relationships)
    - Custom Object Records and Fields
    ADMINISTRATIVE:
    - Job Status monitoring
    - Incremental Exports (tickets, users, organizations)
    - Bulk Operations and Background Jobs
    All methods return ZendeskResponse objects with standardized format.
    Every parameter matches Zendesk's official API documentation exactly.
    No **kwargs usage - all parameters are explicitly typed.
    """

    def __init__(self, client: ZendeskClient) -> None:
        """Initialize with ZendeskClient."""
        self._client = client
        self.http = client.get_client()
        if self.http is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "ZendeskDataSource":
        return self

    async def list_tickets(
        self,
        sort_by: Literal[
            "assignee",
            "assignee.name",
            "created_at",
            "group_id",
            "id",
            "locale_id",
            "requester",
            "requester.name",
            "status",
            "subject",
            "updated_at",
        ]
        | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        external_id: str | None = None,
        include: str | None = None,
        page: int | None = None,
        per_page: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all tickets with pagination support

        Args:
            sort_by (Optional[Literal["assignee", "assignee.name", "created_at", "group_id", "id", "locale_id", "requester", "requester.name", "status", "subject", "updated_at"]], optional): Sort order for results
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            external_id (Optional[str], optional): Filter by external ID
            include (Optional[str], optional): Sideload related data (users,groups,organizations,last_audits,metric_sets,dates,sharing_agreements,comment_count,incident_counts,ticket_forms,metric_events,slas,custom_statuses)
            page (Optional[int], optional): Page number for pagination
            per_page (Optional[int], optional): Number of results per page (max 100)

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets.json"

            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if external_id is not None:
                _params["external_id"] = external_id
            if include is not None:
                _params["include"] = include
            if page is not None:
                _params["page"] = page
            if per_page is not None:
                _params["per_page"] = per_page

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_ticket(
        self,
        ticket_id: int,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show details of a specific ticket

        Args:
            ticket_id (int, required): ID of the ticket to retrieve
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}.json"

            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_ticket(
        self,
        subject: str,
        comment: dict[str, Any],
        requester_id: int | None = None,
        requester: dict[str, Any] | None = None,
        submitter_id: int | None = None,
        assignee_id: int | None = None,
        group_id: int | None = None,
        collaborator_ids: list[int] | None = None,
        follower_ids: list[int] | None = None,
        email_ccs: list[dict[str, str]] | None = None,
        organization_id: int | None = None,
        external_id: str | None = None,
        type: Literal["problem", "incident", "question", "task"] | None = None,
        priority: Literal["urgent", "high", "normal", "low"] | None = None,
        status: Literal["new", "open", "pending", "hold", "solved", "closed"]
        | None = None,
        recipient: str | None = None,
        tags: list[str] | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        due_at: str | None = None,
        ticket_form_id: int | None = None,
        brand_id: int | None = None,
        forum_topic_id: int | None = None,
        problem_id: int | None = None,
        via: dict[str, Any] | None = None,
        macro_ids: list[int] | None = None,
        safe_update: bool | None = None,
        updated_stamp: str | None = None,
        sharing_agreement_ids: list[int] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new ticket

        Args:
            subject (str, required): Ticket subject
            comment (Dict[str, Any], required): Initial comment with body, html_body, public, author_id, uploads
            requester_id (Optional[int], optional): ID of the requester
            requester (Optional[Dict[str, Any]], optional): Requester details (creates user if not exists)
            submitter_id (Optional[int], optional): ID of the submitter
            assignee_id (Optional[int], optional): ID of the assignee
            group_id (Optional[int], optional): ID of the group
            collaborator_ids (Optional[List[int]], optional): List of collaborator user IDs
            follower_ids (Optional[List[int]], optional): List of follower user IDs
            email_ccs (Optional[List[Dict[str, str]]], optional): List of email CCs with user_email and action
            organization_id (Optional[int], optional): ID of the organization
            external_id (Optional[str], optional): External ID for the ticket
            type (Optional[Literal["problem", "incident", "question", "task"]], optional): Type of ticket
            priority (Optional[Literal["urgent", "high", "normal", "low"]], optional): Priority level
            status (Optional[Literal["new", "open", "pending", "hold", "solved", "closed"]], optional): Ticket status
            recipient (Optional[str], optional): Original recipient email
            tags (Optional[List[str]], optional): List of tags
            custom_fields (Optional[List[Dict[str, Any]]], optional): Custom field values
            due_at (Optional[str], optional): Due date (ISO 8601)
            ticket_form_id (Optional[int], optional): ID of the ticket form
            brand_id (Optional[int], optional): ID of the brand
            forum_topic_id (Optional[int], optional): ID of the forum topic
            problem_id (Optional[int], optional): ID of the parent problem ticket
            via (Optional[Dict[str, Any]], optional): Via channel information
            macro_ids (Optional[List[int]], optional): List of macro IDs to apply
            safe_update (Optional[bool], optional): Enable safe update mode
            updated_stamp (Optional[str], optional): Timestamp for safe updates
            sharing_agreement_ids (Optional[List[int]], optional): List of sharing agreement IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets.json"

            _data["subject"] = subject
            _data["comment"] = comment
            if requester_id is not None:
                _data["requester_id"] = requester_id
            if requester is not None:
                _data["requester"] = requester
            if submitter_id is not None:
                _data["submitter_id"] = submitter_id
            if assignee_id is not None:
                _data["assignee_id"] = assignee_id
            if group_id is not None:
                _data["group_id"] = group_id
            if collaborator_ids is not None:
                _data["collaborator_ids"] = collaborator_ids
            if follower_ids is not None:
                _data["follower_ids"] = follower_ids
            if email_ccs is not None:
                _data["email_ccs"] = email_ccs
            if organization_id is not None:
                _data["organization_id"] = organization_id
            if external_id is not None:
                _data["external_id"] = external_id
            if type is not None:
                _data["type"] = type
            if priority is not None:
                _data["priority"] = priority
            if status is not None:
                _data["status"] = status
            if recipient is not None:
                _data["recipient"] = recipient
            if tags is not None:
                _data["tags"] = tags
            if custom_fields is not None:
                _data["custom_fields"] = custom_fields
            if due_at is not None:
                _data["due_at"] = due_at
            if ticket_form_id is not None:
                _data["ticket_form_id"] = ticket_form_id
            if brand_id is not None:
                _data["brand_id"] = brand_id
            if forum_topic_id is not None:
                _data["forum_topic_id"] = forum_topic_id
            if problem_id is not None:
                _data["problem_id"] = problem_id
            if via is not None:
                _data["via"] = via
            if macro_ids is not None:
                _data["macro_ids"] = macro_ids
            if safe_update is not None:
                _data["safe_update"] = safe_update
            if updated_stamp is not None:
                _data["updated_stamp"] = updated_stamp
            if sharing_agreement_ids is not None:
                _data["sharing_agreement_ids"] = sharing_agreement_ids

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_ticket(
        self,
        ticket_id: int,
        subject: str | None = None,
        comment: dict[str, Any] | None = None,
        requester_id: int | None = None,
        submitter_id: int | None = None,
        assignee_id: int | None = None,
        group_id: int | None = None,
        collaborator_ids: list[int] | None = None,
        follower_ids: list[int] | None = None,
        organization_id: int | None = None,
        external_id: str | None = None,
        type: Literal["problem", "incident", "question", "task"] | None = None,
        priority: Literal["urgent", "high", "normal", "low"] | None = None,
        status: Literal["new", "open", "pending", "hold", "solved", "closed"]
        | None = None,
        tags: list[str] | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        due_at: str | None = None,
        additional_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        safe_update: bool | None = None,
        updated_stamp: str | None = None,
        macro_ids: list[int] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing ticket

        Args:
            ticket_id (int, required): ID of the ticket to update
            subject (Optional[str], optional): Ticket subject
            comment (Optional[Dict[str, Any]], optional): Comment to add with body, html_body, public, author_id, uploads
            requester_id (Optional[int], optional): ID of the requester
            submitter_id (Optional[int], optional): ID of the submitter
            assignee_id (Optional[int], optional): ID of the assignee
            group_id (Optional[int], optional): ID of the group
            collaborator_ids (Optional[List[int]], optional): List of collaborator user IDs
            follower_ids (Optional[List[int]], optional): List of follower user IDs
            organization_id (Optional[int], optional): ID of the organization
            external_id (Optional[str], optional): External ID for the ticket
            type (Optional[Literal["problem", "incident", "question", "task"]], optional): Type of ticket
            priority (Optional[Literal["urgent", "high", "normal", "low"]], optional): Priority level
            status (Optional[Literal["new", "open", "pending", "hold", "solved", "closed"]], optional): Ticket status
            tags (Optional[List[str]], optional): List of tags
            custom_fields (Optional[List[Dict[str, Any]]], optional): Custom field values
            due_at (Optional[str], optional): Due date (ISO 8601)
            additional_tags (Optional[List[str]], optional): Tags to add
            remove_tags (Optional[List[str]], optional): Tags to remove
            safe_update (Optional[bool], optional): Enable safe update mode
            updated_stamp (Optional[str], optional): Timestamp for safe updates
            macro_ids (Optional[List[int]], optional): List of macro IDs to apply

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}.json"

            if subject is not None:
                _data["subject"] = subject
            if comment is not None:
                _data["comment"] = comment
            if requester_id is not None:
                _data["requester_id"] = requester_id
            if submitter_id is not None:
                _data["submitter_id"] = submitter_id
            if assignee_id is not None:
                _data["assignee_id"] = assignee_id
            if group_id is not None:
                _data["group_id"] = group_id
            if collaborator_ids is not None:
                _data["collaborator_ids"] = collaborator_ids
            if follower_ids is not None:
                _data["follower_ids"] = follower_ids
            if organization_id is not None:
                _data["organization_id"] = organization_id
            if external_id is not None:
                _data["external_id"] = external_id
            if type is not None:
                _data["type"] = type
            if priority is not None:
                _data["priority"] = priority
            if status is not None:
                _data["status"] = status
            if tags is not None:
                _data["tags"] = tags
            if custom_fields is not None:
                _data["custom_fields"] = custom_fields
            if due_at is not None:
                _data["due_at"] = due_at
            if additional_tags is not None:
                _data["additional_tags"] = additional_tags
            if remove_tags is not None:
                _data["remove_tags"] = remove_tags
            if safe_update is not None:
                _data["safe_update"] = safe_update
            if updated_stamp is not None:
                _data["updated_stamp"] = updated_stamp
            if macro_ids is not None:
                _data["macro_ids"] = macro_ids

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_ticket(
        self,
        ticket_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a ticket

        Args:
            ticket_id (int, required): ID of the ticket to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_many_tickets(
        self,
        tickets: list[dict[str, Any]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create multiple tickets (up to 100)

        Args:
            tickets (List[Dict[str, Any]], required): Array of ticket objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/create_many.json"

            _data["tickets"] = tickets

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_many_tickets(
        self,
        tickets: list[dict[str, Any]],
        ids: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update multiple tickets

        Args:
            tickets (List[Dict[str, Any]], required): Array of ticket update objects
            ids (Optional[str], optional): Comma-separated ticket IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/update_many.json"

            if ids is not None:
                _params["ids"] = ids

            _data["tickets"] = tickets

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def destroy_many_tickets(
        self,
        ids: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete multiple tickets

        Args:
            ids (str, required): Comma-separated ticket IDs to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/destroy_many.json"

            _params["ids"] = ids

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_multiple_tickets(
        self,
        ids: str,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show details of multiple tickets

        Args:
            ids (str, required): Comma-separated ticket IDs
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/show_many.json"

            _params["ids"] = ids
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def merge_tickets(
        self,
        ticket_id: int,
        ids: list[int],
        target_comment: str | None = None,
        source_comment: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Merge tickets into target ticket

        Args:
            ticket_id (int, required): ID of the target ticket
            ids (List[int], required): Array of source ticket IDs to merge
            target_comment (Optional[str], optional): Comment to add to target ticket
            source_comment (Optional[str], optional): Comment to add to source tickets

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}/merge.json"

            _data["ids"] = ids
            if target_comment is not None:
                _data["target_comment"] = target_comment
            if source_comment is not None:
                _data["source_comment"] = source_comment

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def import_ticket(
        self,
        subject: str,
        comments: list[dict[str, Any]],
        requester_id: int,
        created_at: str,
        updated_at: str,
        status: Literal["solved", "closed"],
        submitter_id: int | None = None,
        assignee_id: int | None = None,
        group_id: int | None = None,
        organization_id: int | None = None,
        solved_at: str | None = None,
        priority: Literal["urgent", "high", "normal", "low"] | None = None,
        type: Literal["problem", "incident", "question", "task"] | None = None,
        tags: list[str] | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        external_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Import a ticket with historical data

        Args:
            subject (str, required): Ticket subject
            comments (List[Dict[str, Any]], required): Array of comment objects
            requester_id (int, required): ID of the requester
            submitter_id (Optional[int], optional): ID of the submitter
            assignee_id (Optional[int], optional): ID of the assignee
            group_id (Optional[int], optional): ID of the group
            organization_id (Optional[int], optional): ID of the organization
            created_at (str, required): Creation timestamp (ISO 8601)
            updated_at (str, required): Update timestamp (ISO 8601)
            solved_at (Optional[str], optional): Solved timestamp (ISO 8601)
            status (Literal["solved", "closed"], required): Ticket status (must be solved or closed)
            priority (Optional[Literal["urgent", "high", "normal", "low"]], optional): Priority level
            type (Optional[Literal["problem", "incident", "question", "task"]], optional): Type of ticket
            tags (Optional[List[str]], optional): List of tags
            custom_fields (Optional[List[Dict[str, Any]]], optional): Custom field values
            external_id (Optional[str], optional): External ID for the ticket

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/imports/tickets.json"

            _data["subject"] = subject
            _data["comments"] = comments
            _data["requester_id"] = requester_id
            if submitter_id is not None:
                _data["submitter_id"] = submitter_id
            if assignee_id is not None:
                _data["assignee_id"] = assignee_id
            if group_id is not None:
                _data["group_id"] = group_id
            if organization_id is not None:
                _data["organization_id"] = organization_id
            _data["created_at"] = created_at
            _data["updated_at"] = updated_at
            if solved_at is not None:
                _data["solved_at"] = solved_at
            _data["status"] = status
            if priority is not None:
                _data["priority"] = priority
            if type is not None:
                _data["type"] = type
            if tags is not None:
                _data["tags"] = tags
            if custom_fields is not None:
                _data["custom_fields"] = custom_fields
            if external_id is not None:
                _data["external_id"] = external_id

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_comments(
        self,
        ticket_id: int,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List comments for a ticket

        Args:
            ticket_id (int, required): ID of the ticket
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            include (Optional[str], optional): Sideload related data (users)

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}/comments.json"

            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def redact_comment(
        self,
        ticket_id: int,
        comment_id: int,
        text: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Redact a comment

        Args:
            ticket_id (int, required): ID of the ticket
            comment_id (int, required): ID of the comment
            text (str, required): Replacement text

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = (
                f"{self.base_url}/tickets/{ticket_id}/comments/{comment_id}/redact.json"
            )

            _data["text"] = text

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_users(
        self,
        role: Literal["end-user", "agent", "admin"] | None = None,
        roles_: list[str] | None = None,
        permission_set: int | None = None,
        external_id: str | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all users

        Args:
            role (Optional[Literal["end-user", "agent", "admin"]], optional): Filter by user role
            roles_ (Optional[List[str]], optional): Filter by multiple roles
            permission_set (Optional[int], optional): Filter by permission set ID
            external_id (Optional[str], optional): Filter by external ID
            include (Optional[str], optional): Sideload related data (organizations,roles,abilities,identities,groups)

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users.json"

            if role is not None:
                _params["role"] = role
            if roles_ is not None:
                _params["roles[]"] = roles_
            if permission_set is not None:
                _params["permission_set"] = permission_set
            if external_id is not None:
                _params["external_id"] = external_id
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_user(
        self,
        user_id: int,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific user

        Args:
            user_id (int, required): ID of the user
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}.json"

            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_user(
        self,
        name: str,
        email: str | None = None,
        role: Literal["end-user", "agent", "admin"] | None = None,
        custom_role_id: int | None = None,
        external_id: str | None = None,
        alias: str | None = None,
        details: str | None = None,
        notes: str | None = None,
        organization_id: int | None = None,
        phone: str | None = None,
        shared_phone_number: bool | None = None,
        signature: str | None = None,
        tags: list[str] | None = None,
        time_zone: str | None = None,
        locale: str | None = None,
        locale_id: int | None = None,
        user_fields: dict[str, Any] | None = None,
        verified: bool | None = None,
        restricted_agent: bool | None = None,
        suspended: bool | None = None,
        shared: bool | None = None,
        shared_agent: bool | None = None,
        only_private_comments: bool | None = None,
        default_group_id: int | None = None,
        photo: dict[str, Any] | None = None,
        identities: list[dict[str, Any]] | None = None,
        skip_verify_email: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new user

        Args:
            name (str, required): User name
            email (Optional[str], optional): Primary email address
            role (Optional[Literal["end-user", "agent", "admin"]], optional): User role
            custom_role_id (Optional[int], optional): Custom role ID
            external_id (Optional[str], optional): External ID
            alias (Optional[str], optional): User alias
            details (Optional[str], optional): User details
            notes (Optional[str], optional): Notes about the user
            organization_id (Optional[int], optional): Organization ID
            phone (Optional[str], optional): Phone number
            shared_phone_number (Optional[bool], optional): Whether phone is shared
            signature (Optional[str], optional): User signature
            tags (Optional[List[str]], optional): List of tags
            time_zone (Optional[str], optional): Time zone
            locale (Optional[str], optional): Locale (language)
            locale_id (Optional[int], optional): Locale ID
            user_fields (Optional[Dict[str, Any]], optional): Custom user fields
            verified (Optional[bool], optional): Whether user is verified
            restricted_agent (Optional[bool], optional): Whether agent is restricted
            suspended (Optional[bool], optional): Whether user is suspended
            shared (Optional[bool], optional): Whether user is shared
            shared_agent (Optional[bool], optional): Whether agent is shared
            only_private_comments (Optional[bool], optional): Whether user can only make private comments
            default_group_id (Optional[int], optional): Default group ID
            photo (Optional[Dict[str, Any]], optional): Photo attachment
            identities (Optional[List[Dict[str, Any]]], optional): Additional identities
            skip_verify_email (Optional[bool], optional): Skip email verification

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users.json"

            _data["name"] = name
            if email is not None:
                _data["email"] = email
            if role is not None:
                _data["role"] = role
            if custom_role_id is not None:
                _data["custom_role_id"] = custom_role_id
            if external_id is not None:
                _data["external_id"] = external_id
            if alias is not None:
                _data["alias"] = alias
            if details is not None:
                _data["details"] = details
            if notes is not None:
                _data["notes"] = notes
            if organization_id is not None:
                _data["organization_id"] = organization_id
            if phone is not None:
                _data["phone"] = phone
            if shared_phone_number is not None:
                _data["shared_phone_number"] = shared_phone_number
            if signature is not None:
                _data["signature"] = signature
            if tags is not None:
                _data["tags"] = tags
            if time_zone is not None:
                _data["time_zone"] = time_zone
            if locale is not None:
                _data["locale"] = locale
            if locale_id is not None:
                _data["locale_id"] = locale_id
            if user_fields is not None:
                _data["user_fields"] = user_fields
            if verified is not None:
                _data["verified"] = verified
            if restricted_agent is not None:
                _data["restricted_agent"] = restricted_agent
            if suspended is not None:
                _data["suspended"] = suspended
            if shared is not None:
                _data["shared"] = shared
            if shared_agent is not None:
                _data["shared_agent"] = shared_agent
            if only_private_comments is not None:
                _data["only_private_comments"] = only_private_comments
            if default_group_id is not None:
                _data["default_group_id"] = default_group_id
            if photo is not None:
                _data["photo"] = photo
            if identities is not None:
                _data["identities"] = identities
            if skip_verify_email is not None:
                _data["skip_verify_email"] = skip_verify_email

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_user(
        self,
        user_id: int,
        name: str | None = None,
        email: str | None = None,
        role: Literal["end-user", "agent", "admin"] | None = None,
        custom_role_id: int | None = None,
        external_id: str | None = None,
        alias: str | None = None,
        details: str | None = None,
        notes: str | None = None,
        organization_id: int | None = None,
        phone: str | None = None,
        shared_phone_number: bool | None = None,
        signature: str | None = None,
        tags: list[str] | None = None,
        time_zone: str | None = None,
        locale: str | None = None,
        locale_id: int | None = None,
        user_fields: dict[str, Any] | None = None,
        verified: bool | None = None,
        restricted_agent: bool | None = None,
        suspended: bool | None = None,
        shared: bool | None = None,
        shared_agent: bool | None = None,
        only_private_comments: bool | None = None,
        default_group_id: int | None = None,
        photo: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing user

        Args:
            user_id (int, required): ID of the user to update
            name (Optional[str], optional): User name
            email (Optional[str], optional): Primary email address
            role (Optional[Literal["end-user", "agent", "admin"]], optional): User role
            custom_role_id (Optional[int], optional): Custom role ID
            external_id (Optional[str], optional): External ID
            alias (Optional[str], optional): User alias
            details (Optional[str], optional): User details
            notes (Optional[str], optional): Notes about the user
            organization_id (Optional[int], optional): Organization ID
            phone (Optional[str], optional): Phone number
            shared_phone_number (Optional[bool], optional): Whether phone is shared
            signature (Optional[str], optional): User signature
            tags (Optional[List[str]], optional): List of tags
            time_zone (Optional[str], optional): Time zone
            locale (Optional[str], optional): Locale (language)
            locale_id (Optional[int], optional): Locale ID
            user_fields (Optional[Dict[str, Any]], optional): Custom user fields
            verified (Optional[bool], optional): Whether user is verified
            restricted_agent (Optional[bool], optional): Whether agent is restricted
            suspended (Optional[bool], optional): Whether user is suspended
            shared (Optional[bool], optional): Whether user is shared
            shared_agent (Optional[bool], optional): Whether agent is shared
            only_private_comments (Optional[bool], optional): Whether user can only make private comments
            default_group_id (Optional[int], optional): Default group ID
            photo (Optional[Dict[str, Any]], optional): Photo attachment

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}.json"

            if name is not None:
                _data["name"] = name
            if email is not None:
                _data["email"] = email
            if role is not None:
                _data["role"] = role
            if custom_role_id is not None:
                _data["custom_role_id"] = custom_role_id
            if external_id is not None:
                _data["external_id"] = external_id
            if alias is not None:
                _data["alias"] = alias
            if details is not None:
                _data["details"] = details
            if notes is not None:
                _data["notes"] = notes
            if organization_id is not None:
                _data["organization_id"] = organization_id
            if phone is not None:
                _data["phone"] = phone
            if shared_phone_number is not None:
                _data["shared_phone_number"] = shared_phone_number
            if signature is not None:
                _data["signature"] = signature
            if tags is not None:
                _data["tags"] = tags
            if time_zone is not None:
                _data["time_zone"] = time_zone
            if locale is not None:
                _data["locale"] = locale
            if locale_id is not None:
                _data["locale_id"] = locale_id
            if user_fields is not None:
                _data["user_fields"] = user_fields
            if verified is not None:
                _data["verified"] = verified
            if restricted_agent is not None:
                _data["restricted_agent"] = restricted_agent
            if suspended is not None:
                _data["suspended"] = suspended
            if shared is not None:
                _data["shared"] = shared
            if shared_agent is not None:
                _data["shared_agent"] = shared_agent
            if only_private_comments is not None:
                _data["only_private_comments"] = only_private_comments
            if default_group_id is not None:
                _data["default_group_id"] = default_group_id
            if photo is not None:
                _data["photo"] = photo

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_user(
        self,
        user_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a user

        Args:
            user_id (int, required): ID of the user to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def search_users(
        self,
        query: str,
        external_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Search for users

        Args:
            query (str, required): Search query
            external_id (Optional[str], optional): External ID to search for

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/search.json"

            _params["query"] = query
            if external_id is not None:
                _params["external_id"] = external_id

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def autocomplete_users(
        self,
        name: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Autocomplete user names

        Args:
            name (str, required): Partial name to autocomplete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/autocomplete.json"

            _params["name"] = name

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_current_user(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show the current authenticated user

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/me.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_many_users(
        self,
        users: list[dict[str, Any]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create multiple users (up to 100)

        Args:
            users (List[Dict[str, Any]], required): Array of user objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/create_many.json"

            _data["users"] = users

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_many_users(
        self,
        users: list[dict[str, Any]],
        ids: str | None = None,
        external_ids: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update multiple users

        Args:
            users (List[Dict[str, Any]], required): Array of user update objects
            ids (Optional[str], optional): Comma-separated user IDs
            external_ids (Optional[str], optional): Comma-separated external IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/update_many.json"

            if ids is not None:
                _params["ids"] = ids
            if external_ids is not None:
                _params["external_ids"] = external_ids

            _data["users"] = users

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_or_update_many_users(
        self,
        users: list[dict[str, Any]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create or update multiple users

        Args:
            users (List[Dict[str, Any]], required): Array of user objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/create_or_update_many.json"

            _data["users"] = users

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def destroy_many_users(
        self,
        ids: str,
        external_ids: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete multiple users

        Args:
            ids (str, required): Comma-separated user IDs
            external_ids (Optional[str], optional): Comma-separated external IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/destroy_many.json"

            _params["ids"] = ids
            if external_ids is not None:
                _params["external_ids"] = external_ids

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_multiple_users(
        self,
        ids: str,
        external_ids: str | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show details of multiple users

        Args:
            ids (str, required): Comma-separated user IDs
            external_ids (Optional[str], optional): Comma-separated external IDs
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/show_many.json"

            _params["ids"] = ids
            if external_ids is not None:
                _params["external_ids"] = external_ids
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_user_related_information(
        self,
        user_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show user related ticket and organization information

        Args:
            user_id (int, required): ID of the user

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/related.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def set_user_password(
        self,
        user_id: int,
        password: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Set user password

        Args:
            user_id (int, required): ID of the user
            password (str, required): New password

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/password.json"

            _data["password"] = password

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def change_user_password(
        self,
        user_id: int,
        previous_password: str,
        password: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Change user password

        Args:
            user_id (int, required): ID of the user
            previous_password (str, required): Current password
            password (str, required): New password

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/password.json"

            _data["previous_password"] = previous_password
            _data["password"] = password

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_user_identities(
        self,
        user_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List user identities

        Args:
            user_id (int, required): ID of the user

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_user_identity(
        self,
        user_id: int,
        identity_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific user identity

        Args:
            user_id (int, required): ID of the user
            identity_id (int, required): ID of the identity

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities/{identity_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_user_identity(
        self,
        user_id: int,
        type: Literal[
            "email",
            "twitter",
            "facebook",
            "google",
            "phone_number",
            "agent_forwarding",
            "sdk",
        ],
        value: str,
        verified: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a user identity

        Args:
            user_id (int, required): ID of the user
            type (Literal["email", "twitter", "facebook", "google", "phone_number", "agent_forwarding", "sdk"], required): Type of identity
            value (str, required): Identity value (email, phone, etc.)
            verified (Optional[bool], optional): Whether identity is verified

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities.json"

            _data["type"] = type
            _data["value"] = value
            if verified is not None:
                _data["verified"] = verified

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_user_identity(
        self,
        user_id: int,
        identity_id: int,
        verified: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update a user identity

        Args:
            user_id (int, required): ID of the user
            identity_id (int, required): ID of the identity
            verified (Optional[bool], optional): Whether identity is verified

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities/{identity_id}.json"

            if verified is not None:
                _data["verified"] = verified

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def make_user_identity_primary(
        self,
        user_id: int,
        identity_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Make an identity the primary identity

        Args:
            user_id (int, required): ID of the user
            identity_id (int, required): ID of the identity

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities/{identity_id}/make_primary.json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def verify_user_identity(
        self,
        user_id: int,
        identity_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Verify a user identity

        Args:
            user_id (int, required): ID of the user
            identity_id (int, required): ID of the identity

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = (
                f"{self.base_url}/users/{user_id}/identities/{identity_id}/verify.json"
            )

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def request_user_verification(
        self,
        user_id: int,
        identity_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Request identity verification

        Args:
            user_id (int, required): ID of the user
            identity_id (int, required): ID of the identity

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities/{identity_id}/request_verification.json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_user_identity(
        self,
        user_id: int,
        identity_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a user identity

        Args:
            user_id (int, required): ID of the user
            identity_id (int, required): ID of the identity

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/identities/{identity_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_organizations(
        self,
        external_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all organizations

        Args:
            external_id (Optional[str], optional): Filter by external ID

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations.json"

            if external_id is not None:
                _params["external_id"] = external_id

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_organization(
        self,
        organization_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific organization

        Args:
            organization_id (int, required): ID of the organization

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/{organization_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_organization(
        self,
        name: str,
        details: str | None = None,
        notes: str | None = None,
        external_id: str | None = None,
        domain_names: list[str] | None = None,
        tags: list[str] | None = None,
        shared_tickets: bool | None = None,
        shared_comments: bool | None = None,
        group_id: int | None = None,
        organization_fields: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new organization

        Args:
            name (str, required): Organization name
            details (Optional[str], optional): Organization details
            notes (Optional[str], optional): Notes about the organization
            external_id (Optional[str], optional): External ID
            domain_names (Optional[List[str]], optional): List of domain names
            tags (Optional[List[str]], optional): List of tags
            shared_tickets (Optional[bool], optional): Whether tickets are shared
            shared_comments (Optional[bool], optional): Whether comments are shared
            group_id (Optional[int], optional): Default group ID
            organization_fields (Optional[Dict[str, Any]], optional): Custom organization fields

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations.json"

            _data["name"] = name
            if details is not None:
                _data["details"] = details
            if notes is not None:
                _data["notes"] = notes
            if external_id is not None:
                _data["external_id"] = external_id
            if domain_names is not None:
                _data["domain_names"] = domain_names
            if tags is not None:
                _data["tags"] = tags
            if shared_tickets is not None:
                _data["shared_tickets"] = shared_tickets
            if shared_comments is not None:
                _data["shared_comments"] = shared_comments
            if group_id is not None:
                _data["group_id"] = group_id
            if organization_fields is not None:
                _data["organization_fields"] = organization_fields

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_organization(
        self,
        organization_id: int,
        name: str | None = None,
        details: str | None = None,
        notes: str | None = None,
        external_id: str | None = None,
        domain_names: list[str] | None = None,
        tags: list[str] | None = None,
        shared_tickets: bool | None = None,
        shared_comments: bool | None = None,
        group_id: int | None = None,
        organization_fields: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing organization

        Args:
            organization_id (int, required): ID of the organization to update
            name (Optional[str], optional): Organization name
            details (Optional[str], optional): Organization details
            notes (Optional[str], optional): Notes about the organization
            external_id (Optional[str], optional): External ID
            domain_names (Optional[List[str]], optional): List of domain names
            tags (Optional[List[str]], optional): List of tags
            shared_tickets (Optional[bool], optional): Whether tickets are shared
            shared_comments (Optional[bool], optional): Whether comments are shared
            group_id (Optional[int], optional): Default group ID
            organization_fields (Optional[Dict[str, Any]], optional): Custom organization fields

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/{organization_id}.json"

            if name is not None:
                _data["name"] = name
            if details is not None:
                _data["details"] = details
            if notes is not None:
                _data["notes"] = notes
            if external_id is not None:
                _data["external_id"] = external_id
            if domain_names is not None:
                _data["domain_names"] = domain_names
            if tags is not None:
                _data["tags"] = tags
            if shared_tickets is not None:
                _data["shared_tickets"] = shared_tickets
            if shared_comments is not None:
                _data["shared_comments"] = shared_comments
            if group_id is not None:
                _data["group_id"] = group_id
            if organization_fields is not None:
                _data["organization_fields"] = organization_fields

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_organization(
        self,
        organization_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete an organization

        Args:
            organization_id (int, required): ID of the organization to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/{organization_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def search_organizations(
        self,
        external_id: str | None = None,
        name: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Search for organizations

        Args:
            external_id (Optional[str], optional): External ID to search for
            name (Optional[str], optional): Organization name to search for

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/search.json"

            if external_id is not None:
                _params["external_id"] = external_id
            if name is not None:
                _params["name"] = name

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def autocomplete_organizations(
        self,
        name: str,
        field_id: str | None = None,
        source: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Autocomplete organization names

        Args:
            name (str, required): Partial name to autocomplete
            field_id (Optional[str], optional): Field ID for custom field autocomplete
            source (Optional[str], optional): Source for the autocomplete request

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/autocomplete.json"

            _params["name"] = name
            if field_id is not None:
                _params["field_id"] = field_id
            if source is not None:
                _params["source"] = source

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_many_organizations(
        self,
        organizations: list[dict[str, Any]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create multiple organizations (up to 100)

        Args:
            organizations (List[Dict[str, Any]], required): Array of organization objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/create_many.json"

            _data["organizations"] = organizations

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_many_organizations(
        self,
        organizations: list[dict[str, Any]],
        ids: str | None = None,
        external_ids: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update multiple organizations

        Args:
            organizations (List[Dict[str, Any]], required): Array of organization update objects
            ids (Optional[str], optional): Comma-separated organization IDs
            external_ids (Optional[str], optional): Comma-separated external IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/update_many.json"

            if ids is not None:
                _params["ids"] = ids
            if external_ids is not None:
                _params["external_ids"] = external_ids

            _data["organizations"] = organizations

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_or_update_many_organizations(
        self,
        organizations: list[dict[str, Any]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create or update multiple organizations

        Args:
            organizations (List[Dict[str, Any]], required): Array of organization objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/create_or_update_many.json"

            _data["organizations"] = organizations

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def destroy_many_organizations(
        self,
        ids: str,
        external_ids: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete multiple organizations

        Args:
            ids (str, required): Comma-separated organization IDs
            external_ids (Optional[str], optional): Comma-separated external IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/destroy_many.json"

            _params["ids"] = ids
            if external_ids is not None:
                _params["external_ids"] = external_ids

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_multiple_organizations(
        self,
        ids: str,
        external_ids: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show details of multiple organizations

        Args:
            ids (str, required): Comma-separated organization IDs
            external_ids (Optional[str], optional): Comma-separated external IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organizations/show_many.json"

            _params["ids"] = ids
            if external_ids is not None:
                _params["external_ids"] = external_ids

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_groups(
        self,
        exclude_deleted: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all groups

        Args:
            exclude_deleted (Optional[bool], optional): Exclude deleted groups

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/groups.json"

            if exclude_deleted is not None:
                _params["exclude_deleted"] = exclude_deleted

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_group(
        self,
        group_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific group

        Args:
            group_id (int, required): ID of the group

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/groups/{group_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_group(
        self,
        name: str,
        description: str | None = None,
        default: bool | None = None,
        is_public: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new group

        Args:
            name (str, required): Group name
            description (Optional[str], optional): Group description
            default (Optional[bool], optional): Whether this is the default group
            is_public (Optional[bool], optional): Whether the group is public

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/groups.json"

            _data["name"] = name
            if description is not None:
                _data["description"] = description
            if default is not None:
                _data["default"] = default
            if is_public is not None:
                _data["is_public"] = is_public

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_group(
        self,
        group_id: int,
        name: str | None = None,
        description: str | None = None,
        default: bool | None = None,
        is_public: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing group

        Args:
            group_id (int, required): ID of the group to update
            name (Optional[str], optional): Group name
            description (Optional[str], optional): Group description
            default (Optional[bool], optional): Whether this is the default group
            is_public (Optional[bool], optional): Whether the group is public

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/groups/{group_id}.json"

            if name is not None:
                _data["name"] = name
            if description is not None:
                _data["description"] = description
            if default is not None:
                _data["default"] = default
            if is_public is not None:
                _data["is_public"] = is_public

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_group(
        self,
        group_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a group

        Args:
            group_id (int, required): ID of the group to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/groups/{group_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_group_memberships(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all group memberships

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/group_memberships.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_group_memberships_by_group(
        self,
        group_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List memberships for a specific group

        Args:
            group_id (int, required): ID of the group

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/groups/{group_id}/memberships.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_group_memberships_by_user(
        self,
        user_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List memberships for a specific user

        Args:
            user_id (int, required): ID of the user

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/users/{user_id}/group_memberships.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_group_membership(
        self,
        membership_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific group membership

        Args:
            membership_id (int, required): ID of the membership

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/group_memberships/{membership_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_group_membership(
        self,
        user_id: int,
        group_id: int,
        default: bool | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new group membership

        Args:
            user_id (int, required): ID of the user
            group_id (int, required): ID of the group
            default (Optional[bool], optional): Whether this is the default group for the user

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/group_memberships.json"

            _data["user_id"] = user_id
            _data["group_id"] = group_id
            if default is not None:
                _data["default"] = default

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_group_membership(
        self,
        membership_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a group membership

        Args:
            membership_id (int, required): ID of the membership to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/group_memberships/{membership_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def destroy_many_group_memberships(
        self,
        ids: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete multiple group memberships

        Args:
            ids (str, required): Comma-separated membership IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/group_memberships/destroy_many.json"

            _params["ids"] = ids

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_many_group_memberships(
        self,
        group_memberships: list[dict[str, Any]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create multiple group memberships

        Args:
            group_memberships (List[Dict[str, Any]], required): Array of membership objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/group_memberships/create_many.json"

            _data["group_memberships"] = group_memberships

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def search(
        self,
        query: str,
        sort_by: str | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Search for tickets, users, organizations, and groups

        Args:
            query (str, required): Search query with syntax like "type:ticket status:open"
            sort_by (Optional[str], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/search.json"

            _params["query"] = query
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def search_export(
        self,
        query: str,
        filter: dict[str, str] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Export search results for large datasets

        Args:
            query (str, required): Search query
            filter (Optional[Dict[str, str]], optional): Additional filters

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/search/export.json"

            _params["query"] = query
            if filter is not None:
                _params["filter"] = filter

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def count_search_results(
        self,
        query: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Get count of search results

        Args:
            query (str, required): Search query

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/search/count.json"

            _params["query"] = query

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_requests(
        self,
        status: str | None = None,
        organization_id: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List requests from the end-user perspective

        Args:
            status (Optional[str], optional): Filter by status
            organization_id (Optional[int], optional): Filter by organization ID

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/requests.json"

            if status is not None:
                _params["status"] = status
            if organization_id is not None:
                _params["organization_id"] = organization_id

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_request(
        self,
        request_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific request

        Args:
            request_id (int, required): ID of the request

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/requests/{request_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_request(
        self,
        subject: str,
        comment: dict[str, Any],
        priority: Literal["urgent", "high", "normal", "low"] | None = None,
        type: Literal["problem", "incident", "question", "task"] | None = None,
        custom_fields: list[dict[str, Any]] | None = None,
        fields: list[dict[str, Any]] | None = None,
        recipient: str | None = None,
        collaborator_ids: list[int] | None = None,
        email_ccs: list[dict[str, str]] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new request (end-user creates ticket)

        Args:
            subject (str, required): Request subject
            comment (Dict[str, Any], required): Initial comment with body and uploads
            priority (Optional[Literal["urgent", "high", "normal", "low"]], optional): Priority level
            type (Optional[Literal["problem", "incident", "question", "task"]], optional): Request type
            custom_fields (Optional[List[Dict[str, Any]]], optional): Custom field values
            fields (Optional[List[Dict[str, Any]]], optional): Ticket field values
            recipient (Optional[str], optional): Recipient email
            collaborator_ids (Optional[List[int]], optional): List of collaborator user IDs
            email_ccs (Optional[List[Dict[str, str]]], optional): List of email CCs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/requests.json"

            _data["subject"] = subject
            _data["comment"] = comment
            if priority is not None:
                _data["priority"] = priority
            if type is not None:
                _data["type"] = type
            if custom_fields is not None:
                _data["custom_fields"] = custom_fields
            if fields is not None:
                _data["fields"] = fields
            if recipient is not None:
                _data["recipient"] = recipient
            if collaborator_ids is not None:
                _data["collaborator_ids"] = collaborator_ids
            if email_ccs is not None:
                _data["email_ccs"] = email_ccs

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_request(
        self,
        request_id: int,
        comment: dict[str, Any] | None = None,
        solved: bool | None = None,
        additional_collaborators: list[dict[str, str]] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update a request (add comment or solve)

        Args:
            request_id (int, required): ID of the request
            comment (Optional[Dict[str, Any]], optional): Comment to add
            solved (Optional[bool], optional): Mark request as solved
            additional_collaborators (Optional[List[Dict[str, str]]], optional): Additional collaborators to add

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/requests/{request_id}.json"

            if comment is not None:
                _data["comment"] = comment
            if solved is not None:
                _data["solved"] = solved
            if additional_collaborators is not None:
                _data["additional_collaborators"] = additional_collaborators

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_request_comments(
        self,
        request_id: int,
        sort_order: Literal["asc", "desc"] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List comments for a request

        Args:
            request_id (int, required): ID of the request
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/requests/{request_id}/comments.json"

            if sort_order is not None:
                _params["sort_order"] = sort_order

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_request_comment(
        self,
        request_id: int,
        comment_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific request comment

        Args:
            request_id (int, required): ID of the request
            comment_id (int, required): ID of the comment

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/requests/{request_id}/comments/{comment_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_automations(
        self,
        active: bool | None = None,
        sort_by: str | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all automations

        Args:
            active (Optional[bool], optional): Filter by active status
            sort_by (Optional[str], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations.json"

            if active is not None:
                _params["active"] = active
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_active_automations(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List active automations

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations/active.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_automation(
        self,
        automation_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific automation

        Args:
            automation_id (int, required): ID of the automation

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations/{automation_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_automation(
        self,
        title: str,
        conditions: dict[str, Any],
        actions: list[dict[str, Any]],
        active: bool | None = None,
        position: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new automation

        Args:
            title (str, required): Automation title
            active (Optional[bool], optional): Whether automation is active
            conditions (Dict[str, Any], required): Automation conditions (all, any)
            actions (List[Dict[str, Any]], required): Actions to perform
            position (Optional[int], optional): Position in automation list

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations.json"

            _data["title"] = title
            if active is not None:
                _data["active"] = active
            _data["conditions"] = conditions
            _data["actions"] = actions
            if position is not None:
                _data["position"] = position

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_automation(
        self,
        automation_id: int,
        title: str | None = None,
        active: bool | None = None,
        conditions: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        position: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing automation

        Args:
            automation_id (int, required): ID of the automation to update
            title (Optional[str], optional): Automation title
            active (Optional[bool], optional): Whether automation is active
            conditions (Optional[Dict[str, Any]], optional): Automation conditions
            actions (Optional[List[Dict[str, Any]]], optional): Actions to perform
            position (Optional[int], optional): Position in automation list

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations/{automation_id}.json"

            if title is not None:
                _data["title"] = title
            if active is not None:
                _data["active"] = active
            if conditions is not None:
                _data["conditions"] = conditions
            if actions is not None:
                _data["actions"] = actions
            if position is not None:
                _data["position"] = position

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_automation(
        self,
        automation_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete an automation

        Args:
            automation_id (int, required): ID of the automation to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations/{automation_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_many_automations(
        self,
        automations: dict[str, list[dict[str, Any]]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update multiple automations

        Args:
            automations (Dict[str, List[Dict[str, Any]]], required): Automation objects to update

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/automations/update_many.json"

            _data["automations"] = automations

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_triggers(
        self,
        active: bool | None = None,
        sort_by: str | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        category_id: str | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all triggers

        Args:
            active (Optional[bool], optional): Filter by active status
            sort_by (Optional[str], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            category_id (Optional[str], optional): Filter by category ID
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers.json"

            if active is not None:
                _params["active"] = active
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if category_id is not None:
                _params["category_id"] = category_id
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_active_triggers(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List active triggers

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/active.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_trigger(
        self,
        trigger_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific trigger

        Args:
            trigger_id (int, required): ID of the trigger

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/{trigger_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_trigger(
        self,
        title: str,
        conditions: dict[str, Any],
        actions: list[dict[str, Any]],
        active: bool | None = None,
        position: int | None = None,
        category_id: str | None = None,
        description: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new trigger

        Args:
            title (str, required): Trigger title
            active (Optional[bool], optional): Whether trigger is active
            conditions (Dict[str, Any], required): Trigger conditions (all, any)
            actions (List[Dict[str, Any]], required): Actions to perform
            position (Optional[int], optional): Position in trigger list
            category_id (Optional[str], optional): Category ID
            description (Optional[str], optional): Trigger description

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers.json"

            _data["title"] = title
            if active is not None:
                _data["active"] = active
            _data["conditions"] = conditions
            _data["actions"] = actions
            if position is not None:
                _data["position"] = position
            if category_id is not None:
                _data["category_id"] = category_id
            if description is not None:
                _data["description"] = description

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_trigger(
        self,
        trigger_id: int,
        title: str | None = None,
        active: bool | None = None,
        conditions: dict[str, Any] | None = None,
        actions: list[dict[str, Any]] | None = None,
        position: int | None = None,
        category_id: str | None = None,
        description: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing trigger

        Args:
            trigger_id (int, required): ID of the trigger to update
            title (Optional[str], optional): Trigger title
            active (Optional[bool], optional): Whether trigger is active
            conditions (Optional[Dict[str, Any]], optional): Trigger conditions
            actions (Optional[List[Dict[str, Any]]], optional): Actions to perform
            position (Optional[int], optional): Position in trigger list
            category_id (Optional[str], optional): Category ID
            description (Optional[str], optional): Trigger description

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/{trigger_id}.json"

            if title is not None:
                _data["title"] = title
            if active is not None:
                _data["active"] = active
            if conditions is not None:
                _data["conditions"] = conditions
            if actions is not None:
                _data["actions"] = actions
            if position is not None:
                _data["position"] = position
            if category_id is not None:
                _data["category_id"] = category_id
            if description is not None:
                _data["description"] = description

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_trigger(
        self,
        trigger_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a trigger

        Args:
            trigger_id (int, required): ID of the trigger to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/{trigger_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def reorder_triggers(
        self,
        trigger_ids: list[int],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Reorder triggers

        Args:
            trigger_ids (List[int], required): Array of trigger IDs in new order

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/reorder.json"

            _data["trigger_ids"] = trigger_ids

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_many_triggers(
        self,
        triggers: dict[str, list[dict[str, Any]]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update multiple triggers

        Args:
            triggers (Dict[str, List[Dict[str, Any]]], required): Trigger objects to update

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/update_many.json"

            _data["triggers"] = triggers

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def destroy_many_triggers(
        self,
        ids: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete multiple triggers

        Args:
            ids (str, required): Comma-separated trigger IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/triggers/destroy_many.json"

            _params["ids"] = ids

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_macros(
        self,
        active: bool | None = None,
        access: Literal["personal", "shared"] | None = None,
        group_id: int | None = None,
        category: int | None = None,
        include: str | None = None,
        only_viewable: bool | None = None,
        sort_by: Literal[
            "alphabetical",
            "created_at",
            "updated_at",
            "usage_1h",
            "usage_24h",
            "usage_7d",
            "usage_30d",
            "position",
        ]
        | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all macros

        Args:
            active (Optional[bool], optional): Filter by active status
            access (Optional[Literal["personal", "shared"]], optional): Filter by access level
            group_id (Optional[int], optional): Filter by group ID
            category (Optional[int], optional): Filter by category ID
            include (Optional[str], optional): Sideload related data (usage_1h,usage_24h,usage_7d,usage_30d)
            only_viewable (Optional[bool], optional): Only show viewable macros
            sort_by (Optional[Literal["alphabetical", "created_at", "updated_at", "usage_1h", "usage_24h", "usage_7d", "usage_30d", "position"]], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros.json"

            if active is not None:
                _params["active"] = active
            if access is not None:
                _params["access"] = access
            if group_id is not None:
                _params["group_id"] = group_id
            if category is not None:
                _params["category"] = category
            if include is not None:
                _params["include"] = include
            if only_viewable is not None:
                _params["only_viewable"] = only_viewable
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_active_macros(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List active macros

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/active.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_macro(
        self,
        macro_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific macro

        Args:
            macro_id (int, required): ID of the macro

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/{macro_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_macro(
        self,
        title: str,
        actions: list[dict[str, Any]],
        active: bool | None = None,
        description: str | None = None,
        restriction: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new macro

        Args:
            title (str, required): Macro title
            actions (List[Dict[str, Any]], required): Actions to perform
            active (Optional[bool], optional): Whether macro is active
            description (Optional[str], optional): Macro description
            restriction (Optional[Dict[str, Any]], optional): Access restrictions

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros.json"

            _data["title"] = title
            _data["actions"] = actions
            if active is not None:
                _data["active"] = active
            if description is not None:
                _data["description"] = description
            if restriction is not None:
                _data["restriction"] = restriction

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_macro(
        self,
        macro_id: int,
        title: str | None = None,
        actions: list[dict[str, Any]] | None = None,
        active: bool | None = None,
        description: str | None = None,
        restriction: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing macro

        Args:
            macro_id (int, required): ID of the macro to update
            title (Optional[str], optional): Macro title
            actions (Optional[List[Dict[str, Any]]], optional): Actions to perform
            active (Optional[bool], optional): Whether macro is active
            description (Optional[str], optional): Macro description
            restriction (Optional[Dict[str, Any]], optional): Access restrictions

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/{macro_id}.json"

            if title is not None:
                _data["title"] = title
            if actions is not None:
                _data["actions"] = actions
            if active is not None:
                _data["active"] = active
            if description is not None:
                _data["description"] = description
            if restriction is not None:
                _data["restriction"] = restriction

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_macro(
        self,
        macro_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a macro

        Args:
            macro_id (int, required): ID of the macro to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/{macro_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def apply_macro_to_ticket(
        self,
        ticket_id: int,
        macro_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Apply a macro to a ticket

        Args:
            ticket_id (int, required): ID of the ticket
            macro_id (int, required): ID of the macro to apply

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}/macros/{macro_id}/apply.json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_macro_application_result(
        self,
        macro_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show the result of applying a macro

        Args:
            macro_id (int, required): ID of the macro

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/{macro_id}/apply.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_ticket_after_macro(
        self,
        ticket_id: int,
        macro_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show how a ticket would look after applying a macro

        Args:
            ticket_id (int, required): ID of the ticket
            macro_id (int, required): ID of the macro

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tickets/{ticket_id}/macros/{macro_id}/apply.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_many_macros(
        self,
        macros: dict[str, list[dict[str, Any]]],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update multiple macros

        Args:
            macros (Dict[str, List[Dict[str, Any]]], required): Macro objects to update

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/update_many.json"

            _data["macros"] = macros

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def destroy_many_macros(
        self,
        ids: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete multiple macros

        Args:
            ids (str, required): Comma-separated macro IDs

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/macros/destroy_many.json"

            _params["ids"] = ids

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_webhooks(
        self,
        filter: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all webhooks

        Args:
            filter (Optional[str], optional): Filter webhooks

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks.json"

            if filter is not None:
                _params["filter"] = filter

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_webhook(
        self,
        webhook_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific webhook

        Args:
            webhook_id (str, required): ID of the webhook

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks/{webhook_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_webhook(
        self,
        name: str,
        endpoint: str,
        http_method: Literal["POST", "PUT", "PATCH", "GET", "DELETE"],
        request_format: Literal["json", "xml", "form_encoded"],
        status: Literal["active", "inactive"],
        subscriptions: list[str],
        authentication: dict[str, Any] | None = None,
        custom_headers: dict[str, str] | None = None,
        description: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new webhook

        Args:
            name (str, required): Webhook name
            endpoint (str, required): Destination URL
            http_method (Literal["POST", "PUT", "PATCH", "GET", "DELETE"], required): HTTP method
            request_format (Literal["json", "xml", "form_encoded"], required): Request format
            status (Literal["active", "inactive"], required): Webhook status
            subscriptions (List[str], required): Event subscriptions
            authentication (Optional[Dict[str, Any]], optional): Authentication settings
            custom_headers (Optional[Dict[str, str]], optional): Custom headers
            description (Optional[str], optional): Webhook description

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks.json"

            _data["name"] = name
            _data["endpoint"] = endpoint
            _data["http_method"] = http_method
            _data["request_format"] = request_format
            _data["status"] = status
            _data["subscriptions"] = subscriptions
            if authentication is not None:
                _data["authentication"] = authentication
            if custom_headers is not None:
                _data["custom_headers"] = custom_headers
            if description is not None:
                _data["description"] = description

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_webhook(
        self,
        webhook_id: str,
        name: str | None = None,
        endpoint: str | None = None,
        http_method: Literal["POST", "PUT", "PATCH", "GET", "DELETE"] | None = None,
        request_format: Literal["json", "xml", "form_encoded"] | None = None,
        status: Literal["active", "inactive"] | None = None,
        subscriptions: list[str] | None = None,
        authentication: dict[str, Any] | None = None,
        custom_headers: dict[str, str] | None = None,
        description: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing webhook

        Args:
            webhook_id (str, required): ID of the webhook to update
            name (Optional[str], optional): Webhook name
            endpoint (Optional[str], optional): Destination URL
            http_method (Optional[Literal["POST", "PUT", "PATCH", "GET", "DELETE"]], optional): HTTP method
            request_format (Optional[Literal["json", "xml", "form_encoded"]], optional): Request format
            status (Optional[Literal["active", "inactive"]], optional): Webhook status
            subscriptions (Optional[List[str]], optional): Event subscriptions
            authentication (Optional[Dict[str, Any]], optional): Authentication settings
            custom_headers (Optional[Dict[str, str]], optional): Custom headers
            description (Optional[str], optional): Webhook description

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks/{webhook_id}.json"

            if name is not None:
                _data["name"] = name
            if endpoint is not None:
                _data["endpoint"] = endpoint
            if http_method is not None:
                _data["http_method"] = http_method
            if request_format is not None:
                _data["request_format"] = request_format
            if status is not None:
                _data["status"] = status
            if subscriptions is not None:
                _data["subscriptions"] = subscriptions
            if authentication is not None:
                _data["authentication"] = authentication
            if custom_headers is not None:
                _data["custom_headers"] = custom_headers
            if description is not None:
                _data["description"] = description

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_webhook(
        self,
        webhook_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a webhook

        Args:
            webhook_id (str, required): ID of the webhook to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks/{webhook_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def test_webhook(
        self,
        request: dict[str, Any],
        webhook_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Test a webhook configuration

        Args:
            webhook_id (Optional[str], optional): ID of existing webhook to test
            request (Dict[str, Any], required): Test request configuration

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks/test.json"

            if webhook_id is not None:
                _params["webhook_id"] = webhook_id

            _data["request"] = request

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def clone_webhook(
        self,
        clone_webhook_id: str,
        name: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Clone an existing webhook

        Args:
            clone_webhook_id (str, required): ID of webhook to clone
            name (str, required): Name for the cloned webhook

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks.json"

            _params["clone_webhook_id"] = clone_webhook_id

            _data["name"] = name

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_webhook_signing_secret(
        self,
        webhook_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show webhook signing secret

        Args:
            webhook_id (str, required): ID of the webhook

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks/{webhook_id}/signing_secret.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def reset_webhook_signing_secret(
        self,
        webhook_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Reset webhook signing secret

        Args:
            webhook_id (str, required): ID of the webhook

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/webhooks/{webhook_id}/signing_secret/reset.json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def upload_attachment(
        self,
        filename: str,
        file: bytes,
        token: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Upload an attachment

        Args:
            filename (str, required): Name of the file
            token (Optional[str], optional): Upload token for additional files
            file (bytes, required): File content

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/uploads.json"

            _params["filename"] = filename
            if token is not None:
                _params["token"] = token

            _data["file"] = file

            _headers["Content-Type"] = "application/binary"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_attachment(
        self,
        attachment_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show attachment details

        Args:
            attachment_id (int, required): ID of the attachment

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/attachments/{attachment_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_upload(
        self,
        upload_token: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete an uploaded file before it is used

        Args:
            upload_token (str, required): Upload token from upload response

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/uploads/{upload_token}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_ticket_fields(
        self,
        locale: str | None = None,
        creator_id: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all ticket fields

        Args:
            locale (Optional[str], optional): Locale for field names
            creator_id (Optional[int], optional): Filter by creator ID

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/ticket_fields.json"

            if locale is not None:
                _params["locale"] = locale
            if creator_id is not None:
                _params["creator_id"] = creator_id

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_ticket_field(
        self,
        field_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific ticket field

        Args:
            field_id (int, required): ID of the ticket field

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/ticket_fields/{field_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_ticket_field(
        self,
        type: Literal[
            "text",
            "textarea",
            "checkbox",
            "date",
            "integer",
            "decimal",
            "regexp",
            "partialcreditcard",
            "multiselect",
            "tagger",
            "subject",
            "description",
            "status",
            "priority",
            "group",
            "assignee",
            "custom_status",
            "tickettype",
        ],
        title: str,
        description: str | None = None,
        position: int | None = None,
        active: bool | None = None,
        required: bool | None = None,
        collapsed_for_agents: bool | None = None,
        regexp_for_validation: str | None = None,
        title_in_portal: str | None = None,
        visible_in_portal: bool | None = None,
        editable_in_portal: bool | None = None,
        required_in_portal: bool | None = None,
        tag: str | None = None,
        custom_field_options: list[dict[str, Any]] | None = None,
        system_field_options: list[dict[str, Any]] | None = None,
        sub_type_id: int | None = None,
        removable: bool | None = None,
        agent_description: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new ticket field

        Args:
            type (Literal["text", "textarea", "checkbox", "date", "integer", "decimal", "regexp", "partialcreditcard", "multiselect", "tagger", "subject", "description", "status", "priority", "group", "assignee", "custom_status", "tickettype"], required): Field type
            title (str, required): Field title
            description (Optional[str], optional): Field description
            position (Optional[int], optional): Field position
            active (Optional[bool], optional): Whether field is active
            required (Optional[bool], optional): Whether field is required
            collapsed_for_agents (Optional[bool], optional): Whether field is collapsed for agents
            regexp_for_validation (Optional[str], optional): Regular expression for validation
            title_in_portal (Optional[str], optional): Title shown in help center
            visible_in_portal (Optional[bool], optional): Whether field is visible in help center
            editable_in_portal (Optional[bool], optional): Whether field is editable in help center
            required_in_portal (Optional[bool], optional): Whether field is required in help center
            tag (Optional[str], optional): Tag for tagger field type
            custom_field_options (Optional[List[Dict[str, Any]]], optional): Options for dropdown/multiselect fields
            system_field_options (Optional[List[Dict[str, Any]]], optional): Options for system fields
            sub_type_id (Optional[int], optional): Sub-type ID for lookup fields
            removable (Optional[bool], optional): Whether field can be removed
            agent_description (Optional[str], optional): Description shown to agents

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/ticket_fields.json"

            _data["type"] = type
            _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if active is not None:
                _data["active"] = active
            if required is not None:
                _data["required"] = required
            if collapsed_for_agents is not None:
                _data["collapsed_for_agents"] = collapsed_for_agents
            if regexp_for_validation is not None:
                _data["regexp_for_validation"] = regexp_for_validation
            if title_in_portal is not None:
                _data["title_in_portal"] = title_in_portal
            if visible_in_portal is not None:
                _data["visible_in_portal"] = visible_in_portal
            if editable_in_portal is not None:
                _data["editable_in_portal"] = editable_in_portal
            if required_in_portal is not None:
                _data["required_in_portal"] = required_in_portal
            if tag is not None:
                _data["tag"] = tag
            if custom_field_options is not None:
                _data["custom_field_options"] = custom_field_options
            if system_field_options is not None:
                _data["system_field_options"] = system_field_options
            if sub_type_id is not None:
                _data["sub_type_id"] = sub_type_id
            if removable is not None:
                _data["removable"] = removable
            if agent_description is not None:
                _data["agent_description"] = agent_description

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_ticket_field(
        self,
        field_id: int,
        title: str | None = None,
        description: str | None = None,
        position: int | None = None,
        active: bool | None = None,
        required: bool | None = None,
        collapsed_for_agents: bool | None = None,
        regexp_for_validation: str | None = None,
        title_in_portal: str | None = None,
        visible_in_portal: bool | None = None,
        editable_in_portal: bool | None = None,
        required_in_portal: bool | None = None,
        tag: str | None = None,
        custom_field_options: list[dict[str, Any]] | None = None,
        system_field_options: list[dict[str, Any]] | None = None,
        sub_type_id: int | None = None,
        removable: bool | None = None,
        agent_description: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing ticket field

        Args:
            field_id (int, required): ID of the field to update
            title (Optional[str], optional): Field title
            description (Optional[str], optional): Field description
            position (Optional[int], optional): Field position
            active (Optional[bool], optional): Whether field is active
            required (Optional[bool], optional): Whether field is required
            collapsed_for_agents (Optional[bool], optional): Whether field is collapsed for agents
            regexp_for_validation (Optional[str], optional): Regular expression for validation
            title_in_portal (Optional[str], optional): Title shown in help center
            visible_in_portal (Optional[bool], optional): Whether field is visible in help center
            editable_in_portal (Optional[bool], optional): Whether field is editable in help center
            required_in_portal (Optional[bool], optional): Whether field is required in help center
            tag (Optional[str], optional): Tag for tagger field type
            custom_field_options (Optional[List[Dict[str, Any]]], optional): Options for dropdown/multiselect fields
            system_field_options (Optional[List[Dict[str, Any]]], optional): Options for system fields
            sub_type_id (Optional[int], optional): Sub-type ID for lookup fields
            removable (Optional[bool], optional): Whether field can be removed
            agent_description (Optional[str], optional): Description shown to agents

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/ticket_fields/{field_id}.json"

            if title is not None:
                _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if active is not None:
                _data["active"] = active
            if required is not None:
                _data["required"] = required
            if collapsed_for_agents is not None:
                _data["collapsed_for_agents"] = collapsed_for_agents
            if regexp_for_validation is not None:
                _data["regexp_for_validation"] = regexp_for_validation
            if title_in_portal is not None:
                _data["title_in_portal"] = title_in_portal
            if visible_in_portal is not None:
                _data["visible_in_portal"] = visible_in_portal
            if editable_in_portal is not None:
                _data["editable_in_portal"] = editable_in_portal
            if required_in_portal is not None:
                _data["required_in_portal"] = required_in_portal
            if tag is not None:
                _data["tag"] = tag
            if custom_field_options is not None:
                _data["custom_field_options"] = custom_field_options
            if system_field_options is not None:
                _data["system_field_options"] = system_field_options
            if sub_type_id is not None:
                _data["sub_type_id"] = sub_type_id
            if removable is not None:
                _data["removable"] = removable
            if agent_description is not None:
                _data["agent_description"] = agent_description

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_ticket_field(
        self,
        field_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a ticket field

        Args:
            field_id (int, required): ID of the field to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/ticket_fields/{field_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_user_fields(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all user fields

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/user_fields.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_user_field(
        self,
        field_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific user field

        Args:
            field_id (int, required): ID of the user field

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/user_fields/{field_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_user_field(
        self,
        type: Literal[
            "text",
            "textarea",
            "checkbox",
            "date",
            "integer",
            "decimal",
            "regexp",
            "dropdown",
            "tagger",
            "multiselect",
            "lookup",
        ],
        key: str,
        title: str,
        description: str | None = None,
        position: int | None = None,
        active: bool | None = None,
        required: bool | None = None,
        collapsed_for_agents: bool | None = None,
        regexp_for_validation: str | None = None,
        custom_field_options: list[dict[str, Any]] | None = None,
        tag: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new user field

        Args:
            type (Literal["text", "textarea", "checkbox", "date", "integer", "decimal", "regexp", "dropdown", "tagger", "multiselect", "lookup"], required): Field type
            key (str, required): Field key (unique identifier)
            title (str, required): Field title
            description (Optional[str], optional): Field description
            position (Optional[int], optional): Field position
            active (Optional[bool], optional): Whether field is active
            required (Optional[bool], optional): Whether field is required
            collapsed_for_agents (Optional[bool], optional): Whether field is collapsed for agents
            regexp_for_validation (Optional[str], optional): Regular expression for validation
            custom_field_options (Optional[List[Dict[str, Any]]], optional): Options for dropdown/multiselect fields
            tag (Optional[str], optional): Tag for tagger field type

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/user_fields.json"

            _data["type"] = type
            _data["key"] = key
            _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if active is not None:
                _data["active"] = active
            if required is not None:
                _data["required"] = required
            if collapsed_for_agents is not None:
                _data["collapsed_for_agents"] = collapsed_for_agents
            if regexp_for_validation is not None:
                _data["regexp_for_validation"] = regexp_for_validation
            if custom_field_options is not None:
                _data["custom_field_options"] = custom_field_options
            if tag is not None:
                _data["tag"] = tag

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_user_field(
        self,
        field_id: int,
        title: str | None = None,
        description: str | None = None,
        position: int | None = None,
        active: bool | None = None,
        required: bool | None = None,
        collapsed_for_agents: bool | None = None,
        regexp_for_validation: str | None = None,
        custom_field_options: list[dict[str, Any]] | None = None,
        tag: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing user field

        Args:
            field_id (int, required): ID of the field to update
            title (Optional[str], optional): Field title
            description (Optional[str], optional): Field description
            position (Optional[int], optional): Field position
            active (Optional[bool], optional): Whether field is active
            required (Optional[bool], optional): Whether field is required
            collapsed_for_agents (Optional[bool], optional): Whether field is collapsed for agents
            regexp_for_validation (Optional[str], optional): Regular expression for validation
            custom_field_options (Optional[List[Dict[str, Any]]], optional): Options for dropdown/multiselect fields
            tag (Optional[str], optional): Tag for tagger field type

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/user_fields/{field_id}.json"

            if title is not None:
                _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if active is not None:
                _data["active"] = active
            if required is not None:
                _data["required"] = required
            if collapsed_for_agents is not None:
                _data["collapsed_for_agents"] = collapsed_for_agents
            if regexp_for_validation is not None:
                _data["regexp_for_validation"] = regexp_for_validation
            if custom_field_options is not None:
                _data["custom_field_options"] = custom_field_options
            if tag is not None:
                _data["tag"] = tag

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_user_field(
        self,
        field_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a user field

        Args:
            field_id (int, required): ID of the field to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/user_fields/{field_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_organization_fields(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all organization fields

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organization_fields.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_organization_field(
        self,
        field_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific organization field

        Args:
            field_id (int, required): ID of the organization field

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organization_fields/{field_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_organization_field(
        self,
        type: Literal[
            "text",
            "textarea",
            "checkbox",
            "date",
            "integer",
            "decimal",
            "regexp",
            "dropdown",
            "tagger",
            "multiselect",
            "lookup",
        ],
        key: str,
        title: str,
        description: str | None = None,
        position: int | None = None,
        active: bool | None = None,
        required: bool | None = None,
        collapsed_for_agents: bool | None = None,
        regexp_for_validation: str | None = None,
        custom_field_options: list[dict[str, Any]] | None = None,
        tag: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new organization field

        Args:
            type (Literal["text", "textarea", "checkbox", "date", "integer", "decimal", "regexp", "dropdown", "tagger", "multiselect", "lookup"], required): Field type
            key (str, required): Field key (unique identifier)
            title (str, required): Field title
            description (Optional[str], optional): Field description
            position (Optional[int], optional): Field position
            active (Optional[bool], optional): Whether field is active
            required (Optional[bool], optional): Whether field is required
            collapsed_for_agents (Optional[bool], optional): Whether field is collapsed for agents
            regexp_for_validation (Optional[str], optional): Regular expression for validation
            custom_field_options (Optional[List[Dict[str, Any]]], optional): Options for dropdown/multiselect fields
            tag (Optional[str], optional): Tag for tagger field type

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organization_fields.json"

            _data["type"] = type
            _data["key"] = key
            _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if active is not None:
                _data["active"] = active
            if required is not None:
                _data["required"] = required
            if collapsed_for_agents is not None:
                _data["collapsed_for_agents"] = collapsed_for_agents
            if regexp_for_validation is not None:
                _data["regexp_for_validation"] = regexp_for_validation
            if custom_field_options is not None:
                _data["custom_field_options"] = custom_field_options
            if tag is not None:
                _data["tag"] = tag

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_organization_field(
        self,
        field_id: int,
        title: str | None = None,
        description: str | None = None,
        position: int | None = None,
        active: bool | None = None,
        required: bool | None = None,
        collapsed_for_agents: bool | None = None,
        regexp_for_validation: str | None = None,
        custom_field_options: list[dict[str, Any]] | None = None,
        tag: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing organization field

        Args:
            field_id (int, required): ID of the field to update
            title (Optional[str], optional): Field title
            description (Optional[str], optional): Field description
            position (Optional[int], optional): Field position
            active (Optional[bool], optional): Whether field is active
            required (Optional[bool], optional): Whether field is required
            collapsed_for_agents (Optional[bool], optional): Whether field is collapsed for agents
            regexp_for_validation (Optional[str], optional): Regular expression for validation
            custom_field_options (Optional[List[Dict[str, Any]]], optional): Options for dropdown/multiselect fields
            tag (Optional[str], optional): Tag for tagger field type

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organization_fields/{field_id}.json"

            if title is not None:
                _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if active is not None:
                _data["active"] = active
            if required is not None:
                _data["required"] = required
            if collapsed_for_agents is not None:
                _data["collapsed_for_agents"] = collapsed_for_agents
            if regexp_for_validation is not None:
                _data["regexp_for_validation"] = regexp_for_validation
            if custom_field_options is not None:
                _data["custom_field_options"] = custom_field_options
            if tag is not None:
                _data["tag"] = tag

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_organization_field(
        self,
        field_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete an organization field

        Args:
            field_id (int, required): ID of the field to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/organization_fields/{field_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_tags(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all tags

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tags.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_tags_count(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show count of all tags

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/tags/count.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def autocomplete_tags(
        self,
        name: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Autocomplete tag names

        Args:
            name (str, required): Partial tag name to autocomplete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/autocomplete/tags.json"

            _params["name"] = name

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_views(
        self,
        active: bool | None = None,
        access: Literal["personal", "shared"] | None = None,
        group_id: int | None = None,
        sort_by: str | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all views

        Args:
            active (Optional[bool], optional): Filter by active status
            access (Optional[Literal["personal", "shared"]], optional): Filter by access level
            group_id (Optional[int], optional): Filter by group ID
            sort_by (Optional[str], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views.json"

            if active is not None:
                _params["active"] = active
            if access is not None:
                _params["access"] = access
            if group_id is not None:
                _params["group_id"] = group_id
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_active_views(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List active views

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/active.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_compact_views(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List views in compact format

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/compact.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_view(
        self,
        view_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific view

        Args:
            view_id (int, required): ID of the view

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_view(
        self,
        title: str,
        conditions: dict[str, Any],
        execution: dict[str, Any],
        active: bool | None = None,
        restriction: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new view

        Args:
            title (str, required): View title
            active (Optional[bool], optional): Whether view is active
            conditions (Dict[str, Any], required): View conditions (all, any)
            execution (Dict[str, Any], required): Execution settings (group_by, sort_by, columns, fields)
            restriction (Optional[Dict[str, Any]], optional): Access restrictions

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views.json"

            _data["title"] = title
            if active is not None:
                _data["active"] = active
            _data["conditions"] = conditions
            _data["execution"] = execution
            if restriction is not None:
                _data["restriction"] = restriction

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_view(
        self,
        view_id: int,
        title: str | None = None,
        active: bool | None = None,
        conditions: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        restriction: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing view

        Args:
            view_id (int, required): ID of the view to update
            title (Optional[str], optional): View title
            active (Optional[bool], optional): Whether view is active
            conditions (Optional[Dict[str, Any]], optional): View conditions
            execution (Optional[Dict[str, Any]], optional): Execution settings
            restriction (Optional[Dict[str, Any]], optional): Access restrictions

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}.json"

            if title is not None:
                _data["title"] = title
            if active is not None:
                _data["active"] = active
            if conditions is not None:
                _data["conditions"] = conditions
            if execution is not None:
                _data["execution"] = execution
            if restriction is not None:
                _data["restriction"] = restriction

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_view(
        self,
        view_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a view

        Args:
            view_id (int, required): ID of the view to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def execute_view(
        self,
        view_id: int,
        sort_by: str | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Execute a view to get tickets

        Args:
            view_id (int, required): ID of the view to execute
            sort_by (Optional[str], optional): Override sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Override sort direction
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}/execute.json"

            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def get_view_tickets(
        self,
        view_id: int,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Get tickets from a view

        Args:
            view_id (int, required): ID of the view
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}/tickets.json"

            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def count_view_tickets(
        self,
        view_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Count tickets in a view

        Args:
            view_id (int, required): ID of the view

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}/count.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def export_view(
        self,
        view_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Export view results

        Args:
            view_id (int, required): ID of the view to export

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/views/{view_id}/export.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_articles(
        self,
        locale: str | None = None,
        category: int | None = None,
        section: int | None = None,
        label_names: str | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        sort_by: Literal[
            "created_at", "updated_at", "position", "title", "vote_sum", "vote_count"
        ]
        | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all articles

        Args:
            locale (Optional[str], optional): Filter by locale
            category (Optional[int], optional): Filter by category ID
            section (Optional[int], optional): Filter by section ID
            label_names (Optional[str], optional): Filter by label names (comma-separated)
            created_at (Optional[str], optional): Filter by creation date
            updated_at (Optional[str], optional): Filter by update date
            sort_by (Optional[Literal["created_at", "updated_at", "position", "title", "vote_sum", "vote_count"]], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            include (Optional[str], optional): Sideload related data (sections,categories,users,translations)

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/articles.json"

            if locale is not None:
                _params["locale"] = locale
            if category is not None:
                _params["category"] = category
            if section is not None:
                _params["section"] = section
            if label_names is not None:
                _params["label_names"] = label_names
            if created_at is not None:
                _params["created_at"] = created_at
            if updated_at is not None:
                _params["updated_at"] = updated_at
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_article(
        self,
        article_id: int,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific article

        Args:
            article_id (int, required): ID of the article
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/articles/{article_id}.json"

            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_article(
        self,
        section_id: int,
        title: str,
        body: str,
        locale: str,
        author_id: int | None = None,
        comments_disabled: bool | None = None,
        draft: bool | None = None,
        promoted: bool | None = None,
        position: int | None = None,
        label_names: list[str] | None = None,
        user_segment_id: int | None = None,
        permission_group_id: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new article

        Args:
            section_id (int, required): ID of the section
            title (str, required): Article title
            body (str, required): Article content
            locale (str, required): Article locale
            author_id (Optional[int], optional): Author user ID
            comments_disabled (Optional[bool], optional): Whether comments are disabled
            draft (Optional[bool], optional): Whether article is a draft
            promoted (Optional[bool], optional): Whether article is promoted
            position (Optional[int], optional): Article position
            label_names (Optional[List[str]], optional): Label names
            user_segment_id (Optional[int], optional): User segment ID
            permission_group_id (Optional[int], optional): Permission group ID

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/sections/{section_id}/articles.json"

            _data["title"] = title
            _data["body"] = body
            _data["locale"] = locale
            if author_id is not None:
                _data["author_id"] = author_id
            if comments_disabled is not None:
                _data["comments_disabled"] = comments_disabled
            if draft is not None:
                _data["draft"] = draft
            if promoted is not None:
                _data["promoted"] = promoted
            if position is not None:
                _data["position"] = position
            if label_names is not None:
                _data["label_names"] = label_names
            if user_segment_id is not None:
                _data["user_segment_id"] = user_segment_id
            if permission_group_id is not None:
                _data["permission_group_id"] = permission_group_id

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_article(
        self,
        article_id: int,
        title: str | None = None,
        body: str | None = None,
        author_id: int | None = None,
        comments_disabled: bool | None = None,
        draft: bool | None = None,
        promoted: bool | None = None,
        position: int | None = None,
        section_id: int | None = None,
        label_names: list[str] | None = None,
        user_segment_id: int | None = None,
        permission_group_id: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing article

        Args:
            article_id (int, required): ID of the article to update
            title (Optional[str], optional): Article title
            body (Optional[str], optional): Article content
            author_id (Optional[int], optional): Author user ID
            comments_disabled (Optional[bool], optional): Whether comments are disabled
            draft (Optional[bool], optional): Whether article is a draft
            promoted (Optional[bool], optional): Whether article is promoted
            position (Optional[int], optional): Article position
            section_id (Optional[int], optional): Section ID
            label_names (Optional[List[str]], optional): Label names
            user_segment_id (Optional[int], optional): User segment ID
            permission_group_id (Optional[int], optional): Permission group ID

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/articles/{article_id}.json"

            if title is not None:
                _data["title"] = title
            if body is not None:
                _data["body"] = body
            if author_id is not None:
                _data["author_id"] = author_id
            if comments_disabled is not None:
                _data["comments_disabled"] = comments_disabled
            if draft is not None:
                _data["draft"] = draft
            if promoted is not None:
                _data["promoted"] = promoted
            if position is not None:
                _data["position"] = position
            if section_id is not None:
                _data["section_id"] = section_id
            if label_names is not None:
                _data["label_names"] = label_names
            if user_segment_id is not None:
                _data["user_segment_id"] = user_segment_id
            if permission_group_id is not None:
                _data["permission_group_id"] = permission_group_id

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_article(
        self,
        article_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete an article

        Args:
            article_id (int, required): ID of the article to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/articles/{article_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def search_articles(
        self,
        query: str,
        locale: str | None = None,
        category: int | None = None,
        section: int | None = None,
        label_names: str | None = None,
        snippet: Literal["true", "false"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Search articles

        Args:
            query (str, required): Search query
            locale (Optional[str], optional): Filter by locale
            category (Optional[int], optional): Filter by category ID
            section (Optional[int], optional): Filter by section ID
            label_names (Optional[str], optional): Filter by label names
            snippet (Optional[Literal["true", "false"]], optional): Include snippets in results
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/articles/search.json"

            _params["query"] = query
            if locale is not None:
                _params["locale"] = locale
            if category is not None:
                _params["category"] = category
            if section is not None:
                _params["section"] = section
            if label_names is not None:
                _params["label_names"] = label_names
            if snippet is not None:
                _params["snippet"] = snippet
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_sections(
        self,
        category_id: int | None = None,
        locale: str | None = None,
        sort_by: Literal["position", "created_at", "updated_at", "name"] | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all sections

        Args:
            category_id (Optional[int], optional): Filter by category ID
            locale (Optional[str], optional): Filter by locale
            sort_by (Optional[Literal["position", "created_at", "updated_at", "name"]], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            include (Optional[str], optional): Sideload related data (categories,translations)

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/sections.json"

            if category_id is not None:
                _params["category_id"] = category_id
            if locale is not None:
                _params["locale"] = locale
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_section(
        self,
        section_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific section

        Args:
            section_id (int, required): ID of the section

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/sections/{section_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_section(
        self,
        category_id: int,
        name: str,
        locale: str,
        description: str | None = None,
        position: int | None = None,
        sorting: Literal["manual", "created_at", "updated_at", "name", "title"]
        | None = None,
        theme_template: str | None = None,
        manageable_by: Literal["managers", "managers_and_agents"] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new section

        Args:
            category_id (int, required): ID of the category
            name (str, required): Section name
            description (Optional[str], optional): Section description
            locale (str, required): Section locale
            position (Optional[int], optional): Section position
            sorting (Optional[Literal["manual", "created_at", "updated_at", "name", "title"]], optional): Article sorting method
            theme_template (Optional[str], optional): Theme template
            manageable_by (Optional[Literal["managers", "managers_and_agents"]], optional): Who can manage this section

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/categories/{category_id}/sections.json"

            _data["name"] = name
            if description is not None:
                _data["description"] = description
            _data["locale"] = locale
            if position is not None:
                _data["position"] = position
            if sorting is not None:
                _data["sorting"] = sorting
            if theme_template is not None:
                _data["theme_template"] = theme_template
            if manageable_by is not None:
                _data["manageable_by"] = manageable_by

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_section(
        self,
        section_id: int,
        name: str | None = None,
        description: str | None = None,
        position: int | None = None,
        sorting: Literal["manual", "created_at", "updated_at", "name", "title"]
        | None = None,
        category_id: int | None = None,
        theme_template: str | None = None,
        manageable_by: Literal["managers", "managers_and_agents"] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing section

        Args:
            section_id (int, required): ID of the section to update
            name (Optional[str], optional): Section name
            description (Optional[str], optional): Section description
            position (Optional[int], optional): Section position
            sorting (Optional[Literal["manual", "created_at", "updated_at", "name", "title"]], optional): Article sorting method
            category_id (Optional[int], optional): Category ID
            theme_template (Optional[str], optional): Theme template
            manageable_by (Optional[Literal["managers", "managers_and_agents"]], optional): Who can manage this section

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/sections/{section_id}.json"

            if name is not None:
                _data["name"] = name
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if sorting is not None:
                _data["sorting"] = sorting
            if category_id is not None:
                _data["category_id"] = category_id
            if theme_template is not None:
                _data["theme_template"] = theme_template
            if manageable_by is not None:
                _data["manageable_by"] = manageable_by

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_section(
        self,
        section_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a section

        Args:
            section_id (int, required): ID of the section to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/sections/{section_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_categories(
        self,
        locale: str | None = None,
        sort_by: Literal["position", "created_at", "updated_at", "name"] | None = None,
        sort_order: Literal["asc", "desc"] | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all categories

        Args:
            locale (Optional[str], optional): Filter by locale
            sort_by (Optional[Literal["position", "created_at", "updated_at", "name"]], optional): Sort field
            sort_order (Optional[Literal["asc", "desc"]], optional): Sort direction
            include (Optional[str], optional): Sideload related data (translations)

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/categories.json"

            if locale is not None:
                _params["locale"] = locale
            if sort_by is not None:
                _params["sort_by"] = sort_by
            if sort_order is not None:
                _params["sort_order"] = sort_order
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_category(
        self,
        category_id: int,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific category

        Args:
            category_id (int, required): ID of the category
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/categories/{category_id}.json"

            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_category(
        self,
        name: str,
        locale: str,
        description: str | None = None,
        position: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new category

        Args:
            name (str, required): Category name
            description (Optional[str], optional): Category description
            locale (str, required): Category locale
            position (Optional[int], optional): Category position

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/categories.json"

            _data["name"] = name
            if description is not None:
                _data["description"] = description
            _data["locale"] = locale
            if position is not None:
                _data["position"] = position

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_category(
        self,
        category_id: int,
        name: str | None = None,
        description: str | None = None,
        position: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing category

        Args:
            category_id (int, required): ID of the category to update
            name (Optional[str], optional): Category name
            description (Optional[str], optional): Category description
            position (Optional[int], optional): Category position

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/categories/{category_id}.json"

            if name is not None:
                _data["name"] = name
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_category(
        self,
        category_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a category

        Args:
            category_id (int, required): ID of the category to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/help_center/categories/{category_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_custom_objects(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all custom objects

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_custom_object(
        self,
        custom_object_key: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific custom object

        Args:
            custom_object_key (str, required): Key of the custom object

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_custom_object(
        self,
        key: str,
        title: str,
        title_pluralized: str,
        description: str | None = None,
        configuration: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new custom object

        Args:
            key (str, required): Custom object key
            title (str, required): Display title
            title_pluralized (str, required): Pluralized title
            description (Optional[str], optional): Object description
            configuration (Optional[Dict[str, Any]], optional): Configuration settings

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects.json"

            _data["key"] = key
            _data["title"] = title
            _data["title_pluralized"] = title_pluralized
            if description is not None:
                _data["description"] = description
            if configuration is not None:
                _data["configuration"] = configuration

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_custom_object(
        self,
        custom_object_key: str,
        title: str | None = None,
        title_pluralized: str | None = None,
        description: str | None = None,
        configuration: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing custom object

        Args:
            custom_object_key (str, required): Key of the custom object to update
            title (Optional[str], optional): Display title
            title_pluralized (Optional[str], optional): Pluralized title
            description (Optional[str], optional): Object description
            configuration (Optional[Dict[str, Any]], optional): Configuration settings

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}.json"

            if title is not None:
                _data["title"] = title
            if title_pluralized is not None:
                _data["title_pluralized"] = title_pluralized
            if description is not None:
                _data["description"] = description
            if configuration is not None:
                _data["configuration"] = configuration

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_custom_object(
        self,
        custom_object_key: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a custom object

        Args:
            custom_object_key (str, required): Key of the custom object to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_custom_object_records(
        self,
        custom_object_key: str,
        external_id: str | None = None,
        external_ids: str | None = None,
        sort: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List records for a custom object

        Args:
            custom_object_key (str, required): Key of the custom object
            external_id (Optional[str], optional): Filter by external ID
            external_ids (Optional[str], optional): Filter by multiple external IDs
            sort (Optional[str], optional): Sort field and direction

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}/records.json"

            if external_id is not None:
                _params["external_id"] = external_id
            if external_ids is not None:
                _params["external_ids"] = external_ids
            if sort is not None:
                _params["sort"] = sort

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_custom_object_record(
        self,
        custom_object_key: str,
        custom_object_record_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific custom object record

        Args:
            custom_object_key (str, required): Key of the custom object
            custom_object_record_id (str, required): ID of the record

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}/records/{custom_object_record_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_custom_object_record(
        self,
        custom_object_key: str,
        name: str,
        custom_object_fields: dict[str, Any],
        external_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new custom object record

        Args:
            custom_object_key (str, required): Key of the custom object
            name (str, required): Record name
            external_id (Optional[str], optional): External ID
            custom_object_fields (Dict[str, Any], required): Custom field values

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}/records.json"

            _data["name"] = name
            if external_id is not None:
                _data["external_id"] = external_id
            _data["custom_object_fields"] = custom_object_fields

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_custom_object_record(
        self,
        custom_object_key: str,
        custom_object_record_id: str,
        name: str | None = None,
        external_id: str | None = None,
        custom_object_fields: dict[str, Any] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update a custom object record

        Args:
            custom_object_key (str, required): Key of the custom object
            custom_object_record_id (str, required): ID of the record
            name (Optional[str], optional): Record name
            external_id (Optional[str], optional): External ID
            custom_object_fields (Optional[Dict[str, Any]], optional): Custom field values

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}/records/{custom_object_record_id}.json"

            if name is not None:
                _data["name"] = name
            if external_id is not None:
                _data["external_id"] = external_id
            if custom_object_fields is not None:
                _data["custom_object_fields"] = custom_object_fields

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PATCH",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_custom_object_record(
        self,
        custom_object_key: str,
        custom_object_record_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete a custom object record

        Args:
            custom_object_key (str, required): Key of the custom object
            custom_object_record_id (str, required): ID of the record to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/custom_objects/{custom_object_key}/records/{custom_object_record_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_job_status(
        self,
        job_id: str,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show the status of a background job

        Args:
            job_id (str, required): ID of the job

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/job_statuses/{job_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_job_statuses(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List recent job statuses

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/job_statuses.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def incremental_tickets(
        self,
        start_time: int,
        cursor: str | None = None,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Incremental export of tickets

        Args:
            start_time (int, required): Unix timestamp to start from
            cursor (Optional[str], optional): Pagination cursor
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/incremental/tickets.json"

            _params["start_time"] = start_time
            if cursor is not None:
                _params["cursor"] = cursor
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def incremental_users(
        self,
        start_time: int,
        cursor: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Incremental export of users

        Args:
            start_time (int, required): Unix timestamp to start from
            cursor (Optional[str], optional): Pagination cursor

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/incremental/users.json"

            _params["start_time"] = start_time
            if cursor is not None:
                _params["cursor"] = cursor

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def incremental_organizations(
        self,
        start_time: int,
        cursor: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Incremental export of organizations

        Args:
            start_time (int, required): Unix timestamp to start from
            cursor (Optional[str], optional): Pagination cursor

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/incremental/organizations.json"

            _params["start_time"] = start_time
            if cursor is not None:
                _params["cursor"] = cursor

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def incremental_ticket_events(
        self,
        start_time: int,
        include: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Incremental export of ticket events

        Args:
            start_time (int, required): Unix timestamp to start from
            include (Optional[str], optional): Sideload related data

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/incremental/ticket_events.json"

            _params["start_time"] = start_time
            if include is not None:
                _params["include"] = include

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def list_sla_policies(
        self,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """List all SLA policies

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/slas/policies.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def show_sla_policy(
        self,
        policy_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Show a specific SLA policy

        Args:
            policy_id (int, required): ID of the SLA policy

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/slas/policies/{policy_id}.json"

            request = HTTPRequest(
                method="GET",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def create_sla_policy(
        self,
        title: str,
        filter: dict[str, Any],
        policy_metrics: list[dict[str, Any]],
        description: str | None = None,
        position: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Create a new SLA policy

        Args:
            title (str, required): Policy title
            description (Optional[str], optional): Policy description
            position (Optional[int], optional): Policy position
            filter (Dict[str, Any], required): Filter conditions
            policy_metrics (List[Dict[str, Any]], required): SLA metrics and targets

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/slas/policies.json"

            _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            _data["filter"] = filter
            _data["policy_metrics"] = policy_metrics

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="POST",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def update_sla_policy(
        self,
        policy_id: int,
        title: str | None = None,
        description: str | None = None,
        position: int | None = None,
        filter: dict[str, Any] | None = None,
        policy_metrics: list[dict[str, Any]] | None = None,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Update an existing SLA policy

        Args:
            policy_id (int, required): ID of the policy to update
            title (Optional[str], optional): Policy title
            description (Optional[str], optional): Policy description
            position (Optional[int], optional): Policy position
            filter (Optional[Dict[str, Any]], optional): Filter conditions
            policy_metrics (Optional[List[Dict[str, Any]]], optional): SLA metrics and targets

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/slas/policies/{policy_id}.json"

            if title is not None:
                _data["title"] = title
            if description is not None:
                _data["description"] = description
            if position is not None:
                _data["position"] = position
            if filter is not None:
                _data["filter"] = filter
            if policy_metrics is not None:
                _data["policy_metrics"] = policy_metrics

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def delete_sla_policy(
        self,
        policy_id: int,
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Delete an SLA policy

        Args:
            policy_id (int, required): ID of the policy to delete

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/slas/policies/{policy_id}.json"

            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers=_headers,
                query_params=_params,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def reorder_sla_policies(
        self,
        sla_policy_ids: list[int],
        headers: dict[str, Any] | None = None,
    ) -> ZendeskResponse:
        """Reorder SLA policies

        Args:
            sla_policy_ids (List[int], required): Array of policy IDs in new order

        Returns:
            ZendeskResponse: Standardized response object

        """
        try:
            _headers = dict(headers or {})
            _params = {}
            _data = {}
            url = f"{self.base_url}/slas/policies/reorder.json"

            _data["sla_policy_ids"] = sla_policy_ids

            _headers["Content-Type"] = "application/json"

            request = HTTPRequest(
                method="PUT",
                url=url,
                headers=_headers,
                query_params=_params,
                json=_data if _data else None,
            )
            response = await self.http.execute(
                request=request,
            )

            return ZendeskResponse(
                success=response.status < SUCCESS_CODE_IS_LESS_THAN,
                data=response.json() if response.is_json else None,
                error=response.text
                if response.status >= SUCCESS_CODE_IS_LESS_THAN
                else None,
                status_code=response.status,
            )

        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )

    async def get_api_info(self) -> ZendeskResponse:
        """Get information about available API methods."""
        try:
            info = {
                "total_methods": 177,
                "api_coverage": [
                    "Support API (Tickets, Users, Organizations, Groups)",
                    "Business Rules (Automations, Triggers, Macros, Views)",
                    "Webhooks (Management, Events, Security)",
                    "Help Center (Articles, Sections, Categories)",
                    "Custom Data (Objects, Records, Fields)",
                    "Administrative (Jobs, Exports, Bulk Operations)",
                ],
                "authentication": "Basic Auth with API Token",
                "base_url_pattern": "https://{subdomain}.zendesk.com",
            }
            return ZendeskResponse(
                success=True,
                data=info,
            )
        except Exception as e:
            return ZendeskResponse(
                success=False,
                error=str(e),
            )
