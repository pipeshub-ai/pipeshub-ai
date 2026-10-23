"""Shadow user access: the person who authenticated a Slack Workspace connector sees what its token reads.

A bot token belongs to no Slack account the authenticator could be linked to, as the other
connectors link theirs. They hold the token, though, so Slack already shows them every channel
it fetches: each channel's access list carries a grant for them, rebuilt with the list on every
sync. A user token does name a Slack account, and that account is linked as usual.

A connector's records open only to members of its app. An authenticator outside the workspace
is not one through the user sync, so the grants come with the membership an external
collaborator gets, and it goes with them.
"""

import pytest
from slack_behaviour_fakes import BOT_TOKEN, USER_TOKEN, SlackWorkspace
from slack_behaviour_setup import (
    ALICE,
    BOB,
    GENERAL,
    PARTNERS,
    SECRET,
    standard_workspace,
    workspace_config,
    workspace_connector,
)

from app.models.permission import EntityType, PermissionType

ADMIN = "it-admin@acme.com"
CHANNELS = (GENERAL, SECRET, PARTNERS)


@pytest.fixture
def workspace(slack: SlackWorkspace) -> SlackWorkspace:
    return standard_workspace(slack)


def grants_for(store, channel_id: str, email: str) -> list:
    return [
        p for p in store.group_access[channel_id]
        if p.entity_type == EntityType.USER and (p.email or "").lower() == email.lower()
    ]


class TestBotToken:
    async def test_the_authenticator_reads_every_channel_the_token_fetched(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        for channel_id in CHANNELS:
            (grant,) = grants_for(store, channel_id, ADMIN)
            assert grant.type == PermissionType.READ, channel_id

    async def test_the_members_keep_their_access_beside_the_authenticator(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com", ADMIN}
        assert store.access_emails(PARTNERS) == {"alice@acme.com", "carol@partner.com", ADMIN}

    async def test_an_authenticator_who_is_already_a_member_is_granted_once(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = "Alice@Acme.com"
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert len(grants_for(store, SECRET, "alice@acme.com")) == 1
        (grant,) = grants_for(store, GENERAL, "alice@acme.com")
        assert grant.email == "Alice@Acme.com", "a public channel names its members through the role, not one by one"

    async def test_a_bot_has_no_account_to_link_and_an_earlier_link_is_removed(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints, workspace_config(BOT_TOKEN))

        await connector.run_sync()

        assert store.links == []
        assert store.unlinks == 1


class TestUpdates:
    async def test_a_new_authenticator_replaces_the_old_one_on_the_next_sync(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        store.authenticator_email = "new-admin@acme.com"
        await connector.run_sync()

        for channel_id in CHANNELS:
            assert grants_for(store, channel_id, ADMIN) == [], channel_id
            assert len(grants_for(store, channel_id, "new-admin@acme.com")) == 1, channel_id

    async def test_a_member_removed_from_a_channel_still_loses_access(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        workspace.members[SECRET] = [ALICE]
        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", ADMIN}

    async def test_an_authenticator_the_graph_cannot_name_adds_no_grant(self, workspace, store, checkpoints) -> None:
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com"}


class TestUserToken:
    async def test_the_account_a_user_token_belongs_to_is_linked(self, workspace, store, checkpoints) -> None:
        workspace.auth_user_id = BOB
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints, workspace_config(USER_TOKEN))

        await connector.run_sync()

        assert store.links == [("bob@acme.com", BOB)]
        assert store.unlinks == 0
        assert len(grants_for(store, SECRET, ADMIN)) == 1
        assert store.external_members == {ADMIN}


def calls(store) -> list[str]:
    return [method for method, _ in store.events]


class TestMembership:
    async def test_an_authenticator_outside_the_workspace_becomes_a_member_of_the_app(
        self, workspace, store, checkpoints,
    ) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.external_members == {ADMIN}

    async def test_an_authenticator_in_the_workspace_keeps_the_membership_the_user_sync_gave(
        self, workspace, store, checkpoints,
    ) -> None:
        store.authenticator_email = "Alice@Acme.com"
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.external_members == set()

    async def test_the_membership_is_judged_once_every_channel_is_written(self, workspace, store, checkpoints) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        channel_writes = [
            i for i, (method, ids) in enumerate(store.events)
            if method == "on_new_record_groups" and set(ids) & set(CHANNELS)
        ]
        assert channel_writes
        assert calls(store).index("on_external_app_users") > max(channel_writes)
        assert calls(store).index("reap_external_app_users") > calls(store).index("on_external_app_users")

    async def test_a_former_authenticator_loses_the_membership_with_the_grants(
        self, workspace, store, checkpoints,
    ) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        store.authenticator_email = "new-admin@acme.com"
        await connector.run_sync()

        assert store.external_members == {"new-admin@acme.com"}

    async def test_an_authenticator_the_graph_cannot_name_gets_none_and_a_former_one_loses_theirs(
        self, workspace, store, checkpoints,
    ) -> None:
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)
        await connector.run_sync()

        store.authenticator_email = None
        store.events.clear()
        await connector.run_sync()

        assert "on_external_app_users" not in calls(store)
        assert store.external_members == set()

    async def test_a_membership_that_cannot_be_written_does_not_stop_the_sync(
        self, workspace, store, checkpoints, monkeypatch,
    ) -> None:
        async def _unavailable(*_: object) -> None:
            raise RuntimeError("graph unavailable")

        monkeypatch.setattr(store, "on_external_app_users", _unavailable)
        monkeypatch.setattr(store, "reap_external_app_users", _unavailable)
        store.authenticator_email = ADMIN
        connector, _ = await workspace_connector(store, checkpoints)

        await connector.run_sync()

        assert store.access_emails(SECRET) == {"alice@acme.com", "bob@acme.com", ADMIN}
        assert workspace.calls_to("conversations.history"), "the messages were still read"
