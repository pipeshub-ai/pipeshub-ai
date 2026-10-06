"""Journey J-13 on the real Python services: draft in chat, create from the draft, handle and privacy on the real graph.

Given the agent builder flag on and a shared chat
When B asks the assistant to build an agent (the real `draft_agent` tool runs), then clicks Create
Then the draft is a card with no toolsets and no agent exists yet; Create goes through the real Python create-from-chat route:
     the agent exists, private to B, with its handle; another draft with the same handle is refused with a suggestion;
     the picker offers the agent to B only.

Node, the query service (draft tool, create route, handle allocator) and the graph run for real; only the model's words are scripted.
"""

from __future__ import annotations

import re
import time
import uuid

import pytest

from helper.collab_stack import collab
from helper.collab_stack.fake_llm import llm_turn, tool_call, tool_call_ending
from helper.collab_stack.real_python import lacks_ending, offers_ending, stream_turn
from helper.collab_stack.seeds import insert_session, messages_of, user_row

pytestmark = [
    pytest.mark.integration,
    pytest.mark.collab_chats,
    pytest.mark.collab_stack,
    pytest.mark.collab_real_python,
    pytest.mark.usefixtures("flag_on_for_module"),
]

BUILDER_FLAG = "ENABLE_CHAT_AGENT_BUILDER"
MENTIONS_FLAG = "ENABLE_CHAT_MENTIONS"
FLAG_CACHE_S = 12
AGENTS = "/api/v1/agents"
DRAFT_TOOL = "__draft_agent"
INSTRUCTIONS = "Write friendly, short offer letters."


@pytest.fixture(scope="module")
def builder_on(stack, flags, flag_on_for_module):  # noqa: ANN001, ANN201
    flags.set(True, key=BUILDER_FLAG)
    flags.set(True, key=MENTIONS_FLAG)
    time.sleep(FLAG_CACHE_S)
    yield
    flags.set(False, key=BUILDER_FLAG)
    flags.set(False, key=MENTIONS_FLAG)


def unique_name() -> str:
    return f"Offer drafter {uuid.uuid4().hex[:8]}"


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def draft_turns(name: str):  # noqa: ANN201
    """Tools of a big toolset are disclosed lazily: the model loads the AgentBuilder toolset, then calls its one tool."""
    args = {"name": name, "purpose": "Drafts offer letters", "instructions": INSTRUCTIONS, "suggested_tools": ["jira__create_issue"]}
    return (
        llm_turn(tool_calls=[tool_call("fetch_tools", {"toolset": "agent_builder"})], when=lacks_ending(DRAFT_TOOL)),
        llm_turn(tool_calls=[tool_call_ending(DRAFT_TOOL, args)], when=offers_ending(DRAFT_TOOL)),
        llm_turn("I drafted it for you to review.", when=offers_ending(DRAFT_TOOL)),
    )


def drafted(stack, api, fake, requester, chat: str, name: str) -> str:  # noqa: ANN001
    """The requester asks the assistant for a draft; returns the card's message id."""
    before = {str(r["_id"]) for r in messages_of(stack.db, chat) if r["messageType"] == "tool_call"}
    call = stream_turn(api, fake, requester, chat, "Create an agent that drafts offer letters", *draft_turns(name))
    assert any(e.event == "CUSTOM" and e.data.get("name") == "agent_draft" for e in call.events), call.text[-500:]
    [card] = [r for r in messages_of(stack.db, chat) if r["messageType"] == "tool_call" and str(r["_id"]) not in before]
    return str(card["_id"])


@pytest.fixture
def world(stack, builder_on):  # noqa: ANN001, ANN201
    owner, writer, reader = collab.fresh_actor(stack, "J13A"), collab.fresh_actor(stack, "J13B"), collab.fresh_actor(stack, "J13C")
    seeded = insert_session(stack.db, f"j13r-{owner.name}", owner, shared_with=[user_row(writer, "write", principal_type=True), user_row(reader, "read", principal_type=True)])
    return owner, writer, reader, seeded.sid


def create(api, who, chat: str, message: str, name: str, **body):  # noqa: ANN001, ANN003, ANN201
    spec = {"name": name, "handle": slug(name), "description": "Drafts offer letters", "instructions": INSTRUCTIONS, **body}
    return api.post(f"{AGENTS}/create", who, json_body={**spec, "draftRef": {"conversationId": chat, "messageId": message}})


def test_j13_the_draft_is_a_card_with_no_tools_and_nothing_is_created_before_the_click(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    name = unique_name()

    message = drafted(stack, api, fake, writer, chat, name)

    card = next(r for r in messages_of(stack.db, chat) if str(r["_id"]) == message)
    draft = card["tools"][0]["toolResult"]
    assert draft["name"] == name and draft["handleSuggestion"] == slug(name)
    assert draft["instructions"] == INSTRUCTIONS and draft["toolsets"] == [] and draft["requestedBy"] == writer.user_id
    assert draft["suggestedTools"] == ["jira__create_issue"], "tools are hints, never enabled"
    listing = api.get(AGENTS, writer)
    assert listing.status_code == 200 and not [a for a in listing.json().get("agents", []) if a.get("name") == name]
    free = api.get(f"{AGENTS}/handle-availability", writer, params={"handle": f"@{slug(name)}"})
    assert free.status_code == 200 and free.json()["available"] is True


def test_j13_create_makes_a_private_agent_with_its_handle_and_the_plain_chat_offers_no_agent(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    name = unique_name()
    message = drafted(stack, api, fake, writer, chat, name)

    resp = create(api, writer, chat, message, name)

    assert resp.status_code == 201, resp.text[:500]
    agent = resp.json()["agent"]
    assert agent["handle"] == slug(name)
    key = agent["_key"]
    assert api.get(f"{AGENTS}/{key}", writer).status_code == 200
    for other in (owner, stack.roster.stranger):
        assert api.get(f"{AGENTS}/{key}", other).status_code in (403, 404), "a new agent is private to its creator"
    taken = api.get(f"{AGENTS}/handle-availability", owner, params={"handle": f"@{slug(name)}"})
    assert taken.status_code == 200 and taken.json()["available"] is False and taken.json()["reason"] == "taken"

    def agents_offered(who):  # noqa: ANN001, ANN202
        return [i for i in collab.mentionables(api, who, chat) if i["type"] == "agent"]

    # RR #16a: the picker offers what the validator accepts, and a plain chat accepts no agent mention (M1).
    assert agents_offered(writer) == []
    assert agents_offered(owner) == []


def test_j13_a_second_draft_with_the_same_handle_is_refused_with_a_suggestion(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, _reader, chat = world
    name = unique_name()
    first = drafted(stack, api, fake, writer, chat, name)
    assert create(api, writer, chat, first, name).status_code == 201
    second = drafted(stack, api, fake, owner, chat, unique_name())

    resp = create(api, owner, chat, second, "Another drafter", handle=slug(name))

    assert resp.status_code == 409, resp.text[:400]
    error = resp.json()["error"]
    assert error["code"] == "HANDLE_TAKEN" and (error.get("details") or {}).get("suggestion") == f"{slug(name)}-2"


def test_j13_nobody_else_can_create_from_someone_elses_draft(stack, api, fake, world) -> None:  # noqa: ANN001
    owner, writer, reader, chat = world
    name = unique_name()
    message = drafted(stack, api, fake, writer, chat, name)
    for who in (owner, reader, stack.roster.stranger):
        assert create(api, who, chat, message, name).status_code == 404
    assert api.get(f"{AGENTS}/handle-availability", writer, params={"handle": f"@{slug(name)}"}).json()["available"] is True
