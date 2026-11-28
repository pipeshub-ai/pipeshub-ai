# pyright: ignore-file

"""
GitHub Teams on the simulator: who may read what
================================================

GitHub's rules (organisation owner, base permission, teams and their children, direct collaborators, public / internal /
private) decide who reads a repository; after every sync PipesHub's permission query is compared with them for every
person: the owner, a team member, a member with no grant, an outside collaborator, a GitHub account outside the
organisation, a PipesHub user with no GitHub account, and the PipesHub user who connected the connector.

  TC-GHSIM-ACL-001  Private repository: the team, the collaborator and the owner read; a member with no grant, a stranger
                    and a PipesHub user with no GitHub account read nothing [E]
  TC-GHSIM-ACL-002  Public repository: every GitHub account reads, a PipesHub user with no account does not [E]
  TC-GHSIM-ACL-003  A PipesHub user with no GitHub account reads nothing, in a private and in an internal repository [E]
  TC-GHSIM-ACL-004  A collaborator and a team member removed lose access on the next sync [E]
  TC-GHSIM-ACL-005  A child team inherits; the base permission raised and lowered moves everyone [E]
  TC-GHSIM-ACL-006  Visibility private -> public -> private -> internal [E]
  TC-GHSIM-ACL-007  A grandchild team inherits; a team removed from the repository and a team moved out from under its parent [E]
  TC-GHSIM-ACL-008  A member leaves the organisation; an outside collaborator joins it; a member made owner and demoted [E]
  TC-GHSIM-ACL-009  An archived repository stays readable by the same people [E]
  TC-GHSIM-ACL-010  Two repositories in one organisation never share access (a repository filter selects one) [E]
  TC-GHSIM-ACL-011  The PipesHub user who connected the connector reads what the connected GitHub account reads, and
                    nothing else (the account's repository is private to everyone else) [E]
  TC-GHSIM-ACL-012  The comparison is not satisfied by a world nobody reads: the readers differ per person [O]
  TC-GHSIM-ACL-013  A GitHub account with no public e-mail: its verified-domain address is used; one with none reads nothing
                    and never gives access to a wrong PipesHub user [E]
  TC-GHSIM-ACL-014  A GitHub account that is in no organisation reads a public repository and nothing private [E]
  TC-GHSIM-ACL-015  A PipesHub user with no GitHub account must not read a public repository's records: strict known gap,
                    fixed by the open pull request #6209 [E]
"""

import pytest
from connectors.github_teams.sim_instances import ADMIN, ALICE, BOB, CAROL, CLIENT_EMAIL, DAVE, OLGA, acme
from connectors.github_teams.sim_world import KNOWN_GAPS, GithubWorld, succeeded

ONE = [{"op": "add_issue", "repo": "widgets", "title": "Issue", "key": "issue"},
       {"op": "add_pull", "repo": "widgets", "title": "PR", "changes": {"src/pr.py": "pr = 1\n"}}]


async def readers(w: GithubWorld, *people: str) -> dict[str, bool]:
    """person -> whether they read anything of the connector."""
    holdings = await w.holdings()
    return {p: bool(holdings[p]) for p in people}


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimAccess:

    async def test_tc_ghsim_acl_001_private_repository(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        await w.assert_matches_source("private repository")
        assert await readers(w, ADMIN, ALICE, OLGA, BOB, DAVE, CAROL) == {
            ADMIN: True, ALICE: True, OLGA: True, BOB: False, DAVE: False, CAROL: False}

    async def test_tc_ghsim_acl_002_public_repository(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["repos"][0]["visibility"] = "public"
        assert succeeded(await w.start(spec, ops=ONE))
        await w.assert_matches_source("public repository")
        # CAROL (a PipesHub user with no GitHub account) is TC-GHSIM-ACL-015's known gap
        assert await readers(w, ADMIN, ALICE, OLGA, BOB, DAVE) == {ADMIN: True, ALICE: True, OLGA: True, BOB: True, DAVE: True}

    async def test_tc_ghsim_acl_003_user_with_no_github_account(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        assert not (await w.holdings())[CAROL]
        w.ops({"op": "set_repo", "repo": "widgets", "visibility": "internal"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("internal repository")
        assert not (await w.holdings())[CAROL], "a person with no GitHub account reads an internal repository"
        assert (await w.holdings())[BOB], "an organisation member does not read an internal repository"

    async def test_tc_ghsim_acl_004_removed_collaborator_and_team_member(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        assert (await w.holdings())[OLGA] and (await w.holdings())[ALICE]
        w.ops({"op": "set_collaborator", "repo": "widgets", "user": "olga", "role": None},
              {"op": "set_team_member", "team": "core", "user": "alice", "member": False})
        assert succeeded(await w.sync())
        assert not (await w.holdings())[OLGA] and not (await w.holdings())[ALICE]
        await w.assert_matches_source("after access was removed")

    async def test_tc_ghsim_acl_005_child_team_and_base_permission(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "set_member", "org": "acme", "user": "dave", "role": "member"},
               {"op": "add_team", "org": "acme", "slug": "core-mobile", "members": ["dave"], "parent": "core"}, *ONE]
        assert succeeded(await w.start(acme(), ops=ops))
        await w.assert_matches_source("child team inherits")
        assert (await w.holdings())[DAVE]
        w.ops({"op": "set_base_permission", "org": "acme", "role": "read"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("base permission raised")
        assert (await w.holdings())[BOB]
        w.ops({"op": "set_base_permission", "org": "acme", "role": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("base permission lowered")
        assert not (await w.holdings())[BOB]

    async def test_tc_ghsim_acl_006_visibility_changes(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        for visibility, bob in (("public", True), ("private", False), ("internal", True)):
            w.ops({"op": "set_repo", "repo": "widgets", "visibility": visibility})
            assert succeeded(await w.sync())
            await w.assert_matches_source(visibility)
            assert bool((await w.holdings())[BOB]) is bob, f"{visibility}: bob (member, no grant) reads: {not bob}"
            if visibility != "public":  # public: TC-GHSIM-ACL-015
                assert not (await w.holdings())[CAROL], f"{visibility}: a person with no GitHub account reads"
        assert (await w.holdings())[OLGA], "an outside collaborator lost her direct grant on an internal repository"

    async def test_tc_ghsim_acl_007_nested_teams(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "set_member", "org": "acme", "user": "dave", "role": "member"},
               {"op": "add_team", "org": "acme", "slug": "mobile", "members": [], "parent": "core"},
               {"op": "add_team", "org": "acme", "slug": "ios", "members": ["dave"], "parent": "acme/mobile"}, *ONE]
        assert succeeded(await w.start(acme(), ops=ops))
        await w.assert_matches_source("grandchild inherits")
        assert (await w.holdings())[DAVE]
        w.ops({"op": "set_team_repo", "team": "core", "repo": "widgets", "role": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("team removed from the repository")
        assert not (await w.holdings())[DAVE] and not (await w.holdings())[ALICE]
        w.ops({"op": "set_team_repo", "team": "core", "repo": "widgets", "role": "write"},
              {"op": "set_team_parent", "team": "acme/mobile", "parent": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("team moved out from under its parent")
        assert not (await w.holdings())[DAVE], "dave still reads after his team left core"

    async def test_tc_ghsim_acl_008_membership_changes(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        w.ops({"op": "set_member", "org": "acme", "user": "alice", "role": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("member removed")
        assert not (await w.holdings())[ALICE]
        w.ops({"op": "set_member", "org": "acme", "user": "olga", "role": "member"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("outside collaborator joined the organisation")
        assert (await w.holdings())[OLGA], "olga lost her direct grant by joining the organisation"
        w.ops({"op": "set_member", "org": "acme", "user": "bob", "role": "admin"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("bob made owner")
        assert (await w.holdings())[BOB]
        w.ops({"op": "set_member", "org": "acme", "user": "bob", "role": "member"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("owner demoted")
        assert not (await w.holdings())[BOB]

    async def test_tc_ghsim_acl_009_archived_repository(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        w.ops({"op": "set_repo", "repo": "widgets", "archived": True})
        assert succeeded(await w.sync())
        await w.assert_matches_source("archived")
        assert (await w.holdings())[ALICE], "an archived repository stopped being readable"

    async def test_tc_ghsim_acl_010_two_repositories_never_share_access(self, github_world: GithubWorld) -> None:
        w = github_world
        ops = [{"op": "add_issue", "repo": "widgets", "title": "Widgets issue"},
               {"op": "add_issue", "repo": "gadgets", "title": "Gadgets issue"}]
        assert succeeded(await w.start(acme(repos=2), ops=ops))
        await w.assert_matches_source("widgets")
        assert not (await w.holdings())[BOB], "bob (a reader of gadgets only) reads widgets"
        assert succeeded(await w.restart(repo_filter_for("acme/gadgets")))
        await w.assert_matches_source("gadgets")
        assert (await w.holdings())[BOB] and not (await w.holdings())[ALICE], \
            "after the switch bob (reader of gadgets) must read and alice (team of widgets) must not"

    async def test_tc_ghsim_acl_011_creator_reads_what_the_connected_account_reads(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        holdings = await w.holdings()
        assert holdings[CLIENT_EMAIL] == holdings[ADMIN] and holdings[CLIENT_EMAIL], \
            "the user who connected the connector must read exactly what the connected account (the owner) reads"
        await w.assert_matches_source("creator and connected account")

    async def test_tc_ghsim_acl_012_readers_differ_per_person(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        holdings = await w.holdings()
        assert len({frozenset(holdings[p]) for p in (ADMIN, BOB, CAROL)}) == 2, \
            "everybody reads the same: the comparison would prove nothing"

    async def test_tc_ghsim_acl_013_account_without_a_public_email(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["orgs"][0]["verified_domain"] = True
        # bob has no public address; the organisation's verified domain gives one. erin has none at all.
        for u in spec["users"]:
            if u["login"] == "bob":
                u["email"], u["corporate_email"] = None, BOB
        spec["users"].append({"login": "erin", "email": None})
        spec["orgs"][0]["members"].append("erin")
        spec["repos"][0]["collaborators"]["bob"] = "read"
        spec["repos"][0]["collaborators"]["erin"] = "read"
        assert succeeded(await w.start(spec, ops=ONE))
        await w.assert_matches_source("accounts without a public e-mail")
        assert (await w.holdings())[BOB], "the verified-domain address of bob was not used"

    async def test_tc_ghsim_acl_014_account_in_no_organisation(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        assert not (await w.holdings())[DAVE]
        w.ops({"op": "set_repo", "repo": "widgets", "visibility": "public"})
        assert succeeded(await w.sync())
        assert (await w.holdings())[DAVE], "a GitHub account outside the organisation cannot read a public repository"
        await w.assert_matches_source("public, with a stranger")

    async def test_tc_ghsim_acl_015_public_content_needs_an_account(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["repos"][0]["visibility"] = "public"
        assert succeeded(await w.start(spec, ops=ONE))
        problems = await w.differences()
        w.assert_gap("public repository", problems, "LEAK:public-without-account")
        assert {p["user"] for p in problems if p["type"] == "LEAK:public-without-account"} == {CAROL}, \
            "only the PipesHub user with no GitHub account may read public records without a GitHub account"
        assert not [p for p in problems if p["type"] not in KNOWN_GAPS], problems


def repo_filter_for(name: str):
    from connectors.github_teams.sim_world import repo_filter
    return repo_filter(name)
