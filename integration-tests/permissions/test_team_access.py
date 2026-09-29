"""What a team gives its members, as the team changes.

CTO list: Group Permissions, and Cleanup "Teams created/updated/deleted/added".
``response-validation/.../integration_test_permission_matrix.py`` already
checks one person against one knowledge base for join, leave, unshare and
delete. This module adds what that cannot show, because there the person only
ever had access through the team:

* renaming a team changes nothing about what its members reach;
* leaving or deleting a team removes only what the team gave. Access the person
  was given directly, to a knowledge base the team also had, stays.

Checked as the member, through search, opening the record and their
knowledge-base list.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterator

import pytest
import requests

from helper import kb_sharing
from helper.access_probe import listed_kb_ids, open_status, search
from helper.kb_notes import Note, create_kb_with_note, delete_kb
from helper.pipeshub_client import PipeshubClient
from helper.second_user import (
    NO_ACCESS_STATUSES,
    SecondUser,
    create_second_user,
    delete_second_user,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.permissions,
    # One member and two knowledge bases shared by every test here.
    pytest.mark.xdist_group("team-access"),
]

KB_PREFIX = "it-team-access"


@dataclasses.dataclass(frozen=True)
class Setup:
    member: SecondUser
    # Shared with each test's team only.
    team_only: Note
    # Shared with the member directly, and with each test's team as well.
    also_direct: Note


@pytest.fixture(scope="module")
def setup(pipeshub_client: PipeshubClient, ai_models_configured) -> Iterator[Setup]:
    del ai_models_configured  # ordering only: indexing needs an LLM and an embedding model
    pipeshub_client._ensure_access_token()
    created: list[Note] = []
    member = create_second_user(pipeshub_client)
    try:
        created.append(create_kb_with_note(pipeshub_client, KB_PREFIX, "teamonlyfern"))
        created.append(create_kb_with_note(pipeshub_client, KB_PREFIX, "directquill"))
        team_only, also_direct = created
        kb_sharing.grant(pipeshub_client, also_direct.kb_id, user_ids=[member.user_id])
        yield Setup(member=member, team_only=team_only, also_direct=also_direct)
    finally:
        for note in created:
            delete_kb(pipeshub_client, note.kb_id)
        delete_second_user(pipeshub_client, member, strict=True)


@pytest.fixture
def team(pipeshub_client: PipeshubClient, setup: Setup) -> Iterator[str]:
    """A new team with access to both knowledge bases and no members yet."""
    team_id = kb_sharing.create_team(pipeshub_client)
    try:
        for note in (setup.team_only, setup.also_direct):
            kb_sharing.grant(pipeshub_client, note.kb_id, team_ids=[team_id])
        yield team_id
    finally:
        kb_sharing.delete_team(pipeshub_client, team_id, strict=False)


def assert_reaches(user: SecondUser, note: Note, why: str) -> None:
    outcome = search(user, note.word, note.kb_id)
    assert outcome.found(note.virtual_id), (
        f"{why}: searching for {note.word!r} did not return the note ({outcome.describe()})."
    )
    status = open_status(user, note.record_id)
    assert status == 200, f"{why}: opening the note returned HTTP {status}."
    listed = listed_kb_ids(user, KB_PREFIX)
    assert listed is not None and note.kb_id in listed, (
        f"{why}: the knowledge base is missing from their list."
    )


def assert_cannot_reach(user: SecondUser, note: Note, why: str) -> None:
    for kb_id in (note.kb_id, None):
        outcome = search(user, note.word, kb_id)
        assert outcome.refused_or_missing(note.virtual_id), (
            f"{why}: searching for {note.word!r} gave {outcome.describe()}; the note must "
            "not come back, and an error does not prove it stayed hidden."
        )
    status = open_status(user, note.record_id)
    assert status in NO_ACCESS_STATUSES, f"{why}: opening the note returned HTTP {status}."
    listed = listed_kb_ids(user, KB_PREFIX)
    assert listed is not None and note.kb_id not in listed, (
        f"{why}: the knowledge base is still in their list."
    )


class TestTeamMembership:
    def test_joining_gives_the_teams_access(
        self, pipeshub_client: PipeshubClient, setup: Setup, team: str
    ) -> None:
        assert_cannot_reach(setup.member, setup.team_only, "before joining the team")
        kb_sharing.add_team_members(pipeshub_client, team, [setup.member.user_id])
        assert_reaches(setup.member, setup.team_only, "after joining the team")

    def test_leaving_takes_only_what_the_team_gave(
        self, pipeshub_client: PipeshubClient, setup: Setup, team: str
    ) -> None:
        kb_sharing.add_team_members(pipeshub_client, team, [setup.member.user_id])
        assert_reaches(setup.member, setup.team_only, "while in the team")

        kb_sharing.remove_team_members(pipeshub_client, team, [setup.member.user_id])
        assert_cannot_reach(setup.member, setup.team_only, "after leaving the team")
        assert_reaches(
            setup.member, setup.also_direct,
            "after leaving the team, on a knowledge base also shared with them directly",
        )

    def test_renaming_the_team_changes_nothing(
        self, pipeshub_client: PipeshubClient, setup: Setup, team: str
    ) -> None:
        kb_sharing.add_team_members(pipeshub_client, team, [setup.member.user_id])
        assert_reaches(setup.member, setup.team_only, "before the rename")

        new_name = f"it-team-renamed-{uuid.uuid4().hex[:8]}"
        resp = requests.put(
            f"{pipeshub_client.base_url}/api/v1/teams/{team}",
            headers=pipeshub_client._headers(),
            json={"name": new_name},
            timeout=pipeshub_client.timeout_seconds,
        )
        assert resp.status_code == 200, f"renaming the team failed: HTTP {resp.status_code}: {resp.text[:200]}"
        assert (resp.json().get("team") or {}).get("name") == new_name, (
            f"the rename was accepted but the team is not called {new_name!r}: {resp.text[:200]}"
        )

        assert_reaches(setup.member, setup.team_only, "after the team was renamed")

    def test_deleting_the_team_takes_only_what_it_gave(
        self, pipeshub_client: PipeshubClient, setup: Setup, team: str
    ) -> None:
        kb_sharing.add_team_members(pipeshub_client, team, [setup.member.user_id])
        assert_reaches(setup.member, setup.team_only, "before the team was deleted")

        kb_sharing.delete_team(pipeshub_client, team)
        assert_cannot_reach(setup.member, setup.team_only, "after the team was deleted")
        assert_reaches(
            setup.member, setup.also_direct,
            "after the team was deleted, on a knowledge base also shared with them directly",
        )
