# pyright: ignore-file

"""
Jira Cloud on the simulator: sign-in and set-up
===============================================

  TC-JCSIM-SETUP-001  An address that is not an Atlassian site is refused and the simulator receives nothing
  TC-JCSIM-SETUP-002  A site address with a port, credentials, or another scheme the connector does not take is refused
  TC-JCSIM-SETUP-003  An account that does not exist (401) is refused
  TC-JCSIM-SETUP-004  A deactivated sync account is refused; reactivated, the same connector syncs
  TC-JCSIM-SETUP-005  A missing token is refused
  TC-JCSIM-SETUP-006  A site address pasted as a page of the site (a path after the host) still syncs
"""

import pytest
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]
import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    CREATOR_EMAIL,
    SYNC_ACCOUNT,
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)

from connectors.jira.cloud_sim_world import REFUSED


async def _refused(w: JiraCloudWorld, **kw) -> bool:
    try:
        outcome = await w.start(**kw)
    except PipeshubClientError:
        return True
    return outcome == REFUSED


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimSetup:

    @pytest.mark.parametrize("url", ["http://127.0.0.1", "http://169.254.169.254", "https://example.com",
                                     "http://localhost", "http://10.0.0.1", "https://evil.atlassian.net.example.com"])
    async def test_tc_jcsim_setup_001_not_an_atlassian_site(self, jira_cloud_world: JiraCloudWorld, url: str) -> None:
        w = jira_cloud_world
        assert await _refused(w, base_url=url)
        assert not w.sim.paths_called(), "the connector reached the simulator with an address that is not its site"

    @pytest.mark.parametrize("url", ["http://jira-cloud-sim.internal:8080", "http://user:pw@jira-cloud-sim.internal",
                                     "ftp://jira-cloud-sim.internal", "http://jira-cloud-sim.internal:notaport"])
    async def test_tc_jcsim_setup_002_odd_addresses(self, jira_cloud_world: JiraCloudWorld, url: str) -> None:
        w = jira_cloud_world
        assert await _refused(w, base_url=url)
        assert not w.sim.paths_called()

    async def test_tc_jcsim_setup_003_unknown_account(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert await _refused(w, user="nobody@simcorp.example.com")

    async def test_tc_jcsim_setup_004_deactivated_account_then_back(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        w.mutate({"op": "user_active", "user": SYNC_ACCOUNT, "active": False})
        assert await _refused(w, reset=False)
        w.mutate({"op": "user_active", "user": SYNC_ACCOUNT, "active": True})
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the account was reactivated")

    async def test_tc_jcsim_setup_005_no_token(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert await _refused(w, secret="")

    async def test_tc_jcsim_setup_006_a_pasted_page_of_the_site(self, jira_cloud_world: JiraCloudWorld) -> None:
        from connectors.jira.cloud_sim_world import CONNECTOR_URL

        w = jira_cloud_world
        assert succeeded(await w.start(base_url=f"{CONNECTOR_URL}/jira/software/projects/CASC/boards/1"))
        await w.assert_matches_source("after a sync from a pasted page address")
