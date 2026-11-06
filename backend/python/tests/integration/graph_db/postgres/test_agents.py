"""The agent document with what hangs off it, and the agents that use a model or a web search provider, on PostgreSQL against a real database.

Both ways: an agent reaches a caller only as its owner in the org or through the org (another org, a deleted agent, a non-owner grant, a user outside the org
get nothing), and what an agent has (toolsets and their tools, knowledge, skills, MCP servers, the org share) is all in its document. A read that fails answers
its default and answers right once the store is back.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import pytest

from .test_access import World, _org
from .test_access import w as w
from .test_grants import failing, in_org

pytestmark = pytest.mark.integration


async def agent_world(w: World) -> None:
    await w.nodes("organizations", {"_key": w.org})
    await w.nodes("agentInstances", {"_key": "a1", "name": "Alpha", "createdBy": "boss", "models": ["gpt-4", "claude_haiku", "gpt-4_turbo"], "webSearch": "serper"},
                  {"_key": "a2", "name": "Beta", "createdBy": "ann", "models": ["gpt-40"], "webSearch": "bing"},
                  {"_key": "a3", "name": "Shared", "createdBy": "boss", "models": ["gpt-4"], "webSearch": "serper"},
                  {"_key": "gone", "name": "Gone", "createdBy": "boss", "models": ["gpt-4"], "webSearch": "serper", "isDeleted": True},
                  {"_key": "w1", "name": "Writer", "createdBy": "boss", "models": ["gpt-4"], "webSearch": "serper"},
                  {"_key": "nomodels", "name": "Bare", "createdBy": "nobody", "models": "gpt-4", "webSearch": "serper"},
                  {"_key": "gtype", "name": "Gtype", "createdBy": "boss", "models": ["gpt-4"], "webSearch": "serper"}, {"_key": "out_only", "name": "Out", "createdBy": "outsider", "models": ["gpt-4"], "webSearch": "serper"},
                  {"_key": "dom", "name": "Dom", "createdBy": "boss", "models": ["gpt-4"], "webSearch": "serper"},
                  {"_key": "num", "name": "Num", "createdBy": "boss", "models": [5, {"k": "5"}, None], "webSearch": "serper"})
    await w.nodes("users", {"_key": "boss", "fullName": "The Boss"}, {"_key": "ann", "name": "Ann"}, {"_key": "outsider", "fullName": "Out"})
    for u in ("boss", "ann"):
        await w.edge("belongsTo", ("users", u), ("organizations", w.org), entityType="ORGANIZATION")
    for a in ("a1", "a2", "gone", "nomodels", "num"):
        await w.edge("permission", ("users", "boss" if a != "a2" else "ann"), ("agentInstances", a), type="USER", role="OWNER")
    await w.edge("permission", ("users", "boss"), ("agentInstances", "w1"), type="USER", role="WRITER")           # a writer is not the owner
    await w.edge("permission", ("users", "outsider"), ("agentInstances", "a3"), type="USER", role="OWNER")        # an owner who is not of the org
    await w.edge("permission", ("users", "boss"), ("agentInstances", "gtype"), type="GROUP", role="OWNER")        # an owner grant of another type is not the user's own
    await w.nodes("organizations", {"_key": "elsewhere"})
    await w.edge("belongsTo", ("users", "outsider"), ("organizations", "elsewhere"), entityType="ORGANIZATION")
    await w.edge("permission", ("users", "outsider"), ("agentInstances", "out_only"), type="USER", role="OWNER")       # owned by a user of another organization
    await w.edge("permission", ("organizations", w.org), ("agentInstances", "dom"), type="DOMAIN", role="READER")        # a share that is not the organization's
    await w.edge("permission", ("organizations", w.org), ("agentInstances", "a3"), type="ORG", role="READER")       # but shared with the org
    await w.edge("permission", ("organizations", w.org), ("agentInstances", "a1"), type="ORG", role="READER")       # owned and shared: listed once


async def test_the_agents_of_a_model_are_those_the_org_owns_or_is_given_and_that_name_it(w: World) -> None:
    await agent_world(w)
    p = w.p
    got = await p.get_agents_by_model_key(w.org, "gpt-4")
    assert got == [{"name": "Alpha", "_key": "a1", "creatorName": "The Boss"}, {"name": "Shared", "_key": "a3", "creatorName": "The Boss"}]      # the key, or <key>_..., not gpt-40
    assert [a["_key"] for a in await p.get_agents_by_model_key(w.org, "claude")] == ["a1"]                  # a key that starts <key>_
    assert [a["_key"] for a in await p.get_agents_by_model_key(w.org, "gpt-40")] == ["a2"] and (await p.get_agents_by_model_key(w.org, "gpt-40"))[0]["creatorName"] == "Ann"
    assert await p.get_agents_by_model_key(w.org, "5") == [] and await p.get_agents_by_model_key(w.org, "k") == []           # a model that is not a string names no key
    assert await p.get_agents_by_model_key(w.org, "nope") == [] and await p.get_agents_by_model_key(w.org, "gpt") == []
    assert await p.get_agents_by_model_key(_org(), "gpt-4") == []
    await p.update_node("a2", "agentInstances", {"createdBy": "unknown"})
    assert (await p.get_agents_by_model_key(w.org, "gpt-40"))[0]["creatorName"] is None
    await p.delete_edge(w.org, "organizations", "a3", "agentInstances", "permission")
    assert [a["_key"] for a in await p.get_agents_by_model_key(w.org, "gpt-4")] == ["a1"]                  # no longer shared: gone from the list
    await p.update_node("a1", "agentInstances", {"isDeleted": True})
    assert await p.get_agents_by_model_key(w.org, "gpt-4") == []


async def test_the_agents_of_a_web_search_provider_follow_the_same_rule(w: World) -> None:
    await agent_world(w)
    p = w.p
    got = await p.get_agents_by_web_search_provider(w.org, "serper")
    assert [(a["_key"], a["creatorName"]) for a in got] == [("a1", "The Boss"), ("a3", "The Boss"), ("nomodels", None), ("num", "The Boss")]
    assert [a["_key"] for a in await p.get_agents_by_web_search_provider(w.org, "bing")] == ["a2"]
    assert await p.get_agents_by_web_search_provider(w.org, "none") == [] and await p.get_agents_by_web_search_provider(_org(), "serper") == []


async def test_a_failed_agent_list_is_empty_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await agent_world(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_agents_by_model_key(w.org, "gpt-4") == [] and await w.p.get_agents_by_web_search_provider(w.org, "serper") == []
    monkeypatch.setattr(w.p, "_q", real)
    assert len(await w.p.get_agents_by_model_key(w.org, "gpt-4")) == 2


async def full_agent(w: World) -> None:
    await w.nodes("agentInstances", {"_key": "ag", "name": "Full"}, {"_key": "ag2", "name": "Other"}, {"_key": "dead", "name": "Dead", "isDeleted": True})
    await w.nodes("agentToolsets", {"_key": "ts", "name": "slack", "displayName": "Slack", "type": "app", "instanceId": "i1", "instanceName": "Team Slack", "selectedTools": ["a"]})
    await w.nodes("agentTools", {"_key": "t1", "name": "send", "fullName": "slack.send", "toolsetName": "slack", "description": "d1"}, {"_key": "t0", "name": "read", "fullName": "slack.read"})
    await w.edge("agentHasToolset", ("agentInstances", "ag"), ("agentToolsets", "ts"))
    await w.edge("toolsetHasTool", ("agentToolsets", "ts"), ("agentTools", "t1"))
    await w.edge("toolsetHasTool", ("agentToolsets", "ts"), ("agentTools", "t0"))
    await w.nodes("agentToolsets", {"_key": "ts2", "name": "mail", "displayName": "Mail", "type": "app", "instanceId": "i2", "instanceName": "Team Mail", "selectedTools": ["z"]})
    await w.nodes("agentTools", {"_key": "x1", "name": "draft", "fullName": "mail.draft"})
    await w.edge("agentHasToolset", ("agentInstances", "ag2"), ("agentToolsets", "ts2"))
    await w.edge("toolsetHasTool", ("agentToolsets", "ts2"), ("agentTools", "x1"))
    await w.nodes("agentKnowledge", {"_key": "k1", "connectorId": "kapp", "filters": '{"recordGroups": ["g"]}'}, {"_key": "k2", "connectorId": "ghost-app", "filters": None},
                  {"_key": "k3", "connectorId": "kapp", "filters": "not json"}, {"_key": "k4", "connectorId": "kapp", "filters": ["a"]})
    await w.nodes("apps", {"_key": "kapp", "name": "Drive", "type": "GOOGLE_DRIVE"})
    for k in ("k1", "k2", "k3", "k4"):
        await w.edge("agentHasKnowledge", ("agentInstances", "ag"), ("agentKnowledge", k))
    await w.nodes("agentSkills", {"_key": "s1", "name": "summarise", "description": "sd", "category": "c", "subcategory": "sc", "version": "2", "status": "ACTIVE"})
    await w.edge("agentHasSkill", ("agentInstances", "ag"), ("agentSkills", "s1"))
    await w.nodes("agentMcpServers", {"_key": "ms", "name": "mcp", "displayName": "MCP", "typeId": "tid", "instanceId": "mi", "allTools": True})
    await w.nodes("agentTools", {"_key": "mt", "name": "mtool", "fullName": "mcp.mtool", "description": "md"})
    await w.edge("agentHasMcpServer", ("agentInstances", "ag"), ("agentMcpServers", "ms"))
    await w.edge("mcpServerHasTool", ("agentMcpServers", "ms"), ("agentTools", "mt"))


async def test_an_agent_comes_with_its_toolsets_knowledge_skills_servers_and_whether_the_org_has_it(w: World) -> None:
    await full_agent(w)
    await w.nodes("organizations", {"_key": w.org}, {"_key": "other-org-node"})
    p = w.p
    a = await p.get_agent("ag", w.org)
    assert a["_key"] == "ag" and a["name"] == "Full" and a["shareWithOrg"] is False and "mcpServersUnavailable" not in a
    assert a["toolsets"] == [{"_key": "ts", "name": "slack", "displayName": "Slack", "type": "app", "instanceId": "i1", "instanceName": "Team Slack", "selectedTools": ["a"],
                              "tools": [{"_key": "t0", "name": "read", "fullName": "slack.read", "toolsetName": None, "description": None},
                                        {"_key": "t1", "name": "send", "fullName": "slack.send", "toolsetName": "slack", "description": "d1"}]}]
    know = {k["_key"]: k for k in a["knowledge"]}
    assert know["k1"]["filtersParsed"] == {"recordGroups": ["g"]} and know["k1"]["name"] == "Drive" and know["k1"]["type"] == "GOOGLE_DRIVE" and know["k1"]["displayName"] == "Drive"
    assert know["k2"]["filtersParsed"] == {} and know["k2"]["type"] == "UNKNOWN" and know["k2"]["name"] == "ghost-app"          # an app that is gone: named by its id
    assert know["k3"]["filtersParsed"] == {} and know["k3"]["filters"] == "not json"
    assert know["k4"]["filtersParsed"] == ["a"]                                                                      # neither null nor a string: passed through
    assert a["skills"] == [{"name": "summarise", "description": "sd", "category": "c", "subcategory": "sc", "version": "2", "status": "ACTIVE", "deprecatedReason": None, "replacedBy": None}]
    assert a["mcpServers"] == [{"_key": "ms", "name": "mcp", "displayName": "MCP", "typeId": "tid", "instanceId": "mi", "allTools": True,
                                "tools": [{"_key": "mt", "name": "mtool", "fullName": "mcp.mtool", "description": "md"}]}]
    other = await p.get_agent("ag2", w.org)
    assert [(t["_key"], [x["_key"] for x in t["tools"]]) for t in other["toolsets"]] == [("ts2", ["x1"])]          # its own toolset and tool, none of ag's
    assert other["knowledge"] == [] and other["skills"] == [] and other["mcpServers"] == [] and [t["_key"] for t in a["toolsets"]] == ["ts"]      # nothing crosses between the two


async def test_the_org_share_is_the_orgs_own_grant_and_a_deleted_or_other_orgs_agent_is_not_there(w: World) -> None:
    await full_agent(w)
    await w.nodes("organizations", {"_key": w.org}, {"_key": "elsewhere"})
    await w.edge("permission", ("organizations", "elsewhere"), ("agentInstances", "ag"), type="ORG", role="READER")
    p = w.p
    assert (await p.get_agent("ag", w.org))["shareWithOrg"] is False                      # another organization's grant is not this org's
    assert (await p.get_agent("ag"))["shareWithOrg"] is True                               # with no org named any organization's grant counts
    await w.edge("permission", ("organizations", w.org), ("agentInstances", "ag"), type="ORG", role="READER")
    assert (await p.get_agent("ag", w.org))["shareWithOrg"] is True
    await p.delete_edge(w.org, "organizations", "ag", "agentInstances", "permission")
    assert (await p.get_agent("ag", w.org))["shareWithOrg"] is False
    assert await p.get_agent("dead", w.org) is None and await p.get_agent("missing") is None
    assert await p.get_agent("ag", _org()) is None                                          # an org that is not the ambient one reads nothing
    with in_org(_org()):
        assert await p.get_agent("ag", w.org) is None


async def test_an_agent_whose_servers_cannot_be_read_is_returned_without_them_and_marked(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await full_agent(w)

    async def broken(*a: object, **k: object) -> dict:
        raise RuntimeError("store down")
    monkeypatch.setattr(w.p, "_project_agents_mcp_servers", broken)
    a = await w.p.get_agent("ag", w.org)
    assert a["mcpServers"] == [] and a["mcpServersUnavailable"] is True and len(a["toolsets"]) == 1
    monkeypatch.undo()
    assert len((await w.p.get_agent("ag", w.org))["mcpServers"]) == 1


async def test_a_failed_agent_read_is_none_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await full_agent(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_agent("ag", w.org) is None
    monkeypatch.setattr(w.p, "_q", real)
    assert (await w.p.get_agent("ag", w.org))["name"] == "Full"
