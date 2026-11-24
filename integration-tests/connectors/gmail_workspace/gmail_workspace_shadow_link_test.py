# pyright: ignore-file

"""Gmail Workspace: who is linked to the Workspace admin the service account acts as.

The service account reads as the admin. A user who authenticated the connector and is
not one of the Workspace's users is linked to that admin so they reach the admin's mail.
A user the Workspace lists has their own mailbox and is never linked, so they must not
reach the admin's.

  order 0  a listed authenticator has no link and is refused the admin's mail
  order 1  an unlisted authenticator is linked to the admin and sees the admin's mailbox only
  order 2  a link an earlier sync wrote for a listed authenticator goes on the next sync
  order 3  a listed authenticator the Workspace suspends stays unlinked
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    LISTED_AUTHENTICATORS_KEY,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.gmail.gmail_users import ensure_active_user
from connectors.google_drive_workspace.drive_workspace_test_utils import (  # type: ignore[import-not-found]
    sync_and_wait,
)
from helper.authenticated_identity import _linked_user_keys  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import async_poll_until  # type: ignore[import-not-found]
from helper.oauth_token_helper import delete_backend_kv_key  # type: ignore[import-not-found]
from helper.record_access import wait_for_record_access  # type: ignore[import-not-found]
from helper.second_user import SecondUser, second_user  # noqa: F401 - fixture
from helper.shadow_access_e2e import (  # type: ignore[import-not-found]
    SEARCH_SETTLE_TIMEOUT_SEC,
    _connector_links,
    authenticator_session,
    verify_listed_authenticator_has_no_link,
    verify_shadow_access,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.gmail_workspace,
    pytest.mark.asyncio(loop_scope="session"),
]

_SYNC_TIMEOUT_SEC = int(os.getenv("GMAIL_WORKSPACE_SYNC_TIMEOUT", "600"))


async def _set_authenticator(graph_provider: GraphProviderProtocol, connector_id: str, user_id: str) -> None:
    assert await graph_provider.update_node(
        connector_id, CollectionNames.APPS.value, {"authenticatedBy": user_id},
    ), f"could not set the authenticator of {connector_id}"


async def _mailbox_record_ids(
    sim: Any, graph_provider: GraphProviderProtocol, connector_id: str, mailbox: str,
) -> set[str]:
    """Graph ids of a mailbox's messages and attachments."""
    ids: set[str] = set()
    for msg in sim.messages_of(mailbox):
        external_ids = [msg["id"], *(f"{msg['id']}~{a['part']}" for a in msg.get("attachments") or [])]
        for external_id in external_ids:
            record = await graph_provider.get_record_by_external_id(connector_id, external_id)
            if record is not None:
                ids.add(record.id)
    assert ids, f"no record of {mailbox}'s mailbox is in the graph"
    return ids


async def _admin_mailbox_ids(
    sim: Any, env: Any, graph_provider: GraphProviderProtocol, connector_id: str,
) -> set[str]:
    """The admin's own message copies, which must not be the owner's or the second user's."""
    admin = await _mailbox_record_ids(sim, graph_provider, connector_id, env.admin_email)
    for mailbox in (env.test_user, env.second_user):
        shared = admin & await _mailbox_record_ids(sim, graph_provider, connector_id, mailbox)
        assert not shared, f"the admin's records are also {mailbox}'s: {sorted(shared)[:5]}"
    return admin


async def _activate_admin(users_client: Any, graph_provider: GraphProviderProtocol, env: Any) -> None:
    """The service account reads only the mailboxes of active users, and the admin is one of them only once active."""
    await ensure_active_user(users_client, graph_provider, env.admin_email)


async def _wait_no_links(graph_provider: GraphProviderProtocol, connector_id: str) -> None:
    async def none() -> bool | None:
        return await _connector_links(graph_provider, connector_id) == 0 or None

    await async_poll_until(none, timeout=SEARCH_SETTLE_TIMEOUT_SEC, interval=5,
                           description="no authenticatedAs link on the connector")


async def _links_from(graph_provider: GraphProviderProtocol, user_id: str, connector_id: str) -> list[str]:
    user = await graph_provider.get_user_by_user_id(user_id)
    assert user, f"user {user_id} is not in the graph"
    return await _linked_user_keys(graph_provider, user.get("_key") or user.get("id"), connector_id)


class TestGmailWorkspaceShadowLink:
    @pytest.mark.order(0)
    async def test_listed_authenticator_is_not_linked(
        self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient, config_service: Any, users_client: Any,
    ) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        owner_id = gmail_ws["user_ids"][env.test_user]
        await _activate_admin(users_client, graph_provider, env)
        await _set_authenticator(graph_provider, cid, owner_id)
        await sync_and_wait(pipeshub_client, graph_provider, cid, timeout=_SYNC_TIMEOUT_SEC)
        await _wait_no_links(graph_provider, cid)
        await verify_listed_authenticator_has_no_link(
            connector_id=cid, pipeshub_client=pipeshub_client, graph_provider=graph_provider,
            config_service=config_service, authenticator_user_id=owner_id,
            admin_mailbox_record_ids=await _admin_mailbox_ids(sim, env, graph_provider, cid),
            sync_timeout=_SYNC_TIMEOUT_SEC,
        )

    @pytest.mark.order(1)
    async def test_unlisted_authenticator_sees_the_admins_mailbox_only(
        self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient, config_service: Any, vector_store: Any,
        second_user: SecondUser,  # noqa: F811
        users_client: Any,
    ) -> None:
        """A control: the unlisted authenticator is linked, so this passes on the old code too."""
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        # The suite's PipesHub admin is not a user of the simulated domain
        assert pipeshub_client.user_id, "the harness user has no id"
        await _activate_admin(users_client, graph_provider, env)
        await _admin_mailbox_ids(sim, env, graph_provider, cid)
        await _set_authenticator(graph_provider, cid, pipeshub_client.user_id)
        await sync_and_wait(pipeshub_client, graph_provider, cid, timeout=_SYNC_TIMEOUT_SEC)
        try:
            report = await verify_shadow_access(
                connector_id=cid, pipeshub_client=pipeshub_client, graph_provider=graph_provider,
                config_service=config_service, vector_store=vector_store, second_user=second_user,
                sync_timeout=_SYNC_TIMEOUT_SEC,
            )
        except pytest.skip.Exception as skipped:
            pytest.fail(f"the admin owns records, so the shadow check must run, not skip: {skipped}")
        assert report.shadow_only, "the authenticator reached none of the admin's mail through the link"
        authenticator = await authenticator_session(pipeshub_client, graph_provider, cid)
        for mailbox in (env.test_user, env.second_user):
            for rid in await _mailbox_record_ids(sim, graph_provider, cid, mailbox):
                wait_for_record_access(
                    authenticator, rid, expect_access=False, timeout=SEARCH_SETTLE_TIMEOUT_SEC,
                    description=f"{mailbox}'s mail, which the admin's link does not reach",
                )

    @pytest.mark.order(2)
    async def test_old_link_goes_on_the_next_sync(
        self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient, config_service: Any,
    ) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        owner_id = gmail_ws["user_ids"][env.test_user]
        await _set_authenticator(graph_provider, cid, owner_id)
        owner = await graph_provider.get_user_by_user_id(owner_id)
        admin = await graph_provider.graph_find_user_by_email(env.admin_email)
        assert owner and admin, "the owner and the admin are users of the graph"
        # As on the first sync after an upgrade: earlier runs left the owner in the marker, which
        # would remove the link without the listing being read for them
        await delete_backend_kv_key(LISTED_AUTHENTICATORS_KEY.format(connector_id=cid))
        # Written as a sync that did not check the listing wrote it
        await graph_provider.upsert_authenticated_as(
            owner.get("_key") or owner.get("id"), admin.get("_key") or admin.get("id"), cid,
            owner.get("orgId") or pipeshub_client.org_id,
        )
        assert await _links_from(graph_provider, owner_id, cid), "the old link is in place"

        await sync_and_wait(pipeshub_client, graph_provider, cid, timeout=_SYNC_TIMEOUT_SEC)

        async def gone() -> bool | None:
            return not await _links_from(graph_provider, owner_id, cid) or None

        await async_poll_until(gone, timeout=SEARCH_SETTLE_TIMEOUT_SEC, interval=5,
                               description="the old link removed by the next sync")
        await verify_listed_authenticator_has_no_link(
            connector_id=cid, pipeshub_client=pipeshub_client, graph_provider=graph_provider,
            config_service=config_service, authenticator_user_id=owner_id,
            admin_mailbox_record_ids=await _admin_mailbox_ids(sim, env, graph_provider, cid),
            sync_timeout=_SYNC_TIMEOUT_SEC,
        )

    @pytest.mark.order(3)
    async def test_suspended_listed_authenticator_stays_unlinked(
        self, gmail_ws: dict[str, Any], graph_provider: GraphProviderProtocol,
        pipeshub_client: PipeshubClient, config_service: Any,
    ) -> None:
        sim, env, cid = gmail_ws["sim"], gmail_ws["env"], gmail_ws["connector_id"]
        owner_id = gmail_ws["user_ids"][env.test_user]
        await _set_authenticator(graph_provider, cid, owner_id)
        admin_ids = await _admin_mailbox_ids(sim, env, graph_provider, cid)
        # Earlier runs left the owner in the marker; without it only the listing itself can keep them unlinked
        await delete_backend_kv_key(LISTED_AUTHENTICATORS_KEY.format(connector_id=cid))
        sim.ops({"op": "suspend_user", "user": "owner"})
        try:
            await sync_and_wait(pipeshub_client, graph_provider, cid, timeout=_SYNC_TIMEOUT_SEC)
            await _wait_no_links(graph_provider, cid)
            await verify_listed_authenticator_has_no_link(
                connector_id=cid, pipeshub_client=pipeshub_client, graph_provider=graph_provider,
                config_service=config_service, authenticator_user_id=owner_id,
                admin_mailbox_record_ids=admin_ids, sync_timeout=_SYNC_TIMEOUT_SEC,
            )
        finally:
            sim.ops({"op": "unsuspend_user", "user": "owner"})
