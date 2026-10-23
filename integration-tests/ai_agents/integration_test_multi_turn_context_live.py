"""A follow-up question is answered, not the first one, once compaction fires.

docs/context-engineering/REPORT.md, LI-01 and LI-02. In a chat the shapers
used to protect the conversation's first question instead of the current one,
so when the current turn's tool results filled the window the follow-up was
summarised away and the model answered the first question again.

The suite adds its own model with a 24K ``contextLength`` so that reading one
long manual is enough to make compaction fire, uploads six ~120K-character
manuals in which the two answers sit in different chapters of different
volumes, and asks A (reminders) then B (choosing a practice) in one
conversation. That runs through a named Agent Builder agent (``quick``) and
through the universal assistant (``agent``).

The answer's wording is the model's, so the checks are on facts only one of
the two manuals' notes contains, and on what the turn's tools were asked.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest
from pipeshub_client import PipeshubClient

from ai_agents.support import (
    AI_MODELS,
    delete_llm,
    error_message,
    json_body,
    stream_agent,
    stream_chat,
    stream_chat_message,
)
from helper.agui_run import RunTrace
from helper.clients.agents_client import AgentsClient
from helper.clients.conversations_client import AgentConversationsClient, ConversationsClient
from helper.clients.kb_client import KBClient
from helper.indexing_progress import wait_until_finished

pytestmark = [pytest.mark.integration, pytest.mark.ai_agents]

logger = logging.getLogger("ai-agents-it")

_CONTEXT_LENGTH = 24_000
_MANUAL_CHARS = 120_000
_QUESTION_A = "How do I set up automated appointment reminders by SMS and email, and can patients confirm or cancel?"
_QUESTION_B = "I have access to more than one practice. How do I pick which practice to log into and set a default?"

# Facts that appear only in the reminders notes (A) or the practice notes (B).
_A_ONLY = re.compile(r"sms credit|48 hours|repl(?:y|ying) (?:with )?['\"]?c\b|cancel with ['\"]?x\b", re.IGNORECASE)
_B_FACTS = re.compile(r"default practice|practice picker|preferences", re.IGNORECASE)
_A_TOOL_ARGS = re.compile(r"remind", re.IGNORECASE)

_FEATURES = [
    "calendar", "patient records", "clinical notes", "prescriptions", "lab orders", "invoices",
    "insurance claims", "inventory", "telehealth", "patient portal", "reports", "integrations",
    "data import", "audit trail", "document templates", "waitlist", "group classes", "payroll export",
]
_VERBS = ["configure", "review", "approve", "export", "archive", "restore", "audit", "share", "lock", "merge"]
_KEY_NOTES = {
    1: [
        "Appointment reminders: enable under Practice Settings > Reminders and choose SMS, email or both.",
        "Default timing is 48 hours and 2 hours before; up to three reminders per appointment.",
        "Patients confirm by replying C or cancel with X; a cancellation frees the slot automatically.",
        "SMS reminders consume practice SMS credits bought under Billing > SMS credits.",
    ],
    3: [
        "Multi-practice accounts: after login a practice picker lists every practice where your role is active.",
        "Choose the practice to work in; the active practice name is shown in the header and can be clicked to switch.",
        "Default practice: Profile > Preferences > Default practice, choose one and Save to skip the picker at login.",
        "Mobile apps use the web default unless a device default is set under the app's Settings > Practice.",
    ],
}


def _manuals() -> dict[str, bytes]:
    """Six long manuals; each key note is buried halfway through its volume.

    Same corpus as docs/context-engineering/repros/live/gen_manuals.py, as
    Markdown: the integration venv has no PDF writer, and uploads take text.
    """
    rnd = random.Random(23)

    def paragraph(feature: str) -> str:
        verb = rnd.choice(_VERBS)
        role = rnd.choice(["Admin", "Clinician", "Front desk", "Billing"])
        return (
            f"To {verb} {feature}, open the {feature} workspace, select the relevant items and use the "
            f"{verb.capitalize()} action. Staff with the {role} role can {verb} {feature} in practices where "
            "that role is active. Changes are recorded in the audit trail with the user, time and previous "
            f"value, and can be reviewed for {rnd.randint(30, 400)} days. Bulk operations run in the "
            f"background and notify the requester when {rnd.randint(2, 99)}% complete."
        )

    manuals: dict[str, bytes] = {}
    for volume in range(1, 7):
        notes = _KEY_NOTES.get(volume)
        lines = [f"# Clinikly Administrator Guide, Volume {volume}"]
        size, chapter, inserted = 0, 0, False
        while size < _MANUAL_CHARS:
            chapter += 1
            feature = rnd.choice(_FEATURES)
            lines.append(f"## Chapter {chapter}: Working with {feature}")
            for _ in range(8):
                lines.append(paragraph(feature))
                size += len(lines[-1])
            if notes and not inserted and size > _MANUAL_CHARS // 2:
                lines.append(f"## Chapter {chapter}.5: Notes")
                lines.extend(notes)
                inserted = True
        manuals[f"Clinikly_Administrator_Guide_Vol{volume}.md"] = "\n\n".join(lines).encode()
    return manuals


@dataclass(frozen=True)
class SmallWindowModel:
    model_key: str
    model_name: str
    provider: str
    is_reasoning: bool


@dataclass(frozen=True)
class Exchange:
    first: RunTrace
    follow_up: RunTrace


@pytest.fixture(scope="module")
def small_window_model(pipeshub_client: PipeshubClient, ai_provider) -> Iterator[SmallWindowModel]:
    """The suite's provider again, with a context length small enough that compaction must fire."""
    provider, model_name, configuration = ai_provider
    configuration = {**configuration, "modelFriendlyName": f"IT 24K {uuid.uuid4().hex[:6]}"}

    def add(is_reasoning: bool):
        return pipeshub_client.request("POST", f"{AI_MODELS}/providers", json={
            "modelType": "llm",
            "provider": provider,
            "configuration": configuration,
            "isMultimodal": False,
            "isReasoning": is_reasoning,
            "isDefault": False,
            "contextLength": _CONTEXT_LENGTH,
        })

    is_reasoning = True
    resp = add(is_reasoning)
    if resp.status_code != 200 and "reason" in error_message(resp).lower():
        is_reasoning = False
        resp = add(is_reasoning)
    assert resp.status_code == 200, (
        f"adding {model_name} on {provider} with contextLength {_CONTEXT_LENGTH} failed: "
        f"{resp.status_code} {error_message(resp)}"
    )
    model_key = (json_body(resp).get("details") or {}).get("modelKey")
    assert model_key, f"add returned no details.modelKey: {resp.text[:500]}"
    try:
        yield SmallWindowModel(model_key, model_name, provider, is_reasoning)
    finally:
        delete_llm(pipeshub_client, model_key)


@pytest.fixture(scope="module")
def manuals_kb(kb_client: KBClient, ai_models_configured) -> Iterator[str]:
    del ai_models_configured
    kb_id = kb_client.create_kb(f"ai-agents-it-manuals-{uuid.uuid4().hex[:8]}")["id"]
    try:
        record_ids = [
            kb_client.upload_file(kb_id, name, body, mimetype="text/markdown")["records"][0]["recordId"]
            for name, body in _manuals().items()
        ]
        final = asyncio.run(wait_until_finished(kb_client, record_ids))
        unfinished = {rid: status for rid, status in final.items() if status != "COMPLETED"}
        assert not unfinished, f"manuals did not finish indexing: {unfinished}"
        yield kb_id
    finally:
        try:
            kb_client.delete_kb(kb_id)
        except Exception as e:  # noqa: BLE001 - teardown must not mask test results
            logger.warning("could not delete KB %s: %s", kb_id, e)


@pytest.fixture(scope="module")
def manuals_agent(
    agents_client: AgentsClient, small_window_model: SmallWindowModel, manuals_kb: str,
) -> Iterator[str]:
    if not small_window_model.is_reasoning:
        pytest.skip(f"{small_window_model.provider} refused the reasoning flag; Agent Builder agents need it")
    resp = agents_client.create_agent(
        name=f"it-manuals-agent-{uuid.uuid4().hex[:6]}",
        description="Answers practice administrators from the Clinikly manuals.",
        systemPrompt="You help practice administrators. Answer from the attached Clinikly manuals.",
        models=[{
            "modelKey": small_window_model.model_key,
            "modelName": small_window_model.model_name,
            "provider": small_window_model.provider,
            "isReasoning": True,
        }],
        knowledge=[{"connectorId": manuals_kb, "filters": {}}],
    )
    assert resp.status_code in (200, 201), f"creating the agent: {resp.status_code} {resp.text[:500]}"
    agent_key = (json_body(resp).get("agent") or {}).get("_key")
    assert agent_key, f"agent create returned no agent._key: {resp.text[:500]}"
    try:
        yield agent_key
    finally:
        deleted = agents_client.delete_agent(agent_key)
        if deleted.status_code >= 300 and deleted.status_code != 404:
            logger.warning("could not delete agent %s: %s", agent_key, deleted.text[:300])


@pytest.fixture(scope="module")
def agent_exchange(
    agent_conversations_client: AgentConversationsClient, manuals_agent: str,
) -> Iterator[Exchange]:
    """LI-02: A then B through a named agent in ``quick`` mode."""
    first = stream_agent(agent_conversations_client, manuals_agent, _QUESTION_A)
    try:
        assert first.finished and not first.error and first.conversation_id, first.describe()
        follow_up = stream_agent(
            agent_conversations_client, manuals_agent, _QUESTION_B, conversation_id=first.conversation_id,
        )
        yield Exchange(first, follow_up)
    finally:
        if first.conversation_id:
            agent_conversations_client.delete_conversation(manuals_agent, first.conversation_id)


@pytest.fixture(scope="module")
def assistant_exchange(
    conversations_client: ConversationsClient, small_window_model: SmallWindowModel, manuals_kb: str,
) -> Iterator[Exchange]:
    """LI-01: A then B through the universal assistant in ``agent`` mode."""

    def body(query: str) -> dict[str, Any]:
        return {
            "query": query,
            "chatMode": "agent",
            "modelKey": small_window_model.model_key,
            "modelName": small_window_model.model_name,
            "filters": {"kb": [manuals_kb]},
        }

    first = stream_chat(conversations_client, body(_QUESTION_A))
    try:
        assert first.finished and not first.error and first.conversation_id, first.describe()
        follow_up = stream_chat_message(conversations_client, first.conversation_id, body(_QUESTION_B))
        yield Exchange(first, follow_up)
    finally:
        if first.conversation_id:
            try:
                conversations_client.delete_conversation(first.conversation_id)
            except Exception as e:  # noqa: BLE001 - teardown must not mask test results
                logger.warning("could not delete conversation %s: %s", first.conversation_id, e)


@pytest.fixture(scope="module", params=["named_agent_quick", "assistant_agent_mode"])
def exchange(request: pytest.FixtureRequest) -> Exchange:
    fixture = "agent_exchange" if request.param == "named_agent_quick" else "assistant_exchange"
    return request.getfixturevalue(fixture)


class TestFollowUpAfterCompaction:
    def test_the_first_question_is_answered(self, exchange: Exchange) -> None:
        first = exchange.first
        assert _A_ONLY.search(first.answer), (
            f"the reminders answer has none of the reminders facts, so the follow-up check means "
            f"nothing: {first.describe()}"
        )

    def test_the_follow_up_question_is_answered(self, exchange: Exchange) -> None:
        follow_up = exchange.follow_up
        assert follow_up.finished and not follow_up.error, follow_up.describe()
        assert _B_FACTS.search(follow_up.answer), (
            f"the follow-up answer does not say how to pick or default a practice: {follow_up.describe()}"
        )

    def test_the_follow_up_does_not_answer_the_first_question(self, exchange: Exchange) -> None:
        follow_up = exchange.follow_up
        found = _A_ONLY.findall(follow_up.answer)
        assert not found, f"the follow-up answer repeats the reminders answer ({found}): {follow_up.describe()}"

    def test_the_follow_up_does_not_search_for_the_first_question(self, exchange: Exchange) -> None:
        about_a = [(call.name, call.args) for call in exchange.follow_up.tool_calls if _A_TOOL_ARGS.search(call.args)]
        assert not about_a, f"the follow-up turn looked up reminders: {about_a}"
