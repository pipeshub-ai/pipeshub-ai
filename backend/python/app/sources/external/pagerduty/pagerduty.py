# ruff: noqa
"""
PagerDuty DataSource - Exhaustive SDK wrapper

Hand-written wrapper around the official ``pagerduty`` Python SDK (>=5.0.0).
All methods call the SDK's ``RestApiV2Client`` directly and wrap results in
``PagerDutyResponse``.

Covers:
  - Incidents (alerts, notes, log entries, snooze, merge)
  - Services (integrations)
  - Users (contact methods, notification rules)
  - Teams (members)
  - Escalation Policies
  - Schedules (overrides)
  - On-Calls, Priorities, Vendors, Tags
  - Log Entries, Notifications, Abilities
  - Add-ons, Maintenance Windows, Extensions
  - Business Services, Rulesets (rules)
  - Webhooks, Analytics, Audit
  - Event Orchestrations, Alert Grouping Settings
  - Change Events, Licenses, Incident Workflows
  - Status Dashboards, Templates, Standards
"""
from __future__ import annotations

from typing import Any, cast

from app.sources.client.pagerduty.pagerduty import PagerDutyClient, PagerDutyResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _handle_error(e: Exception, method_name: str) -> PagerDutyResponse:
    """Build a failed ``PagerDutyResponse`` from an exception.

    Args:
        e: The caught exception.
        method_name: Name of the method that failed (for the message).

    Returns:
        PagerDutyResponse with ``success=False``.
    """
    return PagerDutyResponse(
        success=False,
        error=str(e),
        message=f"Failed to execute {method_name}",
    )


# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------


class PagerDutyDataSource:
    """Exhaustive PagerDuty SDK DataSource.

    Typed wrapper around the official ``pagerduty`` SDK covering all major
    PagerDuty REST API v2 resource groups.

    Accepts a ``PagerDutyClient`` (which exposes ``.get_sdk() -> RestApiV2Client``)
    or a raw ``RestApiV2Client`` SDK instance.  All methods are **synchronous**
    (the underlying SDK is synchronous) and return ``PagerDutyResponse`` objects.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, client_or_sdk: PagerDutyClient | object) -> None:
        """Initialise with a PagerDutyClient or raw RestApiV2Client SDK instance.

        Args:
            client_or_sdk: A ``PagerDutyClient`` with ``.get_sdk()`` or a raw
                ``RestApiV2Client`` instance directly.
        """
        if hasattr(client_or_sdk, "get_sdk"):
            self._sdk: Any = client_or_sdk.get_sdk()  # type: ignore[reportUnknownMemberType]
            self._client: Any = client_or_sdk
        else:
            self._sdk = client_or_sdk
            self._client = None

    def get_data_source(self) -> PagerDutyDataSource:
        """Return the data-source instance itself."""
        return self

    def get_client(self) -> Any:
        """Return the underlying ``PagerDutyClient`` if one was provided."""
        return self._client

    def get_sdk(self) -> Any:
        """Return the raw ``RestApiV2Client`` SDK instance."""
        return self._sdk

    # =====================================================================
    # INCIDENTS
    # =====================================================================

    def list_incidents(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List incidents with optional filters.

        Args:
            params: Query parameters (statuses[], urgencies[], since, until, etc.)

        Returns:
            PagerDutyResponse with list of incidents
        """
        try:
            result = self._sdk.list_all('incidents', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed incidents",
            )
        except Exception as e:
            return _handle_error(e, "list_incidents")

    def get_incident(self, incident_id: str) -> PagerDutyResponse:
        """Get an incident by ID.

        Args:
            incident_id: The incident ID.

        Returns:
            PagerDutyResponse with incident data.
        """
        try:
            result = self._sdk.rget(f'incidents/{incident_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved incident",
            )
        except Exception as e:
            return _handle_error(e, "get_incident")

    def create_incident(
        self,
        from_email: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a new incident.

        Args:
            from_email: The email of the user creating the incident (From header).
            body: Incident payload (type, title, service, etc.).

        Returns:
            PagerDutyResponse with created incident data.
        """
        try:
            result = self._sdk.rpost(  # type: ignore[reportUnknownMemberType]
                'incidents',
                json=body,
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created incident",
            )
        except Exception as e:
            return _handle_error(e, "create_incident")

    def update_incident(
        self,
        incident_id: str,
        from_email: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an existing incident.

        Args:
            incident_id: The incident ID.
            from_email: The email of the user updating the incident (From header).
            body: Updated incident payload.

        Returns:
            PagerDutyResponse with updated incident data.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'incidents/{incident_id}',
                json=body,
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated incident",
            )
        except Exception as e:
            return _handle_error(e, "update_incident")

    def manage_incidents(
        self,
        from_email: str,
        incidents: list[dict[str, Any]],
    ) -> PagerDutyResponse:
        """Manage (bulk update) multiple incidents.

        Args:
            from_email: The email of the user managing incidents (From header).
            incidents: List of incident update objects.

        Returns:
            PagerDutyResponse with managed incidents data.
        """
        try:
            result = self._sdk.put(  # type: ignore[reportUnknownMemberType]
                'incidents',
                json={'incidents': incidents},
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result.json()),  # type: ignore[reportUnknownMemberType]
                message="Successfully managed incidents",
            )
        except Exception as e:
            return _handle_error(e, "manage_incidents")

    def merge_incidents(
        self,
        incident_id: str,
        from_email: str,
        source_incidents: list[dict[str, Any]],
    ) -> PagerDutyResponse:
        """Merge one or more incidents into a target incident.

        Args:
            incident_id: The target incident ID to merge into.
            from_email: The email of the user merging incidents (From header).
            source_incidents: List of source incident references to merge.

        Returns:
            PagerDutyResponse with merged incident data.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'incidents/{incident_id}/merge',
                json={'source_incidents': source_incidents},
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully merged incidents",
            )
        except Exception as e:
            return _handle_error(e, "merge_incidents")

    def list_incident_alerts(
        self,
        incident_id: str,
    ) -> PagerDutyResponse:
        """List alerts for an incident.

        Args:
            incident_id: The incident ID.

        Returns:
            PagerDutyResponse with list of alerts.
        """
        try:
            result = self._sdk.list_all(f'incidents/{incident_id}/alerts')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed incident alerts",
            )
        except Exception as e:
            return _handle_error(e, "list_incident_alerts")

    def get_incident_alert(
        self,
        incident_id: str,
        alert_id: str,
    ) -> PagerDutyResponse:
        """Get a specific alert for an incident.

        Args:
            incident_id: The incident ID.
            alert_id: The alert ID.

        Returns:
            PagerDutyResponse with alert data.
        """
        try:
            result = self._sdk.rget(f'incidents/{incident_id}/alerts/{alert_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved incident alert",
            )
        except Exception as e:
            return _handle_error(e, "get_incident_alert")

    def list_incident_log_entries(
        self,
        incident_id: str,
    ) -> PagerDutyResponse:
        """List log entries for an incident.

        Args:
            incident_id: The incident ID.

        Returns:
            PagerDutyResponse with list of log entries.
        """
        try:
            result = self._sdk.list_all(f'incidents/{incident_id}/log_entries')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed incident log entries",
            )
        except Exception as e:
            return _handle_error(e, "list_incident_log_entries")

    def list_incident_notes(
        self,
        incident_id: str,
    ) -> PagerDutyResponse:
        """List notes for an incident.

        Args:
            incident_id: The incident ID.

        Returns:
            PagerDutyResponse with list of notes.
        """
        try:
            result = self._sdk.list_all(f'incidents/{incident_id}/notes')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed incident notes",
            )
        except Exception as e:
            return _handle_error(e, "list_incident_notes")

    def create_incident_note(
        self,
        incident_id: str,
        from_email: str,
        content: str,
    ) -> PagerDutyResponse:
        """Create a note on an incident.

        Args:
            incident_id: The incident ID.
            from_email: The email of the user creating the note (From header).
            content: The note content.

        Returns:
            PagerDutyResponse with created note data.
        """
        try:
            result = self._sdk.rpost(  # type: ignore[reportUnknownMemberType]
                f'incidents/{incident_id}/notes',
                json={'note': {'content': content}},
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created incident note",
            )
        except Exception as e:
            return _handle_error(e, "create_incident_note")

    def snooze_incident(
        self,
        incident_id: str,
        from_email: str,
        duration: int,
    ) -> PagerDutyResponse:
        """Snooze an incident for a given duration.

        Args:
            incident_id: The incident ID.
            from_email: The email of the user snoozing the incident (From header).
            duration: Duration in seconds to snooze.

        Returns:
            PagerDutyResponse with snoozed incident data.
        """
        try:
            result = self._sdk.rpost(  # type: ignore[reportUnknownMemberType]
                f'incidents/{incident_id}/snooze',
                json={'duration': duration},
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully snoozed incident",
            )
        except Exception as e:
            return _handle_error(e, "snooze_incident")

    # =====================================================================
    # SERVICES
    # =====================================================================

    def list_services(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List services with optional filters.

        Args:
            params: Query parameters (team_ids[], include[], etc.)

        Returns:
            PagerDutyResponse with list of services.
        """
        try:
            result = self._sdk.list_all('services', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed services",
            )
        except Exception as e:
            return _handle_error(e, "list_services")

    def get_service(self, service_id: str) -> PagerDutyResponse:
        """Get a service by ID.

        Args:
            service_id: The service ID.

        Returns:
            PagerDutyResponse with service data.
        """
        try:
            result = self._sdk.rget(f'services/{service_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved service",
            )
        except Exception as e:
            return _handle_error(e, "get_service")

    def create_service(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create a new service.

        Args:
            body: Service payload (name, escalation_policy, etc.).

        Returns:
            PagerDutyResponse with created service data.
        """
        try:
            result = self._sdk.rpost('services', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created service",
            )
        except Exception as e:
            return _handle_error(e, "create_service")

    def update_service(
        self,
        service_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an existing service.

        Args:
            service_id: The service ID.
            body: Updated service payload.

        Returns:
            PagerDutyResponse with updated service data.
        """
        try:
            result = self._sdk.rput(f'services/{service_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated service",
            )
        except Exception as e:
            return _handle_error(e, "update_service")

    def delete_service(self, service_id: str) -> PagerDutyResponse:
        """Delete a service.

        Args:
            service_id: The service ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'services/{service_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted service",
            )
        except Exception as e:
            return _handle_error(e, "delete_service")

    def create_service_integration(
        self,
        service_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create an integration on a service.

        Args:
            service_id: The service ID.
            body: Integration payload (type, vendor, etc.).

        Returns:
            PagerDutyResponse with created integration data.
        """
        try:
            result = self._sdk.rpost(  # type: ignore[reportUnknownMemberType]
                f'services/{service_id}/integrations',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created service integration",
            )
        except Exception as e:
            return _handle_error(e, "create_service_integration")

    # =====================================================================
    # USERS
    # =====================================================================

    def list_users(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List users with optional filters.

        Args:
            params: Query parameters (team_ids[], include[], query, etc.)

        Returns:
            PagerDutyResponse with list of users.
        """
        try:
            result = self._sdk.list_all('users', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed users",
            )
        except Exception as e:
            return _handle_error(e, "list_users")

    def get_user(self, user_id: str) -> PagerDutyResponse:
        """Get a user by ID.

        Args:
            user_id: The user ID.

        Returns:
            PagerDutyResponse with user data.
        """
        try:
            result = self._sdk.rget(f'users/{user_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved user",
            )
        except Exception as e:
            return _handle_error(e, "get_user")

    def get_current_user(self) -> PagerDutyResponse:
        """Get the currently authenticated user.

        Returns:
            PagerDutyResponse with current user data.
        """
        try:
            result = self._sdk.rget('users/me')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved current user",
            )
        except Exception as e:
            return _handle_error(e, "get_current_user")

    def create_user(
        self,
        from_email: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a new user.

        Args:
            from_email: The email of the user making the request (From header).
            body: User payload (name, email, role, etc.).

        Returns:
            PagerDutyResponse with created user data.
        """
        try:
            result = self._sdk.rpost(  # type: ignore[reportUnknownMemberType]
                'users',
                json=body,
                headers={'From': from_email},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created user",
            )
        except Exception as e:
            return _handle_error(e, "create_user")

    def update_user(
        self,
        user_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an existing user.

        Args:
            user_id: The user ID.
            body: Updated user payload.

        Returns:
            PagerDutyResponse with updated user data.
        """
        try:
            result = self._sdk.rput(f'users/{user_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated user",
            )
        except Exception as e:
            return _handle_error(e, "update_user")

    def delete_user(self, user_id: str) -> PagerDutyResponse:
        """Delete a user.

        Args:
            user_id: The user ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'users/{user_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted user",
            )
        except Exception as e:
            return _handle_error(e, "delete_user")

    def list_user_contact_methods(
        self,
        user_id: str,
    ) -> PagerDutyResponse:
        """List contact methods for a user.

        Args:
            user_id: The user ID.

        Returns:
            PagerDutyResponse with list of contact methods.
        """
        try:
            result = self._sdk.list_all(f'users/{user_id}/contact_methods')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed user contact methods",
            )
        except Exception as e:
            return _handle_error(e, "list_user_contact_methods")

    def list_user_notification_rules(
        self,
        user_id: str,
    ) -> PagerDutyResponse:
        """List notification rules for a user.

        Args:
            user_id: The user ID.

        Returns:
            PagerDutyResponse with list of notification rules.
        """
        try:
            result = self._sdk.list_all(f'users/{user_id}/notification_rules')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed user notification rules",
            )
        except Exception as e:
            return _handle_error(e, "list_user_notification_rules")

    # =====================================================================
    # TEAMS
    # =====================================================================

    def list_teams(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List teams with optional filters.

        Args:
            params: Query parameters (query, etc.)

        Returns:
            PagerDutyResponse with list of teams.
        """
        try:
            result = self._sdk.list_all('teams', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed teams",
            )
        except Exception as e:
            return _handle_error(e, "list_teams")

    def get_team(self, team_id: str) -> PagerDutyResponse:
        """Get a team by ID.

        Args:
            team_id: The team ID.

        Returns:
            PagerDutyResponse with team data.
        """
        try:
            result = self._sdk.rget(f'teams/{team_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved team",
            )
        except Exception as e:
            return _handle_error(e, "get_team")

    def create_team(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create a new team.

        Args:
            body: Team payload (name, description, etc.).

        Returns:
            PagerDutyResponse with created team data.
        """
        try:
            result = self._sdk.rpost('teams', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created team",
            )
        except Exception as e:
            return _handle_error(e, "create_team")

    def update_team(
        self,
        team_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an existing team.

        Args:
            team_id: The team ID.
            body: Updated team payload.

        Returns:
            PagerDutyResponse with updated team data.
        """
        try:
            result = self._sdk.rput(f'teams/{team_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated team",
            )
        except Exception as e:
            return _handle_error(e, "update_team")

    def delete_team(self, team_id: str) -> PagerDutyResponse:
        """Delete a team.

        Args:
            team_id: The team ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'teams/{team_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted team",
            )
        except Exception as e:
            return _handle_error(e, "delete_team")

    def list_team_members(self, team_id: str) -> PagerDutyResponse:
        """List members of a team.

        Args:
            team_id: The team ID.

        Returns:
            PagerDutyResponse with list of team members.
        """
        try:
            result = self._sdk.list_all(f'teams/{team_id}/members')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed team members",
            )
        except Exception as e:
            return _handle_error(e, "list_team_members")

    def add_user_to_team(
        self,
        team_id: str,
        user_id: str,
        role: str,
    ) -> PagerDutyResponse:
        """Add a user to a team with a given role.

        Args:
            team_id: The team ID.
            user_id: The user ID.
            role: The role for the user in the team.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'teams/{team_id}/users/{user_id}',
                json={'role': role},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result) if result else None,
                message="Successfully added user to team",
            )
        except Exception as e:
            return _handle_error(e, "add_user_to_team")

    def remove_user_from_team(
        self,
        team_id: str,
        user_id: str,
    ) -> PagerDutyResponse:
        """Remove a user from a team.

        Args:
            team_id: The team ID.
            user_id: The user ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'teams/{team_id}/users/{user_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully removed user from team",
            )
        except Exception as e:
            return _handle_error(e, "remove_user_from_team")

    # =====================================================================
    # ESCALATION POLICIES
    # =====================================================================

    def list_escalation_policies(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List escalation policies with optional filters.

        Args:
            params: Query parameters (query, user_ids[], team_ids[], etc.)

        Returns:
            PagerDutyResponse with list of escalation policies.
        """
        try:
            result = self._sdk.list_all('escalation_policies', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed escalation policies",
            )
        except Exception as e:
            return _handle_error(e, "list_escalation_policies")

    def get_escalation_policy(
        self,
        policy_id: str,
    ) -> PagerDutyResponse:
        """Get an escalation policy by ID.

        Args:
            policy_id: The escalation policy ID.

        Returns:
            PagerDutyResponse with escalation policy data.
        """
        try:
            result = self._sdk.rget(f'escalation_policies/{policy_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved escalation policy",
            )
        except Exception as e:
            return _handle_error(e, "get_escalation_policy")

    def create_escalation_policy(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a new escalation policy.

        Args:
            body: Escalation policy payload.

        Returns:
            PagerDutyResponse with created escalation policy data.
        """
        try:
            result = self._sdk.rpost('escalation_policies', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created escalation policy",
            )
        except Exception as e:
            return _handle_error(e, "create_escalation_policy")

    def update_escalation_policy(
        self,
        policy_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an existing escalation policy.

        Args:
            policy_id: The escalation policy ID.
            body: Updated escalation policy payload.

        Returns:
            PagerDutyResponse with updated escalation policy data.
        """
        try:
            result = self._sdk.rput(f'escalation_policies/{policy_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated escalation policy",
            )
        except Exception as e:
            return _handle_error(e, "update_escalation_policy")

    def delete_escalation_policy(
        self,
        policy_id: str,
    ) -> PagerDutyResponse:
        """Delete an escalation policy.

        Args:
            policy_id: The escalation policy ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'escalation_policies/{policy_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted escalation policy",
            )
        except Exception as e:
            return _handle_error(e, "delete_escalation_policy")

    # =====================================================================
    # SCHEDULES
    # =====================================================================

    def list_schedules(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List schedules with optional filters.

        Args:
            params: Query parameters (query, etc.)

        Returns:
            PagerDutyResponse with list of schedules.
        """
        try:
            result = self._sdk.list_all('schedules', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed schedules",
            )
        except Exception as e:
            return _handle_error(e, "list_schedules")

    def get_schedule(
        self,
        schedule_id: str,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """Get a schedule by ID.

        Args:
            schedule_id: The schedule ID.
            params: Optional query parameters (since, until, time_zone, etc.)

        Returns:
            PagerDutyResponse with schedule data.
        """
        try:
            result = self._sdk.rget(  # type: ignore[reportUnknownMemberType]
                f'schedules/{schedule_id}',
                params=params or {},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved schedule",
            )
        except Exception as e:
            return _handle_error(e, "get_schedule")

    def create_schedule(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create a new schedule.

        Args:
            body: Schedule payload.

        Returns:
            PagerDutyResponse with created schedule data.
        """
        try:
            result = self._sdk.rpost('schedules', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created schedule",
            )
        except Exception as e:
            return _handle_error(e, "create_schedule")

    def update_schedule(
        self,
        schedule_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an existing schedule.

        Args:
            schedule_id: The schedule ID.
            body: Updated schedule payload.

        Returns:
            PagerDutyResponse with updated schedule data.
        """
        try:
            result = self._sdk.rput(f'schedules/{schedule_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated schedule",
            )
        except Exception as e:
            return _handle_error(e, "update_schedule")

    def delete_schedule(self, schedule_id: str) -> PagerDutyResponse:
        """Delete a schedule.

        Args:
            schedule_id: The schedule ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'schedules/{schedule_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted schedule",
            )
        except Exception as e:
            return _handle_error(e, "delete_schedule")

    def list_schedule_overrides(
        self,
        schedule_id: str,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List overrides for a schedule.

        Args:
            schedule_id: The schedule ID.
            params: Query parameters (since, until - required by API).

        Returns:
            PagerDutyResponse with list of overrides.
        """
        try:
            result = self._sdk.list_all(  # type: ignore[reportUnknownMemberType]
                f'schedules/{schedule_id}/overrides',
                params=params or {},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed schedule overrides",
            )
        except Exception as e:
            return _handle_error(e, "list_schedule_overrides")

    def create_schedule_overrides(
        self,
        schedule_id: str,
        overrides: list[dict[str, Any]],
    ) -> PagerDutyResponse:
        """Create overrides on a schedule.

        Args:
            schedule_id: The schedule ID.
            overrides: List of override objects (start, end, user).

        Returns:
            PagerDutyResponse with created overrides data.
        """
        try:
            result = self._sdk.rpost(  # type: ignore[reportUnknownMemberType]
                f'schedules/{schedule_id}/overrides',
                json={'overrides': overrides},
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created schedule overrides",
            )
        except Exception as e:
            return _handle_error(e, "create_schedule_overrides")

    # =====================================================================
    # ON-CALLS
    # =====================================================================

    def list_oncalls(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List current on-call entries.

        Args:
            params: Query parameters (schedule_ids[], user_ids[], since, until, etc.)

        Returns:
            PagerDutyResponse with list of on-call entries.
        """
        try:
            result = self._sdk.list_all('oncalls', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed on-calls",
            )
        except Exception as e:
            return _handle_error(e, "list_oncalls")

    # =====================================================================
    # PRIORITIES
    # =====================================================================

    def list_priorities(self) -> PagerDutyResponse:
        """List all incident priorities.

        Returns:
            PagerDutyResponse with list of priorities.
        """
        try:
            result = self._sdk.list_all('priorities')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed priorities",
            )
        except Exception as e:
            return _handle_error(e, "list_priorities")

    # =====================================================================
    # VENDORS
    # =====================================================================

    def list_vendors(self) -> PagerDutyResponse:
        """List all vendors (integration types).

        Returns:
            PagerDutyResponse with list of vendors.
        """
        try:
            result = self._sdk.list_all('vendors')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed vendors",
            )
        except Exception as e:
            return _handle_error(e, "list_vendors")

    def get_vendor(self, vendor_id: str) -> PagerDutyResponse:
        """Get a vendor by ID.

        Args:
            vendor_id: The vendor ID.

        Returns:
            PagerDutyResponse with vendor data.
        """
        try:
            result = self._sdk.rget(f'vendors/{vendor_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved vendor",
            )
        except Exception as e:
            return _handle_error(e, "get_vendor")

    # =====================================================================
    # TAGS
    # =====================================================================

    def list_tags(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List tags with optional filters.

        Args:
            params: Query parameters (query, etc.)

        Returns:
            PagerDutyResponse with list of tags.
        """
        try:
            result = self._sdk.list_all('tags', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed tags",
            )
        except Exception as e:
            return _handle_error(e, "list_tags")

    def create_tag(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create a new tag.

        Args:
            body: Tag payload (label, etc.).

        Returns:
            PagerDutyResponse with created tag data.
        """
        try:
            result = self._sdk.rpost('tags', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created tag",
            )
        except Exception as e:
            return _handle_error(e, "create_tag")

    def delete_tag(self, tag_id: str) -> PagerDutyResponse:
        """Delete a tag.

        Args:
            tag_id: The tag ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'tags/{tag_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted tag",
            )
        except Exception as e:
            return _handle_error(e, "delete_tag")

    # =====================================================================
    # LOG ENTRIES
    # =====================================================================

    def list_log_entries(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List log entries with optional filters.

        Args:
            params: Query parameters (since, until, is_overview, etc.)

        Returns:
            PagerDutyResponse with list of log entries.
        """
        try:
            result = self._sdk.list_all('log_entries', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed log entries",
            )
        except Exception as e:
            return _handle_error(e, "list_log_entries")

    def get_log_entry(self, log_entry_id: str) -> PagerDutyResponse:
        """Get a log entry by ID.

        Args:
            log_entry_id: The log entry ID.

        Returns:
            PagerDutyResponse with log entry data.
        """
        try:
            result = self._sdk.rget(f'log_entries/{log_entry_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved log entry",
            )
        except Exception as e:
            return _handle_error(e, "get_log_entry")

    # =====================================================================
    # NOTIFICATIONS
    # =====================================================================

    def list_notifications(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List notifications with optional filters.

        Args:
            params: Query parameters (since, until, filter, etc.)

        Returns:
            PagerDutyResponse with list of notifications.
        """
        try:
            result = self._sdk.list_all('notifications', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed notifications",
            )
        except Exception as e:
            return _handle_error(e, "list_notifications")

    # =====================================================================
    # ABILITIES
    # =====================================================================

    def list_abilities(self) -> PagerDutyResponse:
        """List all account abilities.

        Returns:
            PagerDutyResponse with list of abilities.
        """
        try:
            result = self._sdk.list_all('abilities')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed abilities",
            )
        except Exception as e:
            return _handle_error(e, "list_abilities")

    def test_ability(self, ability_id: str) -> PagerDutyResponse:
        """Test if an account has a given ability.

        Args:
            ability_id: The ability name/ID to test.

        Returns:
            PagerDutyResponse indicating whether the ability is available.
        """
        try:
            result = self._sdk.rget(f'abilities/{ability_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result) if result else None,
                message="Ability is available",
            )
        except Exception as e:
            return _handle_error(e, "test_ability")

    # =====================================================================
    # ADD-ONS
    # =====================================================================

    def list_addons(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List add-ons with optional filters.

        Args:
            params: Query parameters (filter, service_ids[], etc.)

        Returns:
            PagerDutyResponse with list of add-ons.
        """
        try:
            result = self._sdk.list_all('addons', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed add-ons",
            )
        except Exception as e:
            return _handle_error(e, "list_addons")

    def get_addon(self, addon_id: str) -> PagerDutyResponse:
        """Get an add-on by ID.

        Args:
            addon_id: The add-on ID.

        Returns:
            PagerDutyResponse with add-on data.
        """
        try:
            result = self._sdk.rget(f'addons/{addon_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved add-on",
            )
        except Exception as e:
            return _handle_error(e, "get_addon")

    def install_addon(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Install an add-on.

        Args:
            body: Add-on payload (type, name, src, etc.).

        Returns:
            PagerDutyResponse with installed add-on data.
        """
        try:
            result = self._sdk.rpost('addons', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully installed add-on",
            )
        except Exception as e:
            return _handle_error(e, "install_addon")

    def update_addon(
        self,
        addon_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an add-on.

        Args:
            addon_id: The add-on ID.
            body: Updated add-on payload.

        Returns:
            PagerDutyResponse with updated add-on data.
        """
        try:
            result = self._sdk.rput(f'addons/{addon_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated add-on",
            )
        except Exception as e:
            return _handle_error(e, "update_addon")

    def delete_addon(self, addon_id: str) -> PagerDutyResponse:
        """Delete an add-on.

        Args:
            addon_id: The add-on ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'addons/{addon_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted add-on",
            )
        except Exception as e:
            return _handle_error(e, "delete_addon")

    # =====================================================================
    # MAINTENANCE WINDOWS
    # =====================================================================

    def list_maintenance_windows(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List maintenance windows with optional filters.

        Args:
            params: Query parameters (filter, service_ids[], team_ids[], etc.)

        Returns:
            PagerDutyResponse with list of maintenance windows.
        """
        try:
            result = self._sdk.list_all('maintenance_windows', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed maintenance windows",
            )
        except Exception as e:
            return _handle_error(e, "list_maintenance_windows")

    def get_maintenance_window(
        self,
        window_id: str,
    ) -> PagerDutyResponse:
        """Get a maintenance window by ID.

        Args:
            window_id: The maintenance window ID.

        Returns:
            PagerDutyResponse with maintenance window data.
        """
        try:
            result = self._sdk.rget(f'maintenance_windows/{window_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved maintenance window",
            )
        except Exception as e:
            return _handle_error(e, "get_maintenance_window")

    def create_maintenance_window(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a maintenance window.

        Args:
            body: Maintenance window payload (start_time, end_time, services, etc.).

        Returns:
            PagerDutyResponse with created maintenance window data.
        """
        try:
            result = self._sdk.rpost('maintenance_windows', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created maintenance window",
            )
        except Exception as e:
            return _handle_error(e, "create_maintenance_window")

    def update_maintenance_window(
        self,
        window_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update a maintenance window.

        Args:
            window_id: The maintenance window ID.
            body: Updated maintenance window payload.

        Returns:
            PagerDutyResponse with updated maintenance window data.
        """
        try:
            result = self._sdk.rput(f'maintenance_windows/{window_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated maintenance window",
            )
        except Exception as e:
            return _handle_error(e, "update_maintenance_window")

    def delete_maintenance_window(
        self,
        window_id: str,
    ) -> PagerDutyResponse:
        """Delete a maintenance window.

        Args:
            window_id: The maintenance window ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'maintenance_windows/{window_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted maintenance window",
            )
        except Exception as e:
            return _handle_error(e, "delete_maintenance_window")

    # =====================================================================
    # EXTENSIONS
    # =====================================================================

    def list_extensions(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List extensions with optional filters.

        Args:
            params: Query parameters (query, extension_object_id, etc.)

        Returns:
            PagerDutyResponse with list of extensions.
        """
        try:
            result = self._sdk.list_all('extensions', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed extensions",
            )
        except Exception as e:
            return _handle_error(e, "list_extensions")

    def get_extension(self, extension_id: str) -> PagerDutyResponse:
        """Get an extension by ID.

        Args:
            extension_id: The extension ID.

        Returns:
            PagerDutyResponse with extension data.
        """
        try:
            result = self._sdk.rget(f'extensions/{extension_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved extension",
            )
        except Exception as e:
            return _handle_error(e, "get_extension")

    def create_extension(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create an extension.

        Args:
            body: Extension payload (type, name, endpoint_url, extension_objects, etc.).

        Returns:
            PagerDutyResponse with created extension data.
        """
        try:
            result = self._sdk.rpost('extensions', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created extension",
            )
        except Exception as e:
            return _handle_error(e, "create_extension")

    def update_extension(
        self,
        extension_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an extension.

        Args:
            extension_id: The extension ID.
            body: Updated extension payload.

        Returns:
            PagerDutyResponse with updated extension data.
        """
        try:
            result = self._sdk.rput(f'extensions/{extension_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated extension",
            )
        except Exception as e:
            return _handle_error(e, "update_extension")

    def delete_extension(self, extension_id: str) -> PagerDutyResponse:
        """Delete an extension.

        Args:
            extension_id: The extension ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'extensions/{extension_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted extension",
            )
        except Exception as e:
            return _handle_error(e, "delete_extension")

    # =====================================================================
    # BUSINESS SERVICES
    # =====================================================================

    def list_business_services(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List business services with optional filters.

        Args:
            params: Query parameters.

        Returns:
            PagerDutyResponse with list of business services.
        """
        try:
            result = self._sdk.list_all('business_services', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed business services",
            )
        except Exception as e:
            return _handle_error(e, "list_business_services")

    def get_business_service(
        self,
        service_id: str,
    ) -> PagerDutyResponse:
        """Get a business service by ID.

        Args:
            service_id: The business service ID.

        Returns:
            PagerDutyResponse with business service data.
        """
        try:
            result = self._sdk.rget(f'business_services/{service_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved business service",
            )
        except Exception as e:
            return _handle_error(e, "get_business_service")

    def create_business_service(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a business service.

        Args:
            body: Business service payload (name, description, etc.).

        Returns:
            PagerDutyResponse with created business service data.
        """
        try:
            result = self._sdk.rpost('business_services', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created business service",
            )
        except Exception as e:
            return _handle_error(e, "create_business_service")

    def update_business_service(
        self,
        service_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update a business service.

        Args:
            service_id: The business service ID.
            body: Updated business service payload.

        Returns:
            PagerDutyResponse with updated business service data.
        """
        try:
            result = self._sdk.rput(f'business_services/{service_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated business service",
            )
        except Exception as e:
            return _handle_error(e, "update_business_service")

    def delete_business_service(
        self,
        service_id: str,
    ) -> PagerDutyResponse:
        """Delete a business service.

        Args:
            service_id: The business service ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'business_services/{service_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted business service",
            )
        except Exception as e:
            return _handle_error(e, "delete_business_service")

    # =====================================================================
    # RULESETS
    # =====================================================================

    def list_rulesets(self) -> PagerDutyResponse:
        """List all rulesets.

        Returns:
            PagerDutyResponse with list of rulesets.
        """
        try:
            result = self._sdk.list_all('rulesets')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed rulesets",
            )
        except Exception as e:
            return _handle_error(e, "list_rulesets")

    def get_ruleset(self, ruleset_id: str) -> PagerDutyResponse:
        """Get a ruleset by ID.

        Args:
            ruleset_id: The ruleset ID.

        Returns:
            PagerDutyResponse with ruleset data.
        """
        try:
            result = self._sdk.rget(f'rulesets/{ruleset_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved ruleset",
            )
        except Exception as e:
            return _handle_error(e, "get_ruleset")

    def create_ruleset(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create a new ruleset.

        Args:
            body: Ruleset payload.

        Returns:
            PagerDutyResponse with created ruleset data.
        """
        try:
            result = self._sdk.rpost('rulesets', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created ruleset",
            )
        except Exception as e:
            return _handle_error(e, "create_ruleset")

    def update_ruleset(
        self,
        ruleset_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update a ruleset.

        Args:
            ruleset_id: The ruleset ID.
            body: Updated ruleset payload.

        Returns:
            PagerDutyResponse with updated ruleset data.
        """
        try:
            result = self._sdk.rput(f'rulesets/{ruleset_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated ruleset",
            )
        except Exception as e:
            return _handle_error(e, "update_ruleset")

    def delete_ruleset(self, ruleset_id: str) -> PagerDutyResponse:
        """Delete a ruleset.

        Args:
            ruleset_id: The ruleset ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'rulesets/{ruleset_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted ruleset",
            )
        except Exception as e:
            return _handle_error(e, "delete_ruleset")

    def list_ruleset_rules(self, ruleset_id: str) -> PagerDutyResponse:
        """List rules for a ruleset.

        Args:
            ruleset_id: The ruleset ID.

        Returns:
            PagerDutyResponse with list of ruleset rules.
        """
        try:
            result = self._sdk.list_all(f'rulesets/{ruleset_id}/rules')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed ruleset rules",
            )
        except Exception as e:
            return _handle_error(e, "list_ruleset_rules")

    def create_ruleset_rule(
        self,
        ruleset_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a rule on a ruleset.

        Args:
            ruleset_id: The ruleset ID.
            body: Rule payload.

        Returns:
            PagerDutyResponse with created rule data.
        """
        try:
            result = self._sdk.rpost(f'rulesets/{ruleset_id}/rules', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created ruleset rule",
            )
        except Exception as e:
            return _handle_error(e, "create_ruleset_rule")

    def update_ruleset_rule(
        self,
        ruleset_id: str,
        rule_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update a rule on a ruleset.

        Args:
            ruleset_id: The ruleset ID.
            rule_id: The rule ID.
            body: Updated rule payload.

        Returns:
            PagerDutyResponse with updated rule data.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'rulesets/{ruleset_id}/rules/{rule_id}',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated ruleset rule",
            )
        except Exception as e:
            return _handle_error(e, "update_ruleset_rule")

    def delete_ruleset_rule(
        self,
        ruleset_id: str,
        rule_id: str,
    ) -> PagerDutyResponse:
        """Delete a rule from a ruleset.

        Args:
            ruleset_id: The ruleset ID.
            rule_id: The rule ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'rulesets/{ruleset_id}/rules/{rule_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted ruleset rule",
            )
        except Exception as e:
            return _handle_error(e, "delete_ruleset_rule")

    # =====================================================================
    # WEBHOOKS
    # =====================================================================

    def list_webhook_subscriptions(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List webhook subscriptions with optional filters.

        Args:
            params: Query parameters.

        Returns:
            PagerDutyResponse with list of webhook subscriptions.
        """
        try:
            result = self._sdk.list_all('webhook_subscriptions', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed webhook subscriptions",
            )
        except Exception as e:
            return _handle_error(e, "list_webhook_subscriptions")

    def get_webhook_subscription(
        self,
        subscription_id: str,
    ) -> PagerDutyResponse:
        """Get a webhook subscription by ID.

        Args:
            subscription_id: The webhook subscription ID.

        Returns:
            PagerDutyResponse with webhook subscription data.
        """
        try:
            result = self._sdk.rget(f'webhook_subscriptions/{subscription_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved webhook subscription",
            )
        except Exception as e:
            return _handle_error(e, "get_webhook_subscription")

    def create_webhook_subscription(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a webhook subscription.

        Args:
            body: Webhook subscription payload (delivery_method, events, filter, etc.).

        Returns:
            PagerDutyResponse with created webhook subscription data.
        """
        try:
            result = self._sdk.rpost('webhook_subscriptions', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created webhook subscription",
            )
        except Exception as e:
            return _handle_error(e, "create_webhook_subscription")

    def update_webhook_subscription(
        self,
        subscription_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update a webhook subscription.

        Args:
            subscription_id: The webhook subscription ID.
            body: Updated webhook subscription payload.

        Returns:
            PagerDutyResponse with updated webhook subscription data.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'webhook_subscriptions/{subscription_id}',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated webhook subscription",
            )
        except Exception as e:
            return _handle_error(e, "update_webhook_subscription")

    def delete_webhook_subscription(
        self,
        subscription_id: str,
    ) -> PagerDutyResponse:
        """Delete a webhook subscription.

        Args:
            subscription_id: The webhook subscription ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'webhook_subscriptions/{subscription_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted webhook subscription",
            )
        except Exception as e:
            return _handle_error(e, "delete_webhook_subscription")

    # =====================================================================
    # ANALYTICS
    # =====================================================================

    def get_aggregated_incident_data(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Get aggregated incident analytics data.

        Args:
            body: Analytics query payload (filters, aggregate_unit, time_zone, etc.).

        Returns:
            PagerDutyResponse with aggregated incident analytics.
        """
        try:
            response = self._sdk.post(  # type: ignore[reportUnknownMemberType]
                'analytics/metrics/incidents/all',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], response.json()),  # type: ignore[reportUnknownMemberType]
                message="Successfully retrieved aggregated incident data",
            )
        except Exception as e:
            return _handle_error(e, "get_aggregated_incident_data")

    def get_raw_incidents(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Get raw incident analytics data.

        Args:
            body: Analytics query payload (filters, starting_after, ending_before, etc.).

        Returns:
            PagerDutyResponse with raw incident analytics.
        """
        try:
            response = self._sdk.post(  # type: ignore[reportUnknownMemberType]
                'analytics/raw/incidents',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], response.json()),  # type: ignore[reportUnknownMemberType]
                message="Successfully retrieved raw incidents data",
            )
        except Exception as e:
            return _handle_error(e, "get_raw_incidents")

    def get_raw_incident(self, incident_id: str) -> PagerDutyResponse:
        """Get raw analytics for a specific incident.

        Args:
            incident_id: The incident ID.

        Returns:
            PagerDutyResponse with raw incident analytics.
        """
        try:
            response = self._sdk.get(f'analytics/raw/incidents/{incident_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], response.json()),  # type: ignore[reportUnknownMemberType]
                message="Successfully retrieved raw incident data",
            )
        except Exception as e:
            return _handle_error(e, "get_raw_incident")

    # =====================================================================
    # AUDIT
    # =====================================================================

    def list_audit_records(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List audit records.

        Args:
            params: Query parameters (since, until, root_resource_types[], etc.)

        Returns:
            PagerDutyResponse with audit records data.
        """
        try:
            response = self._sdk.get('audit/records', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], response.json()),  # type: ignore[reportUnknownMemberType]
                message="Successfully listed audit records",
            )
        except Exception as e:
            return _handle_error(e, "list_audit_records")

    # =====================================================================
    # EVENT ORCHESTRATIONS
    # =====================================================================

    def list_event_orchestrations(self) -> PagerDutyResponse:
        """List all event orchestrations.

        Returns:
            PagerDutyResponse with list of event orchestrations.
        """
        try:
            result = self._sdk.list_all('event_orchestrations')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed event orchestrations",
            )
        except Exception as e:
            return _handle_error(e, "list_event_orchestrations")

    def get_event_orchestration(
        self,
        orchestration_id: str,
    ) -> PagerDutyResponse:
        """Get an event orchestration by ID.

        Args:
            orchestration_id: The event orchestration ID.

        Returns:
            PagerDutyResponse with event orchestration data.
        """
        try:
            result = self._sdk.rget(f'event_orchestrations/{orchestration_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved event orchestration",
            )
        except Exception as e:
            return _handle_error(e, "get_event_orchestration")

    def create_event_orchestration(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create an event orchestration.

        Args:
            body: Event orchestration payload.

        Returns:
            PagerDutyResponse with created event orchestration data.
        """
        try:
            result = self._sdk.rpost('event_orchestrations', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created event orchestration",
            )
        except Exception as e:
            return _handle_error(e, "create_event_orchestration")

    def update_event_orchestration(
        self,
        orchestration_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an event orchestration.

        Args:
            orchestration_id: The event orchestration ID.
            body: Updated event orchestration payload.

        Returns:
            PagerDutyResponse with updated event orchestration data.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'event_orchestrations/{orchestration_id}',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated event orchestration",
            )
        except Exception as e:
            return _handle_error(e, "update_event_orchestration")

    def delete_event_orchestration(
        self,
        orchestration_id: str,
    ) -> PagerDutyResponse:
        """Delete an event orchestration.

        Args:
            orchestration_id: The event orchestration ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'event_orchestrations/{orchestration_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted event orchestration",
            )
        except Exception as e:
            return _handle_error(e, "delete_event_orchestration")

    # =====================================================================
    # ALERT GROUPING SETTINGS
    # =====================================================================

    def list_alert_grouping_settings(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List alert grouping settings with optional filters.

        Args:
            params: Query parameters.

        Returns:
            PagerDutyResponse with list of alert grouping settings.
        """
        try:
            result = self._sdk.list_all('alert_grouping_settings', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed alert grouping settings",
            )
        except Exception as e:
            return _handle_error(e, "list_alert_grouping_settings")

    def get_alert_grouping_setting(
        self,
        setting_id: str,
    ) -> PagerDutyResponse:
        """Get an alert grouping setting by ID.

        Args:
            setting_id: The alert grouping setting ID.

        Returns:
            PagerDutyResponse with alert grouping setting data.
        """
        try:
            result = self._sdk.rget(f'alert_grouping_settings/{setting_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved alert grouping setting",
            )
        except Exception as e:
            return _handle_error(e, "get_alert_grouping_setting")

    def create_alert_grouping_setting(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create an alert grouping setting.

        Args:
            body: Alert grouping setting payload.

        Returns:
            PagerDutyResponse with created alert grouping setting data.
        """
        try:
            result = self._sdk.rpost('alert_grouping_settings', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created alert grouping setting",
            )
        except Exception as e:
            return _handle_error(e, "create_alert_grouping_setting")

    def update_alert_grouping_setting(
        self,
        setting_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an alert grouping setting.

        Args:
            setting_id: The alert grouping setting ID.
            body: Updated alert grouping setting payload.

        Returns:
            PagerDutyResponse with updated alert grouping setting data.
        """
        try:
            result = self._sdk.rput(  # type: ignore[reportUnknownMemberType]
                f'alert_grouping_settings/{setting_id}',
                json=body,
            )
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated alert grouping setting",
            )
        except Exception as e:
            return _handle_error(e, "update_alert_grouping_setting")

    def delete_alert_grouping_setting(
        self,
        setting_id: str,
    ) -> PagerDutyResponse:
        """Delete an alert grouping setting.

        Args:
            setting_id: The alert grouping setting ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'alert_grouping_settings/{setting_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted alert grouping setting",
            )
        except Exception as e:
            return _handle_error(e, "delete_alert_grouping_setting")

    # =====================================================================
    # CHANGE EVENTS
    # =====================================================================

    def list_change_events(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List change events with optional filters.

        Args:
            params: Query parameters (since, until, team_ids[], etc.)

        Returns:
            PagerDutyResponse with list of change events.
        """
        try:
            result = self._sdk.list_all('change_events', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed change events",
            )
        except Exception as e:
            return _handle_error(e, "list_change_events")

    def get_change_event(
        self,
        change_event_id: str,
    ) -> PagerDutyResponse:
        """Get a change event by ID.

        Args:
            change_event_id: The change event ID.

        Returns:
            PagerDutyResponse with change event data.
        """
        try:
            result = self._sdk.rget(f'change_events/{change_event_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved change event",
            )
        except Exception as e:
            return _handle_error(e, "get_change_event")

    def create_change_event(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create a change event.

        Args:
            body: Change event payload.

        Returns:
            PagerDutyResponse with created change event data.
        """
        try:
            result = self._sdk.rpost('change_events', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created change event",
            )
        except Exception as e:
            return _handle_error(e, "create_change_event")

    # =====================================================================
    # LICENSES
    # =====================================================================

    def list_licenses(self) -> PagerDutyResponse:
        """List all licenses.

        Returns:
            PagerDutyResponse with list of licenses.
        """
        try:
            result = self._sdk.list_all('licenses')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed licenses",
            )
        except Exception as e:
            return _handle_error(e, "list_licenses")

    def list_license_allocations(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List license allocations with optional filters.

        Args:
            params: Query parameters.

        Returns:
            PagerDutyResponse with list of license allocations.
        """
        try:
            result = self._sdk.list_all('license_allocations', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed license allocations",
            )
        except Exception as e:
            return _handle_error(e, "list_license_allocations")

    # =====================================================================
    # INCIDENT WORKFLOWS
    # =====================================================================

    def list_incident_workflows(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List incident workflows with optional filters.

        Args:
            params: Query parameters (query, etc.)

        Returns:
            PagerDutyResponse with list of incident workflows.
        """
        try:
            result = self._sdk.list_all('incident_workflows', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed incident workflows",
            )
        except Exception as e:
            return _handle_error(e, "list_incident_workflows")

    def get_incident_workflow(
        self,
        workflow_id: str,
    ) -> PagerDutyResponse:
        """Get an incident workflow by ID.

        Args:
            workflow_id: The incident workflow ID.

        Returns:
            PagerDutyResponse with incident workflow data.
        """
        try:
            result = self._sdk.rget(f'incident_workflows/{workflow_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved incident workflow",
            )
        except Exception as e:
            return _handle_error(e, "get_incident_workflow")

    def create_incident_workflow(
        self,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Create an incident workflow.

        Args:
            body: Incident workflow payload.

        Returns:
            PagerDutyResponse with created incident workflow data.
        """
        try:
            result = self._sdk.rpost('incident_workflows', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created incident workflow",
            )
        except Exception as e:
            return _handle_error(e, "create_incident_workflow")

    def update_incident_workflow(
        self,
        workflow_id: str,
        body: dict[str, Any],
    ) -> PagerDutyResponse:
        """Update an incident workflow.

        Args:
            workflow_id: The incident workflow ID.
            body: Updated incident workflow payload.

        Returns:
            PagerDutyResponse with updated incident workflow data.
        """
        try:
            result = self._sdk.rput(f'incident_workflows/{workflow_id}', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully updated incident workflow",
            )
        except Exception as e:
            return _handle_error(e, "update_incident_workflow")

    def delete_incident_workflow(
        self,
        workflow_id: str,
    ) -> PagerDutyResponse:
        """Delete an incident workflow.

        Args:
            workflow_id: The incident workflow ID.

        Returns:
            PagerDutyResponse indicating success or failure.
        """
        try:
            self._sdk.rdelete(f'incident_workflows/{workflow_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                message="Successfully deleted incident workflow",
            )
        except Exception as e:
            return _handle_error(e, "delete_incident_workflow")

    # =====================================================================
    # STATUS DASHBOARDS
    # =====================================================================

    def list_status_dashboards(self) -> PagerDutyResponse:
        """List all status dashboards.

        Returns:
            PagerDutyResponse with list of status dashboards.
        """
        try:
            result = self._sdk.list_all('status_dashboards')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed status dashboards",
            )
        except Exception as e:
            return _handle_error(e, "list_status_dashboards")

    def get_status_dashboard(
        self,
        dashboard_id: str,
    ) -> PagerDutyResponse:
        """Get a status dashboard by ID.

        Args:
            dashboard_id: The status dashboard ID.

        Returns:
            PagerDutyResponse with status dashboard data.
        """
        try:
            result = self._sdk.rget(f'status_dashboards/{dashboard_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved status dashboard",
            )
        except Exception as e:
            return _handle_error(e, "get_status_dashboard")

    # =====================================================================
    # TEMPLATES
    # =====================================================================

    def list_templates(
        self,
        params: dict[str, Any] | None = None,
    ) -> PagerDutyResponse:
        """List templates with optional filters.

        Args:
            params: Query parameters (query, template_type, etc.)

        Returns:
            PagerDutyResponse with list of templates.
        """
        try:
            result = self._sdk.list_all('templates', params=params or {})  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed templates",
            )
        except Exception as e:
            return _handle_error(e, "list_templates")

    def get_template(self, template_id: str) -> PagerDutyResponse:
        """Get a template by ID.

        Args:
            template_id: The template ID.

        Returns:
            PagerDutyResponse with template data.
        """
        try:
            result = self._sdk.rget(f'templates/{template_id}')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully retrieved template",
            )
        except Exception as e:
            return _handle_error(e, "get_template")

    def create_template(self, body: dict[str, Any]) -> PagerDutyResponse:
        """Create a template.

        Args:
            body: Template payload.

        Returns:
            PagerDutyResponse with created template data.
        """
        try:
            result = self._sdk.rpost('templates', json=body)  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(dict[str, object], result),
                message="Successfully created template",
            )
        except Exception as e:
            return _handle_error(e, "create_template")

    # =====================================================================
    # STANDARDS
    # =====================================================================

    def list_standards(self) -> PagerDutyResponse:
        """List all standards.

        Returns:
            PagerDutyResponse with list of standards.
        """
        try:
            result = self._sdk.list_all('standards')  # type: ignore[reportUnknownMemberType]
            return PagerDutyResponse(
                success=True,
                data=cast(list[object], result),
                message="Successfully listed standards",
            )
        except Exception as e:
            return _handle_error(e, "list_standards")
