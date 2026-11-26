# pyright: ignore-file

"""
The personal GitHub connector on the simulator
==============================================

The personal connector syncs one repository its connected account reaches into the creator's own workspace: access is the
creator alone, whatever the repository's collaborators, teams or visibility say on GitHub.

  TC-GHSIM-PERS-001  Private repository: the creator reads every record; the owner, a team member and a collaborator (each a
                     PipesHub user) read nothing [E]
  TC-GHSIM-PERS-002  Public repository: still the creator alone, never the whole workspace [E]
  TC-GHSIM-PERS-003  The first sync equals the repository's issues, pull requests and files [B]
"""

import pytest
from connectors.github_teams.sim_instances import ADMIN, ALICE, BOB, CLIENT_EMAIL, OLGA, acme, rich
from connectors.github_teams.sim_world import GithubWorld, succeeded


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimPersonal:

    async def test_tc_ghsim_pers_001_private_repository(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich(), personal=True, auth="oauth"))
        holdings = await w.holdings()
        assert holdings[CLIENT_EMAIL] == holdings["records"] and holdings["records"], "the creator does not read every record"
        for other in (ADMIN, ALICE, BOB, OLGA):
            assert not holdings[other], f"{other} reads a personal connector's records"
        await w.assert_matches_source("personal connector, private repository")

    async def test_tc_ghsim_pers_002_public_repository(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["repos"][0]["visibility"] = "public"
        assert succeeded(await w.start(spec, ops=rich(), personal=True, auth="oauth"))
        holdings = await w.holdings()
        assert holdings[CLIENT_EMAIL] == holdings["records"] and holdings["records"]
        for other in (ADMIN, ALICE, BOB, OLGA):
            assert not holdings[other], f"{other} reads a personal connector's public repository"
        await w.assert_matches_source("personal connector, public repository")

    async def test_tc_ghsim_pers_003_first_sync_content(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=rich(), personal=True, auth="oauth"))
        kinds = {r["recordType"] for r in await w.records()}
        assert {"TICKET", "PULL_REQUEST", "CODE_FILE"} <= kinds, kinds
        assert "Steps: click login" in await w.stream_text(f"{w.repo_id()}/issues/1")
