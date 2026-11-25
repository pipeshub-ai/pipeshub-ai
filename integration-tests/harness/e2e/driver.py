"""Drives one row's turn on a stack and collects what happened (04 §4.2 steps, §1's four observations).

The turn goes in as a client sends it: the chat stream route of the agent or the assistant (LAT1: one request, the
server routes it), a regenerate on the message route, or a schedule's firing. A card is answered on the interaction
route; a reconnect drops the stream and reattaches with ``Last-Event-ID``; an approval that must expire has its run's
stored deadline moved to now (the clock advance 04 §3 EM-04 asks for, without libfaketime). What is collected: the
frames the client read, the journal (``Stack.journal``), the scripted model's requests, the scripted SaaS's effects
and Node's log records of the request.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass, field
from typing import Any

from fixtures import Fixtures
from lane import Frame, Stack, StackError, Stream, wait_until
from observe import Observation
from plan import RowPlan
from scripts import Script, for_row

Json = dict[str, Any]
DECISIONS = {"allow_once": ("approved", "once"), "allow_conversation": ("approved", "conversation"),
             "deny": ("denied", None)}
APPROVAL = "approval_requested"
TERMINAL = frozenset({"RUN_FINISHED", "RUN_ERROR", "complete", "error"})
CONTENT = frozenset({"TEXT_MESSAGE_CONTENT", "TOOL_CALL_START"})
# A webhook's credential headers (schedule-routes.ts TRIGGER_*_HEADER).
TOKEN_HEADER = "x-pipeshub-trigger-token"
SIGNATURE_HEADER, TIMESTAMP_HEADER = "x-pipeshub-signature", "x-pipeshub-timestamp"


@dataclass
class TurnRecord:
    """Everything the driver saw for the turn under test; turned into an ``Observation`` by ``collect``."""

    request_id: str
    script: Script
    frames: list[Frame] = field(default_factory=list[Frame])
    session_id: str | None = None
    run_id: str | None = None
    decisions: list[str] = field(default_factory=list[str])
    reconnect_ids: list[tuple[str | None, list[str]]] = field(default_factory=list[tuple[str | None, list[str]]])
    """Per reattach: the ``Last-Event-ID`` sent and the frame ids received after it."""
    firings: list[Json] = field(default_factory=list[Json])
    previous_run_id: str | None = None
    """The run a regenerate replaces (the first turn's)."""
    agent_key: str | None = None
    decision: str | None = None
    """What answers this turn's card when it is not the row's own decision (a regenerate's first turn)."""
    notes: list[str] = field(default_factory=list[str])
    role_model: Json | None = None


class Paths:
    """The chat routes of an agent or of the assistant (the same handlers behind both, 04 §2.1 `mode`)."""

    def __init__(self, agent_key: str | None) -> None:
        self.base = f"/api/v1/agents/{agent_key}/conversations" if agent_key else "/api/v1/conversations"

    def stream(self) -> str:
        return f"{self.base}/stream"

    def run(self, session: str, run: str) -> str:
        return f"{self.base}/{session}/runs/{run}"

    def conversation(self, session: str) -> str:
        return f"{self.base}/{session}"

    def regenerate(self, session: str, message: str) -> str:
        return f"{self.base}/{session}/message/{message}/regenerate"

    def follow_up(self, session: str) -> str:
        return f"{self.base}/{session}/messages/stream"


class Driver:
    def __init__(self, stack: Stack, fixtures: Fixtures, plan: RowPlan) -> None:
        self.stack, self.fx, self.plan = stack, fixtures, plan
        self.marker = f"QA6 {plan.row.id} {secrets.token_hex(3)}"
        self.agent_key: str | None = None
        self.paths = Paths(None)
        self.model: Json = {}
        self.role_model: Json | None = None

    # --- setup ------------------------------------------------------------------------

    def prepare(self) -> None:
        self.stack.set_process_env(self.plan.process.env())
        self.fx.apply_org(self.plan.org)
        self.model = self.fx.model(self.plan.turn.wire)
        if self.plan.role_wire is not None:
            self.role_model = self.fx.role_model(self.plan.role_wire)
        if self.plan.turn.kind == "agent":
            self.agent_key = self.fx.agent(self.plan.agent, self.role_model)
        self.paths = Paths(self.agent_key)

    def _reset_fakes(self) -> None:
        self.stack.model.ok("POST", "/__control/reset")
        self.stack.saas.ok("POST", "/__control/reset")

    def _body(self, script: Script) -> Json:
        body: Json = {"query": script.text(), "chatMode": self.plan.turn.chat_mode, "recordIds": [], "filters": {},
                      **self.model}
        if self.plan.turn.project:
            body["projectId"] = self.fx.project()
        if self.plan.turn.harness is not None:
            body["harness"] = self.plan.turn.harness
        return body

    # --- the turn ---------------------------------------------------------------------

    def run(self) -> TurnRecord:
        self.prepare()
        self._reset_fakes()
        script = for_row(self.plan.agent.family, self.marker, engine=self.plan.agent.engine != "none")
        if self.plan.turn.trigger != "interactive":
            return self._scheduled(script)
        if self.plan.turn.action != "regenerate":
            return self._interactive(script)
        # The first turn's held call is denied (04 EM-07), so it ends with an answer to regenerate; the row's own
        # decision is the regenerated turn's.
        first = self._interactive(script, decision="deny")
        message = self._last_answer_id(first)
        self._reset_fakes()
        return self._regenerate(first, message)

    def _request_id(self, suffix: str) -> str:
        return f"qa6-{self.plan.row.id.lower()}-{suffix}-{secrets.token_hex(4)}"

    def _interactive(self, script: Script, decision: str | None = None) -> TurnRecord:
        return self.send(script, decision=decision)

    def send(self, script: Script, *, session_id: str | None = None, decision: str | None = None) -> TurnRecord:
        """One message read to its end: a new conversation, or the next turn of ``session_id``."""
        record = TurnRecord(self._request_id("send"), script, session_id=session_id, agent_key=self.agent_key,
                            decision=decision, role_model=self.role_model)
        drop = self.plan.turn.action == "reconnect"
        path = self.paths.stream() if session_id is None else self.paths.follow_up(session_id)
        stream = self.stack.api.stream("POST", path, self._body(script), headers={"x-request-id": record.request_id},
                                       stop=self._stop(drop_on_content=drop))
        self._follow(record, stream)
        return record

    def _regenerate(self, first: TurnRecord, message_id: str) -> TurnRecord:
        record = TurnRecord(self._request_id("regenerate"), first.script, session_id=first.session_id,
                            previous_run_id=first.run_id, agent_key=self.agent_key, role_model=self.role_model)
        body: Json = {"filters": {}, "chatMode": self.plan.turn.chat_mode, **self.model}
        if self.plan.turn.harness is not None:
            body["harness"] = self.plan.turn.harness
        stream = self.stack.api.stream("POST", self.paths.regenerate(str(first.session_id), message_id), body,
                                       headers={"x-request-id": record.request_id}, stop=self._stop(drop_on_content=False))
        self._follow(record, stream)
        return record

    def _stop(self, *, drop_on_content: bool):  # noqa: ANN202 - a frame predicate
        seen_content = {"done": False}

        def stop(frame: Frame) -> bool:
            if frame.type == "CUSTOM" and frame.name == APPROVAL:
                return True
            if drop_on_content and not seen_content["done"] and frame.type in CONTENT and frame.id:
                seen_content["done"] = True
                return True
            return False

        return stop

    def _follow(self, record: TurnRecord, stream: Stream) -> None:
        """Reads the turn to its end: answers each card, reattaches after a drop, until a terminal frame."""
        if stream.error and not stream.frames:
            raise StackError(f"the turn's stream failed: {stream.status} {stream.error}")
        record.frames.extend(stream.frames)
        self._ids(record)
        for _ in range(8):
            last = record.frames[-1] if record.frames else None
            if last is None or last.type in TERMINAL:
                return
            if record.run_id is None:
                record.notes.append("the stream stopped before a terminal frame on a turn with no run to reattach to")
                self._legacy_rest(record)
                return
            if last.type == "CUSTOM" and last.name == APPROVAL:
                self._decide(record, last)
            after = stream.last_id or next((f.id for f in reversed(record.frames) if f.id), None)
            stream = self.stack.api.stream("GET", f"{self.paths.run(record.session_id or '', record.run_id)}/stream",
                                           headers={"Last-Event-ID": after} if after else None,
                                           stop=self._stop(drop_on_content=False))
            record.reconnect_ids.append((after, [f.id or "" for f in stream.frames]))
            record.frames.extend(stream.frames)
        record.notes.append("the turn did not end after 8 reattaches")

    @staticmethod
    def _ids(record: TurnRecord) -> None:
        for frame in record.frames:
            if frame.type == "CUSTOM" and frame.name == "conversation_created":
                value = frame.data.get("value") or {}
                record.session_id = record.session_id or value.get("conversationId")
                record.run_id = record.run_id or value.get("runId")
            if frame.type == "RUN_STARTED" and record.run_id is None and frame.id:
                record.run_id = frame.data.get("runId")

    def _decide(self, record: TurnRecord, card: Frame) -> None:
        value = card.data.get("value") or {}
        interaction = str(value.get("interactionId"))
        # A sub-agent's card is shown on its parent's stream and answered on the sub-agent's own run.
        run_id = str(value.get("childRunId") or record.run_id)
        decision = record.decision or self.plan.turn.decision
        if decision == "expire":
            self._expire(record)
            record.decisions.append("expire")
            return
        if decision not in DECISIONS:
            record.notes.append(f"a card appeared, the row has no decision ({decision}): denied so the run ends")
            decision = "deny"
        verdict, scope = DECISIONS[decision]
        body: Json = {"decision": verdict, "idempotencyKey": f"qa6-{secrets.token_hex(6)}"}
        if scope is not None:
            body["scope"] = scope
        self.stack.api.ok("POST", f"{self.paths.run(str(record.session_id), run_id)}/interactions/"
                                  f"{interaction}/decision", body)
        record.decisions.append(verdict)

    def _expire(self, record: TurnRecord) -> None:
        self.stack.mongo(
            f"d.agentRuns.updateOne({{runId: {json.dumps(record.run_id)}}}, "
            f"{{$set: {{absoluteDeadlineAt: new Date(Date.now() - 1000)}}}})"
        )

    def _legacy_rest(self, record: TurnRecord) -> None:
        """A legacy turn whose stream the client dropped: what the saved conversation shows once it settles."""
        session = record.session_id
        if session is None:
            return

        def settled() -> bool:
            body = self.stack.api.ok("GET", self.paths.conversation(session))
            conversation = (body or {}).get("conversation") or {}
            return conversation.get("status") not in (None, "Inprogress", "In Progress", "inprogress")

        try:
            wait_until(settled, timeout=120, what="the legacy conversation to settle", every=2)
        except StackError as error:
            record.notes.append(str(error))

    def _last_answer_id(self, record: TurnRecord) -> str:
        body = self.stack.api.ok("GET", self.paths.conversation(str(record.session_id)))
        messages = ((body or {}).get("conversation") or {}).get("messages") or []
        answers = [m for m in messages if m.get("messageType") == "bot_response"]
        if not answers:
            raise StackError("the first turn saved no answer to regenerate")
        return str(answers[-1]["_id"])

    # --- schedules ----------------------------------------------------------------------

    def _scheduled(self, script: Script) -> TurnRecord:
        cron = self.plan.turn.trigger == "schedule_cron"
        trigger: Json = {"kind": "cron", "cron": _next_slot(), "timeZone": "UTC"} if cron else {"kind": "webhook"}
        created = self.stack.api.ok("POST", "/api/v1/agent-harness/schedules", {
            "name": f"QA6 {self.plan.row.id}", "agentKey": self.agent_key,
            "input": {"query": script.text(), "chatMode": self.plan.turn.chat_mode, **self.model},
            "trigger": trigger,
        })
        schedule_id = str(created["schedule"]["id"])
        record = TurnRecord(self._request_id("schedule"), script, agent_key=self.agent_key,
                            role_model=self.role_model)
        try:
            if not cron:
                for delivery in range(2):
                    body: Json = {"event": "qa6", "row": self.plan.row.id, "delivery": delivery}
                    self.stack.api.ok("POST", f"/api/v1/agent-harness/triggers/{schedule_id}/webhook", body,
                                      headers=webhook_headers(created, body))

            def fired() -> bool:
                firings = self.stack.api.ok("GET", f"/api/v1/agent-harness/schedules/{schedule_id}/firings")
                record.firings = list((firings or {}).get("firings") or [])
                refused = [f for f in record.firings if not f.get("runId") and f.get("settledAt")]
                if refused:
                    raise StackError(f"the schedule's firing was {refused[0].get('status')}: {refused[0].get('reason')}")
                return any(f.get("runId") for f in record.firings)

            wait_until(fired, timeout=150, what="the schedule to fire a run", every=2)
        finally:
            self.stack.api.request("DELETE", f"/api/v1/agent-harness/schedules/{schedule_id}")
        firing = next(f for f in record.firings if f.get("runId"))
        record.session_id, record.run_id = str(firing["conversationId"]), str(firing["runId"])
        stream = self.stack.api.stream("GET", f"{self.paths.run(record.session_id, record.run_id)}/stream",
                                       stop=self._stop(drop_on_content=False))
        self._follow(record, stream)
        return record


def webhook_headers(created: Json, body: Json, now: float | None = None) -> dict[str, str]:
    """How a caller authenticates a delivery to the webhook the schedule create answered (TR-04). A new webhook is
    signed unless created with ``auth: token``: ``webhookSecret`` keys ``v1=`` HMAC-SHA256 of ``<timestamp>.<raw
    body>``, the raw body being the bytes ``Http.request`` sends; a token webhook answers ``webhookToken``."""
    if "webhookSecret" in created:
        timestamp = str(int(now if now is not None else time.time()))
        raw = json.dumps(body).encode()
        mac = hmac.new(str(created["webhookSecret"]).encode(), timestamp.encode() + b"." + raw, hashlib.sha256)
        return {SIGNATURE_HEADER: f"v1={mac.hexdigest()}", TIMESTAMP_HEADER: timestamp}
    if "webhookToken" in created:
        return {TOKEN_HEADER: str(created["webhookToken"])}
    raise StackError(f"the schedule create answer holds no webhook credential: {sorted(created)}")


def _surfaces(stack: Stack, plan: RowPlan, record: TurnRecord, obs: Observation) -> None:
    """What the row's role and tool family look at besides the chat (04 §4.4): the admin's run view, the operator's
    health and logs, the person's memories."""
    durable = bool(record.run_id) and obs.route == "durable"
    if plan.role == "admin" and durable:
        view = stack.api.request("GET", Paths(record.agent_key).run(str(record.session_id), str(record.run_id)))
        obs.admin_view = view.body if isinstance(view.body, dict) else {"status": view.status}
    if plan.role == "operator":
        obs.health = stack.api.request("GET", "/api/v1/health").status
        obs.log_text = stack.runtime.node_log()[-200_000:]
    if plan.agent.family == "memory" and durable:
        # This turn's own proposals: the person's list also holds what earlier rows of the stack remembered.
        listed = stack.api.request("GET", "/api/v1/agent-harness/memories")
        mine = (listed.body or {}).get("memories") or [] if isinstance(listed.body, dict) else []
        obs.memories = [str(m.get("content", "")) for m in mine if m.get("sessionId") == record.session_id]


def _next_slot(now: datetime | None = None) -> str:
    """A daily cron whose next slot is the minute after next: a schedule may fire at most every five minutes, and a
    row waits for one real slot, not five."""
    slot = (now or datetime.now(UTC)) + timedelta(minutes=2)
    return f"{slot.minute} {slot.hour} * * *"


def _failed_conversation(data: Json) -> str | None:
    conversation = ((data.get("result") or {}) if isinstance(data.get("result"), dict) else {}).get("conversation") or {}
    if conversation.get("status") != "Failed":
        return None
    errors = conversation.get("conversationErrors") or []
    return str((errors[-1] if errors else {}).get("errorType") or "failed")


def collect(stack: Stack, plan: RowPlan, record: TurnRecord) -> Observation:
    """The turn's ``Observation``: frames, journal, effects, model requests and Node's routing record."""
    obs = Observation(session_id=record.session_id, run_id=record.run_id, decisions=list(record.decisions),
                      notes=list(record.notes), marker=record.script.lines[-1].removeprefix("say "))
    frames = record.frames
    obs.first_frame = (frames[0].name or frames[0].type) if frames else None
    obs.cards = [dict(f.data.get("value") or {}) for f in frames if f.type == "CUSTOM" and f.name == APPROVAL]
    obs.answer = "".join(str(f.data.get("delta", "")) for f in frames if f.type == "TEXT_MESSAGE_CONTENT")
    end = next((f for f in reversed(frames) if f.type in TERMINAL), None)
    if end is not None:
        obs.terminal = "finished" if end.type in ("RUN_FINISHED", "complete") else "error"
        if obs.terminal == "error":
            obs.error_code = str(end.data.get("code") or (end.data.get("error") or {}).get("code") or "error")
        failed = _failed_conversation(end.data)
        if failed is not None:
            # A run that ended while nobody was attached replays as RUN_FINISHED with the saved failure.
            obs.terminal, obs.error_code = "error", failed
    names = {str(f.data.get("toolCallId")): str(f.data.get("toolCallName")) for f in frames if f.type == "TOOL_CALL_START"}
    writes = record.script.writes
    if writes is not None:
        obs.write_results = [str(f.data.get("content", "")) for f in frames if f.type == "TOOL_CALL_RESULT"
                             and names.get(str(f.data.get("toolCallId")), "").endswith(writes)]
    if record.session_id is not None:
        journal = stack.journal(record.session_id)
        obs.session_events = list(journal["events"])
        obs.events = [e for e in journal["events"] if record.run_id is None or e.get("runId") in (record.run_id, None)
                      or str(e.get("streamId", "")).startswith(f"{record.session_id}:")]
        obs.audit = journal["audit"]
        obs.audit_types = journal["auditTypes"]
    durable_run = bool(record.run_id) and any(e.get("type") == "run.admitted" and e.get("runId") == record.run_id
                                              for e in obs.events)
    obs.route = "durable" if durable_run else "legacy"
    legacy = [r for r in stack.node_records(record.request_id) if r.get("event") == "admission.legacy"]
    obs.reason = str(legacy[-1].get("reason")) if legacy else None
    obs.legacy_reasons = [str(r.get("reason")) for r in legacy]
    effects = (stack.saas.ok("GET", "/__control/effects") or {}).get("effects") or []
    obs.effects = len(effects)
    requests = (stack.model.ok("GET", "/__control/requests") or {}).get("requests") or []
    obs.wires = {str(r["wire"]) for r in requests if r.get("status") == 200 and r.get("wire")}
    obs.model_calls = [{k: r.get(k) for k in ("kind", "wire", "model", "tools", "subAgent")} for r in requests
                       if r.get("status") == 200 and r.get("wire")]
    obs.role_model = record.role_model
    if record.run_id is not None:
        obs.child_refusals = [r for r in stack.node_events("child.refused") if r.get("runId") == record.run_id]
    obs.reconnects = record.reconnect_ids
    obs.firings = record.firings
    obs.previous_run_id = record.previous_run_id
    if record.session_id is not None:
        saved = stack.api.request("GET", Paths(record.agent_key).conversation(record.session_id))
        conversation = (saved.body or {}).get("conversation") if isinstance(saved.body, dict) else None
        obs.saved_messages = [str(m.get("messageType")) for m in (conversation or {}).get("messages") or []]
    _surfaces(stack, plan, record, obs)
    obs.frames = [f.data for f in frames]
    obs.frame_ids = [f.id for f in frames]
    return obs
