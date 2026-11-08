# pyright: ignore-file

"""Gmail (personal) connector ITs against the Gmail simulator (design: connectors/gmail/DESIGN.md).

  order 0  TC-GMAIL-IND-001 — first sync of the connected mailbox; its owner opens the mail, another user cannot
  order 1  TC-GMAIL-IND-002 — incremental: new mail appears, trashed mail goes
  order 2  TC-GMAIL-IND-003 — access revoked in Google: the sync fails and nothing is removed
TC-GMAIL-IND-004 (connector deletion removes its data) is the fixture's teardown.
"""

from __future__ import annotations

from typing import Any

import pytest

from connectors.google_drive_workspace.drive_workspace_test_utils import sync_and_wait  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]
from helper.graph_provider_utils import (  # type: ignore[import-not-found]
    async_poll_until,
    wait_for_sync_completion,
)
from helper.record_access import wait_for_record_access  # type: ignore[import-not-found]
from helper.second_user import (  # type: ignore[import-not-found]
    create_second_user,
    delete_second_user,
    log_in_existing_user,
    log_out_existing_user,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

pytestmark = [
    pytest.mark.integration,
    pytest.mark.gmail_individual,
    pytest.mark.asyncio(loop_scope="session"),
]


async def _wait_present(graph: GraphProviderProtocol, cid: str, external_id: str, what: str,
                        present: bool = True) -> None:
    async def check() -> bool | None:
        found = await graph.get_record_by_external_id(cid, external_id) is not None
        return found is present or None

    await async_poll_until(check, timeout=180, interval=3, description=f"{what} {'present' if present else 'absent'}")


class TestGmailPersonal:
    @pytest.mark.order(0)
    async def test_tc_gmail_ind_001_first_sync_and_access(self, gmail_personal: dict[str, Any],
                                                          graph_provider: GraphProviderProtocol,
                                                          pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_personal["sim"], gmail_personal["env"], gmail_personal["connector_id"]
        for key in ("hello", "reply", "news"):
            await _wait_present(graph_provider, cid, sim.message(env.user, key)["id"], key)
        hello = sim.message(env.user, "hello")
        await _wait_present(graph_provider, cid, f"{hello['id']}~1", "agenda.pdf")
        record = await graph_provider.get_record_by_external_id(cid, hello["id"])
        owner = log_in_existing_user(pipeshub_client, gmail_personal["user_id"], env.user)
        other = create_second_user(pipeshub_client)
        try:
            wait_for_record_access(owner, record.id, expect_access=True, description="mail in their own Gmail")
            wait_for_record_access(other, record.id, expect_access=False, description="someone's personal Gmail")
        finally:
            log_out_existing_user(pipeshub_client, owner)
            delete_second_user(pipeshub_client, other)
        assert not sim.unmodeled(), f"the connector made requests the simulator does not model: {sim.unmodeled()}"

    @pytest.mark.order(1)
    async def test_tc_gmail_ind_002_incremental(self, gmail_personal: dict[str, Any],
                                                graph_provider: GraphProviderProtocol,
                                                pipeshub_client: PipeshubClient) -> None:
        sim, env, cid = gmail_personal["sim"], gmail_personal["env"], gmail_personal["connector_id"]
        sim.ops({"op": "deliver", "key": "later", "from": "colleague", "to": ["me"], "subject": "IT later"},
                {"op": "trash", "user": "me", "message": "news"})
        await sync_and_wait(pipeshub_client, graph_provider, cid)
        await _wait_present(graph_provider, cid, sim.message(env.user, "later")["id"], "new mail")
        await _wait_present(graph_provider, cid, sim.message(env.user, "news")["id"], "trashed mail", present=False)

    @pytest.mark.order(2)
    async def test_tc_gmail_ind_003_access_revoked(self, gmail_personal: dict[str, Any],
                                                   graph_provider: GraphProviderProtocol,
                                                   pipeshub_client: PipeshubClient) -> None:
        sim, cid = gmail_personal["sim"], gmail_personal["connector_id"]
        before = await graph_provider.count_records(cid)
        sim.revoke_refresh_token()
        pipeshub_client.resync_connector(cid, full_sync=False)
        await wait_for_sync_completion(pipeshub_client, graph_provider, cid, timeout=300)
        await graph_provider.assert_record_count_unchanged(cid, before)
        outcome = pipeshub_client.get_connector(cid).get("lastSyncOutcome")
        if outcome is not None:
            assert str(outcome).lower() != "succeeded", "a sync Google refused does not report success"
