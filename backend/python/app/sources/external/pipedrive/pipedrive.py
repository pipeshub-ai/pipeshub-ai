"""
Pipedrive REST API DataSource - Auto-generated API wrapper

Generated from Pipedrive REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.pipedrive.pipedrive import PipedriveClient, PipedriveResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class PipedriveDataSource:
    """Pipedrive REST API DataSource

    Provides async wrapper methods for Pipedrive REST API operations:
    - Users management
    - Deals CRUD and management
    - Persons (contacts) CRUD
    - Organizations CRUD
    - Activities management
    - Pipelines and Stages
    - Products management
    - Notes CRUD
    - Leads management
    - Custom fields (Deal, Person, Organization)

    The base URL is determined by the PipedriveClient's configured base URL.

    All methods return PipedriveResponse objects.
    """

    def __init__(self, client: PipedriveClient) -> None:
        """Initialize with PipedriveClient.

        Args:
            client: PipedriveClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'PipedriveDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> PipedriveClient:
        """Return the underlying PipedriveClient."""
        return self._client

    async def list_users(
        self
    ) -> PipedriveResponse:
        """List all users in the company

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific user

        Args:
            id_: The user ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/users/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def get_current_user(
        self
    ) -> PipedriveResponse:
        """Get the current authenticated user

        Returns:
            PipedriveResponse with operation result
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
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_deals(
        self,
        status: str | None = None,
        start: int | None = None,
        limit: int | None = None,
        sort: str | None = None,
        filter_id: int | None = None
    ) -> PipedriveResponse:
        """List all deals

        Args:
            status: Filter by deal status (open, won, lost, deleted, all_not_deleted)
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)
            sort: Field name and sorting mode (e.g. 'title ASC')
            filter_id: ID of the filter to use

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if status is not None:
            query_params['status'] = status
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)
        if sort is not None:
            query_params['sort'] = sort
        if filter_id is not None:
            query_params['filter_id'] = str(filter_id)

        url = self.base_url + "/deals"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_deals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_deals")

    async def get_deal(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific deal

        Args:
            id_: The deal ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/deals/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_deal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_deal")

    async def create_deal(
        self,
        title: str,
        value: str | None = None,
        currency: str | None = None,
        user_id: int | None = None,
        person_id: int | None = None,
        org_id: int | None = None,
        pipeline_id: int | None = None,
        stage_id: int | None = None,
        status: str | None = None,
        expected_close_date: str | None = None,
        probability: int | None = None
    ) -> PipedriveResponse:
        """Create a new deal

        Args:
            title: The title of the deal
            value: Value of the deal
            currency: Currency of the deal (3-letter code)
            user_id: ID of the user who owns the deal
            person_id: ID of a person linked to the deal
            org_id: ID of an organization linked to the deal
            pipeline_id: ID of the pipeline this deal will be placed in
            stage_id: ID of the stage this deal will be placed in
            status: Status of the deal (open, won, lost, deleted)
            expected_close_date: Expected close date (YYYY-MM-DD)
            probability: Deal success probability percentage

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/deals"

        body: dict[str, Any] = {}
        body['title'] = title
        if value is not None:
            body['value'] = value
        if currency is not None:
            body['currency'] = currency
        if user_id is not None:
            body['user_id'] = user_id
        if person_id is not None:
            body['person_id'] = person_id
        if org_id is not None:
            body['org_id'] = org_id
        if pipeline_id is not None:
            body['pipeline_id'] = pipeline_id
        if stage_id is not None:
            body['stage_id'] = stage_id
        if status is not None:
            body['status'] = status
        if expected_close_date is not None:
            body['expected_close_date'] = expected_close_date
        if probability is not None:
            body['probability'] = probability

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_deal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute create_deal")

    async def update_deal(
        self,
        id_: str,
        title: str | None = None,
        value: str | None = None,
        currency: str | None = None,
        user_id: int | None = None,
        person_id: int | None = None,
        org_id: int | None = None,
        pipeline_id: int | None = None,
        stage_id: int | None = None,
        status: str | None = None,
        expected_close_date: str | None = None,
        probability: int | None = None
    ) -> PipedriveResponse:
        """Update a deal

        Args:
            id_: The deal ID
            title: The title of the deal
            value: Value of the deal
            currency: Currency of the deal (3-letter code)
            user_id: ID of the user who owns the deal
            person_id: ID of a person linked to the deal
            org_id: ID of an organization linked to the deal
            pipeline_id: ID of the pipeline
            stage_id: ID of the stage
            status: Status of the deal (open, won, lost, deleted)
            expected_close_date: Expected close date (YYYY-MM-DD)
            probability: Deal success probability percentage

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/deals/{id}".format(id=id_)

        body: dict[str, Any] = {}
        if title is not None:
            body['title'] = title
        if value is not None:
            body['value'] = value
        if currency is not None:
            body['currency'] = currency
        if user_id is not None:
            body['user_id'] = user_id
        if person_id is not None:
            body['person_id'] = person_id
        if org_id is not None:
            body['org_id'] = org_id
        if pipeline_id is not None:
            body['pipeline_id'] = pipeline_id
        if stage_id is not None:
            body['stage_id'] = stage_id
        if status is not None:
            body['status'] = status
        if expected_close_date is not None:
            body['expected_close_date'] = expected_close_date
        if probability is not None:
            body['probability'] = probability

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_deal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute update_deal")

    async def delete_deal(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Delete a deal

        Args:
            id_: The deal ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/deals/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_deal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute delete_deal")

    async def list_persons(
        self,
        start: int | None = None,
        limit: int | None = None,
        sort: str | None = None,
        filter_id: int | None = None
    ) -> PipedriveResponse:
        """List all persons (contacts)

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)
            sort: Field name and sorting mode
            filter_id: ID of the filter to use

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)
        if sort is not None:
            query_params['sort'] = sort
        if filter_id is not None:
            query_params['filter_id'] = str(filter_id)

        url = self.base_url + "/persons"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_persons" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_persons")

    async def get_person(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific person

        Args:
            id_: The person ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/persons/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_person" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_person")

    async def create_person(
        self,
        name: str,
        owner_id: int | None = None,
        org_id: int | None = None,
        email: str | None = None,
        phone: str | None = None
    ) -> PipedriveResponse:
        """Create a new person (contact)

        Args:
            name: The name of the person
            owner_id: ID of the user who owns the person
            org_id: ID of the organization this person belongs to
            email: Email address of the person
            phone: Phone number of the person

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/persons"

        body: dict[str, Any] = {}
        body['name'] = name
        if owner_id is not None:
            body['owner_id'] = owner_id
        if org_id is not None:
            body['org_id'] = org_id
        if email is not None:
            body['email'] = email
        if phone is not None:
            body['phone'] = phone

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_person" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute create_person")

    async def update_person(
        self,
        id_: str,
        name: str | None = None,
        owner_id: int | None = None,
        org_id: int | None = None,
        email: str | None = None,
        phone: str | None = None
    ) -> PipedriveResponse:
        """Update a person

        Args:
            id_: The person ID
            name: The name of the person
            owner_id: ID of the user who owns the person
            org_id: ID of the organization
            email: Email address of the person
            phone: Phone number of the person

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/persons/{id}".format(id=id_)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if owner_id is not None:
            body['owner_id'] = owner_id
        if org_id is not None:
            body['org_id'] = org_id
        if email is not None:
            body['email'] = email
        if phone is not None:
            body['phone'] = phone

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_person" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute update_person")

    async def list_organizations(
        self,
        start: int | None = None,
        limit: int | None = None,
        sort: str | None = None,
        filter_id: int | None = None
    ) -> PipedriveResponse:
        """List all organizations

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)
            sort: Field name and sorting mode
            filter_id: ID of the filter to use

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)
        if sort is not None:
            query_params['sort'] = sort
        if filter_id is not None:
            query_params['filter_id'] = str(filter_id)

        url = self.base_url + "/organizations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_organizations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_organizations")

    async def get_organization(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific organization

        Args:
            id_: The organization ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/organizations/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_organization" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_organization")

    async def create_organization(
        self,
        name: str,
        owner_id: int | None = None
    ) -> PipedriveResponse:
        """Create a new organization

        Args:
            name: The name of the organization
            owner_id: ID of the user who owns the organization

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/organizations"

        body: dict[str, Any] = {}
        body['name'] = name
        if owner_id is not None:
            body['owner_id'] = owner_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_organization" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute create_organization")

    async def list_activities(
        self,
        start: int | None = None,
        limit: int | None = None,
        type_: str | None = None,
        done: int | None = None,
        user_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None
    ) -> PipedriveResponse:
        """List all activities

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)
            type_: Type of activity (e.g. call, meeting, task, deadline, email)
            done: Filter by done status (0 = not done, 1 = done)
            user_id: Filter by user ID
            start_date: Start date filter (YYYY-MM-DD)
            end_date: End date filter (YYYY-MM-DD)

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)
        if type_ is not None:
            query_params['type'] = type_
        if done is not None:
            query_params['done'] = str(done)
        if user_id is not None:
            query_params['user_id'] = str(user_id)
        if start_date is not None:
            query_params['start_date'] = start_date
        if end_date is not None:
            query_params['end_date'] = end_date

        url = self.base_url + "/activities"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_activities" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_activities")

    async def get_activity(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific activity

        Args:
            id_: The activity ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/activities/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_activity" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_activity")

    async def create_activity(
        self,
        subject: str,
        type_: str,
        done: int | None = None,
        due_date: str | None = None,
        due_time: str | None = None,
        duration: str | None = None,
        deal_id: int | None = None,
        person_id: int | None = None,
        org_id: int | None = None,
        user_id: int | None = None,
        note: str | None = None
    ) -> PipedriveResponse:
        """Create a new activity

        Args:
            subject: Subject of the activity
            type_: Type of the activity (e.g. call, meeting, task)
            done: Whether the activity is done (0 or 1)
            due_date: Due date of the activity (YYYY-MM-DD)
            due_time: Due time of the activity (HH:MM)
            duration: Duration of the activity (HH:MM)
            deal_id: ID of the deal this activity is linked to
            person_id: ID of the person this activity is linked to
            org_id: ID of the organization this activity is linked to
            user_id: ID of the user who owns the activity
            note: Note of the activity (HTML format)

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/activities"

        body: dict[str, Any] = {}
        body['subject'] = subject
        body['type'] = type_
        if done is not None:
            body['done'] = done
        if due_date is not None:
            body['due_date'] = due_date
        if due_time is not None:
            body['due_time'] = due_time
        if duration is not None:
            body['duration'] = duration
        if deal_id is not None:
            body['deal_id'] = deal_id
        if person_id is not None:
            body['person_id'] = person_id
        if org_id is not None:
            body['org_id'] = org_id
        if user_id is not None:
            body['user_id'] = user_id
        if note is not None:
            body['note'] = note

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_activity" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute create_activity")

    async def list_pipelines(
        self
    ) -> PipedriveResponse:
        """List all pipelines

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/pipelines"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_pipelines" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_pipelines")

    async def get_pipeline(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific pipeline

        Args:
            id_: The pipeline ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/pipelines/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_pipeline" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_pipeline")

    async def list_stages(
        self,
        pipeline_id: int | None = None
    ) -> PipedriveResponse:
        """List all stages

        Args:
            pipeline_id: Filter stages by pipeline ID

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if pipeline_id is not None:
            query_params['pipeline_id'] = str(pipeline_id)

        url = self.base_url + "/stages"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_stages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_stages")

    async def get_stage(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific stage

        Args:
            id_: The stage ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/stages/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_stage" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_stage")

    async def list_products(
        self,
        start: int | None = None,
        limit: int | None = None
    ) -> PipedriveResponse:
        """List all products

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/products"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_products" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_products")

    async def get_product(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific product

        Args:
            id_: The product ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/products/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_product" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_product")

    async def list_notes(
        self,
        deal_id: int | None = None,
        person_id: int | None = None,
        org_id: int | None = None,
        start: int | None = None,
        limit: int | None = None
    ) -> PipedriveResponse:
        """List all notes

        Args:
            deal_id: Filter notes by deal ID
            person_id: Filter notes by person ID
            org_id: Filter notes by organization ID
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if deal_id is not None:
            query_params['deal_id'] = str(deal_id)
        if person_id is not None:
            query_params['person_id'] = str(person_id)
        if org_id is not None:
            query_params['org_id'] = str(org_id)
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/notes"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_notes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_notes")

    async def get_note(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific note

        Args:
            id_: The note ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/notes/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_note" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_note")

    async def create_note(
        self,
        content: str,
        deal_id: int | None = None,
        person_id: int | None = None,
        org_id: int | None = None
    ) -> PipedriveResponse:
        """Create a new note

        Args:
            content: Content of the note (HTML format)
            deal_id: ID of the deal this note is attached to
            person_id: ID of the person this note is attached to
            org_id: ID of the organization this note is attached to

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/notes"

        body: dict[str, Any] = {}
        body['content'] = content
        if deal_id is not None:
            body['deal_id'] = deal_id
        if person_id is not None:
            body['person_id'] = person_id
        if org_id is not None:
            body['org_id'] = org_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_note" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute create_note")

    async def list_leads(
        self,
        limit: int | None = None,
        start: int | None = None,
        sort: str | None = None,
        filter_id: int | None = None
    ) -> PipedriveResponse:
        """List all leads

        Args:
            limit: Items shown per page (default 100)
            start: Pagination start (default 0)
            sort: Field name and sorting mode
            filter_id: ID of the filter to use

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if start is not None:
            query_params['start'] = str(start)
        if sort is not None:
            query_params['sort'] = sort
        if filter_id is not None:
            query_params['filter_id'] = str(filter_id)

        url = self.base_url + "/leads"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_leads" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_leads")

    async def get_lead(
        self,
        id_: str
    ) -> PipedriveResponse:
        """Get details of a specific lead

        Args:
            id_: The lead ID

        Returns:
            PipedriveResponse with operation result
        """
        url = self.base_url + "/leads/{id}".format(id=id_)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_lead" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute get_lead")

    async def list_deal_fields(
        self,
        start: int | None = None,
        limit: int | None = None
    ) -> PipedriveResponse:
        """List all deal fields (including custom fields)

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/dealFields"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_deal_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_deal_fields")

    async def list_person_fields(
        self,
        start: int | None = None,
        limit: int | None = None
    ) -> PipedriveResponse:
        """List all person fields (including custom fields)

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/personFields"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_person_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_person_fields")

    async def list_organization_fields(
        self,
        start: int | None = None,
        limit: int | None = None
    ) -> PipedriveResponse:
        """List all organization fields (including custom fields)

        Args:
            start: Pagination start (default 0)
            limit: Items shown per page (default 100)

        Returns:
            PipedriveResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start is not None:
            query_params['start'] = str(start)
        if limit is not None:
            query_params['limit'] = str(limit)

        url = self.base_url + "/organizationFields"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return PipedriveResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_organization_fields" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return PipedriveResponse(success=False, error=str(e), message="Failed to execute list_organization_fields")
