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


async def test_a_failed_agent_list_raises_and_comes_back(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await agent_world(w)
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    with pytest.raises(RuntimeError):                       # a delete is refused on this answer: a failed read is never "no agents"
        await w.p.get_agents_by_model_key(w.org, "gpt-4")
    with pytest.raises(RuntimeError):
        await w.p.get_agents_by_web_search_provider(w.org, "serper")
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


async def test_an_agent_read_in_a_transaction_sees_what_that_transaction_linked(w: World) -> None:
    """Every part of the document is read in the caller's transaction: toolsets, tools, knowledge, skills and servers linked in it are there before the commit."""
    await w.nodes("agentInstances", {"_key": "ag", "name": "Tx"})
    p = w.p
    t = await p.begin_transaction([], [])
    e = lambda f, fc, to, tc: {"from_id": f, "from_collection": fc, "to_id": to, "to_collection": tc}      # noqa: E731
    await p.batch_upsert_nodes([{"_key": "ts", "name": "slack"}], "agentToolsets", transaction=t)
    await p.batch_upsert_nodes([{"_key": "t1", "name": "send"}], "agentTools", transaction=t)
    await p.batch_upsert_nodes([{"_key": "k1", "connectorId": "kapp", "filters": None}], "agentKnowledge", transaction=t)
    await p.batch_upsert_nodes([{"_key": "kapp", "name": "Drive", "type": "GOOGLE_DRIVE"}], "apps", transaction=t)
    await p.batch_upsert_nodes([{"_key": "s1", "name": "summarise"}], "agentSkills", transaction=t)
    await p.batch_upsert_nodes([{"_key": "ms", "name": "mcp"}], "agentMcpServers", transaction=t)
    await p.batch_create_edges([e("ag", "agentInstances", "ts", "agentToolsets")], "agentHasToolset", transaction=t)
    await p.batch_create_edges([e("ts", "agentToolsets", "t1", "agentTools")], "toolsetHasTool", transaction=t)
    await p.batch_create_edges([e("ag", "agentInstances", "k1", "agentKnowledge")], "agentHasKnowledge", transaction=t)
    await p.batch_create_edges([e("ag", "agentInstances", "s1", "agentSkills")], "agentHasSkill", transaction=t)
    await p.batch_create_edges([e("ag", "agentInstances", "ms", "agentMcpServers")], "agentHasMcpServer", transaction=t)
    inside = await p.get_agent("ag", w.org, transaction=t)
    outside = await p.get_agent("ag", w.org)
    await p.rollback_transaction(t)
    assert [(s["_key"], [x["_key"] for x in s["tools"]]) for s in inside["toolsets"]] == [("ts", ["t1"])]
    assert [(k["_key"], k["name"]) for k in inside["knowledge"]] == [("k1", "Drive")]
    assert [s["name"] for s in inside["skills"]] == ["summarise"] and [s["_key"] for s in inside["mcpServers"]] == ["ms"]
    assert outside["toolsets"] == [] and outside["knowledge"] == [] and outside["skills"] == [] and outside["mcpServers"] == []


async def test_another_orgs_nodes_with_the_same_ids_never_reach_an_agents_document(w: World) -> None:
    """Every join of the agent's document stays in the agent's org: another org holding the same agent, toolset, tool and app ids, plus a tool and an app
    of its own under them, changes nothing."""
    await full_agent(w)
    before = await w.p.get_agent("ag", w.org)
    other = _org()
    with in_org(other):
        ow = World(w.p, other)
        await full_agent(ow)
        await ow.nodes("agentTools", {"_key": "t9", "name": "leak", "fullName": "slack.leak"})
        await ow.edge("toolsetHasTool", ("agentToolsets", "ts"), ("agentTools", "t9"))
        await ow.nodes("apps", {"_key": "ghost-app", "name": "Leaked", "type": "GOOGLE_DRIVE"})
    assert await w.p.get_agent("ag", w.org) == before


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


async def test_a_save_writes_the_harness_agent_fields_and_a_read_decodes_them(w: World) -> None:
    """WP-114: the harness settings an agent save carries (engine profile, model account, owner team, role models, gates, environment) are stored as
    the other backends store them (the JSON ones as text) and read back as JSON; models are normalised; a user who may not edit changes nothing."""
    await agent_world(w)
    p = w.p
    saved = await p.update_agent("a1", {
        "name": "Alpha 2", "engineProfile": {"engine": "claude-code", "narrow": {"tools": ["git"]}}, "modelAccountId": "acct-1", "ownerTeam": "team-1",
        "roleModels": {"reviewer": "m1"}, "gates": {"planApproval": True}, "environment": {"vars": {"A": "1"}},
        "models": [{"modelKey": "gpt-4", "modelName": "turbo"}, "gpt-4_turbo", "claude"], "notAField": "dropped",
    }, "boss", w.org)
    assert saved is True
    a = await p.get_agent("a1", w.org)
    assert a["name"] == "Alpha 2" and a["updatedBy"] == "boss" and "notAField" not in a
    assert a["engineProfile"] == {"engine": "claude-code", "narrow": {"tools": ["git"]}} and a["modelAccountId"] == "acct-1" and a["ownerTeam"] == "team-1"
    assert a["roleModels"] == {"reviewer": "m1"} and a["gates"] == {"planApproval": True} and a["environment"] == {"vars": {"A": "1"}}
    assert a["models"] == ["gpt-4_turbo", "claude"]
    stored = await p.get_document("a1", "agentInstances")
    assert isinstance(stored["engineProfile"], str) and isinstance(stored["gates"], str)                          # JSON text, as on Neo4j and Arango
    assert await p.update_agent("a1", {"gates": {}}, "boss", w.org) is True
    assert (await p.get_agent("a1", w.org))["gates"] == {}                                                         # a save that drops every gate drops them
    assert await p.update_agent("gtype", {"name": "nope"}, "boss", w.org) is False                               # a group grant is not the user's own: no edit right
    assert await p.update_agent("a1", {"name": "x"}, "outsider", w.org) is False                                 # no grant in this org: nothing written
    assert (await p.get_agent("a1", w.org))["name"] == "Alpha 2"
    assert await p.update_agent("a1", {"name": "x"}, "boss", _org()) is False                                    # another org: nothing written


# ---- the agent list, edit and delete ----------------------------------------------------------------------------------------------------------

async def listing_world(w: World) -> None:
    """boss owns ``mine`` and ``old``, reads ``read``, writes ``both`` (also shared with the org) and owned ``gone`` (deleted); ann owns ``private`` and
    ``shared`` (shared with the org). ``dom`` is the org's under another grant type, ``far`` another organization's share, and another org holds its own
    agents under the same ids and user key."""
    await w.nodes("organizations", {"_key": w.org}, {"_key": "elsewhere"})
    await w.nodes("users", {"_key": "boss"}, {"_key": "ann"})
    await w.nodes("agentInstances",
                  {"_key": "mine", "name": "Sales Helper", "description": "Answers pipeline questions", "tags": ["crm", 42], "updatedAtTimestamp": 50},
                  {"_key": "old", "name": "Archive 100%_done", "updatedAtTimestamp": 10},
                  {"_key": "read", "name": "Reader", "updatedAtTimestamp": 40},
                  {"_key": "both", "name": "Both", "description": "HR policies", "updatedAtTimestamp": 30},
                  {"_key": "gone", "name": "Gone", "isDeleted": True, "updatedAtTimestamp": 60},
                  {"_key": "private", "name": "Ann private", "updatedAtTimestamp": 70},
                  {"_key": "shared", "name": "Ann shared", "tags": ["Finance"], "updatedAtTimestamp": 20},
                  {"_key": "dom", "name": "Domain", "updatedAtTimestamp": 80}, {"_key": "far", "name": "Far", "updatedAtTimestamp": 90})
    for user, agent, role in (("boss", "mine", "OWNER"), ("boss", "old", "OWNER"), ("boss", "read", "READER"), ("boss", "both", "WRITER"),
                              ("boss", "gone", "OWNER"), ("ann", "private", "OWNER"), ("ann", "shared", "OWNER")):
        await w.edge("permission", ("users", user), ("agentInstances", agent), type="USER", role=role)
    for agent in ("both", "shared"):
        await w.edge("permission", ("organizations", w.org), ("agentInstances", agent), type="ORG", role="READER")
    await w.edge("permission", ("organizations", w.org), ("agentInstances", "dom"), type="DOMAIN", role="READER")
    await w.edge("permission", ("organizations", "elsewhere"), ("agentInstances", "far"), type="ORG", role="READER")
    await w.nodes("agentToolsets", {"_key": "ts", "name": "slack"})
    await w.edge("agentHasToolset", ("agentInstances", "mine"), ("agentToolsets", "ts"))


def access(agents: list[dict]) -> dict[str, tuple]:
    return {a["_key"]: (a["access_type"], a["user_role"], a["can_edit"], a["can_delete"], a["can_share"], a["can_view"], a["shareWithOrg"]) for a in agents}


async def test_a_user_lists_their_own_agents_and_the_orgs_shared_ones_and_nothing_else(w: World) -> None:
    await listing_world(w)
    other = _org()
    with in_org(other):
        ow = World(w.p, other)
        await ow.nodes("organizations", {"_key": other})
        await ow.nodes("users", {"_key": "boss"})
        await ow.nodes("agentInstances", {"_key": "theirs", "name": "Theirs"}, {"_key": "mine", "name": "Not yours"})
        for agent in ("theirs", "mine"):
            await ow.edge("permission", ("users", "boss"), ("agentInstances", agent), type="USER", role="OWNER")
        await ow.edge("permission", ("organizations", other), ("agentInstances", "theirs"), type="ORG", role="READER")
    p = w.p
    got = await p.get_all_agents("boss", w.org)
    assert [a["_key"] for a in got] == ["mine", "read", "both", "shared", "old"]                       # newest first by default
    assert access(got) == {
        "mine": ("INDIVIDUAL", "OWNER", True, True, True, True, False),
        "read": ("INDIVIDUAL", "READER", False, False, False, True, False),
        "both": ("INDIVIDUAL", "WRITER", True, False, False, True, True),             # the user's own grant wins over the org's, and the org share shows
        "shared": ("ORG", "READER", False, False, False, True, True),
        "old": ("INDIVIDUAL", "OWNER", True, True, True, True, False)}
    assert next(a for a in got if a["_key"] == "mine")["name"] == "Sales Helper"                # this org's agent, not the other org's of the same id
    assert [a["_key"] for a in await p.get_all_agents("ann", w.org)] == ["private", "both", "shared"]       # ann: hers and the org's, not boss's private ones
    assert [a["_key"] for a in await p.get_all_agents("u", w.org)] == ["both", "shared"]              # a member with no grant of their own: the org's
    assert [a["_key"] for a in await p.get_all_agents("nobody", w.org)] == ["both", "shared"]             # no user node: the org's share only, as on Neo4j
    assert await p.get_all_agents("boss", other) == []                                                # an org that is not the ambient one reads nothing
    with in_org(other):
        assert [(a["_key"], a["name"]) for a in await p.get_all_agents("boss", other)] == [("mine", "Not yours"), ("theirs", "Theirs")]
        assert await p.get_all_agents("boss", w.org) == []


async def test_the_list_takes_deleted_agents_search_sort_and_a_page(w: World) -> None:
    await listing_world(w)
    p = w.p
    assert [a["_key"] for a in await p.get_all_agents("boss", w.org, is_deleted=True)] == ["gone"]       # only the deleted ones, and only those the user reaches
    assert [a["_key"] for a in await p.get_all_agents("ann", w.org, is_deleted=True)] == []
    keys = lambda agents: [a["_key"] for a in agents]  # noqa: E731
    assert keys(await p.get_all_agents("boss", w.org, search="  SALES ")) == ["mine"]                    # name, without case or surrounding space
    assert keys(await p.get_all_agents("boss", w.org, search="pipeline")) == ["mine"]                    # description
    assert keys(await p.get_all_agents("boss", w.org, search="finance")) == ["shared"]                   # a tag
    assert keys(await p.get_all_agents("boss", w.org, search="42")) == ["mine"]                          # a tag that is not a string
    assert keys(await p.get_all_agents("boss", w.org, search="%_")) == ["old"]                           # literal text, not a pattern
    assert keys(await p.get_all_agents("boss", w.org, search="a%e")) == []
    assert keys(await p.get_all_agents("boss", w.org, search="ann")) == ["shared"]                       # ann's private agent is not searched
    assert len(await p.get_all_agents("boss", w.org, search="   ")) == 5                                 # blank: no filter
    assert keys(await p.get_all_agents("boss", w.org, sort_by="name", sort_order="asc")) == ["shared", "old", "both", "read", "mine"]
    assert keys(await p.get_all_agents("boss", w.org, sort_by="updatedAtTimestamp", sort_order="asc")) == ["old", "shared", "both", "read", "mine"]
    page = await p.get_all_agents("boss", w.org, page=2, limit=2)
    assert keys(page["agents"]) == ["both", "shared"] and page["totalItems"] == 5
    assert keys((await p.get_all_agents("boss", w.org, page=3, limit=2))["agents"]) == ["old"]
    assert await p.get_all_agents("boss", w.org, page=9, limit=2) == {"agents": [], "totalItems": 5}       # past the end: no agents, the same total
    assert await p.get_all_agents("boss", w.org, page=1, limit=2, search="nothing like it") == {"agents": [], "totalItems": 0}
    first = (await p.get_all_agents("boss", w.org, page=1, limit=2))["agents"]
    assert [(a["_key"], [t["_key"] for t in a["toolsets"]], a["knowledge"], a["mcpServers"]) for a in first] == [("mine", ["ts"], [], []), ("read", [], [], [])]
    assert "mcpServersUnavailable" not in first[0]


async def test_a_failed_list_is_empty_and_a_failed_enrichment_keeps_the_agents(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    await listing_world(w)

    async def broken(*a: object, **k: object) -> dict:
        raise RuntimeError("store down")
    monkeypatch.setattr(w.p, "_project_agents_mcp_servers", broken)
    got = await w.p.get_all_agents("boss", w.org)
    assert len(got) == 5 and all(a["mcpServers"] == [] and a["mcpServersUnavailable"] is True for a in got)
    assert [t["_key"] for t in got[0]["toolsets"]] == ["ts"]
    monkeypatch.setattr(w.p, "_project_agents_toolsets_and_knowledge", broken)
    assert all(a["toolsets"] == [] and a["knowledge"] == [] for a in await w.p.get_all_agents("boss", w.org))
    monkeypatch.undo()
    real = w.p._q
    monkeypatch.setattr(w.p, "_q", failing)
    assert await w.p.get_all_agents("boss", w.org) == []
    monkeypatch.setattr(w.p, "_q", real)
    assert len(await w.p.get_all_agents("boss", w.org)) == 5


async def test_an_agent_is_edited_only_by_an_owner_writer_or_organizer_and_only_in_its_allowed_fields(w: World) -> None:
    await listing_world(w)
    await w.nodes("users", {"_key": "org_writer"})
    p = w.p
    doc = lambda k: p.get_document(k, "agentInstances")  # noqa: E731
    assert await p.update_agent("mine", {"name": "Renamed", "createdBy": "mallory", "orgId": "x", "isDeleted": True, "systemPrompt": "s",
                                         "serviceAccountUserId": "sa", "models": [{"modelKey": "m1", "modelName": "gpt"}, "m1_gpt", "m2", {"modelKey": None}, 7, "_x"]},
                                "boss", w.org) is True
    d = await doc("mine")
    assert (d["name"], d["systemPrompt"], d["serviceAccountUserId"], d["models"], d["updatedBy"]) == ("Renamed", "s", "sa", ["m1_gpt", "m2"], "boss")
    assert d.get("createdBy") is None and d["orgId"] == w.org and not d.get("isDeleted") and d["updatedAtTimestamp"] > 50       # fields outside the list are not written
    assert await p.update_agent("mine", {"models": "m3"}, "boss", w.org) is True and (await doc("mine"))["models"] == ["m1_gpt", "m2"]       # not a list: left alone
    assert await p.update_agent("mine", {"models": None}, "boss", w.org) is True and (await doc("mine"))["models"] == []
    assert await p.update_agent("both", {"name": "W"}, "boss", w.org) is True                                    # a writer edits
    for agent, user in (("read", "boss"), ("shared", "boss"), ("private", "boss"), ("gone", "boss"), ("missing", "boss"), ("mine", "ann"), ("mine", "nobody")):
        assert await p.update_agent(agent, {"name": "x"}, user, w.org) is False, (agent, user)       # a reader, the org's reader grant, no grant, deleted, missing
    assert (await doc("read"))["name"] == "Reader" and (await doc("shared"))["name"] == "Ann shared" and (await doc("private"))["name"] == "Ann private"
    assert await p.update_agent("mine", {"name": "x"}, "boss", _org()) is False                     # another org named
    with in_org(_org()):
        assert await p.update_agent("mine", {"name": "x"}, "boss", w.org) is False
    assert (await doc("mine"))["name"] == "Renamed"


async def test_an_agent_is_deleted_only_by_its_owner_and_the_delete_is_soft(w: World) -> None:
    await listing_world(w)
    p = w.p
    for agent, user in (("both", "boss"), ("read", "boss"), ("shared", "boss"), ("private", "boss"), ("missing", "boss")):
        assert await p.delete_agent(agent, user, w.org) is False, (agent, user)                     # a writer, a reader, the org's grant, no grant, missing
    assert await p.delete_agent("mine", "boss", _org()) is False
    with in_org(_org()):
        assert await p.delete_agent("mine", "boss", w.org) is False
    assert not (await p.get_document("both", "agentInstances")).get("isDeleted")
    t = await p.begin_transaction([], [])
    assert await p.delete_agent("mine", "boss", w.org, transaction=t) is True
    await p.commit_transaction(t)
    d = await p.get_document("mine", "agentInstances")
    assert d["isDeleted"] is True and d["deletedByUserId"] == "boss" and d["deletedAtTimestamp"] > 0 and d["name"] == "Sales Helper"       # kept, marked
    assert "mine" not in [a["_key"] for a in await p.get_all_agents("boss", w.org)]
    assert sorted(a["_key"] for a in await p.get_all_agents("boss", w.org, is_deleted=True)) == ["gone", "mine"]
    assert await p.delete_agent("mine", "boss", w.org) is False                                      # already deleted


async def test_an_agents_permission_check_reads_through_the_callers_transaction(w: World) -> None:
    """A grant written earlier in the caller's transaction counts for an edit in it; one removed in it no longer allows a delete."""
    await listing_world(w)
    p = w.p
    t = await p.begin_transaction([], [])
    try:
        assert await p.check_agent_permission("private", "boss", w.org, transaction=t) is None
        await p.batch_create_edges([{"from_collection": "users", "from_id": "boss", "to_collection": "agentInstances", "to_id": "private", "type": "USER", "role": "OWNER"}],
                                   "permission", transaction=t)
        assert (await p.check_agent_permission("private", "boss", w.org, transaction=t))["user_role"] == "OWNER"
        assert await p.check_agent_permission("private", "boss", w.org) is None                     # outside it, not yet
        assert await p.update_agent("private", {"name": "Renamed"}, "boss", w.org, transaction=t) is True
        assert await p.delete_edge("boss", "users", "mine", "agentInstances", "permission", transaction=t)
        assert await p.check_agent_permission("mine", "boss", w.org, transaction=t) is None
        assert await p.delete_agent("mine", "boss", w.org, transaction=t) is False
        assert (await p.check_agent_permission("mine", "boss", w.org))["user_role"] == "OWNER"         # outside it, still there
    finally:
        await p.rollback_transaction(t)
    assert (await p.get_document("private", "agentInstances"))["name"] == "Ann private" and not (await p.get_document("mine", "agentInstances")).get("isDeleted")

