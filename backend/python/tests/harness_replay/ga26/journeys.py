"""The GA-26 P0 journeys: what each one runs, on the mock harness, through the VCR gateway, over the seeded tenant.

Two harness paths, each with its outermost I/O only faked:

- the native loop (``durable_driver``: the real assembler, ``RunDriver``, knowledge tools, citations, record-access
  gate and flow guard), whose model transport is a ``CassetteTransport``;
- an engine (``EngineWorld``: ``FakeEngine`` in a fake sandbox under the durable engine kernel, the run's real tool
  endpoint, workspace and artifact publisher), whose model calls go through the real ``ModelGateway`` to a
  ``CassetteTransport``.

A journey records its cassette from a scripted model (``ScriptedModel`` for the loop, the conformance gateway's
scripted brain for engines) and replays from it in the lane; no journey is ever recorded from a provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import patch

from app.harness.testing_adapters import ScriptedModelTransport
from harness_core.effects import ToolExecution
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.skills.builder import StoredSkillBundleBuilder
from harness_core.skills.types import SkillPin, SkillRef
from harness_core.testing import ModelScript, ScriptedModel
from harness_core.testing.cassette import Cassette, CassetteTransport
from harness_core.testing.contracts.tool_catalog import offered
from harness_core.testing.engines.durable import ORG, SESSION, EngineWorld
from harness_core.testing.engines.gateway_model import (
    BrainTransport,
    GatewayModelEndpoint,
)
from harness_core.testing.model_script import Reply, ToolCallSpec, Turn
from harness_core.workspace.types import ObjectScope
from tests.harness_replay import durable_driver
from tests.harness_replay.legacy_driver import AGENT_ROUTE, DriveResult, DriveSpec
from tests.support.parity_cases.wave1_reference import refs_by_doc
from tests.support.seeded_tenant import Tenant, load_tenant
from tests.unit.harness.assembly.world import ACTOR
from tests.unit.harness.assembly.world import ORG as ORG_1

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.models.transports.base import LLMTransport
    from harness_core.testing.model_types import ModelRequest

CASSETTES = Path(__file__).resolve().parents[2] / "fixtures" / "cassettes" / "ga26"
# The client's "now" every journey sends: "last week" is 20-24 October 2025 in the tenant.
CLIENT_TIME = "2025-10-27T09:00:00Z"
# The graph key the durable driver's user lookup gives the run's actor (``durable_driver._services``).
ACTOR_KEY = "ukey-1"
SEARCH = "knowledgegraph__search"
FETCH = "knowledgegraph__fetch_record"
SEND_EMAIL = "gmail__send_email"
BRIEF = "Summarize what changed in project Atlas last week across Slack, Jira and Drive, as a 1-page brief."
BOARD = "rec-atlas-board-update"
RECIPIENT = "team@atlas.example"

Path_ = Literal["loop", "engine"]


def tenant() -> Tenant:
    return load_tenant("atlas")


# --- the native loop ----------------------------------------------------------------------------------------------


def _titles() -> dict[str, str]:
    return {r.title: r.id for r in tenant().records}


def _brief(request: ModelRequest) -> Reply:
    """The brief from what the run's searches and reads showed it, each line cited."""
    refs = refs_by_doc(request, _titles())
    lines = [f"- {tenant().record(doc).title} [source]({ref})" for doc, ref in refs.items()]
    return Reply(text="# Atlas, week of 20 October\n\n" + "\n".join(lines))


def brief_script() -> ModelScript:
    """J-01/J-02: three searches (Slack, Jira, Drive), a read of the board update, then the cited brief. One script
    for every user: what differs between their answers is only what the tenant lets each read."""
    searches = [
        ToolCallSpec(id="s1", name=SEARCH, args={"query": "Atlas standup auth migration latency"}),
        ToolCallSpec(id="s2", name=SEARCH, args={"query": "Atlas issue priority status owner"}),
        ToolCallSpec(id="s3", name=SEARCH, args={"query": "Atlas roadmap general availability board"}),
    ]
    read = ToolCallSpec(id="f1", name=FETCH, args={"record_ids": [BOARD], "reason": "The board update for the brief"})
    return ModelScript(
        id="ga26-brief",
        turns=[Turn(reply=Reply(tool_calls=searches)), Turn(reply=Reply(tool_calls=[read])), Turn(from_request=_brief)],
    )


def _sent_or_not(request: ModelRequest) -> Reply:
    last = request.last("tool")
    sent = last is not None and last.tool_name == SEND_EMAIL and last.tool_status == "succeeded"
    return Reply(text=f"Sent the standup summary to {RECIPIENT}." if sent else "I did not send the email.")


def email_script() -> ModelScript:
    """J-04: read the Slack standup through knowledge search, then email a summary (an external write after an
    outsider-written read: the Rule of Two holds it for the person)."""
    body = "Auth moved to v2 on 21 October; p95 login latency fell from 900 ms to 610 ms."
    return ModelScript(
        id="ga26-email",
        turns=[
            Turn(reply=Reply(tool_calls=[ToolCallSpec(id="s1", name=SEARCH, args={"query": "Atlas standup"})])),
            Turn(reply=Reply(tool_calls=[ToolCallSpec(
                id="m1", name=SEND_EMAIL, args={"to": RECIPIENT, "subject": "Atlas standup", "body": body}
            )])),
            Turn(from_request=_sent_or_not),
        ],
    )


@dataclass(frozen=True)
class LoopJourney:
    id: str
    scenario: str
    user: str
    query: str
    script: Callable[[], ModelScript]
    toolsets: tuple[str, ...] = ()
    decision: str | None = None
    path: Path_ = "loop"

    def recorder(self) -> CassetteTransport:
        return CassetteTransport.record(ScriptedModelTransport(ScriptedModel(self.script())), name=self.id)

    async def run(self, transport: LLMTransport, decisions: list[dict[str, Any]] | None = None) -> DriveResult:
        t = tenant()
        readable = t.readable(self.user)

        def decide(card: dict[str, Any]) -> str:
            if decisions is not None:
                decisions.append(card)
            assert self.decision is not None
            return self.decision

        spec = DriveSpec(
            scenario=self.id, query=self.query, route=AGENT_ROUTE, chat_mode="quick", transport=transport,
            fixtures=t.world_fixtures(self.user), knowledge_search=True, fake_toolsets=self.toolsets,
            record_access=t.record_access(org=ORG_1, actor=self.user, actor_id=ACTOR, actor_key=ACTOR_KEY),
            knowledge_connectors=t.connectors(), knowledge_searchable=lambda doc: doc in readable,
            principal=f"p_{self.user}", decide=decide if self.decision is not None else None,
            current_time=CLIENT_TIME, timezone="UTC",
        )
        # The journeys script no suggested next questions (UX5), as no other scripted rig does.
        with patch("app.harness.assembly.assembler.FOLLOWUP_SUGGESTIONS", new=False):
            return await durable_driver.drive(spec)


# --- an engine ----------------------------------------------------------------------------------------------------

XLSX_SKILL = {"SKILL.md": b"---\nname: xlsx\ndescription: Make spreadsheets\n---\nWrite .xlsx files with openpyxl.\n"}
XLSX_FILE = "open-p1-issues.xlsx"
TWO_HOURS_S = 2 * 3600.0


def _jira_for(user: str) -> Callable[[ToolCall, ToolContext], Awaitable[ToolExecution]]:
    async def search(call: ToolCall, ctx: ToolContext) -> ToolExecution:
        del call, ctx
        found = [t.model_dump() for t in tenant().world_fixtures(user).tickets if t.status == "Open"]
        return ToolExecution(status="ok", content=json.dumps({"issues": found}))

    return search


@dataclass
class EngineRun:
    world: EngineWorld
    endpoint: GatewayModelEndpoint
    runs: list[str] = field(default_factory=list[str])


async def _finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


async def xlsx_journey(transport: LLMTransport) -> EngineRun:
    """J-03: the xlsx skill is pinned and loaded, the user's open P1 issues come from Jira, the file lands in the
    run's outputs and is published as the session's artifact."""
    endpoint = GatewayModelEndpoint(transport)
    w = EngineWorld(model=endpoint, tools=(offered("jira_search"),), handlers={"jira_search": _jira_for("alice")})
    w.skills.publish(ORG, "xlsx", XLSX_SKILL)
    builder = StoredSkillBundleBuilder(source=w.skills, objects=w.objects, workspaces=w.store)
    bundle = await builder.build(ObjectScope(ORG, f"ws:{SESSION}"), SkillRef("xlsx"))
    w.skill_pins = (SkillPin("xlsx", bundle.digest),)
    w.admit(
        "run-j03",
        "Make an XLSX of all open P1 Jira issues with owners.\n"
        "skill xlsx\n"
        'tool jira_search {"jql": "priority = P1 AND status = Open"}\n'
        f"write out/{XLSX_FILE} key,owner\n"
        f"say Saved {XLSX_FILE}",
        principal="p_alice",
    )
    _ = await _finish(w, "run-j03")
    return EngineRun(w, endpoint, ["run-j03"])


ASK = 'tool ask_user {"question": "Which region should the report cover?", "options": ["EU", "US"]}'


async def ask_journey(transport: LLMTransport) -> EngineRun:
    """J-25: the engine asks the person, the run ends waiting for the answer holding nothing, and the answer two
    hours later continues the engine's own conversation."""
    endpoint = GatewayModelEndpoint(transport)
    w = EngineWorld(model=endpoint)
    w.admit("run-ask", f"{ASK}\nsay I need to know which region first.", principal="p_alice")
    _ = await _finish(w, "run-ask")
    _ = w.clock.advance(TWO_HOURS_S)
    w.admit("run-answer", "say EU, thanks", principal="p_alice")
    _ = await _finish(w, "run-answer")
    return EngineRun(w, endpoint, ["run-ask", "run-answer"])


@dataclass(frozen=True)
class EngineJourney:
    id: str
    scenario: str
    build: Callable[[LLMTransport], Awaitable[EngineRun]]
    path: Path_ = "engine"

    def recorder(self) -> CassetteTransport:
        return CassetteTransport.record(BrainTransport(), name=self.id)

    async def run(self, transport: LLMTransport) -> EngineRun:
        return await self.build(transport)


Journey = LoopJourney | EngineJourney

JOURNEYS: dict[str, Journey] = {
    j.id: j
    for j in (
        LoopJourney("j01-alice", "J-01", "alice", BRIEF, brief_script),
        LoopJourney("j02-bob", "J-02", "bob", BRIEF, brief_script),
        LoopJourney("j02-carol", "J-02", "carol", BRIEF, brief_script),
        LoopJourney("j04-approved", "J-04", "alice", f"Summarize the #atlas standup and email it to {RECIPIENT}.",
                    email_script, toolsets=("gmail",), decision="approved"),
        LoopJourney("j04-denied", "J-04", "alice", f"Summarize the #atlas standup and email it to {RECIPIENT}.",
                    email_script, toolsets=("gmail",), decision="denied"),
        EngineJourney("j03-xlsx", "J-03", xlsx_journey),
        EngineJourney("j25-ask", "J-25", ask_journey),
    )
}


def cassette_path(journey_id: str) -> Path:
    return CASSETTES / f"{journey_id}.json"


def replayer(journey_id: str) -> CassetteTransport:
    return CassetteTransport.replay(Cassette.load(cassette_path(journey_id)))


async def record(journey: Journey) -> Cassette:
    """Runs ``journey`` on its scripted model and returns what the VCR kept."""
    transport = journey.recorder()
    _ = await journey.run(transport)
    return transport.cassette()


__all__ = [
    "BOARD",
    "BRIEF",
    "CASSETTES",
    "FETCH",
    "JOURNEYS",
    "RECIPIENT",
    "SEARCH",
    "SEND_EMAIL",
    "XLSX_FILE",
    "EngineJourney",
    "EngineRun",
    "Journey",
    "LoopJourney",
    "cassette_path",
    "record",
    "replayer",
    "tenant",
]
