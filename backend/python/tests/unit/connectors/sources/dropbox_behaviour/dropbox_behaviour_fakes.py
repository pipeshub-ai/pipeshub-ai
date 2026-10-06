"""Fakes for behaviour tests of the Dropbox team connector's group event sync.

Only Dropbox's HTTP API and our own databases are faked. The connector,
``DropboxDataSource`` and the Dropbox SDK are real: ``DropboxApiStub`` answers
the SDK's requests with event pages the SDK's own serializer wrote, so the
connector reads real ``TeamEvent`` objects. Group members and sync points live
in memory so a second sync sees what the first one wrote.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

import requests
from dropbox import stone_serializers, team_log

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

EVENTS_PATH = "/2/team_log/get_events/continue"
_WHEN = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def _event(event_type: team_log.EventType, details: team_log.EventDetails, group: tuple[str, str],
           user: str | None = None) -> team_log.TeamEvent:
    group_id, group_name = group
    participants = [team_log.ParticipantLogInfo.group(team_log.GroupLogInfo(display_name=group_name, group_id=group_id))]
    if user:
        participants.append(team_log.ParticipantLogInfo.user(team_log.TeamMemberLogInfo(email=user, display_name=user)))
    return team_log.TeamEvent(
        timestamp=_WHEN, event_category=team_log.EventCategory.groups, event_type=event_type, details=details,
        participants=participants,
    )


def member_removed(group: tuple[str, str], email: str) -> team_log.TeamEvent:
    return _event(
        team_log.EventType.group_remove_member(team_log.GroupRemoveMemberType("Removed team members from group")),
        team_log.EventDetails.group_remove_member_details(team_log.GroupRemoveMemberDetails()),
        group, email,
    )


def member_added(group: tuple[str, str], email: str) -> team_log.TeamEvent:
    return _event(
        team_log.EventType.group_add_member(team_log.GroupAddMemberType("Added team members to group")),
        team_log.EventDetails.group_add_member_details(team_log.GroupAddMemberDetails(is_group_owner=False)),
        group, email,
    )


def group_deleted(group: tuple[str, str]) -> team_log.TeamEvent:
    return _event(
        team_log.EventType.group_delete(team_log.GroupDeleteType("Deleted group")),
        team_log.EventDetails.group_delete_details(team_log.GroupDeleteDetails()),
        group,
    )


def group_renamed(group: tuple[str, str], previous_name: str) -> team_log.TeamEvent:
    return _event(
        team_log.EventType.group_rename(team_log.GroupRenameType("Renamed group")),
        team_log.EventDetails.group_rename_details(
            team_log.GroupRenameDetails(previous_value=previous_name, new_value=group[1])
        ),
        group,
    )


class DropboxApiStub(requests.adapters.BaseAdapter):
    """Answers the SDK's HTTP calls. ``pages`` maps a cursor to the event page it returns."""

    def __init__(self) -> None:
        super().__init__()
        self.pages: dict[str, tuple[list[team_log.TeamEvent], str, bool]] = {}
        self.cursors_read: list[str] = []
        self.unavailable = False

    def page(self, cursor: str, events: list[team_log.TeamEvent], *, next_cursor: str, has_more: bool = False) -> None:
        self.pages[cursor] = (events, next_cursor, has_more)

    def send(self, request: requests.PreparedRequest, **_: object) -> requests.Response:
        path = urlparse(request.url).path
        assert path == EVENTS_PATH, f"unexpected Dropbox call: {request.method} {request.url}"
        response = requests.Response()
        response.request = request
        response.url = request.url
        if self.unavailable:
            response.status_code = 503
            response._content = b"Service Unavailable"
            return response
        cursor = json.loads(request.body)["cursor"]
        self.cursors_read.append(cursor)
        events, next_cursor, has_more = self.pages[cursor]
        result = team_log.GetTeamEventsResult(events=events, cursor=next_cursor, has_more=has_more)
        response.status_code = 200
        response.headers["Content-Type"] = "application/json"
        response._content = stone_serializers.json_encode(team_log.GetTeamEventsResult_validator, result).encode()
        return response

    def close(self) -> None:
        pass


class FakeCheckpointStore:
    """In-memory sync points with ArangoDB ``UPDATE`` (merge) semantics."""

    def __init__(self) -> None:
        self.sync_points: dict[str, dict[str, Any]] = {}

    async def get_sync_point(self, key: str, raise_on_error: bool = False) -> dict[str, Any] | None:
        stored = self.sync_points.get(key)
        return dict(stored) if stored is not None else None

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.sync_points.setdefault(key, {}).update(data)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[FakeCheckpointStore]:
        yield self


class FakeGroupsDb:
    """In-memory stand-in for the group methods of ``DataSourceEntitiesProcessor``.

    ``groups`` maps a Dropbox group id to its name and member emails.
    """

    def __init__(self, org_id: str = "org-1") -> None:
        self.org_id = org_id
        self.groups: dict[str, dict[str, Any]] = {}
        self.fail_member_removal: set[tuple[str, str]] = set()
        self.fail_group_delete: set[str] = set()
        self.fail_rename: set[str] = set()

    def add_group(self, group: tuple[str, str], *members: str) -> None:
        self.groups[group[0]] = {"name": group[1], "members": list(members)}

    def members(self, group: tuple[str, str]) -> list[str]:
        return self.groups[group[0]]["members"]

    async def on_user_group_member_removed(self, external_group_id: str, user_email: str, connector_id: str) -> bool:
        # Like the real processor: a delete that fails raises; False only when the
        # group or the membership isn't stored, so there was nothing to remove.
        if (external_group_id, user_email) in self.fail_member_removal:
            raise RuntimeError(f"database unavailable removing {user_email} from group {external_group_id}")
        group = self.groups.get(external_group_id)
        if group is None or user_email not in group["members"]:
            return False
        group["members"].remove(user_email)
        return True

    async def on_user_group_member_added(self, external_group_id: str, user_email: str, permission_type: object,
                                         connector_id: str) -> bool:
        group = self.groups.get(external_group_id)
        if group is None or user_email in group["members"]:
            return False
        group["members"].append(user_email)
        return True

    async def on_user_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        # Like the real processor: a delete that fails raises; a group that isn't stored is already gone.
        if external_group_id in self.fail_group_delete:
            raise RuntimeError(f"database unavailable deleting group {external_group_id}")
        self.groups.pop(external_group_id, None)
        return True

    async def update_user_group_name(self, external_group_id: str, new_name: str, connector_id: str) -> bool:
        if external_group_id in self.fail_rename:
            raise RuntimeError(f"database unavailable renaming group {external_group_id}")
        group = self.groups.get(external_group_id)
        if group is None:
            return False
        group["name"] = new_name
        return True
