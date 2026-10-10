"""A shared world for the flag-on journeys: one chat shared every way the product allows.

Roles (with the collaboration flag on), for the chat ``shared`` and its agent twin:

    Owner owner | Writer write (direct) | Reader read (direct) | TeamWriter write (team T-write)
    TeamReader read (team T-read) | ProjectViewer read (project viewer) | ProjectEditor read (project editor,
    ceiling viewer) | ProjectTeamMember read (project member through team T-proj) | Stranger, Outsider none

``peditor`` lives in a project whose chat ceiling is ``editor``: ProjectEditor can write there.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from bson import ObjectId

from helper.collab_stack.identity import Actor
from helper.collab_stack.seeds import (
    Seeded,
    insert_project,
    insert_session,
    project_member,
    project_team_member,
    team_row,
    user_row,
)
from helper.collab_stack.stack import CollabStack

# Role of each actor on the chat `shared`, with the flag on.
SHARED_ROLES = {
    "Owner": "owner",
    "Writer": "write",
    "Reader": "read",
    "TeamWriter": "write",
    "TeamReader": "read",
    "ProjectViewer": "read",
    "ProjectEditor": "read",
    "ProjectTeamMember": "read",
    "Stranger": "none",
    "Outsider": "none",
}


@dataclass
class CollabWorld:
    actors: dict[str, Actor]
    sessions: dict[str, Seeded]
    projects: dict[str, ObjectId]
    teams: dict[str, str]
    ids: dict[str, str] = field(default_factory=dict)

    def fill(self, template: str) -> str:
        out = template
        for key, value in self.ids.items():
            out = out.replace("{" + key + "}", value)
        return out

    def fill_body(self, body):  # noqa: ANN001, ANN201
        if isinstance(body, str):
            return self.fill(body)
        if isinstance(body, list):
            return [self.fill_body(b) for b in body]
        if isinstance(body, dict):
            return {k: self.fill_body(v) for k, v in body.items()}
        return body


def named_actors(stack: CollabStack) -> dict[str, Actor]:
    r = stack.roster
    assert r is not None
    return {
        "Admin": r.admin,
        "Owner": r.owner,
        "Writer": r.write_recipient,
        "Reader": r.read_recipient,
        "ProjectViewer": r.project_viewer,
        "ProjectEditor": r.project_editor,
        "ProjectTeamMember": r.project_team_member,
        "TeamWriter": r.team_writer,
        "TeamReader": r.team_reader,
        "Stranger": r.stranger,
        "Outsider": r.other_org,
        "Disabled": r.disabled,
    }


def seed_collab_world(stack: CollabStack) -> CollabWorld:
    """Teams in the fake backend and documents in Mongo. Call after ``stack.reset_state()``."""
    actors = named_actors(stack)
    owner, db, fake = actors["Owner"], stack.db, stack.fake
    org = owner.org_id
    teams = {
        "T-write": fake.add_team(org, "writers", {actors["TeamWriter"].user_id: "WRITER"}).team_id,
        "T-read": fake.add_team(org, "readers", {actors["TeamReader"].user_id: "READER"}).team_id,
        "T-proj": fake.add_team(org, "project team", {actors["ProjectTeamMember"].user_id: "READER"}).team_id,
    }
    members = [
        project_member(actors["ProjectViewer"], "viewer", owner),
        project_member(actors["ProjectEditor"], "editor", owner),
        project_team_member(teams["T-proj"], "viewer", owner),
    ]
    projects = {
        "PRJ": insert_project(db, "PRJ", owner, members),
        "PRJ-editor": insert_project(db, "PRJ-editor", owner, members[:2], projectChatAccess="editor"),
    }
    shares = [
        user_row(actors["Reader"], "read", principal_type=True),
        user_row(actors["Writer"], "write", principal_type=True),
        team_row(teams["T-write"], "write"),
        team_row(teams["T-read"], "read"),
    ]
    sessions = {
        "shared": insert_session(db, "shared", owner, shared_with=shares, project=projects["PRJ"], project_visibility="project", age_minutes=6),
        "agent": insert_session(db, "agent", owner, kind="agent", shared_with=shares, project=projects["PRJ"], project_visibility="project", age_minutes=5),
        "peditor": insert_session(db, "peditor", owner, project=projects["PRJ-editor"], project_visibility="project", age_minutes=4),
        "team-only": insert_session(db, "team-only", owner, shared_with=[team_row(teams["T-read"], "read")], age_minutes=3),
        "private": insert_session(db, "private", owner, age_minutes=2),
    }
    ids = {
        "chat": sessions["shared"].sid,
        "chat_answer": sessions["shared"].answer_id,
        "agent": sessions["agent"].sid,
        "agent_answer": sessions["agent"].answer_id,
        "project": str(projects["PRJ"]),
        **{name: a.user_id for name, a in actors.items()},
    }
    return CollabWorld(actors=actors, sessions=sessions, projects=projects, teams=teams, ids=ids)
