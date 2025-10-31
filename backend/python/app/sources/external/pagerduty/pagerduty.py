"""PagerDuty Data Source - Business API Layer (SDK-based).

This module provides high-level business methods for PagerDuty integration.
It wraps the official PagerDuty SDK with meaningful business operations.

Architecture:
- Uses official pagerduty SDK (RestApiV2Client)
- Returns parsed JSON responses as dictionaries
- Supports pagination, filtering, and error handling
- Type hints for better IDE support

Supported Categories:
1. Incidents Management (10 methods)
2. Services Management (5 methods)
3. Users Management (4 methods)
4. Schedules Management (3 methods)
5. On-Call Management (2 methods)
6. Escalation Policies (3 methods)
7. Teams Management (2 methods)

Total: 32 API methods covering core PagerDuty operations

Reference: https://developer.pagerduty.com/api-reference/
"""

import logging
from typing import Any

from app.sources.client.pagerduty.pagerduty import PagerDutyClient, PagerDutyResponse

logger = logging.getLogger(__name__)

# HTTP status codes
HTTP_NO_CONTENT = 204  # Successful deletion with no response body


class PagerDutyDataSource:
    """PagerDuty REST API DataSource using official SDK.

    Provides access to PagerDuty Business APIs across:
    - Incidents APIs (10 methods)
    - Services APIs (5 methods)
    - Users APIs (4 methods)
    - Schedules APIs (3 methods)
    - On-Call APIs (2 methods)
    - Escalation Policies (3 methods)
    - Teams APIs (2 methods)

    All methods return PagerDutyResponse objects with standardized format.

    Example:
        >>> client = await PagerDutyClient.build_with_config(config)
        >>> ds = PagerDutyDataSource(client)
        >>> response = ds.get_incidents()
        >>> if response.success:
        ...     print(response.data['incidents'])

    """

    def __init__(self, client: PagerDutyClient) -> None:
        """Initialize PagerDuty DataSource.

        Args:
            client: PagerDutyClient instance (wraps official SDK)

        """
        self.client = client
        self.sdk = client.get_sdk_client()  # Get RestApiV2Client from official SDK

    # ==================== INCIDENTS APIS ====================

    def get_incidents(
        self,
        statuses: list[str] | None = None,
        service_ids: list[str] | None = None,
        team_ids: list[str] | None = None,
        urgencies: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List incidents with filtering options.

        Args:
            statuses: Filter by incident status (triggered, acknowledged, resolved)
            service_ids: Filter by service IDs
            team_ids: Filter by team IDs
            urgencies: Filter by urgency (high, low)
            since: Start of date range (ISO 8601 format)
            until: End of date range (ISO 8601 format)
            limit: Number of results per page (default: 25, max: 100)
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'incidents' list and pagination metadata

        API Reference:
            https://developer.pagerduty.com/api-reference/b3A6Mjc0ODIzNw-list-incidents

        """
        try:
            params = {"limit": limit, "offset": offset}

            if statuses:
                params["statuses[]"] = statuses
            if service_ids:
                params["service_ids[]"] = service_ids
            if team_ids:
                params["team_ids[]"] = team_ids
            if urgencies:
                params["urgencies[]"] = urgencies
            if since:
                params["since"] = since
            if until:
                params["until"] = until

            response = self.sdk.get("/incidents", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting incidents")
            return PagerDutyResponse(success=False, error=str(e))

    def get_incident(self, incident_id: str) -> PagerDutyResponse:
        """Get details of a specific incident.

        Args:
            incident_id: The ID of the incident

        Returns:
            PagerDutyResponse: Standardized response with incident details

        API Reference:
            https://developer.pagerduty.com/api-reference/b3A6Mjc0ODIzOA-get-an-incident

        """
        try:
            response = self.sdk.get(f"/incidents/{incident_id}")
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def create_incident(
        self,
        title: str,
        service_id: str,
        body: dict[str, Any] | None = None,
        urgency: str = "high",
        incident_key: str | None = None,
        escalation_policy_id: str | None = None,
        assignments: list[dict[str, str]] | None = None,
    ) -> PagerDutyResponse:
        """Create a new incident.

        Args:
            title: Brief description of the incident
            service_id: The ID of the service the incident is on
            body: Additional incident details
            urgency: Incident urgency ('high' or 'low')
            incident_key: Unique incident key for deduplication
            escalation_policy_id: Override default escalation policy
            assignments: List of user assignments [{"assignee": {"id": "USER_ID", "type": "user_reference"}}]

        Returns:
            PagerDutyResponse: Standardized response with created incident details

        API Reference:
            https://developer.pagerduty.com/api-reference/b3A6Mjc0ODIzOQ-create-an-incident

        """
        try:
            incident_data = {
                "incident": {
                    "type": "incident",
                    "title": title,
                    "service": {
                        "id": service_id,
                        "type": "service_reference",
                    },
                    "urgency": urgency,
                },
            }

            if body:
                incident_data["incident"]["body"] = body
            if incident_key:
                incident_data["incident"]["incident_key"] = incident_key
            if escalation_policy_id:
                incident_data["incident"]["escalation_policy"] = {
                    "id": escalation_policy_id,
                    "type": "escalation_policy_reference",
                }
            if assignments:
                incident_data["incident"]["assignments"] = assignments

            response = self.sdk.post("/incidents", json=incident_data)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error creating incident")
            return PagerDutyResponse(success=False, error=str(e))

    def update_incident(
        self,
        incident_id: str,
        title: str | None = None,
        status: str | None = None,
        urgency: str | None = None,
        escalation_level: int | None = None,
        assignments: list[dict[str, str]] | None = None,
        resolution: str | None = None,
    ) -> PagerDutyResponse:
        """Update an incident.

        Args:
            incident_id: The ID of the incident
            title: New incident title
            status: New status (acknowledged, resolved)
            urgency: New urgency (high, low)
            escalation_level: Escalate to this level
            assignments: New assignments
            resolution: Resolution notes (when resolving)

        Returns:
            PagerDutyResponse: Standardized response with updated incident details

        API Reference:
            https://developer.pagerduty.com/api-reference/b3A6Mjc0ODI0MA-update-an-incident

        """
        try:
            incident_data: dict[str, Any] = {"incident": {"type": "incident"}}

            if title:
                incident_data["incident"]["title"] = title
            if status:
                incident_data["incident"]["status"] = status
            if urgency:
                incident_data["incident"]["urgency"] = urgency
            if escalation_level is not None:
                incident_data["incident"]["escalation_level"] = escalation_level
            if assignments:
                incident_data["incident"]["assignments"] = assignments
            if resolution:
                incident_data["incident"]["resolution"] = resolution

            response = self.sdk.put(f"/incidents/{incident_id}", json=incident_data)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error updating incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def acknowledge_incident(self, incident_id: str, from_email: str) -> PagerDutyResponse:
        """Acknowledge an incident (mark as being worked on).

        Args:
            incident_id: The ID of the incident
            from_email: Email of user acknowledging the incident

        Returns:
            PagerDutyResponse: Standardized response with updated incident details

        """
        try:
            incident_data = {
                "incident": {
                    "type": "incident_reference",
                    "status": "acknowledged",
                },
            }

            headers = {"From": from_email}
            response = self.sdk.put(
                f"/incidents/{incident_id}",
                json=incident_data,
                headers=headers,
            )
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error acknowledging incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def resolve_incident(
        self,
        incident_id: str,
        from_email: str,
        resolution: str | None = None,
    ) -> PagerDutyResponse:
        """Resolve an incident (mark as fixed).

        Args:
            incident_id: The ID of the incident
            from_email: Email of user resolving the incident
            resolution: Optional resolution notes

        Returns:
            PagerDutyResponse: Standardized response with resolved incident details

        """
        try:
            incident_data: dict[str, Any] = {
                "incident": {
                    "type": "incident_reference",
                    "status": "resolved",
                },
            }

            if resolution:
                incident_data["incident"]["resolution"] = resolution

            headers = {"From": from_email}
            response = self.sdk.put(
                f"/incidents/{incident_id}",
                json=incident_data,
                headers=headers,
            )
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error resolving incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def reassign_incident(
        self,
        incident_id: str,
        from_email: str,
        assignees: list[dict[str, str]],
    ) -> PagerDutyResponse:
        """Reassign an incident to different users/escalation policies.

        Args:
            incident_id: The ID of the incident
            from_email: Email of user reassigning the incident
            assignees: List of assignee dicts [{"assignee": {"id": "USER_ID", "type": "user_reference"}}]

        Returns:
            PagerDutyResponse: Standardized response with updated incident details

        """
        try:
            incident_data = {
                "incident": {
                    "type": "incident_reference",
                    "assignments": assignees,
                },
            }

            headers = {"From": from_email}
            response = self.sdk.put(
                f"/incidents/{incident_id}",
                json=incident_data,
                headers=headers,
            )
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error reassigning incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def snooze_incident(
        self,
        incident_id: str,
        duration: int,
        from_email: str,
    ) -> PagerDutyResponse:
        """Snooze an incident (suppress notifications temporarily).

        Args:
            incident_id: The ID of the incident
            duration: Snooze duration in seconds
            from_email: Email of user snoozing the incident

        Returns:
            PagerDutyResponse: Standardized response with updated incident details

        """
        try:
            snooze_data = {
                "duration": duration,
            }

            headers = {"From": from_email}
            response = self.sdk.post(
                f"/incidents/{incident_id}/snooze",
                json=snooze_data,
                headers=headers,
            )
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error snoozing incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def merge_incidents(
        self,
        incident_id: str,
        source_incident_ids: list[str],
        from_email: str,
    ) -> PagerDutyResponse:
        """Merge multiple incidents into one.

        Args:
            incident_id: Target incident ID (incidents will be merged into this)
            source_incident_ids: List of incident IDs to merge
            from_email: Email of user performing merge

        Returns:
            PagerDutyResponse: Standardized response with merged incident details

        """
        try:
            merge_data = {
                "source_incidents": [
                    {"id": inc_id, "type": "incident_reference"}
                    for inc_id in source_incident_ids
                ],
            }

            headers = {"From": from_email}
            response = self.sdk.put(
                f"/incidents/{incident_id}/merge",
                json=merge_data,
                headers=headers,
            )
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error merging incidents into {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def get_incident_notes(self, incident_id: str) -> PagerDutyResponse:
        """Get notes for an incident.

        Args:
            incident_id: The ID of the incident

        Returns:
            PagerDutyResponse: Standardized response with list of incident notes

        """
        try:
            response = self.sdk.get(f"/incidents/{incident_id}/notes")
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting notes for incident {incident_id}")
            return PagerDutyResponse(success=False, error=str(e))

    # ==================== SERVICES APIS ====================

    def get_services(
        self,
        team_ids: list[str] | None = None,
        query: str | None = None,
        include: list[str] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List services with filtering options.

        Args:
            team_ids: Filter by team IDs
            query: Search query for service name/description
            include: Include related resources (escalation_policies, teams, integrations)
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'services' list and pagination metadata

        """
        try:
            params = {"limit": limit, "offset": offset}

            if team_ids:
                params["team_ids[]"] = team_ids
            if query:
                params["query"] = query
            if include:
                params["include[]"] = include

            response = self.sdk.get("/services", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting services")
            return PagerDutyResponse(success=False, error=str(e))

    def get_service(self, service_id: str, include: list[str] | None = None) -> PagerDutyResponse:
        """Get details of a specific service.

        Args:
            service_id: The ID of the service
            include: Include related resources

        Returns:
            PagerDutyResponse: Standardized response with service details

        """
        try:
            params = {}
            if include:
                params["include[]"] = include

            response = self.sdk.get(f"/services/{service_id}", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting service {service_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def create_service(
        self,
        name: str,
        description: str | None = None,
        escalation_policy_id: str | None = None,
        auto_resolve_timeout: int | None = None,
        acknowledgement_timeout: int | None = None,
        alert_creation: str = "create_alerts_and_incidents",
    ) -> PagerDutyResponse:
        """Create a new service.

        Args:
            name: Service name
            description: Service description
            escalation_policy_id: ID of escalation policy
            auto_resolve_timeout: Seconds before auto-resolve (null to disable)
            acknowledgement_timeout: Seconds before auto-acknowledgement
            alert_creation: Alert behavior (create_alerts_and_incidents, create_incidents)

        Returns:
            PagerDutyResponse: Standardized response with created service details

        """
        try:
            service_data: dict[str, Any] = {
                "service": {
                    "type": "service",
                    "name": name,
                    "alert_creation": alert_creation,
                },
            }

            if description:
                service_data["service"]["description"] = description
            if escalation_policy_id:
                service_data["service"]["escalation_policy"] = {
                    "id": escalation_policy_id,
                    "type": "escalation_policy_reference",
                }
            if auto_resolve_timeout is not None:
                service_data["service"]["auto_resolve_timeout"] = auto_resolve_timeout
            if acknowledgement_timeout is not None:
                service_data["service"]["acknowledgement_timeout"] = acknowledgement_timeout

            response = self.sdk.post("/services", json=service_data)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error creating service")
            return PagerDutyResponse(success=False, error=str(e))

    def update_service(
        self,
        service_id: str,
        name: str | None = None,
        description: str | None = None,
        escalation_policy_id: str | None = None,
        status: str | None = None,
    ) -> PagerDutyResponse:
        """Update a service.

        Args:
            service_id: The ID of the service
            name: New service name
            description: New description
            escalation_policy_id: New escalation policy ID
            status: Service status (active, warning, critical, maintenance, disabled)

        Returns:
            PagerDutyResponse: Standardized response with updated service details

        """
        try:
            service_data: dict[str, Any] = {"service": {}}

            if name:
                service_data["service"]["name"] = name
            if description:
                service_data["service"]["description"] = description
            if escalation_policy_id:
                service_data["service"]["escalation_policy"] = {
                    "id": escalation_policy_id,
                    "type": "escalation_policy_reference",
                }
            if status:
                service_data["service"]["status"] = status

            response = self.sdk.put(f"/services/{service_id}", json=service_data)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error updating service {service_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def delete_service(self, service_id: str) -> PagerDutyResponse:
        """Delete a service.

        Args:
            service_id: The ID of the service to delete

        Returns:
            PagerDutyResponse: Standardized response indicating deletion success

        """
        try:
            response = self.sdk.delete(f"/services/{service_id}")
            # Handle case where response might be None or not have status_code
            success = getattr(response, "status_code", None) == HTTP_NO_CONTENT
            return PagerDutyResponse(success=success, data={"deleted": success})
        except Exception as e:
            logger.exception(f"Error deleting service {service_id}")
            return PagerDutyResponse(success=False, error=str(e))

    # ==================== USERS APIS ====================

    def get_users(
        self,
        query: str | None = None,
        team_ids: list[str] | None = None,
        include: list[str] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List users with filtering options.

        Args:
            query: Search query for user name/email
            team_ids: Filter by team IDs
            include: Include related resources (contact_methods, notification_rules, teams)
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'users' list and pagination metadata

        """
        try:
            params = {"limit": limit, "offset": offset}

            if query:
                params["query"] = query
            if team_ids:
                params["team_ids[]"] = team_ids
            if include:
                params["include[]"] = include

            response = self.sdk.get("/users", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting users")
            return PagerDutyResponse(success=False, error=str(e))

    def get_user(self, user_id: str, include: list[str] | None = None) -> PagerDutyResponse:
        """Get details of a specific user.

        Args:
            user_id: The ID of the user
            include: Include related resources

        Returns:
            PagerDutyResponse: Standardized response with user details

        """
        try:
            params = {}
            if include:
                params["include[]"] = include

            response = self.sdk.get(f"/users/{user_id}", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting user {user_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def get_current_user(self, include: list[str] | None = None) -> PagerDutyResponse:
        """Get details of the current authenticated user.

        Args:
            include: Include related resources

        Returns:
            PagerDutyResponse: Standardized response with current user details

        """
        try:
            params = {}
            if include:
                params["include[]"] = include

            response = self.sdk.get("/users/me", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting current user")
            return PagerDutyResponse(success=False, error=str(e))

    def get_user_contact_methods(self, user_id: str) -> PagerDutyResponse:
        """Get contact methods for a user.

        Args:
            user_id: The ID of the user

        Returns:
            PagerDutyResponse: Standardized response with list of contact methods

        """
        try:
            response = self.sdk.get(f"/users/{user_id}/contact_methods")
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting contact methods for user {user_id}")
            return PagerDutyResponse(success=False, error=str(e))

    # ==================== SCHEDULES APIS ====================

    def get_schedules(
        self,
        query: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List schedules with filtering options.

        Args:
            query: Search query for schedule name
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'schedules' list and pagination metadata

        """
        try:
            params = {"limit": limit, "offset": offset}

            if query:
                params["query"] = query

            response = self.sdk.get("/schedules", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting schedules")
            return PagerDutyResponse(success=False, error=str(e))

    def get_schedule(
        self,
        schedule_id: str,
        since: str | None = None,
        until: str | None = None,
    ) -> PagerDutyResponse:
        """Get details of a specific schedule.

        Args:
            schedule_id: The ID of the schedule
            since: Start of date range (ISO 8601)
            until: End of date range (ISO 8601)

        Returns:
            PagerDutyResponse: Standardized response with schedule details including shifts

        """
        try:
            params = {}
            if since:
                params["since"] = since
            if until:
                params["until"] = until

            response = self.sdk.get(f"/schedules/{schedule_id}", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting schedule {schedule_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def get_schedule_users(self, schedule_id: str) -> PagerDutyResponse:
        """Get users assigned to a schedule.

        Args:
            schedule_id: The ID of the schedule

        Returns:
            PagerDutyResponse: Standardized response with list of users in the schedule

        """
        try:
            response = self.sdk.get(f"/schedules/{schedule_id}/users")
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting users for schedule {schedule_id}")
            return PagerDutyResponse(success=False, error=str(e))

    # ==================== ON-CALL APIS ====================

    def get_oncalls(
        self,
        user_ids: list[str] | None = None,
        schedule_ids: list[str] | None = None,
        escalation_policy_ids: list[str] | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List on-call entries with filtering options.

        Args:
            user_ids: Filter by user IDs
            schedule_ids: Filter by schedule IDs
            escalation_policy_ids: Filter by escalation policy IDs
            since: Start of date range (ISO 8601)
            until: End of date range (ISO 8601)
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'oncalls' list showing who's on-call when

        """
        try:
            params = {"limit": limit, "offset": offset}

            if user_ids:
                params["user_ids[]"] = user_ids
            if schedule_ids:
                params["schedule_ids[]"] = schedule_ids
            if escalation_policy_ids:
                params["escalation_policy_ids[]"] = escalation_policy_ids
            if since:
                params["since"] = since
            if until:
                params["until"] = until

            response = self.sdk.get("/oncalls", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting oncalls")
            return PagerDutyResponse(success=False, error=str(e))

    def get_oncall_users(
        self,
        escalation_policy_ids: list[str] | None = None,
        schedule_ids: list[str] | None = None,
    ) -> PagerDutyResponse:
        """Get list of users currently on-call.

        Args:
            escalation_policy_ids: Filter by escalation policy IDs
            schedule_ids: Filter by schedule IDs

        Returns:
            PagerDutyResponse: Standardized response with list of on-call users

        """
        try:
            params = {}

            if escalation_policy_ids:
                params["escalation_policy_ids[]"] = escalation_policy_ids
            if schedule_ids:
                params["schedule_ids[]"] = schedule_ids

            response = self.sdk.get("/oncalls", params=params)
            oncalls = response.json()

            # Extract unique users from oncalls
            users = []
            seen_user_ids = set()
            for oncall in oncalls.get("oncalls", []):
                user = oncall.get("user")
                if user and user["id"] not in seen_user_ids:
                    users.append(user)
                    seen_user_ids.add(user["id"])

            return PagerDutyResponse(success=True, data={"users": users})
        except Exception as e:
            logger.exception("Error getting oncall users")
            return PagerDutyResponse(success=False, error=str(e))

    # ==================== ESCALATION POLICIES APIS ====================

    def get_escalation_policies(
        self,
        query: str | None = None,
        user_ids: list[str] | None = None,
        team_ids: list[str] | None = None,
        include: list[str] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List escalation policies with filtering options.

        Args:
            query: Search query for policy name
            user_ids: Filter policies containing these users
            team_ids: Filter by team IDs
            include: Include related resources (services, teams)
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'escalation_policies' list and pagination metadata

        """
        try:
            params = {"limit": limit, "offset": offset}

            if query:
                params["query"] = query
            if user_ids:
                params["user_ids[]"] = user_ids
            if team_ids:
                params["team_ids[]"] = team_ids
            if include:
                params["include[]"] = include

            response = self.sdk.get("/escalation_policies", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting escalation policies")
            return PagerDutyResponse(success=False, error=str(e))

    def get_escalation_policy(
        self,
        policy_id: str,
        include: list[str] | None = None,
    ) -> PagerDutyResponse:
        """Get details of a specific escalation policy.

        Args:
            policy_id: The ID of the escalation policy
            include: Include related resources

        Returns:
            PagerDutyResponse: Standardized response with escalation policy details

        """
        try:
            params = {}
            if include:
                params["include[]"] = include

            response = self.sdk.get(f"/escalation_policies/{policy_id}", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting escalation policy {policy_id}")
            return PagerDutyResponse(success=False, error=str(e))

    def create_escalation_policy(
        self,
        name: str,
        escalation_rules: list[dict[str, Any]],
        description: str | None = None,
        num_loops: int = 0,
        teams: list[dict[str, str]] | None = None,
    ) -> PagerDutyResponse:
        """Create a new escalation policy.

        Args:
            name: Policy name
            escalation_rules: List of escalation rules with targets and delays
            description: Policy description
            num_loops: Number of times to loop through escalation rules (0 = no repeat)
            teams: List of team references [{"id": "TEAM_ID", "type": "team_reference"}]

        Returns:
            PagerDutyResponse: Standardized response with created escalation policy details

        Example escalation_rules:
            [
                {
                    "escalation_delay_in_minutes": 30,
                    "targets": [
                        {"id": "USER_ID", "type": "user_reference"}
                    ]
                }
            ]

        """
        try:
            policy_data: dict[str, Any] = {
                "escalation_policy": {
                    "type": "escalation_policy",
                    "name": name,
                    "escalation_rules": escalation_rules,
                    "num_loops": num_loops,
                },
            }

            if description:
                policy_data["escalation_policy"]["description"] = description
            if teams:
                policy_data["escalation_policy"]["teams"] = teams

            response = self.sdk.post("/escalation_policies", json=policy_data)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error creating escalation policy")
            return PagerDutyResponse(success=False, error=str(e))

    # ==================== TEAMS APIS ====================

    def get_teams(
        self,
        query: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> PagerDutyResponse:
        """List teams with filtering options.

        Args:
            query: Search query for team name
            limit: Number of results per page
            offset: Pagination offset

        Returns:
            PagerDutyResponse: Standardized response with 'teams' list and pagination metadata

        """
        try:
            params = {"limit": limit, "offset": offset}

            if query:
                params["query"] = query

            response = self.sdk.get("/teams", params=params)
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception("Error getting teams")
            return PagerDutyResponse(success=False, error=str(e))

    def get_team(self, team_id: str) -> PagerDutyResponse:
        """Get details of a specific team.

        Args:
            team_id: The ID of the team

        Returns:
            PagerDutyResponse: Standardized response with team details

        """
        try:
            response = self.sdk.get(f"/teams/{team_id}")
            return PagerDutyResponse(success=True, data=response.json())
        except Exception as e:
            logger.exception(f"Error getting team {team_id}")
            return PagerDutyResponse(success=False, error=str(e))
