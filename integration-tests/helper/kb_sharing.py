"""Sharing a knowledge base the ways the product offers, as the admin.

Thin wrappers over the knowledge-base permission and teams APIs, each checking
its own response. A grant that silently did nothing would make a "can see it"
test fail for the wrong reason and a "cannot see it" test pass for the wrong
reason, so every call here fails loudly instead.

User ids are the Mongo ids that ``POST /users`` returns; the APIs resolve them
to graph users themselves.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable

import requests

from helper.pipeshub_client import PipeshubClient

logger = logging.getLogger("kb-sharing")


def all_team_id(org_id: str) -> str:
    """The team every member of an org belongs to, created with the org.

    Sharing with it is how a knowledge base is shared with the whole org.
    """
    return f"all_{org_id}"


def _permissions_url(client: PipeshubClient, kb_id: str) -> str:
    return f"{client.base_url}/api/v1/knowledgeBase/{kb_id}/permissions"


def grant(
    client: PipeshubClient,
    kb_id: str,
    *,
    user_ids: Iterable[str] = (),
    team_ids: Iterable[str] = (),
    role: str = "READER",
) -> None:
    body = {"userIds": list(user_ids), "teamIds": list(team_ids), "role": role}
    resp = requests.post(
        _permissions_url(client, kb_id),
        headers=client._headers(),
        json=body,
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 201, f"grant {body} failed: {resp.status_code}: {resp.text}"
    granted = (resp.json().get("permissionResult") or {}).get("grantedCount") or 0
    assert int(granted) >= 1, f"grant {body} reported grantedCount=0: {resp.text}"


def revoke(
    client: PipeshubClient,
    kb_id: str,
    *,
    user_ids: Iterable[str] = (),
    team_ids: Iterable[str] = (),
    strict: bool = True,
) -> None:
    body = {"userIds": list(user_ids), "teamIds": list(team_ids)}
    resp = requests.delete(
        _permissions_url(client, kb_id),
        headers=client._headers(),
        json=body,
        timeout=client.timeout_seconds,
    )
    if resp.status_code != 200:
        message = f"revoke {body} failed: {resp.status_code}: {resp.text[:300]}"
        if strict:
            raise AssertionError(message)
        logger.warning(message)


def create_team(client: PipeshubClient, member_user_ids: Iterable[str] = ()) -> str:
    """A new team, owned by the admin, with these users as members. Returns its id."""
    resp = requests.post(
        f"{client.base_url}/api/v1/teams",
        headers=client._headers(),
        json={
            "name": f"it-permission-matrix-{uuid.uuid4().hex[:8]}",
            "userRoles": [{"userId": uid, "role": "READER"} for uid in member_user_ids],
        },
        timeout=client.timeout_seconds,
    )
    assert resp.status_code < 300, f"createTeam failed: {resp.status_code}: {resp.text}"
    team_id = (resp.json().get("data") or {}).get("id")
    assert team_id, f"createTeam response has no data.id: {resp.text}"
    return str(team_id)


def add_team_members(client: PipeshubClient, team_id: str, user_ids: Iterable[str]) -> None:
    _update_team(
        client,
        team_id,
        {"addUserRoles": [{"userId": uid, "role": "READER"} for uid in user_ids]},
    )


def remove_team_members(client: PipeshubClient, team_id: str, user_ids: Iterable[str]) -> None:
    _update_team(client, team_id, {"removeUserIds": list(user_ids)})


def _update_team(client: PipeshubClient, team_id: str, body: dict) -> None:
    resp = requests.put(
        f"{client.base_url}/api/v1/teams/{team_id}",
        headers=client._headers(),
        json=body,
        timeout=client.timeout_seconds,
    )
    assert resp.status_code == 200, f"updateTeam {body} failed: {resp.status_code}: {resp.text}"


def delete_team(client: PipeshubClient, team_id: str, *, strict: bool = True) -> None:
    try:
        resp = requests.delete(
            f"{client.base_url}/api/v1/teams/{team_id}",
            headers=client._headers(),
            timeout=client.timeout_seconds,
        )
    except requests.RequestException as exc:
        if strict:
            raise
        logger.warning("Could not delete team %s: %s", team_id, exc)
        return
    if resp.status_code >= 300:
        message = f"deleteTeam {team_id} failed: {resp.status_code}: {resp.text[:300]}"
        if strict:
            raise AssertionError(message)
        logger.warning(message)
