# pyright: ignore-file

"""
GitLab Personal on the simulator – the creator alone reads what the account reads
=================================================================================

The personal connector (type "GitLab Personal", personal scope) syncs what the signed-in account reads and grants it to
that one person: no membership list is synced, the project record groups carry no grants of their own and inherit up to
the app, which the creator reaches through their user-to-app edge. Nobody else, not even a GitLab member of the project,
reads any of it.

  TC-GLSIM-PERS-001  a first sync stores what the account reads, and only the creator reads it
  TC-GLSIM-PERS-002  after changes at the source (a member removed, an issue made confidential, a deletion) the creator
                     still reads exactly what the account reads and nobody else reads anything
  TC-GLSIM-PERS-003  a lasting 403 on the members of the project and a 503 once on the issues: nothing lost, nobody else reads
"""

import copy
import uuid

import pytest

from connectors.gitlab.sim_instances import base_instance, email_of
from connectors.gitlab.sim_world import (
    CONNECTOR_URL,
    GitLabWorld,
    Scope,
    compare,
    fault,
    succeeded,
)
from helper.clients.connectors_client import ConnectorOAuthConfigsClient

PERSONAL = "GitLab Personal"
_ORG_APPS: dict[str, str] = {}


def org_oauth_app(w: GitLabWorld) -> str:
    """The id of an OAuth app the admin saved for GitLab Personal, made once per stack. A person's own app must name a
    public https instance (connectors/api/router.py ``_require_public_oauth_app_addresses``); only an app saved for the
    org may name the simulator's address. The tests sign in by storing a token (``inject_access_token``), so the client
    id and secret are never used."""
    key = w.client.base_url
    if key not in _ORG_APPS:
        resp = ConnectorOAuthConfigsClient(w.client).create_config(PERSONAL, {
            "oauthInstanceName": f"gitlab-personal-sim-{uuid.uuid4().hex[:8]}",
            "config": {"clientId": "gitlab-sim-client", "clientSecret": "gitlab-sim-secret", "instanceUrl": CONNECTOR_URL},
        })
        assert resp.status_code in (200, 201), f"saving the GitLab Personal OAuth app answered HTTP {resp.status_code}"
        oauth = resp.json().get("oauthConfig") or {}
        _ORG_APPS[key] = str(oauth.get("_id") or oauth.get("id") or "")
        assert _ORG_APPS[key], "saving the GitLab Personal OAuth app returned no id"
    return _ORG_APPS[key]


def owner_only(expected: dict) -> dict:
    """The oracle of a personal connector: whatever the account reads, only the account (the creator) reads."""
    only = copy.deepcopy(expected)
    for r in only["records"]:
        r["readers"] = ["admin"] if "admin" in r["readers"] else []
        r["anonymous"] = False
    return only


async def personal_differences(w: GitLabWorld) -> list[dict]:
    records = await w.records()
    problems = compare(owner_only(w.sim.expected()), w.scope, records, await w.readable(records))
    # A personal connector has no member list, so it files a confidential issue with the other issues: the record
    # group carries no access of its own and only the creator reads either.
    return [p for p in problems if not (p["type"] == "PARENT" and "confidential" in str(p.get("should")))]


async def start_personal(w: GitLabWorld, instance: dict | None = None) -> str | None:
    w.load(instance or base_instance())
    w.scope = Scope(projects=["acme/widgets"])
    from connectors.gitlab.sim_world import sync_filters

    config = {"auth": {"oauthConfigId": org_oauth_app(w), "instanceUrl": CONNECTOR_URL}, "filters": sync_filters()}
    created = w.client.create_connector(connector_type=PERSONAL, instance_name=f"gitlab-personal-sim-{uuid.uuid4().hex[:8]}", scope="personal",
                                        config=config, auth_type="OAUTH")
    w.connector_id = created.connector_id
    from helper.oauth_token_helper import inject_access_token

    inject_access_token(w.connector_id, w.tokens["oa"])
    return await w.enable()


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimPersonal:

    async def _clean(self, w: GitLabWorld, label: str) -> None:
        problems = [p for p in await personal_differences(w) if not p["type"].startswith("DENIED:project-visibility")]
        assert not problems, f"{label}: {problems[:15]}"

    async def test_tc_glsim_pers_001_only_the_creator_reads(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await start_personal(w))
        await self._clean(w, "a personal first sync")
        held = await w.holdings()
        assert held[email_of("admin")], "the creator reads nothing"
        others = {who: len(v) for who, v in held.items() if who not in ("records", email_of("admin")) and v}
        assert not others, f"other people read a personal connector's records: {others}"

    async def test_tc_glsim_pers_002_after_changes(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await start_personal(w))
        w.ops({"op": "set_member", "project": "widgets", "user": "dev", "level": None},
              {"op": "set_issue", "issue": "open", "confidential": True}, {"op": "delete_issue", "issue": "closed"},
              {"op": "add_issue", "project": "widgets", "title": "Added later", "author": "gus"})
        assert succeeded(await w.sync())
        await self._clean(w, "a personal run after changes")
        held = await w.holdings()
        others = {who: len(v) for who, v in held.items() if who not in ("records", email_of("admin")) and v}
        assert not others, f"other people read a personal connector's records: {others}"

    async def test_tc_glsim_pers_003_faults(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await start_personal(w))
        held = await w.holdings()
        w.ops({"op": "add_issue", "project": "widgets", "title": "During faults", "author": "dev"})
        w.faults(fault(r"^/api/v4/projects/\d+/members/all$", 403), fault(r"^/api/v4/projects/\d+/issues$", 503, first_n=1))
        await w.sync()
        w.clear_faults(every_rule=False)
        after = await w.holdings()
        lost = {k: sorted(held[k] - after[k]) for k in held if held[k] - after[k]}
        assert not lost, f"a personal run under faults lost {lost}"
        others = {who: len(v) for who, v in after.items() if who not in ("records", email_of("admin")) and v}
        assert not others, f"other people read a personal connector's records: {others}"
        assert succeeded(await w.sync())
        await self._clean(w, "after the faults cleared")
