"""Jira Cloud: what a refused read says about the thing it read (``_note_unread``).

A 400 ("the request is invalid") never says a thing is not there. A 404 of a project's roles or of one of them says the project
or role is not found or the account has no administrative permission for the project
(https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-project-roles/), so what is stored is kept, as a 403
keeps it. A 404 of a group's members ("the group is not found") leaves nothing to keep.
"""

from __future__ import annotations

import pytest

from tests.unit.connectors.sources.test_jira_cloud_connector import _make_connector


def _connector():
    connector = _make_connector()
    connector._kept_unread_access = False
    connector._kept_after_refusal = []
    return connector


@pytest.mark.parametrize("kept", [None, ("user_group", "g1"), ("role", "PROJ_10"), ("project_roles", "PROJ"), ("project_issues", "PROJ")])
def test_a_400_makes_the_full_sync_incomplete(kept) -> None:
    connector = _connector()
    connector._note_unread(400, kept)
    assert connector.full_sync_coverage().complete is False
    assert connector._kept_after_refusal == []


@pytest.mark.parametrize("kept", [("role", "PROJ_10"), ("project_roles", "PROJ")])
@pytest.mark.parametrize("status", [403, 404])
def test_a_refused_project_role_read_keeps_the_stored_role(status, kept) -> None:
    connector = _connector()
    connector._note_unread(status, kept)
    assert connector._kept_after_refusal == [kept]
    assert connector.full_sync_coverage().complete is True


@pytest.mark.parametrize("kept", [("user_group", "g1"), ("user_groups", ""), None])
def test_a_404_of_anything_else_leaves_nothing_to_keep(kept) -> None:
    connector = _connector()
    connector._note_unread(404, kept)
    assert connector._kept_after_refusal == [] and connector.full_sync_coverage().complete is True


@pytest.mark.parametrize("status", [None, 401, 408, 429, 500, 503])
def test_anything_that_may_pass_makes_the_full_sync_incomplete(status) -> None:
    connector = _connector()
    connector._note_unread(status, ("user_group", "g1"))
    assert connector.full_sync_coverage().complete is False
