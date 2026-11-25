"""In-process reference adapters for the journal ports.

``InMemoryJournal`` plays both sides of ``/internal/harness/*``: the worker
side (``JournalPort``) and the Node side tests need (admit, cancel, resolve an
interaction, sweep expired leases). It enforces what Node enforces: the writer
allowlist from the generated ``EVENT_WRITERS``, generated-schema validation,
gap-free ``seq`` per stream, the ``{attemptId, fence, lease > now}`` predicate on
DB time, and idempotent ``commandId`` receipts.

``oplog`` makes it survive ``kill -9``: every successful mutation is appended
to a JSON-lines file and fsynced before it returns, and a new instance replays
the file. A mutation lost with the process was never acknowledged, which is
exactly a crash before commit.

Attempt counting: ``attempts`` counts claims since the run last left
``running`` cleanly (suspend, yield). A crash loop therefore reaches the
quarantine cap while a run that yields or waits for approvals does not.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import JsonValue, TypeAdapter, ValidationError

from harness_core.clock import SystemClock
from harness_core.contracts import (
    canonical_digest,
    canonical_json,
    dump_contract,
    validate_contract,
)
from harness_core.contracts.generated.common import ContentRef
from harness_core.contracts.generated.event_catalog import EVENT_MODELS, EVENT_WRITERS
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.interactions.connect import skipped_connects
from harness_core.journal.child_model import (
    ROLE_MODELS_DIMENSION,
    ChildSpecModel,
    child_model_of,
)
from harness_core.journal.children import (
    ChildAdmitted,
    ChildRefusal,
    ChildRefused,
    ChildRequest,
    ChildWorkspace,
    GrantDecision,
    JoinReady,
    child_executor,
    child_grants,
    child_run_id_for,
    child_spec,
    forks_for,
    run_workspace,
)
from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS
from harness_core.journal.inline import inline_bytes, inline_ref
from harness_core.journal.live_rate import LiveRateCap
from harness_core.journal.open_calls import node_closing_outcomes
from harness_core.journal.types import (
    LIVE_BUFFER_FRAMES,
    TERMINAL_STATUSES,
    AttemptToken,
    Cancelled,
    Checkpoint,
    ChildResultRef,
    Claim,
    CommandConflict,
    CommitReceipt,
    CommitRejected,
    CommitRequest,
    CompactAsk,
    Complete,
    ContentMissing,
    ConversationGrantNotOffered,
    EditNotOffered,
    EventRejected,
    Fail,
    InputArtifactRef,
    InputPending,
    InteractionChannel,
    InteractionDecision,
    InteractionNotOpen,
    InteractionResolved,
    JournalEvent,
    JournalUnavailable,
    LeaseGrant,
    LiveFrame,
    NewEvent,
    OwnershipLost,
    Quarantine,
    RunNotClaimable,
    RunStatus,
    Suspend,
    Transition,
    Writer,
    WriterNotAllowed,
    Yield,
    db_timestamp,
)
from harness_core.priority import (
    DEFAULT_CLASS,
    PriorityClass,
    after_approval,
    class_for_run_kind,
    priority_rank,
)
from harness_core.workspace.forks import child_fork_of, session_scope

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from pydantic import BaseModel

    from harness_core.clock import Clock
    from harness_core.workspace.ports import WorkspaceStore

DEFAULT_LEASE_MS = 30_000

_COMMIT = TypeAdapter(CommitRequest)
# Node's default (AGENT_HARNESS_CLAIM_FLOOR_EVERY).
DEFAULT_CLAIM_FLOOR_EVERY = 5
_TOKEN = TypeAdapter(AttemptToken)
_EVENT_DICT = TypeAdapter(dict[str, JsonValue])


@dataclass(frozen=True)
class LegacyImport:
    """What Node journals as ``session.imported``: the legacy history entries
    (``previousConversations``), inline unless ``transcript_ref`` holds them."""

    turns: Sequence[Mapping[str, JsonValue]]
    up_to_message_seq: int
    replaces_history: bool = False
    transcript_ref: ContentRef | None = None

    def transcript(self) -> bytes:
        return canonical_json(cast("JsonValue", [dict(turn) for turn in self.turns]))

    def data(self) -> dict[str, JsonValue]:
        ref = self.transcript_ref or inline_ref(self.transcript())
        data: dict[str, JsonValue] = {
            "upToMessageSeq": self.up_to_message_seq,
            "transcriptDigest": ref.hash,
            "transcriptRef": dump_contract(ref),
            "messages": len(self.turns),
        }
        if self.replaces_history:
            data["replacesHistory"] = True
        return data


# A run ends at one of these; one Node writes with no attempt closes the run's open calls first (EV-06).
_TERMINAL_EVENTS = frozenset({"run.completed", "run.failed", "run.cancelled", "run.quarantined"})


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass
class _Run:
    run_id: str
    org_id: str
    session_id: str
    stream_id: str
    spec: dict[str, JsonValue]
    input_text: str
    status: RunStatus = "queued"
    input_artifacts: tuple[InputArtifactRef, ...] = ()
    compact: CompactAsk | None = None
    trigger_context: dict[str, JsonValue] | None = None
    attempts: int = 0
    total_claims: int = 0
    fence: int = 0
    attempt_id: str | None = None
    lease_ms: int = DEFAULT_LEASE_MS
    lease_expires_at: datetime | None = None
    not_before: datetime | None = None
    spec_pinned: bool = False
    checkpoint_ref: str | None = None
    cancel_requested: bool = False
    open_interactions: dict[str, str] = field(default_factory=dict[str, str])
    # Steering: Node's count of queued inputs and those no commit consumed yet.
    input_seq: int = 0
    pending_inputs: list[int] = field(default_factory=list[int])
    receipts: dict[str, tuple[str, CommitReceipt]] = field(
        default_factory=dict[str, tuple[str, CommitReceipt]]
    )
    # Child runs (P6.1): who spawned this one, what it was asked; a parent's counts.
    parent_run_id: str | None = None
    depth: int = 0
    request_digest: str | None = None
    children_spawned: int = 0
    children_open: int = 0
    suspended_for: str | None = None
    # MA1: the root definition's model per sub-agent role (``<provider>:<model>``); a child holds its root's.
    role_routes: dict[str, str] = field(default_factory=dict[str, str])
    # G-45: from the run kind, a child's from its parent, raised once a person answers it (Node's rule).
    priority_class: PriorityClass = DEFAULT_CLASS


# How a child ended: outcome, error type, and the result its completion named.
_Ending = tuple[str, str | None, ChildResultRef | None]


def _ending(transition: Transition | None) -> _Ending:
    match transition:
        case Complete(outcome=outcome, child_result=result):
            return outcome, None, result
        case Fail(error_type=error_type):
            return "failed", error_type, None
        case Quarantine():
            return "failed", "quarantined", None
        case _:
            return "cancelled", "cancelled", None


def _engine_kind_of(spec: Mapping[str, JsonValue]) -> str | None:
    executor = spec.get("executor")
    if not isinstance(executor, dict) or executor.get("kind") != "engine":
        return None
    engine = executor.get("engine")
    kind = engine.get("kind") if isinstance(engine, dict) else None
    return kind if isinstance(kind, str) else None


def _compact_ask(value: JsonValue) -> CompactAsk | None:
    if not isinstance(value, dict):
        return None
    focus = value.get("focus")
    return CompactAsk(focus if isinstance(focus, str) else None)


def _request_digest(request: ChildRequest) -> str:
    body: dict[str, JsonValue] = {
        "invocationId": request.invocation_id,
        "taskId": request.task_id,
        "goal": request.goal,
        "grants": request.grants.wire(),
    }
    if request.definition is not None:
        body["definition"] = request.definition
    if request.executor is not None:
        body["executor"] = request.executor.wire()
    return canonical_digest(body)


class InMemoryJournal:
    """Reference ``JournalPort`` and ``ChildRunPort`` plus the Node-side operations tests need."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        lease_ms: int = DEFAULT_LEASE_MS,
        oplog: Path | None = None,
        extension_writers: Mapping[str, frozenset[Writer]] | None = None,
        extension_models: Mapping[str, type[BaseModel]] | None = None,
        workspaces: WorkspaceStore | None = None,
        engine_authority: JsonValue | None = None,
        accepts: frozenset[str] | None = None,
        refuses: frozenset[str] = frozenset(),
        max_commit_events: int = MAX_COMMIT_EVENTS,
        configured_models: Mapping[str, str] | None = None,
        claim_floor_every: int = DEFAULT_CLAIM_FLOOR_EVERY,
    ) -> None:
        """``claim_floor_every``: every n-th claim of the next run takes the oldest claimable of any class, as Node's
        ``AGENT_HARNESS_CLAIM_FLOOR_EVERY`` (G-45); 0: strict priority."""
        self._clock: Clock = clock or SystemClock()
        self._floor_every = claim_floor_every
        self._next_claims = 0
        self._max_commit_events = max_commit_events
        # UX2: what claims advertise (``None``: this build, everything) and the event types this journal does not
        # know, as an older Node would not: together they play a Node older than the worker.
        self._accepts = accepts
        self._refuses = refuses
        # The engine profile a loop parent's org grants its engine children (Node resolves it, E1).
        self._engine_authority = engine_authority
        # MA1: the models the deployment configures, route to ``modelKey``, as Node reads them at a spawn. A role
        # whose model is not among them is refused. Public: a test withdraws a model by removing it.
        self.configured_models: dict[str, str] = dict(configured_models or {})
        # Set: a child granted the workspace mount gets a fork at admission, as Node forks it (P6.4).
        self._workspaces = workspaces
        self._lease_ms = lease_ms
        self._runs: dict[str, _Run] = {}
        self._streams: dict[str, list[JournalEvent]] = {}
        self._extension_writers = dict(extension_writers or {})
        # Extension envelopes validated like Node validates them (its catalog).
        self._extension_models = dict(extension_models or {})
        self._oplog = oplog
        self._replay_now: datetime | None = None
        # Fault injection (never logged): the next N commit/renew calls fail transiently.
        self.unavailable_calls = 0
        # Test hook: the next N reads return a stale prefix (a lagging secondary).
        self.stale_reads = 0
        # Fault injection: the next N reads fail transiently (Node restarting).
        self.unavailable_reads = 0
        # Fault injection: the next N commits are applied, then their reply is lost.
        self.lost_acks = 0
        if oplog is not None and oplog.exists():
            self._replay(oplog)

    # ---- JournalPort ----

    async def claim(
        self,
        run_id: str | None = None,
        *,
        worker_id: str,
        org_id: str | None = None,
        above: PriorityClass | None = None,
    ) -> Claim | None:
        """Node's queue (G-45): the highest class first, admission order within a class; every n-th claim of the
        next run the first claimable of any class. ``above``: only a run of a strictly higher class, never the floor."""
        now = self._now()
        run = self._pick(run_id, now, above)
        if run is not None and org_id is not None and run.org_id != org_id:
            raise RunNotClaimable(run.run_id)
        if run is None:
            return None
        claim = self._do_claim(run, now)
        self._log("claim", {"runId": run.run_id, "workerId": worker_id})
        return claim

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        if self.unavailable_calls > 0:
            self.unavailable_calls -= 1
            raise JournalUnavailable("journal unavailable (injected)")
        if len(request.events) > self._max_commit_events:
            # Node validates the body before it looks at the run: nothing is written, and no retry helps.
            raise CommitRejected("VALIDATION_ERROR")
        now = self._now()
        receipt = self._do_commit(request, now)
        if not receipt.replayed:
            self._log("commit", {"request": _COMMIT.dump_python(request, mode="json")})
        if self.lost_acks > 0:
            self.lost_acks -= 1
            raise JournalUnavailable("journal reply lost (injected)")
        return receipt

    async def read(
        self, stream_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[JournalEvent]:
        if self.unavailable_reads > 0:
            self.unavailable_reads -= 1
            raise JournalUnavailable("journal unavailable (injected)")
        events = [e for e in self._streams.get(stream_id, []) if e.seq > after_seq]
        if self.stale_reads > 0 and events:
            self.stale_reads -= 1
            events = events[:-1]
        return events if limit is None else events[:limit]

    async def last_seq(self, stream_id: str) -> int:
        stream = self._streams.get(stream_id)
        return stream[-1].seq if stream else 0

    async def renew(self, token: AttemptToken) -> LeaseGrant:
        if self.unavailable_calls > 0:
            self.unavailable_calls -= 1
            raise JournalUnavailable("journal unavailable (injected)")
        now = self._now()
        run = self._owned(token, now)
        run.lease_expires_at = now + timedelta(milliseconds=run.lease_ms)
        self._log("renew", {"token": _TOKEN.dump_python(token, mode="json")})
        return LeaseGrant(run.lease_ms)

    def forget(self, token: AttemptToken) -> None:
        """Nothing to drop: an in-process journal holds no attempt credential."""

    async def release(self, token: AttemptToken, *, reason: str) -> None:
        now = self._now()
        run = self._owned(token, now)
        if run.cancel_requested:
            # Node's rule (SL-05): a run being cancelled only ends; handed back, another attempt would run it.
            raise CommitRejected("INVALID_TRANSITION: the run is being cancelled")
        self._requeue(run, now)
        self._log(
            "release",
            {"token": _TOKEN.dump_python(token, mode="json"), "reason": reason},
        )

    # ---- ChildRunPort (Node's child admission) ----

    async def spawn(self, token: AttemptToken, request: ChildRequest) -> ChildAdmitted:
        now = self._now()
        parent = self._owned(token, now)
        child_id = child_run_id_for(parent.org_id, parent.run_id, request.invocation_id)
        digest = _request_digest(request)
        existing = self._runs.get(child_id)
        if existing is not None:
            if existing.request_digest != digest or existing.parent_run_id != parent.run_id:
                raise CommandConflict(f"{request.invocation_id}: another child request")
            return ChildAdmitted(child_id, created=False)
        spec = validate_contract(RunSpec, parent.spec)
        if spec.budgets.max_children <= 0:
            raise ChildRefused(ChildRefusal.NOT_GRANTED)
        decision = child_grants(spec, request.grants, parent.depth + 1, request.mcp_pins)
        if decision.refusal is not None:
            reason = (
                ChildRefusal.DEPTH_EXCEEDED
                if decision.refusal["code"] == "CHILD_DEPTH_EXCEEDED"
                else ChildRefusal.GRANT_TOO_LARGE
            )
            raise ChildRefused(reason, str(decision.refusal["dimension"]))
        if parent.children_spawned >= spec.budgets.max_children:
            raise ChildRefused(ChildRefusal.LIMIT_REACHED)
        assert decision.grants is not None
        executor = child_executor(spec, request.executor, self._engine_authority)
        if isinstance(executor, GrantDecision):
            assert executor.refusal is not None
            raise ChildRefused(ChildRefusal.GRANT_TOO_LARGE, str(executor.refusal["dimension"]))
        role_model = child_model_of(parent.role_routes, request.definition)
        spec_model: ChildSpecModel | None = None
        if role_model is not None:
            model_key = self.configured_models.get(role_model.route)
            if model_key is None:
                # Refused, never the parent's model instead (MA1 C3).
                raise ChildRefused(ChildRefusal.GRANT_TOO_LARGE, f"{ROLE_MODELS_DIMENSION}.{role_model.role}")
            spec_model = ChildSpecModel(role_model.provider, role_model.model, model_key)
        workspace = await self._fork_for(parent, spec, decision.grants, child_id)
        if child_id in self._runs:
            return ChildAdmitted(child_id, created=False)
        child_spec_doc = self._host_pinned(
            child_spec(
                spec, decision.grants, request.definition, workspace, executor, request.inherited_label, spec_model,
                request.inherited_grants, request.mcp_pins,
            ),
            spec_model,
        )
        self._pin_engine_carry(parent, child_spec_doc)
        args: dict[str, JsonValue] = {
            "parentRunId": parent.run_id,
            "childRunId": child_id,
            "invocationId": request.invocation_id,
            "goal": request.goal,
            "spec": child_spec_doc,
            "requestDigest": digest,
            "grantDigest": canonical_digest(
                {"grants": child_spec_doc["grants"], "budgets": child_spec_doc["budgets"]}
            ),
        }
        if request.task_id is not None:
            args["taskId"] = request.task_id
        if role_model is not None:
            args["model"] = role_model.wire()
        self._do_spawn(args, now)
        self._log("spawn", args)
        return ChildAdmitted(child_id, created=True)

    def _host_pinned(self, spec: dict[str, JsonValue], model: ChildSpecModel | None) -> dict[str, JsonValue]:
        """The child's spec with what else its host's admission pins; nothing here. A host that pins more for a
        child (PipesHub: its resolved config, naming ``model`` when its role set one) overrides this in the
        reference journal its tests run on."""
        return spec

    def _pin_engine_carry(self, parent: _Run, child: dict[str, JsonValue]) -> None:
        """Node's admission of a root loop run's engine child (CTL-6, LCFIX): the last checkpoint of the session's
        latest completed engine child of a root loop run of the same principal, when it ran the same engine kind,
        pinned as ``x.pipeshub.engineCarry`` (each child journals on its own stream, which no worker reads)."""
        extensions = child.get("extensions")
        extension = extensions.get("x.pipeshub") if isinstance(extensions, dict) else None
        kind = _engine_kind_of(child)
        if parent.parent_run_id is not None or kind is None or not isinstance(extension, dict):
            return
        principal = parent.spec.get("principalRef")
        for event in reversed(self._streams.get(parent.stream_id, [])):
            ended = self._runs.get(str(event.data.get("childRunId"))) if event.type == "child.completed" else None
            above = self._runs.get(ended.parent_run_id or "") if ended is not None else None
            if ended is None or above is None or ended.status != "completed":
                continue
            if above.parent_run_id is not None or above.spec.get("principalRef") != principal:
                continue
            ended_kind = _engine_kind_of(ended.spec)
            if ended_kind is None or _engine_kind_of(above.spec) is not None:
                continue
            if ended_kind != kind:
                return
            own = self._streams.get(ended.stream_id, [])
            checkpoints = [e.data for e in own if e.type == "engine.checkpointed"]
            snapshots = [e.data for e in own if e.type == "engine.snapshotted"]
            if not checkpoints:
                return
            carry: dict[str, JsonValue] = {"runId": ended.run_id, "checkpoint": dict(checkpoints[-1])}
            if snapshots:
                carry["snapshot"] = dict(snapshots[-1])
            extension["engineCarry"] = carry
            return

    async def _fork_for(
        self, parent: _Run, spec: RunSpec, grants: dict[str, JsonValue], child_id: str
    ) -> ChildWorkspace | None:
        """Node's admission fork: the parent's own workspace at its head."""
        origin = run_workspace(spec, parent.session_id)
        if self._workspaces is None or origin is None or not forks_for(grants, spec):
            return None
        scope = session_scope(parent.org_id, parent.session_id)
        head = await self._workspaces.head(scope, origin)
        fork_id = child_fork_of(origin, child_id)
        _ = await self._workspaces.fork(scope, origin, at_revision=head.revision, fork_id=fork_id)
        return ChildWorkspace(fork_id, origin, head.revision)

    def parent_of(self, run_id: str) -> str | None:
        return self._runs[run_id].parent_run_id

    def children_of(self, run_id: str) -> list[str]:
        return [r.run_id for r in self._runs.values() if r.parent_run_id == run_id]

    # ---- Node side (admission, interactions, cancel, sweeper) ----

    def admit(
        self,
        run_id: str,
        *,
        org_id: str,
        session_id: str,
        spec: RunSpec,
        input_text: str,
        stream_id: str | None = None,
        principal_ref: str = "p_user",
        imported: LegacyImport | None = None,
        session_role: str | None = None,
        input_artifacts: Sequence[InputArtifactRef] = (),
        role_routes: Mapping[str, str] | None = None,
        compact: CompactAsk | None = None,
        trigger_context: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """``imported``: Node found conversation history the journal does not
        hold and journals it before the run (``session.imported``, P1b T3).
        ``session_role``: the actor's role when the run is in a shared session (SS2).
        ``input_artifacts``: artifacts the run's input names (E1b), handed to every claim.
        ``role_routes``: the definition's model per sub-agent role (MA1), which every spawn under this run reads.
        ``compact``: the person's ``/compact [focus]`` Node took into this run's input (SLASH2 C2).
        ``trigger_context``: what fired a scheduled run (TR-03), handed to every claim."""
        if run_id in self._runs:
            raise ValueError(f"run {run_id!r} already admitted")
        args: dict[str, JsonValue] = {
            "runId": run_id,
            "orgId": org_id,
            "sessionId": session_id,
            "streamId": stream_id or session_id,
            "spec": dump_contract(spec),
            "inputText": input_text,
            "principalRef": principal_ref,
        }
        if imported is not None:
            args["imported"] = imported.data()
        if session_role is not None:
            args["sessionRole"] = session_role
        if input_artifacts:
            args["inputArtifacts"] = [
                {"artifactId": ref.artifact_id, **({"version": ref.version} if ref.version is not None else {})}
                for ref in input_artifacts
            ]
        if role_routes:
            args["roleRoutes"] = dict(role_routes)
        if compact is not None:
            args["compact"] = {"focus": compact.focus} if compact.focus else {}
        if trigger_context is not None:
            args["triggerContext"] = dict(trigger_context)
        self._do_admit(args, self._now())
        self._log("admit", args)

    def request_cancel(self, run_id: str, *, by: str = "p_user") -> None:
        now = self._now()
        run = self._runs[run_id]
        if run.status in TERMINAL_STATUSES:
            return
        self._do_cancel(run, by, now)
        self._log("cancel", {"runId": run_id, "by": by})
        # Node cancels a parent's open children with it, each its own command.
        for child in [r.run_id for r in self._runs.values() if r.parent_run_id == run_id]:
            self.request_cancel(child, by=by)

    def queue_input(
        self,
        run_id: str,
        text: str,
        *,
        attachments: Sequence[Mapping[str, JsonValue]] = (),
        record_ids: Sequence[str] = (),
    ) -> int:
        """Node's steering admission (D19): the message joins the running run,
        naming stored attachments and records by reference (Node authorized them).
        Returns its ``messageSeq``; a run that ended takes nothing."""
        run = self._runs[run_id]
        if run.status in TERMINAL_STATUSES or run.cancel_requested:
            raise RunNotClaimable(f"{run_id}: not taking input")
        args: dict[str, JsonValue] = {"runId": run_id, "text": text}
        if attachments:
            args["attachments"] = [dict(a) for a in attachments]
        if record_ids:
            args["recordIds"] = list(record_ids)
        seq = self._do_queue(args, self._now())
        self._log("queue", args)
        return seq

    def pending_inputs(self, run_id: str) -> list[int]:
        return list(self._runs[run_id].pending_inputs)

    def resolve_interaction(
        self,
        run_id: str,
        interaction_id: str,
        decision: InteractionDecision,
        *,
        by: str = "p_user",
        channel: InteractionChannel = "web",
        scope: Literal["conversation"] | None = None,
        reason: str | None = None,
        edits: Mapping[str, JsonValue] | None = None,
    ) -> None:
        """``reason``: Node's rule (AP-05), a denial's reason carried inline as ``answerRef``. ``edits``: the
        arguments a person approved in place of the call's (AP-06), only where the worker offered it and never with a
        scope, carried inline as canonical JSON."""
        now = self._now()
        run = self._runs[run_id]
        # An ended run keeps the ids it opened, but nothing it asked can be decided any more (SL-05).
        if interaction_id not in run.open_interactions or run.cancel_requested or run.status in TERMINAL_STATUSES:
            raise InteractionNotOpen(interaction_id)
        if scope is not None and (decision != "approved" or not self._grant_offered(run, interaction_id)):
            raise ConversationGrantNotOffered(interaction_id)
        if edits is not None and (
            decision != "approved" or scope is not None or not self._edit_offered(run.stream_id, interaction_id)
        ):
            raise EditNotOffered(interaction_id)
        args: dict[str, JsonValue] = {
            "runId": run_id,
            "interactionId": interaction_id,
            "decision": decision,
            "by": by,
            "channel": channel,
        }
        if scope is not None:
            args["scope"] = scope
        if reason is not None and reason.strip() and decision == "denied":
            args["answerRef"] = cast("JsonValue", inline_ref(reason.strip().encode("utf-8")).model_dump(mode="json"))
        if edits is not None:
            args["answerRef"] = cast("JsonValue", inline_ref(canonical_json(dict(edits))).model_dump(mode="json"))
        self._do_resolve(args, now)
        self._log("resolve", args)

    def sweep(self) -> list[str]:
        """Re-queue every running run whose lease expired (the sweeper only re-queues)."""
        now = self._now()
        expired = [
            r.run_id
            for r in self._runs.values()
            if r.status == "running"
            and r.lease_expires_at is not None
            and r.lease_expires_at <= now
        ]
        for run_id in expired:
            self._do_sweep(self._runs[run_id], now)
            self._log("sweep", {"runId": run_id})
        return expired

    def inject(self, stream_id: str, event: Mapping[str, JsonValue]) -> JournalEvent:
        """Append an event without validation (tests: events from a newer release)."""
        doc = _EVENT_DICT.validate_python(dict(event))
        stored = self._do_inject(stream_id, doc, self._now())
        self._log("inject", {"streamId": stream_id, "event": doc})
        return stored

    def status(self, run_id: str) -> RunStatus:
        return self._runs[run_id].status

    def attempts(self, run_id: str) -> int:
        """Claims since the run last left ``running`` cleanly (what the quarantine cap counts)."""
        return self._runs[run_id].attempts

    def claims(self, run_id: str) -> int:
        return self._runs[run_id].total_claims

    def open_interactions(self, run_id: str) -> dict[str, str]:
        return dict(self._runs[run_id].open_interactions)

    def events(self, stream_id: str) -> list[JournalEvent]:
        return list(self._streams.get(stream_id, []))

    def stream_ids(self) -> list[str]:
        return list(self._streams)

    def _grant_offered(self, run: _Run, interaction_id: str) -> bool:
        # The run's own offer: root runs share the session's stream, and another run may name the same interaction.
        return any(
            e.type == "interaction.explained"
            and e.run_id == run.run_id
            and e.data.get("interactionId") == interaction_id
            and e.data.get("conversationGrant") is True
            for e in self._streams.get(run.stream_id, [])
        )

    def _offered_grant(self, run: _Run, interaction_id: str, by: JsonValue) -> dict[str, JsonValue]:
        """What the run's worker's latest reason for the ask offers, as Node names it on the grant (FLOWGRANT), with
        who gave it (R38-AUTHZ: it counts only for that person's runs)."""
        for e in reversed(self._streams.get(run.stream_id, [])):
            if (
                e.type != "interaction.explained"
                or e.writer != "worker"
                or e.run_id != run.run_id
                or e.data.get("interactionId") != interaction_id
            ):
                continue
            destination, tool_id, sensitivity = e.data.get("destination"), e.data.get("toolId"), e.data.get("sensitivity")
            key = destination.get("key") if isinstance(destination, dict) else None
            if isinstance(tool_id, str) and isinstance(key, str):
                return {
                    "toolId": tool_id,
                    "key": key,
                    "sensitivity": sensitivity if isinstance(sensitivity, str) else "unknown",
                    "by": by,
                }
            return {}
        return {}

    def share_grant(self, run_id: str, interaction_id: str, from_run_id: str, grant: Mapping[str, JsonValue]) -> None:
        """Node's FLOWGRANT copy: a grant a person gave on ``from_run_id``'s ask, written on another open run of the
        conversation (``ConversationGrantReach``)."""
        run = self._runs[run_id]
        data: dict[str, JsonValue] = {
            "interactionId": interaction_id,
            "scope": "conversation",
            **dict(grant),
            "fromRunId": from_run_id,
        }
        event = self._node_event(run, "interaction.granted", data, self._now(), self._head(run.stream_id) + 1, critical=False)
        self._write_batch(run.stream_id, [event])

    def _edit_offered(self, stream_id: str, interaction_id: str) -> bool:
        return any(
            e.type == "interaction.explained"
            and e.writer == "worker"
            and e.data.get("interactionId") == interaction_id
            and e.data.get("editable") is True
            for e in self._streams.get(stream_id, [])
        )

    def checkpoint_ref(self, run_id: str) -> str | None:
        return self._runs[run_id].checkpoint_ref

    def commits(self, run_id: str) -> list[tuple[str, list[str]]]:
        """``(commandId, event types)`` per worker commit, in commit order."""
        return [
            (command_id, [e.type for e in receipt.events])
            for command_id, (_, receipt) in self._runs[run_id].receipts.items()
        ]

    def commit_events(self, run_id: str) -> list[tuple[str, tuple[JournalEvent, ...]]]:
        """``(commandId, events)`` per worker commit, in commit order."""
        return [(command_id, receipt.events) for command_id, (_, receipt) in self._runs[run_id].receipts.items()]

    # ---- implementation ----

    def _now(self) -> datetime:
        return self._replay_now if self._replay_now is not None else self._clock.now()

    def _pick(self, run_id: str | None, now: datetime, above: PriorityClass | None = None) -> _Run | None:
        if run_id is not None:
            run = self._runs.get(run_id)
            if run is None or not self._claimable(run, now):
                raise RunNotClaimable(run_id)
            return run
        due = [r for r in self._runs.values() if self._claimable(r, now)]
        if above is not None:
            due = [r for r in due if priority_rank(r.priority_class) < priority_rank(above)]
        elif self._floor_every > 0:
            self._next_claims = (self._next_claims + 1) % self._floor_every
            if self._next_claims == 0:
                return due[0] if due else None
        return min(due, key=lambda r: priority_rank(r.priority_class), default=None)

    @staticmethod
    def _claimable(run: _Run, now: datetime) -> bool:
        return run.status == "queued" and (
            run.not_before is None or run.not_before <= now
        )

    def _owned(self, token: AttemptToken, now: datetime) -> _Run:
        run = self._runs.get(token.run_id)
        if run is None:
            raise OwnershipLost(f"run {token.run_id!r} not found")
        if (
            run.status != "running"
            or run.attempt_id != token.attempt_id
            or run.fence != token.fence
            or run.lease_expires_at is None
            or run.lease_expires_at <= now
        ):
            raise OwnershipLost(
                f"run {token.run_id!r}: stale fence {token.fence} or expired lease"
            )
        return run

    def _do_sweep(self, run: _Run, now: datetime) -> None:
        """An expired lease: the run goes back to the queue, unless it was being cancelled, which then ends it
        (Node's sweep, SL-05)."""
        if not run.cancel_requested:
            self._requeue(run, now)
            return
        event = self._node_event(run, "run.cancelled", {}, now, self._head(run.stream_id) + 1)
        run.status = "cancelled"
        run.lease_expires_at = None
        run.attempt_id = None
        run.open_interactions.clear()
        self._write_batch(run.stream_id, [event])
        self._join(run, ("cancelled", "cancelled", None), now)

    def _requeue(self, run: _Run, now: datetime) -> None:
        run.status = "queued"
        run.lease_expires_at = None
        run.attempt_id = None
        run.not_before = now

    def _append(
        self,
        run: _Run | None,
        stream_id: str,
        *,
        org_id: str,
        session_id: str,
        event: NewEvent,
        writer: Writer,
        now: datetime,
        seq: int,
        token: AttemptToken | None,
    ) -> JournalEvent:
        stored = JournalEvent(
            org_id=org_id,
            session_id=session_id,
            stream_id=stream_id,
            seq=seq,
            run_id=run.run_id if run else None,
            attempt_id=token.attempt_id if token else None,
            fence=token.fence if token else None,
            type=event.type,
            critical=event.critical,
            ts=db_timestamp(now),
            writer=writer,
            visibility=event.visibility,
            labels=event.labels,
            data=event.data,
        )
        self._validate(stored)
        return stored

    def _validate(self, event: JournalEvent) -> None:
        writers = EVENT_WRITERS.get(event.type) or self._extension_writers.get(
            event.type
        )
        if writers is None or event.type in self._refuses:
            raise EventRejected(f"unknown event type {event.type!r}")
        if event.writer not in writers:
            raise WriterNotAllowed(f"{event.writer} may not write {event.type!r}")
        if event.writer == "worker" and event.visibility != "owner":
            # Node's rule (S6): who else sees an event is Node's decision, never the worker's.
            raise EventRejected(f"{event.type}: a worker writes visibility owner only")
        model: type[BaseModel] | None = EVENT_MODELS.get(
            event.type
        ) or self._extension_models.get(event.type)
        if model is None:
            return
        try:
            _ = validate_contract(model, event.envelope())
        except ValidationError as exc:
            raise EventRejected(f"{event.type}: {exc.errors()[0]['msg']}") from exc

    def _write_batch(self, stream_id: str, batch: list[JournalEvent]) -> None:
        stream = self._streams.setdefault(stream_id, [])
        stream.extend(self._closed(stream, batch))

    def _closed(self, stream: list[JournalEvent], batch: list[JournalEvent]) -> list[JournalEvent]:
        """A run Node ends itself (its terminal event names no attempt: a cancel of a run no worker holds, a sweep)
        closes the calls an attempt left open, with a cancelled outcome ahead of the end (EV-06), as Node does."""
        out: list[JournalEvent] = []
        for event in batch:
            if event.type in _TERMINAL_EVENTS and event.attempt_id is None and event.run_id is not None:
                out.extend(
                    event.model_copy(update={"type": closing.type, "data": closing.data})
                    for closing in node_closing_outcomes([*stream, *out], event.run_id)
                )
            out.append(event)
        first = batch[0].seq if batch else 0
        return [e.model_copy(update={"seq": first + n}) for n, e in enumerate(out)] if len(out) != len(batch) else out

    def _node_event(
        self,
        run: _Run,
        event_type: str,
        data: dict[str, JsonValue],
        now: datetime,
        seq: int,
        token: AttemptToken | None = None,
        *,
        critical: bool = True,
    ) -> JournalEvent:
        return self._append(
            run,
            run.stream_id,
            org_id=run.org_id,
            session_id=run.session_id,
            event=NewEvent(event_type, data, critical=critical),
            writer="node",
            now=now,
            seq=seq,
            token=token,
        )

    def _do_admit(self, args: Mapping[str, JsonValue], now: datetime) -> None:
        spec = args["spec"]
        assert isinstance(spec, dict)
        run = _Run(
            run_id=str(args["runId"]),
            org_id=str(args["orgId"]),
            session_id=str(args["sessionId"]),
            stream_id=str(args["streamId"]),
            spec=spec,
            input_text=str(args["inputText"]),
            input_artifacts=tuple(
                InputArtifactRef(str(a["artifactId"]), cast("int | None", a.get("version")))
                for a in cast("list[dict[str, JsonValue]]", args.get("inputArtifacts") or [])
            ),
            role_routes=cast("dict[str, str]", args.get("roleRoutes") or {}),
            compact=_compact_ask(args.get("compact")),
            trigger_context=cast("dict[str, JsonValue] | None", args.get("triggerContext")),
            priority_class=class_for_run_kind(cast("str | None", spec.get("runKind"))),
        )
        head = self._head(run.stream_id)
        batch: list[JournalEvent] = []
        imported = args.get("imported")
        if isinstance(imported, dict):
            batch.append(self._node_event(run, "session.imported", imported, now, head + 1))
        batch.append(
            self._node_event(
                run,
                "run.admitted",
                {
                    "runKind": spec.get("runKind", "interactive"),
                    "principalRef": args["principalRef"],
                    **({"sessionRole": args["sessionRole"]} if "sessionRole" in args else {}),
                },
                now,
                head + len(batch) + 1,
            )
        )
        self._runs[run.run_id] = run
        self._write_batch(run.stream_id, batch)

    def _head(self, stream_id: str) -> int:
        stream = self._streams.get(stream_id)
        return stream[-1].seq if stream else 0

    def _do_claim(self, run: _Run, now: datetime) -> Claim:
        attempt = run.attempts + 1
        fence = run.fence + 1
        attempt_id = f"{run.run_id}.a{run.total_claims + 1}"
        token = AttemptToken(run.run_id, attempt_id, fence)
        head = self._head(run.stream_id)
        batch = [
            self._node_event(
                run,
                "run.claimed",
                {"attempt": attempt, "fence": fence, "leaseMs": self._lease_ms},
                now,
                head + 1,
                token,
            )
        ]
        if not run.spec_pinned:
            batch.append(
                self._node_event(
                    run,
                    "run.spec_pinned",
                    {"specDigest": canonical_digest(run.spec), "spec": run.spec},
                    now,
                    head + 2,
                    token,
                )
            )
        run.attempts = attempt
        run.total_claims += 1
        run.fence = fence
        run.attempt_id = attempt_id
        run.status = "running"
        run.lease_ms = self._lease_ms
        run.lease_expires_at = now + timedelta(milliseconds=self._lease_ms)
        run.not_before = None
        run.spec_pinned = True
        self._write_batch(run.stream_id, batch)
        return Claim(
            run_id=run.run_id,
            org_id=run.org_id,
            session_id=run.session_id,
            stream_id=run.stream_id,
            attempt_id=attempt_id,
            attempt=attempt,
            fence=fence,
            lease_ms=self._lease_ms,
            last_seq=batch[-1].seq,
            spec=validate_contract(RunSpec, run.spec),
            input_text=run.input_text,
            checkpoint_ref=run.checkpoint_ref,
            input_artifacts=run.input_artifacts,
            accepts=self._accepts,
            compact=run.compact,
            trigger_context=run.trigger_context,
            priority_class=run.priority_class,
            skipped_connects=frozenset(
                skipped_connects([e for stream in self._streams.values() for e in stream], run.run_id)
            ),
            depth=run.depth,
            store_now_ms=int(now.timestamp() * 1000),
        )

    def _do_commit(self, request: CommitRequest, now: datetime) -> CommitReceipt:
        run = self._runs.get(request.token.run_id)
        if run is None:
            raise OwnershipLost(f"run {request.token.run_id!r} not found")
        digest = canonical_digest(_COMMIT.dump_python(request, mode="json"))
        stored = run.receipts.get(request.command_id)
        if stored is not None:
            if stored[0] != digest:
                raise CommandConflict(request.command_id)
            receipt = stored[1]
            return CommitReceipt(
                receipt.command_id, receipt.stream_id, receipt.events, replayed=True
            )
        run = self._owned(request.token, now)
        if run.cancel_requested and not isinstance(request.transition, (Cancelled, Fail, Quarantine, type(None))):
            # Node's rule (SL-05): an attempt told to stop may report progress and end the run, nothing else.
            raise CommitRejected("INVALID_TRANSITION: the run is being cancelled")
        seq = self._head(run.stream_id)
        batch: list[JournalEvent] = []
        for event in request.events:
            seq += 1
            batch.append(
                self._append(
                    run,
                    run.stream_id,
                    org_id=run.org_id,
                    session_id=run.session_id,
                    event=event,
                    writer="worker",
                    now=now,
                    seq=seq,
                    token=request.token,
                )
            )
        consumed = {
            int(str(e.data["messageSeq"])) for e in batch if e.type == "input.consumed"
        }
        pending = [s for s in run.pending_inputs if s not in consumed]
        match request.transition:
            case Complete(leaves_input=False) if pending:
                # Node's rule: the user was told the run would take it.
                raise InputPending(f"{run.run_id}: input {pending} is queued")
            case Complete(child_result=None) if run.parent_run_id is not None:
                raise CommitRejected("INVALID_COMMIT: a child run completes with its result")
            case Complete(child_result=ChildResultRef()) if run.parent_run_id is None:
                raise CommitRejected("INVALID_COMMIT: only a child run names a result")
            case Suspend(reason="join") if run.children_open == 0:
                raise JoinReady(f"{run.run_id}: every child ended")
            case _:
                pass
        opened = [
            str(e.data["interactionId"])
            for e in batch
            if e.type == "interaction.requested"
        ]
        kinds = {
            str(e.data["interactionId"]): str(e.data["kind"])
            for e in batch
            if e.type == "interaction.requested"
        }
        match request.transition:
            case Suspend(reason="approval") if not (
                set(run.open_interactions) | set(opened)
            ):
                # Node's rule: a run suspended with nothing left open would never resume.
                raise InteractionResolved(f"{run.run_id}: no open interaction")
            case _:
                pass
        transition_event = self._transition_event(
            run, request.transition, now, seq + 1, request.token
        )
        if transition_event is not None:
            batch.append(transition_event)
        # Everything validated: apply atomically.
        self._write_batch(run.stream_id, batch)
        run.pending_inputs = pending
        for interaction_id in opened:
            run.open_interactions[interaction_id] = kinds[interaction_id]
        self._apply_transition(run, request.transition, now)
        if run.status in TERMINAL_STATUSES:
            self._join(run, _ending(request.transition), now)
        receipt = CommitReceipt(request.command_id, run.stream_id, tuple(batch))
        run.receipts[request.command_id] = (digest, receipt)
        return receipt

    def _transition_event(
        self,
        run: _Run,
        transition: Transition | None,
        now: datetime,
        seq: int,
        token: AttemptToken,
    ) -> JournalEvent | None:
        match transition:
            case None:
                return None
            case Suspend(reason=reason, interaction_id=interaction_id):
                data: dict[str, JsonValue] = {"reason": reason}
                if interaction_id is not None:
                    data["interactionId"] = interaction_id
                return self._node_event(run, "run.suspended", data, now, seq, token)
            case Yield(not_before=not_before, reason=reason):
                return self._node_event(
                    run,
                    "run.yielded",
                    {"notBefore": not_before, "reason": reason},
                    now,
                    seq,
                    token,
                )
            case Complete(outcome=outcome, truncated_by=truncated_by):
                completed: dict[str, JsonValue] = {"outcome": outcome}
                if truncated_by is not None:
                    completed["truncatedBy"] = truncated_by
                return self._node_event(run, "run.completed", completed, now, seq, token)
            case Fail(error_type=error_type, message=message, servers=servers, memory_limit_mb=memory_limit_mb):
                data: dict[str, JsonValue] = {"errorType": error_type}
                if message:
                    data["message"] = message
                if servers:
                    data["servers"] = list(servers)
                if memory_limit_mb is not None:
                    data["memoryLimitMb"] = memory_limit_mb
                return self._node_event(run, "run.failed", data, now, seq, token)
            case Cancelled():
                return self._node_event(run, "run.cancelled", {}, now, seq, token)
            case Quarantine(attempts=attempts, last_error_type=last_error_type):
                data = {"attempts": attempts}
                if last_error_type:
                    data["lastErrorType"] = last_error_type
                return self._node_event(run, "run.quarantined", data, now, seq, token)
            case _:  # pragma: no cover - exhaustive
                raise EventRejected(f"unknown transition {transition!r}")

    def _apply_transition(
        self, run: _Run, transition: Transition | None, now: datetime
    ) -> None:
        if transition is None:
            return
        run.lease_expires_at = None
        run.attempt_id = None
        run.suspended_for = None
        match transition:
            case Suspend(reason=reason):
                run.status = "suspended"
                run.suspended_for = reason
                run.attempts = 0
            case Yield(not_before=not_before, checkpoint_ref=ref):
                run.status = "queued"
                run.attempts = 0
                run.not_before = max(now, _parse_ts(not_before))
                run.checkpoint_ref = ref
            case Complete():
                run.status = "completed"
            case Fail():
                run.status = "failed"
            case Cancelled():
                run.status = "cancelled"
            case Quarantine():
                run.status = "quarantined"
            case _:  # pragma: no cover - exhaustive
                pass

    def _do_cancel(self, run: _Run, by: str, now: datetime) -> None:
        head = self._head(run.stream_id)
        batch = [
            self._node_event(run, "run.cancel_requested", {"by": by}, now, head + 1)
        ]
        if run.status in ("queued", "suspended"):
            batch.append(
                self._node_event(run, "run.cancelled", {"by": by}, now, head + 2)
            )
            run.status = "cancelled"
            run.open_interactions.clear()
        run.cancel_requested = True
        self._write_batch(run.stream_id, batch)
        if run.status == "cancelled":
            self._join(run, ("cancelled", "cancelled", None), now)

    def _do_spawn(self, args: Mapping[str, JsonValue], now: datetime) -> None:
        parent = self._runs[str(args["parentRunId"])]
        spec = args["spec"]
        assert isinstance(spec, dict)
        child = _Run(
            run_id=str(args["childRunId"]),
            org_id=parent.org_id,
            session_id=parent.session_id,
            stream_id=str(args["childRunId"]),
            spec=spec,
            input_text=str(args["goal"]),
            parent_run_id=parent.run_id,
            depth=parent.depth + 1,
            request_digest=str(args["requestDigest"]),
            spec_pinned=True,
            role_routes=parent.role_routes,
            priority_class=parent.priority_class,
        )
        admitted: dict[str, JsonValue] = {
            "runKind": "child",
            "principalRef": spec["principalRef"],
            "parentRunId": parent.run_id,
        }
        self._write_batch(
            child.stream_id,
            [
                self._node_event(child, "run.admitted", admitted, now, 1),
                self._node_event(
                    child,
                    "run.spec_pinned",
                    {"specDigest": canonical_digest(spec), "spec": spec},
                    now,
                    2,
                ),
            ],
        )
        spawned: dict[str, JsonValue] = {
            "childRunId": child.run_id,
            "invocationId": args["invocationId"],
            "grantDigest": args["grantDigest"],
        }
        if "taskId" in args:
            spawned["taskId"] = args["taskId"]
        if "model" in args:
            spawned["model"] = args["model"]
        self._write_batch(
            parent.stream_id,
            [
                self._node_event(
                    parent, "child.spawned", spawned, now, self._head(parent.stream_id) + 1
                )
            ],
        )
        self._runs[child.run_id] = child
        parent.children_spawned += 1
        parent.children_open += 1

    def _join(self, child: _Run, ending: _Ending, now: datetime) -> None:
        """Node's side of a child ending: ``child.completed`` on the parent's stream, and the
        parent woken when it waits on its last child."""
        if child.parent_run_id is None:
            return
        parent = self._runs[child.parent_run_id]
        parent.children_open = max(0, parent.children_open - 1)
        if parent.status in TERMINAL_STATUSES:
            return
        outcome, error_type, result = ending
        if result is None:
            failure = error_type or "worker_failed"
            ref = inline_ref(canonical_json({"error": failure, "output": "", "success": False}))
            data: dict[str, JsonValue] = {
                "childRunId": child.run_id,
                "resultRef": dump_contract(ref),
                "labels": {"integrity": "trusted"},
                "outcome": outcome,
                "errorType": failure,
            }
        else:
            data = {
                "childRunId": child.run_id,
                "resultRef": dump_contract(result.result_ref),
                "labels": dict(result.labels),
                "outcome": outcome,
            }
        head = self._head(parent.stream_id)
        batch = [self._node_event(parent, "child.completed", data, now, head + 1)]
        if (
            parent.children_open == 0
            and parent.status == "suspended"
            and parent.suspended_for == "join"
        ):
            batch.append(
                self._node_event(parent, "run.resumed", {"cause": "child_done"}, now, head + 2)
            )
            parent.status = "queued"
            parent.suspended_for = None
            parent.not_before = now
        self._write_batch(parent.stream_id, batch)

    def _do_queue(self, args: Mapping[str, JsonValue], now: datetime) -> int:
        run = self._runs[str(args["runId"])]
        seq = run.input_seq + 1
        ref = inline_ref(str(args["text"]).encode("utf-8"))
        data: dict[str, JsonValue] = {"messageSeq": seq, "textRef": dump_contract(ref)}
        for key in ("attachments", "recordIds"):
            if key in args:
                data[key] = args[key]
        event = self._node_event(
            run,
            "input.queued",
            data,
            now,
            self._head(run.stream_id) + 1,
        )
        run.input_seq = seq
        run.pending_inputs.append(seq)
        self._write_batch(run.stream_id, [event])
        return seq

    def _do_resolve(self, args: Mapping[str, JsonValue], now: datetime) -> None:
        run = self._runs[str(args["runId"])]
        interaction_id = str(args["interactionId"])
        head = self._head(run.stream_id)
        batch = [
            self._node_event(
                run,
                "interaction.resolved",
                {
                    "interactionId": interaction_id,
                    "decision": args["decision"],
                    "by": args["by"],
                    "channel": args["channel"],
                    **({"answerRef": args["answerRef"]} if args.get("answerRef") is not None else {}),
                },
                now,
                head + 1,
            )
        ]
        if args.get("scope") is not None:
            # Node's rule (UX1): the grant is written with the decision, in one commit, naming what the worker offered
            # (FLOWGRANT).
            batch.append(
                self._node_event(
                    run,
                    "interaction.granted",
                    {
                        "interactionId": interaction_id,
                        "scope": args["scope"],
                        **self._offered_grant(run, interaction_id, args["by"]),
                    },
                    now,
                    head + 1 + len(batch),
                    critical=False,
                )
            )
        del run.open_interactions[interaction_id]
        if run.status == "suspended" and not run.open_interactions:
            batch.append(
                self._node_event(
                    run, "run.resumed", {"cause": "resolved"}, now, head + 1 + len(batch)
                )
            )
            run.status = "queued"
            run.not_before = now
            run.priority_class = after_approval(run.priority_class)
        self._write_batch(run.stream_id, batch)

    def _do_inject(
        self, stream_id: str, doc: dict[str, JsonValue], now: datetime
    ) -> JournalEvent:
        run_id = doc.get("runId")
        run = self._runs.get(str(run_id)) if run_id is not None else None
        event = JournalEvent.model_validate(
            {
                "org_id": doc.get("orgId", run.org_id if run else "org"),
                "session_id": doc.get(
                    "sessionId", run.session_id if run else stream_id
                ),
                "stream_id": stream_id,
                "seq": self._head(stream_id) + 1,
                "run_id": run_id,
                "type": doc["type"],
                "schema_version": doc.get("schemaVersion", 1),
                "critical": doc.get("critical", True),
                "ts": db_timestamp(now),
                "writer": doc.get("writer", "node"),
                "data": doc.get("data", {}),
            }
        )
        self._write_batch(stream_id, [event])
        return event

    # ---- op log ----

    def _log(self, op: str, args: dict[str, JsonValue]) -> None:
        if self._oplog is None or self._replay_now is not None:
            return
        line = json.dumps(
            {"op": op, "now": self._now().isoformat(), "args": args}, sort_keys=True
        )
        with self._oplog.open("a", encoding="utf-8") as fh:
            _ = fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())

    def _replay(self, oplog: Path) -> None:
        for raw in oplog.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            entry: dict[str, Any] = json.loads(raw)
            self._replay_now = datetime.fromisoformat(entry["now"])
            try:
                self._replay_op(entry["op"], entry["args"])
            finally:
                self._replay_now = None

    def _replay_op(self, op: str, args: dict[str, Any]) -> None:
        now = self._now()
        if op == "admit":
            self._do_admit(args, now)
        elif op == "claim":
            _ = self._do_claim(self._runs[args["runId"]], now)
        elif op == "commit":
            _ = self._do_commit(_COMMIT.validate_python(args["request"]), now)
        elif op == "renew":
            run = self._runs[_TOKEN.validate_python(args["token"]).run_id]
            run.lease_expires_at = now + timedelta(milliseconds=run.lease_ms)
        elif op == "release":
            self._requeue(self._runs[_TOKEN.validate_python(args["token"]).run_id], now)
        elif op == "sweep":
            self._do_sweep(self._runs[args["runId"]], now)
        elif op == "cancel":
            self._do_cancel(self._runs[args["runId"]], args["by"], now)
        elif op == "resolve":
            self._do_resolve(args, now)
        elif op == "queue":
            _ = self._do_queue(args, now)
        elif op == "spawn":
            self._do_spawn(args, now)
        elif op == "inject":
            _ = self._do_inject(args["streamId"], args["event"], now)
        else:
            raise ValueError(f"unknown op {op!r} in journal op log")


class InMemoryContentStore:
    """Content-addressed blobs; ``directory`` persists them across processes."""

    def __init__(self, directory: Path | None = None) -> None:
        self._blobs: dict[str, bytes] = {}
        self._directory = directory
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    async def put(self, data: bytes) -> ContentRef:
        digest = hashlib.sha256(data).hexdigest()
        ref = f"cas:{digest}"
        self._blobs[ref] = data
        if self._directory is not None:
            path = self._directory / digest
            if not path.exists():
                tmp = path.with_suffix(".tmp")
                _ = tmp.write_bytes(data)
                _ = tmp.replace(path)
        return ContentRef(ref=ref, hash=f"sha256:{digest}", size=len(data))

    async def put_sealed(self, data: bytes) -> ContentRef:
        """Its refs never carry the bytes: the same ``put``."""
        return await self.put(data)

    async def get(self, ref: ContentRef) -> bytes:
        # Node's own content (a steering message) arrives inline.
        data = inline_bytes(ref)
        if data is None:
            data = self._blobs.get(ref.ref)
        if data is None and self._directory is not None and ref.ref.startswith("cas:"):
            path = self._directory / ref.ref.removeprefix("cas:")
            data = path.read_bytes() if path.exists() else None
        if data is None:
            raise ContentMissing(ref.ref)
        if (
            len(data) != ref.size
            or f"sha256:{hashlib.sha256(data).hexdigest()}" != ref.hash
        ):
            raise ContentMissing(f"{ref.ref}: hash or size mismatch")
        return data

    def drop(self, ref: ContentRef) -> None:
        """Test hook: lose a blob."""
        _ = self._blobs.pop(ref.ref, None)
        if self._directory is not None and ref.ref.startswith("cas:"):
            (self._directory / ref.ref.removeprefix("cas:")).unlink(missing_ok=True)


class InMemoryCheckpointStore:
    def __init__(self, directory: Path | None = None) -> None:
        self._checkpoints: dict[str, Checkpoint] = {}
        self._directory = directory
        self._n = 0
        if directory is not None:
            directory.mkdir(parents=True, exist_ok=True)

    async def save(self, checkpoint: Checkpoint) -> str:
        body = checkpoint.model_dump_json().encode("utf-8")
        ref = f"ckpt:{checkpoint.run_id}:{checkpoint.seq}:{hashlib.sha256(body).hexdigest()[:16]}"
        self._checkpoints[ref] = checkpoint
        if self._directory is not None:
            _ = (
                self._directory / hashlib.sha256(ref.encode()).hexdigest()
            ).write_bytes(body)
        return ref

    async def load(self, ref: str) -> Checkpoint | None:
        found = self._checkpoints.get(ref)
        if found is None and self._directory is not None:
            path = self._directory / hashlib.sha256(ref.encode()).hexdigest()
            if path.exists():
                found = Checkpoint.model_validate_json(path.read_bytes())
        return found


class InMemoryLiveSink:
    """Keeps frames per run and drops frames from a fence lower than one already seen."""

    def __init__(self) -> None:
        self.frames: list[LiveFrame] = []
        self.dropped: list[LiveFrame] = []
        self._max_fence: dict[str, int] = {}

    async def publish(self, frame: LiveFrame) -> None:
        highest = self._max_fence.get(frame.run_id, 0)
        if frame.fence < highest:
            self.dropped.append(frame)
            return
        self._max_fence[frame.run_id] = frame.fence
        self.frames.append(frame)


class InMemoryLiveBus:
    """``LiveBusPort`` for one process: a bounded deque of frames per run."""

    def __init__(self, maxlen: int = LIVE_BUFFER_FRAMES, *, cap: LiveRateCap | None = None) -> None:
        self._maxlen = maxlen
        self._runs: dict[str, deque[LiveFrame]] = {}
        # SEC-DOS-02: a run's frames past its rate are dropped here, as the Redis bus drops them.
        self.cap = cap or LiveRateCap()

    async def publish(self, frame: LiveFrame) -> None:
        if not self.cap.admit(frame.run_id):
            return
        buffer = self._runs.get(frame.run_id)
        if buffer is None:
            buffer = self._runs[frame.run_id] = deque(maxlen=self._maxlen)
        buffer.append(frame)

    async def read(self, run_id: str) -> list[LiveFrame]:
        return list(self._runs.get(run_id, ()))


__all__ = [
    "DEFAULT_LEASE_MS",
    "LIVE_BUFFER_FRAMES",
    "InMemoryCheckpointStore",
    "InMemoryContentStore",
    "InMemoryJournal",
    "InMemoryLiveBus",
    "InMemoryLiveSink",
    "LegacyImport",
    "db_timestamp",
]
