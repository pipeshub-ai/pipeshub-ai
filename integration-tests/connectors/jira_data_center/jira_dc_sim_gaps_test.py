# pyright: ignore-file

"""
Jira Data Center on the simulator – differences an open pull request fixes
==========================================================================

``sim_world.KNOWN_GAPS`` tolerates two differences from Jira's rules, in every test that runs with the simulator's default
answers. Each is held here by a strict expected failure naming the pull request: the day the pull request lands the test
passes unexpectedly, fails the run, and the entry (and this test) go.

  TC-JDSIM-GAP-001  A Browse grant naming a user: Data Center answers the holder's username; everyone a scheme names
                    that way reads the project (#6114)
  TC-JDSIM-GAP-002  A leveled issue: with no route listing a level's holders, everyone Jira shows it to reads it (#6114)
"""

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimKnownGaps:

    @pytest.mark.xfail(strict=True, reason="open pull request #6114: a user holder's parameter is read as a username first")
    async def test_tc_jdsim_gap_001_user_holder_by_username(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync, with no gap tolerated", known_gaps=frozenset())

    @pytest.mark.xfail(strict=True, reason="open pull request #6114: who reads a leveled issue is read per issue")
    async def test_tc_jdsim_gap_002_leveled_issue_without_the_members_route(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches={"holder_user_parameter": "key"}))
        w.mutate({"op": "security", "issue": w.issue_keys("CASC")[0], "level": "Internal"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an issue was put at a level, with no gap tolerated", known_gaps=frozenset())
