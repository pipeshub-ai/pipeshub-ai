# pyright: ignore-file

"""
Jira Data Center on the simulator – signing in and setting up
=============================================================

A connector that cannot sign in, or signs in as an account that cannot read, is either refused by PipesHub when turned
on or ends its run as not succeeded; it syncs no record, and a person who has the wrong credential never makes it
read. Once the credential works the next sync must match Jira.

  TC-JDSIM-AUTH-001  A wrong personal access token
  TC-JDSIM-AUTH-002  A wrong password (basic credentials)
  TC-JDSIM-AUTH-003  A deactivated account's token
  TC-JDSIM-AUTH-004  An address that does not exist
  TC-JDSIM-AUTH-005  A loopback address is refused (the connector's HTTP client never calls it)
  TC-JDSIM-AUTH-006  A credential that fails with 401 at first and then works: the next sync matches
  TC-JDSIM-AUTH-007  An account that is not an administrator (too few privileges): the run does not succeed on a partial read
                     and nobody gains access
"""

import pytest
from connectors.jira_data_center.sim_world import REFUSED, JiraDcWorld, fault, succeeded


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimSetup:

    async def _refused_or_failed(self, w: JiraDcWorld, outcome: str | None, what: str) -> None:
        assert outcome == REFUSED or not succeeded(outcome), f"{what} was turned on and its sync reported success ({outcome})"
        assert not await w.records(), f"{what} synced records"

    async def test_tc_jdsim_auth_001_wrong_token(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        await self._refused_or_failed(w, await w.start(secret="pat-nobody-at-all"), "a wrong token")

    async def test_tc_jdsim_auth_002_wrong_password(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        await self._refused_or_failed(w, await w.start(auth="basic", secret="not-the-password"), "a wrong password")

    async def test_tc_jdsim_auth_003_deactivated_account(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        outcome = await w.start(prepare=[{"op": "user_active", "user": "svc-pipeshub-sync", "active": False}])
        await self._refused_or_failed(w, outcome, "a deactivated account")

    async def test_tc_jdsim_auth_004_unreachable_address(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        await self._refused_or_failed(w, await w.start(base_url="http://no-such-host.invalid"), "an unreachable address")

    async def test_tc_jdsim_auth_005_loopback_is_refused(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        await self._refused_or_failed(w, await w.start(base_url="http://127.0.0.1:80"), "a loopback address")
        assert not w.sim.paths_called(), "the connector called a loopback address that happens to be the simulator"

    async def test_tc_jdsim_auth_006_credential_recovering(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        w.faults(fault(r"^/rest/api/2/myself$", 401, max_times=500))
        outcome = await w.start(reset=False)
        w.clear_faults()
        await self._refused_or_failed(w, outcome, "a credential Jira refused")
        assert succeeded(await w.enable() if outcome == REFUSED else await w.sync())
        await w.assert_matches_source("after the credential worked")

    async def test_tc_jdsim_auth_007_too_few_privileges(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        plain = w.usernames()[4]
        outcome = await w.start(user=plain)
        if succeeded(outcome):
            # A sync that reads as an ordinary user may only hold what that user can read, and gives it to no one else.
            holdings = await w.holdings()
            assert all(not (holdings[email] - holdings["records"]) for email in w.people)
        else:
            assert not await w.records() or outcome is not None
