"""``JournalPort`` over Node's ``/internal/harness`` routes (REPORT 6.1.4, 6.1.8).

Claims present the per-deployment worker credential; every later call
presents the attempt token Node returned for that claim. Tokens stay inside
this adapter, keyed by attempt: ``Claim``/``AttemptToken`` carry ids only.

Transitions map onto Node's run statuses: suspend -> ``waiting_approval`` for
an approval and ``paused`` otherwise, yield -> ``queued``, quarantine ->
``failed``; Node journals the matching ``run.*`` event from the detail.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast
from urllib.parse import quote

from pydantic import JsonValue, ValidationError

from app.harness.contracts.generated.pipeshub.errors import ERROR_CODES, WIRE_PREFIX
from app.harness.contracts.state import HarnessRun, PinnedRunSpec
from app.harness.journal_node.http import NodeServiceClient
from app.harness.placement import deployment_region
from app.harness.worker.capabilities import WORKER_CAPABILITIES
from harness_core.contracts import dump_contract
from harness_core.contracts.generated.node_capabilities import NodeCapabilities
from harness_core.journal.children import (
    ChildAdmitted,
    ChildRefusal,
    ChildRefused,
    JoinReady,
)
from harness_core.journal.types import (
    AttemptToken,
    Cancelled,
    Claim,
    CommandConflict,
    CommitReceipt,
    CommitRejected,
    CompactAsk,
    Complete,
    ContentMissing,
    EventRejected,
    Fail,
    InputArtifactRef,
    InputPending,
    InteractionResolved,
    JournalError,
    JournalEvent,
    JournalUnavailable,
    LeaseGrant,
    OwnershipLost,
    Quarantine,
    RunNotClaimable,
    Suspend,
    WriterNotAllowed,
    Yield,
)
from harness_core.logs import HarnessLog
from harness_core.priority import PriorityClass, parse_class
from harness_core.region import UNSET_REGION

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from app.harness.adapters.content_store import SealedContentPort
    from app.harness.contracts.generated.pipeshub.errors import ErrorCode
    from app.harness.contracts.state import WorkerCredentialProvider
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.contracts.generated.worker_capabilities import WorkerCapabilities
    from harness_core.journal.children import ChildRequest
    from harness_core.journal.types import CommitRequest, NewEvent, Transition
    from harness_core.memories.ports import MemorySource

ClaimRole = Literal["inline", "dispatched"]
INTERNAL_PREFIX = "/api/v1/agent-harness/internal"
JOURNAL_PAGE = 500
# A run of an older harness major cannot run here; it is failed, not retried.
HARNESS_V1_RETIRED = "harness_v1_retired"

_NOT_CLAIMABLE: frozenset[ErrorCode] = frozenset(
    {"RUN_NOT_CLAIMABLE", "DEADLINE_EXCEEDED", "SPEC_UNRESOLVABLE", "SPEC_NOT_PINNED"}
)
# A run Node no longer has answers 404, which `_classify` already treats as lost; a run whose conversation was
# deleted is lost too, under its own code so the log names the cause (QA21-F4).
_LOST: frozenset[ErrorCode] = frozenset(
    {"STALE_ATTEMPT", "DEADLINE_EXCEEDED", "REVISION_CONFLICT", "CONVERSATION_DELETED"}
)
# Node's child admission refusals (P6.1) by their reason code.
_CHILD_REFUSALS = {refusal.value.upper(): refusal for refusal in ChildRefusal}
_ACCESS_GROUPS = frozenset({"model_access", "model_account", "engine_profile"})
_ENGINE_CONFLICTS = frozenset({"ENGINE_UNAVAILABLE", "ENGINE_OFF"})


def _access_refusal(code: str) -> bool:
    """A refusal of a child's model access, model account or engine (Node's catalog groups, no engine image, or an
    engine an admin turned off since the turn started, WP-111)."""
    entry = ERROR_CODES.get(code)  # type: ignore[call-overload]
    return code in _ENGINE_CONFLICTS or (entry is not None and entry.group in _ACCESS_GROUPS)
LOGGER = "app.harness.journal_node"
# Attempts kept for settling after their run left this worker; far more than a worker holds at once.
SETTLING_ATTEMPTS = 256


def _ms(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


@dataclass(frozen=True)
class NodeClaim(Claim):
    """A claim plus the admitted run snapshot the assembler reads (actor, agent, input),
    and the attempt's sealed content route (its token stays in ``NodeJournal``)."""

    run: HarnessRun | None = None
    content: SealedContentPort | None = field(default=None, compare=False, repr=False)
    # The run's ledger account, reached with the attempt's token (P6.5).
    budget: BudgetAccounts | None = field(default=None, compare=False, repr=False)
    # The run's governed memory (P7.3): its pinned memories and its proposals, with the attempt's token.
    memories: MemorySource | None = field(default=None, compare=False, repr=False)
    # Where suggested next questions go after the answer (UX5), with the attempt's token.
    followups: AttemptFollowups | None = field(default=None, compare=False, repr=False)
    # The attempt's routes under ``/runs/{runId}``: its session workspace for ``run_code`` (SLASH3), with its token.
    attempt_route: AttemptRoute | None = field(default=None, compare=False, repr=False)


@dataclass
class _Attempt:
    org_id: str
    run_id: str
    stream_id: str
    attempt_id: str
    fence: int
    token: str
    head: int

    def log(self) -> HarnessLog:
        return HarnessLog.named(
            LOGGER,
            orgId=self.org_id,
            runId=self.run_id,
            attemptId=self.attempt_id,
            fence=self.fence,
        )


class _AttemptContent:
    """``SealedContentPort`` for one attempt: the journal adds the attempt's token."""

    def __init__(self, journal: NodeJournal, token: AttemptToken) -> None:
        self._journal = journal
        self._token = token

    async def put(self, data: bytes) -> str:
        return await self._journal.put_content(self._token, data)

    async def get(self, ref: str) -> bytes:
        return await self._journal.get_content(self._token, ref)


class AttemptFollowups:
    """Suggested next questions handed to Node after the run's answer (UX5), with the attempt's token."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route

    async def deliver(self, items: Sequence[str]) -> bool:
        response = await self._route.post(
            "/followups", body={"items": [{"text": item} for item in items]}, operation="followups"
        )
        return response.status_code == 200


class AttemptRoute:
    """Authenticated calls under ``/runs/{runId}`` for one attempt (P3.2 workspaces).

    The journal keeps the attempt's token (renew refreshes it) and adds the
    attempt's id and fence; callers see responses, never the token.
    """

    def __init__(self, journal: NodeJournal, token: AttemptToken) -> None:
        self._journal = journal
        self._token = token

    @property
    def org_id(self) -> str:
        return self._journal.attempt_org(self._token)

    @property
    def run_id(self) -> str:
        return self._token.run_id

    def log(self, name: str) -> HarnessLog:
        return self._journal.attempt_log(self._token, name)

    def secret_values(self) -> tuple[str, ...]:
        """The attempt's bearer token as it is now (renew refreshes it): what a scan for leaked secrets
        looks for (P5.9b). Never logged, never sent anywhere."""
        return (self._journal.attempt_secret(self._token),)

    async def get(
        self, suffix: str, *, params: dict[str, str | int] | None = None, operation: str
    ) -> httpx.Response:
        return await self._journal.attempt_request(
            self._token, "GET", suffix, params=params, operation=operation
        )

    async def post(
        self, suffix: str, *, body: dict[str, JsonValue], operation: str
    ) -> httpx.Response:
        return await self._journal.attempt_request(
            self._token, "POST", suffix, body=body, operation=operation
        )


def error_code(response: httpx.Response) -> str:
    """Node's error code without its ``HTTP_``/``AGENT_HARNESS_`` prefixes; "" if none."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        return ""
    for prefix in ("HTTP_", WIRE_PREFIX):
        code = code.removeprefix(prefix)
    return code


# Node's refusal sentences are a line or two; a longer body is not one of them.
_ERROR_MESSAGE_MAX = 300


def error_message(response: httpx.Response) -> str:
    """Node's own sentence for an error (its static text, with the names it refused); "" if none."""
    try:
        body = response.json()
    except ValueError:
        return ""
    error = body.get("error") if isinstance(body, dict) else None
    message = error.get("message") if isinstance(error, dict) else None
    return message[:_ERROR_MESSAGE_MAX] if isinstance(message, str) else ""


def node_accepts(advertised: object, log: HarnessLog) -> frozenset[str]:
    """What the Node of a claim accepts beyond every v2 Node (UX2). An older Node sends nothing: nothing more."""
    if advertised is None:
        return frozenset()
    try:
        return frozenset(name.root for name in NodeCapabilities.model_validate(advertised).features)
    except ValidationError:
        log.warning("journal.node_capabilities_unreadable", reasonCode="node_capabilities_invalid")
        return frozenset()


def _trigger_context(run: HarnessRun) -> dict[str, JsonValue] | None:
    """What fired a scheduled run (TR-03), as its wire JSON; ``None`` for any other run."""
    trigger = run.input.trigger if run.input is not None else None
    if trigger is None:
        return None
    return cast("dict[str, JsonValue]", trigger.model_dump(mode="json", by_alias=True, exclude_none=True))


def _compact_ask(run: HarnessRun) -> CompactAsk | None:
    compact = run.input.compact if run.input is not None else None
    return CompactAsk(compact.focus) if compact is not None else None


def _event(document: dict[str, Any]) -> JournalEvent:
    return JournalEvent.from_envelope(document)


def _event_input(event: NewEvent) -> dict[str, JsonValue]:
    wire: dict[str, JsonValue] = {
        "type": event.type,
        "schemaVersion": 1,
        "critical": event.critical,
        "visibility": event.visibility,
        "data": event.data,
    }
    if event.labels is not None:
        wire["labels"] = event.labels
    return wire


def transition_wire(transition: Transition) -> dict[str, JsonValue]:
    """The Node transition for a worker ``Transition`` (the S1 status plus its detail)."""
    match transition:
        case Suspend(reason=reason, interaction_id=interaction_id):
            detail: dict[str, JsonValue] = {"kind": "suspend", "reason": reason}
            if interaction_id is not None:
                detail["interactionId"] = interaction_id
            status = "waiting_approval" if reason == "approval" else "paused"
            return {"status": status, "detail": detail}
        case Yield(not_before=not_before, reason=reason, checkpoint_ref=ref):
            detail = {"kind": "yield", "reason": reason, "notBefore": not_before}
            if ref is not None:
                detail["checkpointRef"] = ref
            return {"status": "queued", "detail": detail}
        case Complete(outcome=outcome, leaves_input=leaves_input, child_result=result, truncated_by=truncated_by):
            wire: dict[str, JsonValue] = {
                "status": "completed",
                "outcome": "needs_input" if outcome == "needs_input" else "answer",
            }
            if leaves_input:
                wire["leavesInput"] = True
            if truncated_by is not None:
                wire["truncatedBy"] = truncated_by
            if result is not None:
                wire["childResult"] = {
                    "resultRef": dump_contract(result.result_ref),
                    "labels": dict(result.labels),
                }
            return wire
        case Fail(error_type=error_type, message=message, servers=servers, memory_limit_mb=memory_limit_mb):
            wire = {"status": "failed", "errorType": error_type}
            failed: dict[str, JsonValue] = {"kind": "fail"}
            if message:
                failed["message"] = message
            if servers:
                failed["servers"] = list(servers)
            if memory_limit_mb is not None:
                failed["memoryLimitMb"] = memory_limit_mb
            wire["detail"] = failed
            return wire
        case Cancelled():
            return {"status": "cancelled"}
        case Quarantine(attempts=attempts, last_error_type=last_error_type):
            detail = {"kind": "quarantine", "attempts": attempts}
            if last_error_type:
                detail["lastErrorType"] = last_error_type
            return {
                "status": "failed",
                "errorType": last_error_type or "quarantined",
                "detail": detail,
            }
        case _:  # pragma: no cover - exhaustive
            raise CommitRejected(f"unknown transition {transition!r}")


class NodeJournal:
    """One process's journal client; safe for concurrent attempts."""

    def __init__(
        self,
        *,
        base_url: str,
        credentials: WorkerCredentialProvider,
        role: ClaimRole = "dispatched",
        lease_ms: int = 60_000,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        capabilities: WorkerCapabilities = WORKER_CAPABILITIES,
        region: str | None = None,
    ) -> None:
        self._credentials = credentials
        # OWD-12: Node hands this worker only runs whose data lives in its region. A worker with no region set sends no
        # `workerRegion`: a control plane from before regions refuses a claim with a field it does not know (UX2;
        # WP88-FU-2 found the claim refused). It still fails closed: the advertisement names `region_aware`, so a
        # Node that knows regions places such a worker as no region's (`default`), never as its own.
        resolved = region if region is not None else deployment_region()
        self._placement: dict[str, JsonValue] = {} if resolved == UNSET_REGION else {"workerRegion": resolved}
        self._role = role
        self._lease_ms = lease_ms
        self._capabilities = cast(
            "dict[str, JsonValue]", capabilities.model_dump(mode="json", by_alias=True)
        )
        self._owns_client = http_client is None
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.journal"
        )
        self._attempts: dict[tuple[str, str, int], _Attempt] = {}
        self._latest: dict[str, _Attempt] = {}
        # Attempts whose run left this worker: still able to settle what they reserved and to hand over
        # what follows their answer (UX5), never to commit; Node bounds them by the token's own expiry.
        self._settling: OrderedDict[tuple[str, str, int], _Attempt] = OrderedDict()

    @property
    def lease_ms(self) -> int:
        return self._lease_ms

    async def aclose(self) -> None:
        if self._owns_client:
            await self._node.aclose()

    # ---- claim ----

    async def claim(
        self,
        run_id: str | None = None,
        *,
        worker_id: str,
        org_id: str | None = None,
        above: PriorityClass | None = None,
    ) -> NodeClaim | None:
        """``above``: a full worker that will preempt a run of that class asks only for a higher one (G-45); sent
        only once a claim from this Node named ``priority_preemption``."""
        log = HarnessLog.named(LOGGER, orgId=org_id, runId=run_id, role=self._role)
        started = time.monotonic()
        credential = await self._credentials.worker_credential()
        if run_id is None:
            response = await self._send(
                "POST",
                f"{INTERNAL_PREFIX}/runs/claim",
                credential,
                json={
                    "workerId": worker_id,
                    "leaseDurationMs": self._lease_ms,
                    "role": self._role,
                    "workerCapabilities": self._capabilities,
                    **self._placement,
                    **({"aboveClass": above} if above is not None else {}),
                },
                log=log,
                operation="journal.claim",
            )
            if response.status_code == 204:
                log.debug("journal.claim_empty", ms=_ms(started))
                return None
        else:
            if org_id is None:
                raise ValueError("claiming a run by id needs its org_id")
            response = await self._send(
                "POST",
                f"{INTERNAL_PREFIX}/runs/{quote(run_id, safe='')}/claim",
                credential,
                json={
                    "orgId": org_id,
                    "workerId": worker_id,
                    "leaseDurationMs": self._lease_ms,
                    "workerCapabilities": self._capabilities,
                    **self._placement,
                },
                log=log,
                operation="journal.claim",
            )
        if response.is_error:
            code = error_code(response)
            if response.status_code in (404, 409) or code in _NOT_CLAIMABLE:
                log.info("journal.claim_not_claimable", status=response.status_code, reason=code or None)
                raise RunNotClaimable(f"{run_id or 'next'}: {code or response.status_code}")
            log.warning("journal.claim_refused", status=response.status_code, reason=code or None)
            raise self._unavailable_or(response, JournalError)
        try:
            body = response.json()
        except ValueError as exc:
            log.warning("journal.claim_unreadable", status=response.status_code, errorType=type(exc).__name__)
            raise JournalError("claim response is not JSON") from exc
        claim = await self._adopt(body)
        if claim is None:
            if run_id is not None:
                raise RunNotClaimable(f"{run_id}: {HARNESS_V1_RETIRED}")
            return None
        log.bind(
            orgId=claim.org_id,
            sessionId=claim.session_id,
            runId=claim.run_id,
            attemptId=claim.attempt_id,
            fence=claim.fence,
        ).debug(
            "journal.claim",
            attempt=claim.attempt,
            lastSeq=claim.last_seq,
            leaseMs=self._lease_ms,
            resumed=claim.checkpoint_ref is not None,
            ms=_ms(started),
        )
        return claim

    async def _adopt(self, body: object) -> NodeClaim | None:
        # Validated whole before the token is kept: an answer this build cannot read must not strand an attempt.
        # Its lease lapses and the run is claimed again, by a worker that can read it or until its deadline.
        try:
            fields = cast("dict[str, Any]", body)
            run = HarnessRun.model_validate(fields["run"])
            token = fields["attemptToken"]["token"]
            last = cast("dict[str, int]", fields["journal"]["lastSeq"])
            stream_id, head = next(iter(last.items()), (run.session_id, 0))
            raw_pin = fields.get("pinned")
            pinned = (
                PinnedRunSpec.model_validate(raw_pin) if run.harness_major == 2 and raw_pin is not None else None
            )
        except (AttributeError, KeyError, TypeError, ValidationError) as exc:
            raise JournalError("invalid claim response") from exc
        if run.attempt_id is None or not isinstance(token, str):
            raise JournalError("claim response names no attempt")
        attempt = _Attempt(
            run.org_id, run.run_id, stream_id, run.attempt_id, run.fence, token, head
        )
        self._remember(attempt)
        if pinned is None:
            await self._retire_v1(run, attempt)
            return None
        token = AttemptToken(run.run_id, run.attempt_id, run.fence)
        return NodeClaim(
            run_id=run.run_id,
            org_id=run.org_id,
            session_id=run.session_id,
            stream_id=stream_id,
            attempt_id=run.attempt_id,
            attempt=run.attempts or run.fence,
            fence=run.fence,
            lease_ms=self._lease_ms,
            last_seq=head,
            spec=pinned.run_spec(),
            input_text=run.input.query if run.input is not None else "",
            checkpoint_ref=run.checkpoint_ref,
            capabilities=cast("dict[str, JsonValue]", pinned.capabilities),
            input_artifacts=tuple(
                InputArtifactRef(item.artifact_id, item.version)
                for item in (run.input.input_artifacts if run.input is not None else ())
            ),
            run=run,
            content=_AttemptContent(self, token),
            budget=self._budget(token),
            memories=self._memories(token),
            followups=AttemptFollowups(self.attempt_route(token)),
            attempt_route=self.attempt_route(token),
            accepts=node_accepts(body.get("nodeCapabilities"), attempt.log()),
            compact=_compact_ask(run),
            trigger_context=_trigger_context(run),
            priority_class=parse_class(body.get("priorityClass")),
            skipped_connects=_skipped_connects(body.get("skippedConnects")),
            deadline_at_ms=run.absolute_deadline_at,
            depth=_tree_depth(body.get("treeDepth")),
            # Every execution store writes the claimed run's ``updatedAt`` with its own clock in the claim's
            # transaction; ``leaseExpiresAt`` less the lease is not that reading, as the deadline caps the lease.
            store_now_ms=run.updated_at,
        )

    def _budget(self, token: AttemptToken) -> BudgetAccounts:
        # Imported here: the ledger adapter is built on this module's attempt routes.
        from app.harness.access.budget import AttemptBudgetAccounts

        return AttemptBudgetAccounts(self.attempt_route(token))

    def _memories(self, token: AttemptToken) -> MemorySource:
        # Imported here: the memory adapter is built on this module's attempt routes.
        from app.harness.memories import NodeMemorySource

        return NodeMemorySource(self.attempt_route(token))

    async def _retire_v1(self, run: HarnessRun, attempt: _Attempt) -> None:
        """Durable v1 execution was the synthetic HTTP path, removed in S5 (D-16)."""
        log = attempt.log()
        log.warning("journal.v1_retired", reason=HARNESS_V1_RETIRED)
        response = await self._send(
            "POST",
            f"{INTERNAL_PREFIX}/runs/{quote(run.run_id, safe='')}/commit",
            attempt.token,
            json={
                "commandId": f"{attempt.attempt_id}:{HARNESS_V1_RETIRED}",
                "attemptId": attempt.attempt_id,
                "fence": attempt.fence,
                "expectedRevision": run.revision,
                "status": "failed",
                "events": [{"type": "run_failed", "data": {"errorType": HARNESS_V1_RETIRED}}],
                "outbox": [],
            },
            log=log,
            operation="journal.commit",
        )
        if response.is_error and response.status_code not in (404, 409):
            raise self._unavailable_or(response, JournalError)

    # ---- after the claim ----

    async def commit(self, request: CommitRequest) -> CommitReceipt:
        attempt = self._attempt(request.token)
        body: dict[str, JsonValue] = {
            "commandId": request.command_id,
            "attemptId": attempt.attempt_id,
            "fence": attempt.fence,
            "events": [_event_input(event) for event in request.events],
            "legacyEvents": [],
            "outbox": [],
        }
        if request.transition is not None:
            body["transition"] = transition_wire(request.transition)
        log = attempt.log().bind(commandId=request.command_id)
        started = time.monotonic()
        response = await self._send(
            "POST",
            f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/commit",
            attempt.token,
            json=body,
            log=log,
            operation="journal.commit",
        )
        if response.is_error:
            raise self._commit_error(response, log, "journal.commit")
        payload = response.json()
        events = tuple(_event(document) for document in payload.get("events") or [])
        if events:
            attempt.head = max(attempt.head, events[-1].seq)
        log.info(
            "journal.commit",
            seq=events[-1].seq if events else None,
            firstSeq=events[0].seq if events else None,
            events=len(request.events),
            transition=getattr(request.transition, "kind", None),
            replayed=payload.get("replayed") is True,
            ms=_ms(started),
        )
        return CommitReceipt(
            command_id=request.command_id,
            stream_id=attempt.stream_id,
            events=events,
            replayed=payload.get("replayed") is True,
        )

    async def read(
        self, stream_id: str, *, after_seq: int = 0, limit: int | None = None
    ) -> list[JournalEvent]:
        attempt = self._latest.get(stream_id)
        if attempt is None:
            raise JournalError(f"no attempt of this worker reads stream {stream_id!r}")
        log = attempt.log()
        started = time.monotonic()
        events: list[JournalEvent] = []
        after = after_seq
        while limit is None or len(events) < limit:
            page_size = JOURNAL_PAGE if limit is None else min(JOURNAL_PAGE, limit - len(events))
            # Node restarting is transient, like a commit that cannot reach it.
            response = await self._send(
                "GET",
                f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/events",
                attempt.token,
                params={
                    "attemptId": attempt.attempt_id,
                    "fence": attempt.fence,
                    "after": after,
                    "limit": page_size,
                    "scope": "stream",
                },
                log=log,
                operation="journal.read",
            )
            if response.is_error:
                raise self._commit_error(response, log, "journal.read")
            page = [_event(document) for document in response.json().get("events") or []]
            events.extend(page)
            if len(page) < page_size:
                break
            after = page[-1].seq
        if events:
            attempt.head = max(attempt.head, events[-1].seq)
        log.debug("journal.read", after=after_seq, events=len(events), seq=attempt.head, ms=_ms(started))
        return events

    async def last_seq(self, stream_id: str) -> int:
        attempt = self._latest.get(stream_id)
        if attempt is None:
            raise JournalError(f"no attempt of this worker reads stream {stream_id!r}")
        _ = await self.read(stream_id, after_seq=attempt.head)
        return attempt.head

    async def renew(self, token: AttemptToken) -> LeaseGrant:
        attempt = self._attempt(token)
        log = attempt.log()
        started = time.monotonic()
        response = await self._send(
            "POST",
            f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/renew",
            attempt.token,
            json={
                "attemptId": attempt.attempt_id,
                "fence": attempt.fence,
                "leaseDurationMs": self._lease_ms,
            },
            log=log,
            operation="journal.renew",
        )
        if response.is_error:
            raise self._commit_error(response, log, "journal.renew")
        issued = response.json().get("attemptToken")
        fresh = issued.get("token") if isinstance(issued, dict) else None
        if isinstance(fresh, str) and fresh:
            attempt.token = fresh
        log.info("journal.renew", leaseMs=self._lease_ms, tokenRefreshed=bool(fresh), ms=_ms(started))
        return LeaseGrant(self._lease_ms)

    async def release(self, token: AttemptToken, *, reason: str) -> None:
        attempt = self._attempt(token)
        log = attempt.log()
        response = await self._send(
            "POST",
            f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/release",
            attempt.token,
            json={"attemptId": attempt.attempt_id, "fence": attempt.fence, "reason": reason},
            log=log,
            operation="journal.release",
        )
        if response.is_error:
            raise self._commit_error(response, log, "journal.release")
        log.info("journal.release", reason=reason)

    async def put_content(self, token: AttemptToken, data: bytes) -> str:
        """Node seals ``data`` for this attempt's run (DP-1); returns its ``sealed:`` ref."""
        attempt = self._attempt(token)
        log = attempt.log()
        started = time.monotonic()
        response = await self._send(
            "POST",
            f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/content",
            attempt.token,
            params={"attemptId": attempt.attempt_id, "fence": attempt.fence},
            content=data,
            content_type="application/octet-stream",
            log=log,
            operation="content.put",
        )
        if response.is_error:
            raise self._commit_error(response, log, "content.put")
        ref = response.json().get("ref")
        if not isinstance(ref, str) or not ref:
            raise JournalError("content response names no ref")
        log.debug("content.sealed", ref=ref, size=len(data), ms=_ms(started))
        return ref

    async def get_content(self, token: AttemptToken, ref: str) -> bytes:
        attempt = self._attempt(token)
        log = attempt.log()
        started = time.monotonic()
        response = await self._send(
            "GET",
            f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/content",
            attempt.token,
            params={"attemptId": attempt.attempt_id, "fence": attempt.fence, "ref": ref},
            log=log,
            operation="content.get",
        )
        if response.is_error:
            raise self._commit_error(response, log, "content.get")
        log.debug("content.opened", ref=ref, size=len(response.content), ms=_ms(started))
        return response.content

    async def spawn(self, token: AttemptToken, request: ChildRequest) -> ChildAdmitted:
        """``ChildRunPort``: Node admits the child of this attempt's run (P6.1)."""
        attempt = self._attempt(token)
        log = attempt.log().bind(invocationId=request.invocation_id)
        started = time.monotonic()
        body: dict[str, JsonValue] = {
            "attemptId": attempt.attempt_id,
            "fence": attempt.fence,
            "invocationId": request.invocation_id,
            "goal": request.goal,
            "grants": request.grants.wire(),
        }
        if request.task_id is not None:
            body["taskId"] = request.task_id
        if request.definition is not None:
            body["subAgent"] = request.definition
        if request.executor is not None:
            body["executor"] = request.executor.wire()
        if request.inherited_label is not None:
            body["inheritedLabel"] = request.inherited_label
        if request.inherited_grants is not None:
            body["inheritedGrants"] = request.inherited_grants
        if request.task is not None:
            body["task"] = request.task
        if request.mcp_pins is not None:
            body["mcpPins"] = {instance: dict(tools) for instance, tools in request.mcp_pins.items()}
        response = await self._send(
            "POST",
            f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}/children",
            attempt.token,
            json=body,
            log=log,
            operation="child.spawn",
        )
        if response.is_error:
            code = error_code(response)
            refusal = _CHILD_REFUSALS.get(code)
            if refusal is not None:
                log.info("journal.child_refused", status=response.status_code, reason=refusal.value)
                raise ChildRefused(refusal, error_message(response))
            if _access_refusal(code):
                # CTL-6: a code_task child's model access or engine is refused: the call fails, the run goes on.
                log.info("journal.child_refused", status=response.status_code, reason=ChildRefusal.ACCESS_REFUSED.value, code=code)
                raise ChildRefused(ChildRefusal.ACCESS_REFUSED, error_message(response), code=code)
            raise self._commit_error(response, log, "child.spawn")
        payload = response.json()
        child = payload.get("childRunId")
        if not isinstance(child, str) or not child:
            raise JournalError("child admission names no child run")
        created = payload.get("created") is True
        log.info("journal.child_admitted", childRunId=child, created=created, ms=_ms(started))
        return ChildAdmitted(child, created)

    def attempt_route(self, token: AttemptToken) -> AttemptRoute:
        """The attempt's authenticated route for stores beyond the journal."""
        _ = self._attempt(token)
        return AttemptRoute(self, token)

    def attempt_org(self, token: AttemptToken) -> str:
        return self._attempt(token).org_id

    def attempt_secret(self, token: AttemptToken) -> str:
        return self._attempt(token).token

    def attempt_log(self, token: AttemptToken, name: str) -> HarnessLog:
        attempt = self._attempt(token)
        return HarnessLog.named(
            name,
            orgId=attempt.org_id,
            runId=attempt.run_id,
            attemptId=attempt.attempt_id,
            fence=attempt.fence,
        )

    async def attempt_request(
        self,
        token: AttemptToken,
        method: Literal["GET", "POST"],
        suffix: str,
        *,
        params: dict[str, str | int] | None = None,
        body: dict[str, JsonValue] | None = None,
        operation: str,
    ) -> httpx.Response:
        """One call under ``/runs/{runId}`` with the attempt's token, id and fence.

        Unreachable Node raises ``JournalUnavailable``; every HTTP answer is
        returned for the caller to classify.
        """
        key = (token.run_id, token.attempt_id, token.fence)
        attempt = self._settling.get(key) if key not in self._attempts else None
        attempt = attempt or self._attempt(token)
        path = f"{INTERNAL_PREFIX}/runs/{quote(attempt.run_id, safe='')}{suffix}"
        ids: dict[str, str | int] = {"attemptId": attempt.attempt_id, "fence": attempt.fence}
        if method == "GET":
            return await self._send(
                method, path, attempt.token, params={**(params or {}), **ids}, log=attempt.log(), operation=operation
            )
        return await self._send(
            method, path, attempt.token, json={**(body or {}), **ids}, log=attempt.log(), operation=operation
        )

    async def worker_request(
        self, method: Literal["GET", "POST"], suffix: str, *, body: dict[str, JsonValue], operation: str
    ) -> httpx.Response:
        """One call under the internal prefix on the worker credential (no run: the trusted cache fill, WP-100).

        Unreachable Node raises ``JournalUnavailable``; every HTTP answer is returned for the caller to classify."""
        credential = await self._credentials.worker_credential()
        log = HarnessLog.named(LOGGER, role=self._role)
        return await self._send(
            method, f"{INTERNAL_PREFIX}{suffix}", credential, json=body, log=log, operation=operation
        )

    def forget(self, token: AttemptToken) -> None:
        """Drops an attempt's token once its run left this worker for good."""
        key = (token.run_id, token.attempt_id, token.fence)
        attempt = self._attempts.pop(key, None)
        if attempt is not None and self._latest.get(attempt.stream_id) is attempt:
            del self._latest[attempt.stream_id]
        if attempt is not None:
            self._settling[key] = attempt
            while len(self._settling) > SETTLING_ATTEMPTS:
                _ = self._settling.popitem(last=False)

    # ---- plumbing ----

    def _remember(self, attempt: _Attempt) -> None:
        self._attempts[(attempt.run_id, attempt.attempt_id, attempt.fence)] = attempt
        self._latest[attempt.stream_id] = attempt

    def _attempt(self, token: AttemptToken) -> _Attempt:
        attempt = self._attempts.get((token.run_id, token.attempt_id, token.fence))
        if attempt is None:
            HarnessLog.for_token(LOGGER, token).warning(
                "journal.no_attempt_token", reason="attempt_not_claimed_here"
            )
            raise OwnershipLost("this worker holds no token for that attempt")
        return attempt

    async def _send(
        self,
        method: Literal["GET", "POST"],
        path: str,
        bearer: str,
        *,
        log: HarnessLog,
        operation: str,
        json: dict[str, JsonValue] | None = None,
        params: dict[str, str | int] | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> httpx.Response:
        def unreachable(exc: httpx.HTTPError) -> JournalUnavailable:
            log.warning("journal.unreachable", operation=operation, errorType=type(exc).__name__)
            return JournalUnavailable(f"journal unreachable: {type(exc).__name__}")

        return await self._node.send(
            method,
            path,
            bearer=bearer,
            json=json,
            params=params,
            content=content,
            content_type=content_type,
            unreachable=unreachable,
        )

    @staticmethod
    def _unavailable_or(response: httpx.Response, error: type[JournalError]) -> JournalError:
        if response.status_code == 429 or response.status_code >= 500:
            return JournalUnavailable(f"journal answered {response.status_code}")
        return error(f"journal refused: {response.status_code} {error_code(response)}")

    def _commit_error(
        self, response: httpx.Response, log: HarnessLog, operation: str
    ) -> JournalError:
        code = error_code(response)
        status = response.status_code
        error = self._classify(code, status)
        fields = {"status": status, "reason": code or None, "operation": operation}
        if isinstance(error, JournalUnavailable):
            log.warning("journal.unavailable", **fields)
        elif isinstance(error, InputPending):
            log.info("journal.input_pending", **fields)
        elif isinstance(error, JoinReady):
            log.info("journal.join_ready", **fields)
        elif isinstance(error, InteractionResolved):
            log.info("journal.interaction_resolved", **fields)
        elif isinstance(error, OwnershipLost):
            # 401 is the attempt-token authority refusing; the token is never logged.
            log.warning("journal.ownership_lost", **fields)
        else:
            log.error("journal.rejected", **fields)
        return error

    @staticmethod
    def _classify(code: str, status: int) -> JournalError:
        if status == 429 or status >= 500:
            return JournalUnavailable(f"journal answered {status}")
        if code == "IDEMPOTENCY_CONFLICT":
            return CommandConflict(code)
        if code == "INPUT_PENDING":
            return InputPending(code)
        if code == "JOIN_READY":
            return JoinReady(code)
        if code == "INTERACTION_RESOLVED":
            return InteractionResolved(code)
        if code == "JOURNAL_PAYLOAD_MISSING":
            return ContentMissing(code)
        if code == "CONTENT_TOO_LARGE":
            return EventRejected(code)
        if code == "JOURNAL_WRITER_NOT_ALLOWED" or code == "JOURNAL_VISIBILITY_NOT_ALLOWED":
            return WriterNotAllowed(code)
        if code.startswith("JOURNAL_"):
            return EventRejected(code)
        if status in (401, 404) or code in _LOST:
            return OwnershipLost(code or str(status))
        return CommitRejected(code or str(status))


__all__ = [
    "HARNESS_V1_RETIRED",
    "AttemptRoute",
    "ClaimRole",
    "NodeClaim",
    "NodeJournal",
    "error_code",
    "transition_wire",
]


def _skipped_connects(raw: object) -> frozenset[str]:
    """The run tree's skipped items as Node names them (WP-64); an older Node names none."""
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str) and item)


def _tree_depth(raw: object) -> int:
    """How far below its root the run is, as Node names it (ADR-RUN-01 D4); an older Node names none: a root."""
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else 0
