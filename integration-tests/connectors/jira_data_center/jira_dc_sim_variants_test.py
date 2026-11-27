# pyright: ignore-file

"""
Jira Data Center on the simulator – vendor variants
===================================================

Each case changes a switch of the simulator that changes what the connector is shown (``POST /_sim/jiradc/switches``),
each as the Atlassian release it models documents, and checks the sync still equals the oracle.

  TC-JDSIM-VAR-001  Jira 10.2: no /user/list (404), the connector lists users through /user/search
  TC-JDSIM-VAR-002  Releases 10.3.20 and 11.0.0 (their /user/list and /user/search caps)
  TC-JDSIM-VAR-003  /user/search stopping at 5 users (a cap smaller than the site): the run does not report success on a
                    partial user list, or it reads everyone another way
  TC-JDSIM-VAR-004  Embedded comments cut to 1 per issue: the rest are read from /issue/{id}/comment
  TC-JDSIM-VAR-005  The permission scheme's ?expand=all answering instead of failing
  TC-JDSIM-VAR-006  The level-members route answering (it is a 404 by default) with a level and its holders set
  TC-JDSIM-VAR-007  A Browse grant to a user that names the user by key (the default names it by username: see
                    jira_dc_sim_gaps_test.py)
"""

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimVariants:

    async def test_tc_jdsim_var_001_jira_10_2(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"version": "10.2.1"}))
        await w.assert_matches_source("on Jira 10.2.1")
        called = w.sim.paths_called()
        assert "GET /rest/api/2/user/search" in called

    @pytest.mark.parametrize("version", ["10.3.20", "11.0.0"])
    async def test_tc_jdsim_var_002_releases(self, jira_dc_world: JiraDcWorld, version: str) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"version": version}))
        await w.assert_matches_source(f"on Jira {version}")

    async def test_tc_jdsim_var_003_user_search_cap(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        outcome = await w.start(switches={"version": "10.2.1", "user_search_cap": 5})
        if succeeded(outcome):
            await w.assert_matches_source("on a Jira whose user search stops after 5 users")
        else:
            holdings = await w.holdings()
            assert all(not (holdings[e] - holdings["records"]) for e in w.people)

    async def test_tc_jdsim_var_004_embedded_comments(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"embedded_comments": 1}))
        await w.assert_matches_source("with embedded comments cut to one")
        assert "GET /rest/api/2/issue/{ref}/comment" in w.sim.paths_called()

    async def test_tc_jdsim_var_005_expand_all_answers(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"permissionscheme_expand_all": "ok"}))
        await w.assert_matches_source("with ?expand=all answering")

    async def test_tc_jdsim_var_006_level_members_route(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        issue = w.issue_keys("GRAN")[0]
        assert succeeded(await w.start(reset=False, switches={"level_members": True}, prepare=[
            {"op": "security", "issue": issue, "level": "Internal"},
            {"op": "level_member", "project": "GRAN", "level": "Internal", "holder": "user",
             "parameter": w.usernames()[5]}]))
        await w.assert_matches_source("with the level-members route answering")

    async def test_tc_jdsim_var_007_holder_named_by_key(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        names = w.usernames()
        assert succeeded(await w.start(switches={"holder_user_parameter": "key"}, prepare=[
            {"op": "grant", "project": "LIGH", "holder": "user", "parameter": names[2]},
            {"op": "grant", "project": "LIGH", "holder": "user", "parameter": names[6]}]))
        assert "DENIED:user-holder-by-name" not in w.known_gaps()
        await w.assert_matches_source("with Browse grants naming users by user key (no gap tolerated)")
