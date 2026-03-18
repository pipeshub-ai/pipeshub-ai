"""
Affinity REST API DataSource - Auto-generated API wrapper

Generated from Affinity REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.affinity.affinity import AffinityClient, AffinityResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class AffinityDataSource:
    """Affinity REST API DataSource

    Provides async wrapper methods for Affinity REST API operations:
    - Lists and list entries
    - Persons
    - Organizations
    - Opportunities
    - Notes
    - Entity files
    - Fields
    - Relationship strengths
    - Who Am I (authentication check)

    The base URL is https://api.affinity.co by default.
    All methods return AffinityResponse objects.
    """

    def __init__(self, client: AffinityClient) -> None:
        """Initialize with AffinityClient.

        Args:
            client: AffinityClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "AffinityDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> AffinityClient:
        """Return the underlying AffinityClient."""
        return self._client

    # ------------------------------------------------------------------
    # Who Am I
    # ------------------------------------------------------------------

    async def whoami(self) -> AffinityResponse:
        """Get the current authenticated user

        HTTP GET /whoami

        Returns:
            AffinityResponse with current user data
        """
        url = self.base_url + "/whoami"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed whoami"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute whoami",
            )

    # ------------------------------------------------------------------
    # Lists
    # ------------------------------------------------------------------

    async def get_lists(self) -> AffinityResponse:
        """Get all lists

        HTTP GET /lists

        Returns:
            AffinityResponse with all lists
        """
        url = self.base_url + "/lists"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_lists"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_lists",
            )

    async def get_list(
        self,
        list_id: int | str,
    ) -> AffinityResponse:
        """Get a specific list by ID

        HTTP GET /lists/{list_id}

        Args:
            list_id: The list ID

        Returns:
            AffinityResponse with list data
        """
        url = self.base_url + "/lists/{list_id}".format(list_id=list_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_list",
            )

    # ------------------------------------------------------------------
    # List Entries
    # ------------------------------------------------------------------

    async def get_list_entries(
        self,
        list_id: int | str,
        *,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get entries in a list

        HTTP GET /lists/{list_id}/list-entries

        Args:
            list_id: The list ID
            page_size: Number of entries per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with list entries
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

        url = self.base_url + "/lists/{list_id}/list-entries".format(
            list_id=list_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list_entries"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_list_entries",
            )

    async def get_list_entry(
        self,
        list_id: int | str,
        list_entry_id: int | str,
    ) -> AffinityResponse:
        """Get a specific list entry

        HTTP GET /lists/{list_id}/list-entries/{list_entry_id}

        Args:
            list_id: The list ID
            list_entry_id: The list entry ID

        Returns:
            AffinityResponse with list entry data
        """
        url = self.base_url + "/lists/{list_id}/list-entries/{list_entry_id}".format(
            list_id=list_id, list_entry_id=list_entry_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_list_entry"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_list_entry",
            )

    # ------------------------------------------------------------------
    # Persons
    # ------------------------------------------------------------------

    async def get_persons(
        self,
        *,
        term: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get all persons

        HTTP GET /persons

        Args:
            term: Search term to filter persons
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with persons list
        """
        query_params: dict[str, Any] = {}
        if term is not None:
            query_params["term"] = term
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

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
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_persons"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_persons",
            )

    async def get_person(
        self,
        person_id: int | str,
    ) -> AffinityResponse:
        """Get a person by ID

        HTTP GET /persons/{person_id}

        Args:
            person_id: The person ID

        Returns:
            AffinityResponse with person data
        """
        url = self.base_url + "/persons/{person_id}".format(
            person_id=person_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_person"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_person",
            )

    # ------------------------------------------------------------------
    # Organizations
    # ------------------------------------------------------------------

    async def get_organizations(
        self,
        *,
        term: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get all organizations

        HTTP GET /organizations

        Args:
            term: Search term to filter organizations
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with organizations list
        """
        query_params: dict[str, Any] = {}
        if term is not None:
            query_params["term"] = term
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

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
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_organizations"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_organizations",
            )

    async def get_organization(
        self,
        organization_id: int | str,
    ) -> AffinityResponse:
        """Get an organization by ID

        HTTP GET /organizations/{organization_id}

        Args:
            organization_id: The organization ID

        Returns:
            AffinityResponse with organization data
        """
        url = self.base_url + "/organizations/{organization_id}".format(
            organization_id=organization_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_organization"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_organization",
            )

    # ------------------------------------------------------------------
    # Opportunities
    # ------------------------------------------------------------------

    async def get_opportunities(
        self,
        *,
        term: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get all opportunities

        HTTP GET /opportunities

        Args:
            term: Search term to filter opportunities
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with opportunities list
        """
        query_params: dict[str, Any] = {}
        if term is not None:
            query_params["term"] = term
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

        url = self.base_url + "/opportunities"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_opportunities"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_opportunities",
            )

    async def get_opportunity(
        self,
        opportunity_id: int | str,
    ) -> AffinityResponse:
        """Get an opportunity by ID

        HTTP GET /opportunities/{opportunity_id}

        Args:
            opportunity_id: The opportunity ID

        Returns:
            AffinityResponse with opportunity data
        """
        url = self.base_url + "/opportunities/{opportunity_id}".format(
            opportunity_id=opportunity_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_opportunity"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_opportunity",
            )

    # ------------------------------------------------------------------
    # Notes
    # ------------------------------------------------------------------

    async def get_notes(
        self,
        *,
        person_id: int | None = None,
        organization_id: int | None = None,
        opportunity_id: int | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get all notes

        HTTP GET /notes

        Args:
            person_id: Filter notes by person ID
            organization_id: Filter notes by organization ID
            opportunity_id: Filter notes by opportunity ID
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with notes list
        """
        query_params: dict[str, Any] = {}
        if person_id is not None:
            query_params["person_id"] = str(person_id)
        if organization_id is not None:
            query_params["organization_id"] = str(organization_id)
        if opportunity_id is not None:
            query_params["opportunity_id"] = str(opportunity_id)
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

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
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_notes"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_notes",
            )

    async def get_note(
        self,
        note_id: int | str,
    ) -> AffinityResponse:
        """Get a note by ID

        HTTP GET /notes/{note_id}

        Args:
            note_id: The note ID

        Returns:
            AffinityResponse with note data
        """
        url = self.base_url + "/notes/{note_id}".format(note_id=note_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_note"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_note",
            )

    # ------------------------------------------------------------------
    # Entity Files
    # ------------------------------------------------------------------

    async def get_entity_files(
        self,
        *,
        person_id: int | None = None,
        organization_id: int | None = None,
        opportunity_id: int | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get entity files

        HTTP GET /entity-files

        Args:
            person_id: Filter by person ID
            organization_id: Filter by organization ID
            opportunity_id: Filter by opportunity ID
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with entity files
        """
        query_params: dict[str, Any] = {}
        if person_id is not None:
            query_params["person_id"] = str(person_id)
        if organization_id is not None:
            query_params["organization_id"] = str(organization_id)
        if opportunity_id is not None:
            query_params["opportunity_id"] = str(opportunity_id)
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

        url = self.base_url + "/entity-files"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_entity_files"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_entity_files",
            )

    # ------------------------------------------------------------------
    # Fields
    # ------------------------------------------------------------------

    async def get_fields(
        self,
        *,
        list_id: int | None = None,
        value_type: int | None = None,
    ) -> AffinityResponse:
        """Get all fields

        HTTP GET /fields

        Args:
            list_id: Filter fields by list ID
            value_type: Filter fields by value type

        Returns:
            AffinityResponse with fields
        """
        query_params: dict[str, Any] = {}
        if list_id is not None:
            query_params["list_id"] = str(list_id)
        if value_type is not None:
            query_params["value_type"] = str(value_type)

        url = self.base_url + "/fields"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_fields"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_fields",
            )

    # ------------------------------------------------------------------
    # Relationship Strengths
    # ------------------------------------------------------------------

    async def get_relationship_strengths(
        self,
        *,
        person_id: int | None = None,
        organization_id: int | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> AffinityResponse:
        """Get relationship strengths

        HTTP GET /relationship-strengths

        Args:
            person_id: Filter by person ID
            organization_id: Filter by organization ID
            page_size: Number of results per page
            page_token: Token for pagination

        Returns:
            AffinityResponse with relationship strength data
        """
        query_params: dict[str, Any] = {}
        if person_id is not None:
            query_params["person_id"] = str(person_id)
        if organization_id is not None:
            query_params["organization_id"] = str(organization_id)
        if page_size is not None:
            query_params["page_size"] = str(page_size)
        if page_token is not None:
            query_params["page_token"] = page_token

        url = self.base_url + "/relationship-strengths"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AffinityResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_relationship_strengths"
                if response.status < HTTP_ERROR_THRESHOLD
                else f"Failed with status {response.status}",
            )
        except Exception as e:
            return AffinityResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_relationship_strengths",
            )
