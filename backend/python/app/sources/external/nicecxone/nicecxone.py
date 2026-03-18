# ruff: noqa
"""
NICE CXone REST API DataSource - Auto-generated API wrapper

Generated from NICE CXone REST API v31.0 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.nicecxone.nicecxone import NiceCXoneClient, NiceCXoneResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class NiceCXoneDataSource:
    """NICE CXone REST API DataSource

    Provides async wrapper methods for NICE CXone REST API operations:
    - Agents management and state monitoring
    - Contacts (active and history)
    - Skills management
    - Teams management
    - Campaigns management
    - Quality management evaluations
    - Reporting and contact history
    - Dialing rules

    The base URL is cluster-specific and determined by the NiceCXoneClient
    configuration. Create a client with the desired cluster and pass it here.

    All methods return NiceCXoneResponse objects.
    """

    def __init__(self, client: NiceCXoneClient) -> None:
        """Initialize with NiceCXoneClient.

        Args:
            client: NiceCXoneClient instance with configured authentication and cluster
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'NiceCXoneDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> NiceCXoneClient:
        """Return the underlying NiceCXoneClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Agents
    # -----------------------------------------------------------------------

    async def get_agents(
        self,
        *,
        updated_since: str | None = None,
        skip: int | None = None,
        top: int | None = None,
        order_by: str | None = None,
        is_active: bool | None = None,
    ) -> NiceCXoneResponse:
        """Get all agents

        Args:
            updated_since: Filter agents updated since this date (ISO 8601)
            skip: Number of records to skip for pagination
            top: Number of records to return
            order_by: Field to order results by
            is_active: Filter by active status

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if updated_since is not None:
            query_params['updatedSince'] = updated_since
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)
        if order_by is not None:
            query_params['orderBy'] = order_by
        if is_active is not None:
            query_params['isActive'] = str(is_active).lower()

        url = self.base_url + "/agents"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_agents" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_agents")

    async def get_agent(
        self,
        agent_id: str,
    ) -> NiceCXoneResponse:
        """Get a specific agent by ID

        Args:
            agent_id: The agent ID

        Returns:
            NiceCXoneResponse with operation result
        """
        url = self.base_url + "/agents/{agent_id}".format(agent_id=agent_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_agent" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_agent")

    async def get_agent_states(
        self,
        *,
        updated_since: str | None = None,
        fields: str | None = None,
    ) -> NiceCXoneResponse:
        """Get all agent states

        Args:
            updated_since: Filter states updated since this date (ISO 8601)
            fields: Comma-separated list of fields to include

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if updated_since is not None:
            query_params['updatedSince'] = updated_since
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/agents/states"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_agent_states" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_agent_states")

    # -----------------------------------------------------------------------
    # Contacts
    # -----------------------------------------------------------------------

    async def get_active_contacts(
        self,
        *,
        updated_since: str | None = None,
        skip: int | None = None,
        top: int | None = None,
    ) -> NiceCXoneResponse:
        """Get all active contacts

        Args:
            updated_since: Filter contacts updated since this date (ISO 8601)
            skip: Number of records to skip for pagination
            top: Number of records to return

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if updated_since is not None:
            query_params['updatedSince'] = updated_since
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)

        url = self.base_url + "/contacts/active"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_active_contacts" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_active_contacts")

    async def get_contact(
        self,
        contact_id: str,
    ) -> NiceCXoneResponse:
        """Get a specific contact by ID

        Args:
            contact_id: The contact ID

        Returns:
            NiceCXoneResponse with operation result
        """
        url = self.base_url + "/contacts/{contact_id}".format(contact_id=contact_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contact" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_contact")

    # -----------------------------------------------------------------------
    # Skills
    # -----------------------------------------------------------------------

    async def get_skills(
        self,
        *,
        updated_since: str | None = None,
        skip: int | None = None,
        top: int | None = None,
        order_by: str | None = None,
        is_active: bool | None = None,
        media_type_id: int | None = None,
    ) -> NiceCXoneResponse:
        """Get all skills

        Args:
            updated_since: Filter skills updated since this date (ISO 8601)
            skip: Number of records to skip for pagination
            top: Number of records to return
            order_by: Field to order results by
            is_active: Filter by active status
            media_type_id: Filter by media type ID

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if updated_since is not None:
            query_params['updatedSince'] = updated_since
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)
        if order_by is not None:
            query_params['orderBy'] = order_by
        if is_active is not None:
            query_params['isActive'] = str(is_active).lower()
        if media_type_id is not None:
            query_params['mediaTypeId'] = str(media_type_id)

        url = self.base_url + "/skills"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_skills" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_skills")

    async def get_skill(
        self,
        skill_id: str,
    ) -> NiceCXoneResponse:
        """Get a specific skill by ID

        Args:
            skill_id: The skill ID

        Returns:
            NiceCXoneResponse with operation result
        """
        url = self.base_url + "/skills/{skill_id}".format(skill_id=skill_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_skill" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_skill")

    # -----------------------------------------------------------------------
    # Teams
    # -----------------------------------------------------------------------

    async def get_teams(
        self,
        *,
        updated_since: str | None = None,
        skip: int | None = None,
        top: int | None = None,
        order_by: str | None = None,
        is_active: bool | None = None,
    ) -> NiceCXoneResponse:
        """Get all teams

        Args:
            updated_since: Filter teams updated since this date (ISO 8601)
            skip: Number of records to skip for pagination
            top: Number of records to return
            order_by: Field to order results by
            is_active: Filter by active status

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if updated_since is not None:
            query_params['updatedSince'] = updated_since
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)
        if order_by is not None:
            query_params['orderBy'] = order_by
        if is_active is not None:
            query_params['isActive'] = str(is_active).lower()

        url = self.base_url + "/teams"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_teams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_teams")

    async def get_team(
        self,
        team_id: str,
    ) -> NiceCXoneResponse:
        """Get a specific team by ID

        Args:
            team_id: The team ID

        Returns:
            NiceCXoneResponse with operation result
        """
        url = self.base_url + "/teams/{team_id}".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_team" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_team")

    # -----------------------------------------------------------------------
    # Campaigns
    # -----------------------------------------------------------------------

    async def get_campaigns(
        self,
        *,
        updated_since: str | None = None,
        skip: int | None = None,
        top: int | None = None,
        order_by: str | None = None,
        is_active: bool | None = None,
    ) -> NiceCXoneResponse:
        """Get all campaigns

        Args:
            updated_since: Filter campaigns updated since this date (ISO 8601)
            skip: Number of records to skip for pagination
            top: Number of records to return
            order_by: Field to order results by
            is_active: Filter by active status

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if updated_since is not None:
            query_params['updatedSince'] = updated_since
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)
        if order_by is not None:
            query_params['orderBy'] = order_by
        if is_active is not None:
            query_params['isActive'] = str(is_active).lower()

        url = self.base_url + "/campaigns"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_campaigns" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_campaigns")

    async def get_campaign(
        self,
        campaign_id: str,
    ) -> NiceCXoneResponse:
        """Get a specific campaign by ID

        Args:
            campaign_id: The campaign ID

        Returns:
            NiceCXoneResponse with operation result
        """
        url = self.base_url + "/campaigns/{campaign_id}".format(campaign_id=campaign_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_campaign" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_campaign")

    # -----------------------------------------------------------------------
    # Quality Management Evaluations
    # -----------------------------------------------------------------------

    async def get_quality_management_evaluations(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        skip: int | None = None,
        top: int | None = None,
    ) -> NiceCXoneResponse:
        """Get quality management evaluations

        Args:
            start_date: Start date filter (ISO 8601)
            end_date: End date filter (ISO 8601)
            skip: Number of records to skip for pagination
            top: Number of records to return

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if start_date is not None:
            query_params['startDate'] = start_date
        if end_date is not None:
            query_params['endDate'] = end_date
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)

        url = self.base_url + "/wfo-data/quality-management/evaluations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_quality_management_evaluations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_quality_management_evaluations")

    # -----------------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------------

    async def get_contact_history(
        self,
        *,
        start_date: str,
        end_date: str,
        skip: int | None = None,
        top: int | None = None,
        order_by: str | None = None,
    ) -> NiceCXoneResponse:
        """Get contact history report

        Args:
            start_date: Start date for the report (ISO 8601, required)
            end_date: End date for the report (ISO 8601, required)
            skip: Number of records to skip for pagination
            top: Number of records to return
            order_by: Field to order results by

        Returns:
            NiceCXoneResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['startDate'] = start_date
        query_params['endDate'] = end_date
        if skip is not None:
            query_params['skip'] = str(skip)
        if top is not None:
            query_params['top'] = str(top)
        if order_by is not None:
            query_params['orderBy'] = order_by

        url = self.base_url + "/reporting/contact-history"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_contact_history" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_contact_history")

    # -----------------------------------------------------------------------
    # Dialing Rules
    # -----------------------------------------------------------------------

    async def get_dialing_rules(
        self,
    ) -> NiceCXoneResponse:
        """Get all dialing rules

        Returns:
            NiceCXoneResponse with operation result
        """
        url = self.base_url + "/dialing-rules"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return NiceCXoneResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_dialing_rules" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return NiceCXoneResponse(success=False, error=str(e), message="Failed to execute get_dialing_rules")
