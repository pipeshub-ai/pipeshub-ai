"""
Webex DataSource - API wrapper using the official wxc_sdk.

Provides typed wrapper methods for common Webex operations including
people, rooms/spaces, messages, teams, meetings, memberships,
organizations, webhooks, and recordings.

All methods return WebexResponse objects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from wxc_sdk import WebexSimpleApi  # type: ignore[import-untyped]

from app.sources.client.webex.webex import (
    WebexClient,
    WebexClientViaOAuth,
    WebexClientViaToken,
    WebexResponse,
)


class WebexDataSource:
    """Webex DataSource

    Typed wrapper over the wxc_sdk WebexSimpleApi for common operations.

    Accepts either a WebexClient, WebexClientViaToken, or WebexClientViaOAuth.

    Coverage:
    - People: list, get, get_me
    - Rooms/Spaces: list, get
    - Messages: list
    - Teams: list, get
    - Meetings: list, get
    - Memberships: list
    - Organizations: list, get
    - Webhooks: list
    - Recordings: list
    """

    def __init__(
        self,
        client_or_wrapper: WebexClient | WebexClientViaToken | WebexClientViaOAuth,
    ) -> None:
        """Initialize with a Webex client.

        Args:
            client_or_wrapper: WebexClient, WebexClientViaToken,
                               or WebexClientViaOAuth instance
        """
        if isinstance(client_or_wrapper, WebexClient):
            self._sdk: WebexSimpleApi = client_or_wrapper.get_sdk()  # type: ignore[no-any-unimported]
        else:
            self._sdk = client_or_wrapper.get_sdk()

    def get_data_source(self) -> "WebexDataSource":
        """Return the data source instance."""
        return self

    # =========================================================================
    # PEOPLE OPERATIONS
    # =========================================================================

    def list_people(
        self,
        email: str | None = None,
        display_name: str | None = None,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List people in the organization.

        Args:
            email: Filter by email address
            display_name: Filter by display name
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of people
        """
        try:
            kwargs: dict[str, Any] = {}
            if email:
                kwargs["email"] = email
            if display_name:
                kwargs["display_name"] = display_name
            if max_results is not None:
                kwargs["max"] = max_results

            people = cast(list[object], list(self._sdk.people.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(people)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} people",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list people",
            )

    def get_person(self, person_id: str) -> WebexResponse:
        """Get details of a specific person.

        Args:
            person_id: The person ID

        Returns:
            WebexResponse with person details
        """
        try:
            person = self._sdk.people.details(person_id)  # type: ignore[no-untyped-call]
            data = self._serialize_object(person)
            return WebexResponse(
                success=True,
                data=data,
                message="Successfully retrieved person",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to get person",
            )

    def get_me(self) -> WebexResponse:
        """Get the authenticated user's details.

        Returns:
            WebexResponse with current user details
        """
        try:
            me = self._sdk.people.me()  # type: ignore[no-untyped-call]
            data = self._serialize_object(me)
            return WebexResponse(
                success=True,
                data=data,
                message="Successfully retrieved current user",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to get current user",
            )

    # =========================================================================
    # ROOMS / SPACES OPERATIONS
    # =========================================================================

    def list_rooms(
        self,
        team_id: str | None = None,
        room_type: str | None = None,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List rooms (spaces).

        Args:
            team_id: Filter by team ID
            room_type: Filter by room type ('direct' or 'group')
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of rooms
        """
        try:
            kwargs: dict[str, Any] = {}
            if team_id:
                kwargs["team_id"] = team_id
            if room_type:
                kwargs["type_"] = room_type
            if max_results is not None:
                kwargs["max"] = max_results

            rooms = cast(list[object], list(self._sdk.rooms.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(rooms)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} rooms",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list rooms",
            )

    def get_room(self, room_id: str) -> WebexResponse:
        """Get details of a specific room.

        Args:
            room_id: The room ID

        Returns:
            WebexResponse with room details
        """
        try:
            room = self._sdk.rooms.details(room_id)  # type: ignore[no-untyped-call]
            data = self._serialize_object(room)
            return WebexResponse(
                success=True,
                data=data,
                message="Successfully retrieved room",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to get room",
            )

    # =========================================================================
    # MESSAGE OPERATIONS
    # =========================================================================

    def list_messages(
        self,
        room_id: str,
        max_results: int | None = None,
        mentioned_people: str | None = None,
        before: str | None = None,
    ) -> WebexResponse:
        """List messages in a room.

        Args:
            room_id: Room ID to list messages from
            max_results: Maximum number of results to return
            mentioned_people: Filter by mentioned person ID (use 'me' for self)
            before: List messages before this date/time (ISO 8601)

        Returns:
            WebexResponse with list of messages
        """
        try:
            kwargs: dict[str, Any] = {"room_id": room_id}
            if max_results is not None:
                kwargs["max"] = max_results
            if mentioned_people:
                kwargs["mentioned_people"] = mentioned_people
            if before:
                kwargs["before"] = before

            messages = cast(list[object], list(self._sdk.messages.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(messages)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} messages",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list messages",
            )

    # =========================================================================
    # TEAM OPERATIONS
    # =========================================================================

    def list_teams(
        self,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List teams.

        Args:
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of teams
        """
        try:
            kwargs: dict[str, Any] = {}
            if max_results is not None:
                kwargs["max"] = max_results

            teams = cast(list[object], list(self._sdk.teams.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(teams)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} teams",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list teams",
            )

    def get_team(self, team_id: str) -> WebexResponse:
        """Get details of a specific team.

        Args:
            team_id: The team ID

        Returns:
            WebexResponse with team details
        """
        try:
            team = self._sdk.teams.details(team_id)  # type: ignore[no-untyped-call]
            data = self._serialize_object(team)
            return WebexResponse(
                success=True,
                data=data,
                message="Successfully retrieved team",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to get team",
            )

    # =========================================================================
    # MEETING OPERATIONS
    # =========================================================================

    def list_meetings(
        self,
        meeting_type: str | None = None,
        state: str | None = None,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List meetings.

        Args:
            meeting_type: Filter by meeting type
            state: Filter by meeting state
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of meetings
        """
        try:
            kwargs: dict[str, Any] = {}
            if meeting_type:
                kwargs["meeting_type"] = meeting_type
            if state:
                kwargs["state"] = state
            if max_results is not None:
                kwargs["max"] = max_results

            meetings = cast(list[object], list(self._sdk.meetings.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(meetings)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} meetings",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list meetings",
            )

    def get_meeting(self, meeting_id: str) -> WebexResponse:
        """Get details of a specific meeting.

        Args:
            meeting_id: The meeting ID

        Returns:
            WebexResponse with meeting details
        """
        try:
            meeting = self._sdk.meetings.get(meeting_id)  # type: ignore[no-untyped-call]
            data = self._serialize_object(meeting)
            return WebexResponse(
                success=True,
                data=data,
                message="Successfully retrieved meeting",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to get meeting",
            )

    # =========================================================================
    # MEMBERSHIP OPERATIONS
    # =========================================================================

    def list_memberships(
        self,
        room_id: str | None = None,
        person_id: str | None = None,
        person_email: str | None = None,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List memberships.

        Args:
            room_id: Filter by room ID
            person_id: Filter by person ID
            person_email: Filter by person email
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of memberships
        """
        try:
            kwargs: dict[str, Any] = {}
            if room_id:
                kwargs["room_id"] = room_id
            if person_id:
                kwargs["person_id"] = person_id
            if person_email:
                kwargs["person_email"] = person_email
            if max_results is not None:
                kwargs["max"] = max_results

            memberships = cast(list[object], list(self._sdk.membership.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(memberships)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} memberships",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list memberships",
            )

    # =========================================================================
    # ORGANIZATION OPERATIONS
    # =========================================================================

    def list_organizations(self) -> WebexResponse:
        """List organizations.

        Returns:
            WebexResponse with list of organizations
        """
        try:
            orgs = cast(list[object], list(self._sdk.organizations.list()))  # type: ignore[no-untyped-call]
            data = self._serialize_list(orgs)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} organizations",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list organizations",
            )

    def get_organization(self, org_id: str) -> WebexResponse:
        """Get details of a specific organization.

        Args:
            org_id: The organization ID

        Returns:
            WebexResponse with organization details
        """
        try:
            org = self._sdk.organizations.details(org_id)  # type: ignore[no-untyped-call]
            data = self._serialize_object(org)
            return WebexResponse(
                success=True,
                data=data,
                message="Successfully retrieved organization",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to get organization",
            )

    # =========================================================================
    # WEBHOOK OPERATIONS
    # =========================================================================

    def list_webhooks(
        self,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List webhooks.

        Args:
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of webhooks
        """
        try:
            kwargs: dict[str, Any] = {}
            if max_results is not None:
                kwargs["max"] = max_results

            webhooks = cast(list[object], list(self._sdk.webhook.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(webhooks)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} webhooks",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list webhooks",
            )

    # =========================================================================
    # RECORDING OPERATIONS
    # =========================================================================

    def list_recordings(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
        max_results: int | None = None,
    ) -> WebexResponse:
        """List recordings.

        Args:
            from_date: Filter recordings from this date (ISO 8601)
            to_date: Filter recordings up to this date (ISO 8601)
            max_results: Maximum number of results to return

        Returns:
            WebexResponse with list of recordings
        """
        try:
            kwargs: dict[str, Any] = {}
            if from_date:
                kwargs["from_"] = from_date
            if to_date:
                kwargs["to_"] = to_date
            if max_results is not None:
                kwargs["max"] = max_results

            recordings = cast(list[object], list(self._sdk.recordings.list(**kwargs)))  # type: ignore[no-untyped-call]
            data = self._serialize_list(recordings)
            return WebexResponse(
                success=True,
                data=data,
                message=f"Found {len(data)} recordings",
            )
        except Exception as e:
            return WebexResponse(
                success=False,
                error=str(e),
                message="Failed to list recordings",
            )

    # =========================================================================
    # HELPERS
    # =========================================================================

    @staticmethod
    def _serialize_object(obj: object) -> dict[str, object]:
        """Serialize a wxc_sdk model object to a dictionary.

        The wxc_sdk models use dataclass-like structures. This method
        converts them to plain dicts for consistent response formatting.

        Args:
            obj: A wxc_sdk model object

        Returns:
            Dictionary representation of the object
        """
        if obj is None:
            return {}
        if isinstance(obj, dict):
            return obj  # type: ignore[return-value]
        if hasattr(obj, "model_dump"):
            return obj.model_dump()  # type: ignore[union-attr]
        if hasattr(obj, "json"):
            import json

            return json.loads(obj.json())  # type: ignore[union-attr]
        if hasattr(obj, "__dict__"):
            return {
                k: v
                for k, v in vars(obj).items()
                if not k.startswith("_")
            }
        return {"value": obj}

    @classmethod
    def _serialize_list(cls, items: list[object]) -> list[object]:
        """Serialize a list of wxc_sdk model objects.

        Args:
            items: List of wxc_sdk model objects

        Returns:
            List of dictionary representations
        """
        return [cls._serialize_object(item) for item in items]
