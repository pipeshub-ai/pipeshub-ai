"""Jira comments only some readers of their issue may read, against a real Neo4j and ArangoDB.

Jira shows a comment restricted to a group or a project role to the readers of its issue in that group or role, and a
Service Management internal note to the readers of its issue with product access (not portal customers). Each such
comment is a record of its own under its issue; the issue's content holds only public comments. Comments are built
and written by the connector's own code on the project world of ``test_jira_issue_security_real_backends``, and every
user's access is read three ways: the access check, search, and the Knowledge Hub listing under the issue.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.models.entities import AppRole, AppUserGroup
from tests.integration.graph_db import (
    test_jira_issue_security_real_backends as project_world,
)
from tests.integration.graph_db.test_jira_issue_security_real_backends import (
    ISSUES,
    PEOPLE,
    PROJECT_ID,
    PROJECT_KEY,
    _access,
    _edited,
    _email,
    _listed_under,
    _sync_project,
    _ticket_ids,
    _users,
)

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
world = project_world.world  # the same fixture: one project per run, on Neo4j and on ArangoDB

ADMINS_GROUP, PRODUCT_GROUP = "g-admins", "g-product"
GROUPS = {ADMINS_GROUP: {"alice", "dave"}, PRODUCT_GROUP: {"alice", "bob"}}
ROLE_MEMBERS = {"carol"}  # PROJ's Administrators role, id 10002


def _comment(comment_id: str, *, group: str | None = None, role: str | None = None, internal: bool = False) -> dict:
    out: dict[str, Any] = {"id": comment_id, "author": {"accountId": PEOPLE["bob"], "displayName": "bob"},
                           "created": "2024-06-15T10:00:00.000+0000", "updated": "2024-06-15T10:00:00.000+0000",
                           "jsdPublic": not internal}
    if group:
        out["visibility"] = {"type": "group", "value": "jira-admins", "identifier": group}
    if role:
        out["visibility"] = {"type": "role", "value": role, "identifier": role}
    return out


async def _comment_world(world: dict[str, Any]) -> None:
    await _sync_project(world)
    connector, processor, users = world["connector"], world["processor"], {u.email: u for u in _users(world)}
    by_name = {name: users[_email(name, world["run"])] for name in PEOPLE}
    await processor.on_new_user_groups([
        (AppUserGroup(app_name=Connectors.JIRA, connector_id=world["connector_id"], source_user_group_id=gid, name=gid,
                      org_id=world["org_id"]), [by_name[n] for n in sorted(members)])
        for gid, members in GROUPS.items()])
    await processor.on_new_app_roles([(AppRole(app_name=Connectors.JIRA, connector_id=world["connector_id"],
                                               source_role_id=f"{PROJECT_KEY}_10002", name="PROJ - Administrators",
                                               org_id=world["org_id"]), [by_name[n] for n in sorted(ROLE_MEMBERS)])])
    connector._role_ids = {"Administrators": "10002"}
    connector._group_ids = {gid: gid for gid in GROUPS}
    connector._app_roles_mapping = {"jira-servicedesk": [{"groupId": PRODUCT_GROUP}]}


async def _write_comments(world: dict[str, Any], label: str, updated: str, comments: list[dict]) -> None:
    """Write ``label``'s issue with ``comments`` as a sync does, removing what the build says is gone."""
    issue = _edited(label, updated, comment={"comments": comments, "total": len(comments)})
    built, delete_ids = await world["connector"]._build_issue_records([issue], PROJECT_ID, _users(world))
    if delete_ids:
        await world["processor"].on_records_deleted_cascade(delete_ids, world["connector_id"], cascade_children=False)
    await world["connector"]._process_new_records(built, PROJECT_KEY, {"new_count": 0, "updated_count": 0})


async def _comment_ids(world: dict[str, Any]) -> dict[str, str]:
    docs = await world["graph"].get_nodes_by_filters(CollectionNames.RECORDS.value, {"connectorId": world["connector_id"]})
    out = {}
    for d in docs:
        if d.get("recordType") == "COMMENT":
            record_id = d.get("id") or d.get("_key")
            out[d["externalRecordId"]] = record_id
            await world["graph"].update_node(record_id, CollectionNames.RECORDS.value, {
                "indexingStatus": "COMPLETED", "virtualRecordId": f"vr-{record_id}"})
    return out


async def _comment_mismatches(world: dict[str, Any], readers: dict[str, set[str]]) -> list[str]:
    """Where PipesHub disagrees with ``readers`` (comment external id -> who Jira shows it to) by the access check,
    search, and the Knowledge Hub listing under the issue."""
    ids = await _comment_ids(world)
    assert set(ids) == set(readers), f"comment records {sorted(ids)}, expected {sorted(readers)}"
    tickets = await _ticket_ids(world)
    problems: list[str] = []
    for name in PEOPLE:
        checked, searched, _ = await _access(world, name, list(ids.values()))
        issue_readers = {issue["key"] for issue, who in ISSUES.values() if name in who}
        for ext, record_id in ids.items():
            allowed = name in readers[ext]
            for how, seen in (("read", checked), ("find in search", searched)):
                if (record_id in seen) != allowed:
                    problems.append(f"{name} {'cannot' if allowed else 'can'} {how} {ext}")
        for key in issue_readers:
            listed = await _listed_under(world, name, tickets[key]) & set(ids.values())
            expected = {record_id for ext, record_id in ids.items() if name in readers[ext]
                        and ext in COMMENTS_UNDER.get(key, ())}
            if listed != expected:
                problems.append(f"{name} under {key}: listed {sorted(listed)}, expected {sorted(expected)}")
    return problems


COMMENTS_UNDER: dict[str, tuple[str, ...]] = {"PROJ-1": ("comment_501", "comment_502", "comment_503"),
                                              "PROJ-2": ("comment_511",)}


async def test_a_restricted_comment_is_read_by_the_readers_of_its_issue_its_role_or_group_names(
    world: dict[str, Any],
) -> None:
    """dave is in the group but cannot browse the project; bob reads the issue but is in neither the group nor the role."""
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [
        _comment("500"), _comment("501", group=ADMINS_GROUP), _comment("502", role="Administrators"),
        _comment("503", internal=True)])
    await _write_comments(world, "leveled epic", "2024-06-16T10:00:00.000+0000", [_comment("511", group=ADMINS_GROUP)])

    readers = {"comment_501": {"alice"}, "comment_502": {"carol"}, "comment_503": {"alice", "bob"},
               "comment_511": {"alice"}}
    assert not await _comment_mismatches(world, readers)


async def test_a_comment_made_public_or_deleted_loses_its_record_and_one_restricted_again_gets_it_back(
    world: dict[str, Any],
) -> None:
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [
        _comment("501", group=ADMINS_GROUP), _comment("502", role="Administrators")])
    await _write_comments(world, "plain task", "2024-06-17T10:00:00.000+0000", [{**_comment("501"), "updated": "2024-06-17T10:00:00.000+0000"}])
    assert not await _comment_ids(world)

    moved = {**_comment("501", role="Administrators"), "updated": "2024-06-18T10:00:00.000+0000"}
    await _write_comments(world, "plain task", "2024-06-18T10:00:00.000+0000", [moved])
    assert not await _comment_mismatches(world, {"comment_501": {"carol"}})


async def test_deleting_an_issue_deletes_its_comment_records(world: dict[str, Any]) -> None:
    await _comment_world(world)
    await _write_comments(world, "plain task", "2024-06-16T10:00:00.000+0000", [_comment("501", group=ADMINS_GROUP)])
    assert await _comment_ids(world)
    stored = await world["processor"].get_record_by_external_id(connector_id=world["connector_id"], external_record_id="1")
    await world["connector"]._delete_issue_record(stored, "PROJ-1")
    assert not await _comment_ids(world)
