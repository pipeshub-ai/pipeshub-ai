"""Who can see what: Slack Workspace channel access, driven over a fake Slack workspace.

The stored access list of a channel is what search uses to decide who sees its
messages. Saving a channel replaces that list, so these tests check both that
it is right and that a failed read from Slack never widens or wipes it.
"""

import pytest
from slack_behaviour_fakes import (
    HttpFailure,
    RateLimited,
    SlackError,
    SlackWorkspace,
    ts_minutes_ago,
)
from slack_behaviour_setup import (
    ALICE,
    BOB,
    CAROL,
    GENERAL,
    NOEMAIL,
    PARTNERS,
    SECRET,
    standard_workspace,
    workspace_connector,
)

from app.models.permission import EntityType, PermissionType

MEMBER_READ_FAILURES = [
    pytest.param(SlackError("internal_error"), id="slack-error"),
    pytest.param(RateLimited(30), id="rate-limited"),
    pytest.param(HttpFailure(503), id="http-503"),
]


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


def role_member_emails(store) -> set[str]:
    _, members = store.roles["workspace_member"]
    return {m.email for m in members}


class TestChannelAccess:
    async def test_public_channel_is_open_to_workspace_members_through_one_role(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        (grant,) = store.group_access[GENERAL]
        assert (grant.entity_type, grant.external_id, grant.type) == (EntityType.ROLE, "workspace_member", PermissionType.READ)
        assert role_member_emails(store) == {"alice@acme.com", "bob@acme.com"}

    async def test_private_channel_is_visible_to_exactly_its_members(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}
        assert all(p.entity_type == EntityType.USER for p in store.group_access[SECRET])

    async def test_a_guest_in_a_private_channel_sees_it_and_a_member_without_email_is_left_out(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.access_emails(PARTNERS) == {"alice@acme.com", "carol@partner.com"}
        assert NOEMAIL not in {p.external_id for p in store.group_access[PARTNERS]}

    async def test_every_page_of_members_counts(self, workspace, store, checkpoints) -> None:
        workspace.page_size["conversations.members"] = 1
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}
        member_calls = [c for c in workspace.calls_to("conversations.members") if c.params["channel"] == SECRET]
        assert [c.params.get("cursor") for c in member_calls] == [None, "page:1"]

    async def test_someone_removed_from_a_private_channel_loses_access_on_the_next_sync(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.members[SECRET].remove(BOB)
        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com"}

    async def test_messages_and_threads_inherit_their_channels_access(self, workspace, store, checkpoints) -> None:
        parent = ts_minutes_ago(100)
        workspace.post(SECRET, parent, ALICE, "plan")
        workspace.reply(SECRET, parent, ts_minutes_ago(90), BOB, "agreed")
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        records = [r for r in store.messages() if SECRET in r.external_record_group_id]
        assert {r.external_record_group_id for r in records} == {SECRET, f"thread_{SECRET}_{parent}"}
        assert all(r.inherit_permissions for r in records)
        assert store.group_access[f"thread_{SECRET}_{parent}"] == []
        assert store.record_groups[f"thread_{SECRET}_{parent}"].parent_external_group_id == SECRET

    async def test_user_groups_are_stored_with_their_known_members(self, workspace, store, checkpoints) -> None:
        workspace.usergroups.append({"id": "S0ONCALL", "handle": "oncall", "name": "On call", "users": [ALICE, "U0GONE"]})
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        group, members = store.user_groups["S0ONCALL"]
        assert group.name == "On call"
        assert [m.email for m in members] == ["alice@acme.com"]

    @pytest.mark.xfail(strict=True, reason=(
        "A guest who is a member of a public channel can read it in Slack, but public channels "
        "are shared only through the workspace-member role, which leaves guests out."
    ))
    async def test_a_guest_who_is_a_member_of_a_public_channel_can_see_it(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        reachable = store.access_emails(GENERAL) | (
            role_member_emails(store) if any(p.entity_type == EntityType.ROLE for p in store.group_access[GENERAL]) else set()
        )
        assert "carol@partner.com" in reachable


class TestFailedReads:
    @pytest.mark.parametrize("failure", MEMBER_READ_FAILURES)
    @pytest.mark.xfail(strict=True, reason=(
        "When a private channel's member list can't be read, the channel is saved with an empty "
        "access list, which replaces the stored one: everyone loses access until a later read succeeds."
    ))
    async def test_a_failed_member_read_keeps_the_stored_access(self, workspace, store, checkpoints, failure) -> None:
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("conversations.members", failure, when=lambda p: p.get("channel") == SECRET)
        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}

    @pytest.mark.xfail(strict=True, reason=(
        "When a later page of a channel's members fails, the members read so far are saved as the "
        "whole access list, so members on the unread pages lose access."
    ))
    async def test_a_member_read_failing_part_way_does_not_narrow_access(self, workspace, store, checkpoints) -> None:
        workspace.page_size["conversations.members"] = 1
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail(
            "conversations.members", SlackError("internal_error"),
            when=lambda p: p.get("channel") == SECRET and p.get("cursor") == "page:1",
        )
        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}

    @pytest.mark.parametrize("failure", MEMBER_READ_FAILURES)
    async def test_a_failed_member_read_never_opens_a_private_channel_up(self, workspace, store, checkpoints, failure) -> None:
        workspace.fail("conversations.members", failure, times=None, when=lambda p: p.get("channel") == SECRET)
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        grants = store.group_access.get(SECRET, [])
        assert not any(p.entity_type in (EntityType.ROLE, EntityType.ORG) for p in grants)
        assert store.access_emails(SECRET) <= {"alice@acme.com", "bob@acme.com"}

    @pytest.mark.xfail(strict=True, reason=(
        "When the user list fails part way, the workspace-member role is saved with only the users "
        "read so far, which replaces its members: everyone else loses access to every public channel."
    ))
    async def test_a_user_list_failing_part_way_keeps_the_workspace_role(self, workspace, store, checkpoints) -> None:
        workspace.page_size["users.list"] = 1
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("users.list", SlackError("internal_error"), when=lambda p: p.get("cursor") == "page:1")
        await connector.run_sync()

        assert role_member_emails(store) == {"alice@acme.com", "bob@acme.com"}

    async def test_a_user_list_failing_outright_leaves_the_workspace_role_alone(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.fail("users.list", RateLimited(20))
        await connector.run_sync()

        assert role_member_emails(store) == {"alice@acme.com", "bob@acme.com"}
        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}


class TestArchivedAndDirectConversations:
    async def test_an_archived_channel_is_no_longer_read_but_keeps_what_was_stored(self, workspace, store, checkpoints) -> None:
        kept = ts_minutes_ago(100)
        workspace.post(SECRET, kept, ALICE, "before archiving")
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.channels[SECRET]["is_archived"] = True
        workspace.post(SECRET, ts_minutes_ago(10), BOB, "after archiving")
        history_before = len(workspace.calls_to("conversations.history"))
        await connector.run_sync()

        assert all(c.params.get("exclude_archived") == "1" for c in workspace.calls_to("conversations.list"))
        assert not any(
            c.params.get("channel") == SECRET for c in workspace.calls_to("conversations.history")[history_before:]
        )
        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}
        assert kept in store.message_ts_count()

    async def test_a_channel_archived_before_the_first_sync_is_not_synced(self, workspace, store, checkpoints) -> None:
        workspace.channels[SECRET]["is_archived"] = True
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert SECRET not in store.record_groups

    async def test_the_workspace_connector_reads_only_public_and_private_channels(self, workspace, store, checkpoints) -> None:
        workspace.add_channel("D0ALICEBOB", "", kind="im", members=[ALICE, BOB], dm_with=BOB)
        workspace.add_channel("G0MPIM", "mpdm-alice--bob--carol-1", kind="mpim", members=[ALICE, BOB, CAROL])
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert {c.params["types"] for c in workspace.calls_to("conversations.list")} == {"public_channel,private_channel"}
        assert "D0ALICEBOB" not in store.record_groups
        assert "G0MPIM" not in store.record_groups
