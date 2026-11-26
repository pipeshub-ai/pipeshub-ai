"""NodeJournal wire mapping: transitions, errors, tokens, retired v1 runs (D-13, D-16)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.harness.journal_node import HARNESS_V1_RETIRED, NodeJournal, transition_wire
from harness_core.contracts import dump_contract
from harness_core.journal.children import (
    ChildGrantRequest,
    ChildRefusal,
    ChildRefused,
    ChildRequest,
    JoinReady,
)
from harness_core.journal.inline import inline_ref
from harness_core.journal.types import (
    AttemptToken,
    Cancelled,
    ChildResultRef,
    CommandConflict,
    CommitRequest,
    Complete,
    Fail,
    InputArtifactRef,
    InputPending,
    JournalError,
    JournalUnavailable,
    NewEvent,
    OwnershipLost,
    Quarantine,
    RunNotClaimable,
    Suspend,
    WriterNotAllowed,
    Yield,
)
from harness_core.kernel.fold import FOLD_VERSION
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from harness_core.journal.types import Transition

ADVERTISED = {
    "harnessMajor": 2,
    "supportedSchemaVersions": [2, 3],
    "foldVersion": FOLD_VERSION,
    "features": ["agent_code", "approval_edits", "assistant_agent", "caller", "chat", "child_asks", "child_models", "child_runs", "code_task_tool", "command_input", "compact_request", "deep_mode", "deliverable_pin", "descriptor_pins", "engine_inputs", "environment_pin", "executor_engine", "governed_memory", "inherited_grants", "inherited_labels", "keyword_search_budget", "knowledge_selection", "mcp_carried_pins", "mcp_first_use", "model_fallback", "output_locale", "region_aware", "release_pin", "resolved_config", "run_gates", "run_surface", "sandbox_command", "session_roles", "steering", "steering_by", "steering_references", "trace_content", "trigger_context", "turn_labels", "vendor_signin", "workspace_forks"],
}


class _Credential:
    async def worker_credential(self) -> str:
        return "phw_worker"


def _run(major: int = 2) -> dict[str, Any]:
    return {
        "runId": "run-1",
        "orgId": "org-1",
        "sessionId": "ses-1",
        "actorId": "actor-1",
        "userMessageKey": "m1",
        "idempotencyKey": "k1",
        "payloadHash": "sha256:x",
        "harnessMajor": major,
        "profile": "dispatched",
        "status": "running",
        "outcome": None,
        "revision": 1,
        "fence": 1,
        "attempts": 1,
        "notBefore": 0,
        "checkpointRef": None,
        "attemptId": "att-1",
        "workerId": "w1",
        "leaseExpiresAt": 5,
        "absoluteDeadlineAt": 10,
        "checkpoint": None,
        "input": {
            "query": "hello",
            "recordIds": [],
            "filters": {},
            "attachments": [],
            "model": {"chatMode": "quick"},
        },
        "createdAt": 0,
        "updatedAt": 0,
    }


def _claimed(major: int = 2, token: str = "tok-1") -> dict[str, Any]:
    body: dict[str, Any] = {
        "run": _run(major),
        "journal": {"lastSeq": {"ses-1": 3}},
        "attemptToken": {"token": token, "expiresAt": 1},
    }
    if major == 2:
        spec = dump_contract(minimal_run_spec())
        body["pinned"] = {
            "spec": spec,
            "specDigest": "sha256:" + "1" * 64,
            "capabilities": {"journal.durable": True},
            "capabilitiesDigest": "sha256:" + "2" * 64,
        }
    return body


def _claimed_naming_its_history() -> dict[str, Any]:
    body = _claimed()
    body["run"]["input"]["historyRef"] = {
        "parts": ["sealed:run-1/mongo:org-1:sha256:" + "3" * 64],
        "hash": "sha256:" + "4" * 64,
        "size": 120,
        "turns": 2,
    }
    return body


class _Node:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.replies: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.replies.pop(0)

    def journal(self, region: str | None = None) -> NodeJournal:
        return NodeJournal(
            base_url="http://node",
            credentials=_Credential(),
            http_client=httpx.AsyncClient(
                base_url="http://node", transport=httpx.MockTransport(self.handler)
            ),
            region=region,
        )


def _error(status: int, code: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": f"HTTP_AGENT_HARNESS_{code}", "message": "x"}})


@pytest.mark.parametrize(
    ("transition", "wire"),
    [
        (
            Suspend("approval", "ix-1"),
            {"status": "waiting_approval", "detail": {"kind": "suspend", "reason": "approval", "interactionId": "ix-1"}},
        ),
        (
            Suspend("capability_lost"),
            {"status": "paused", "detail": {"kind": "suspend", "reason": "capability_lost"}},
        ),
        (
            Yield("2026-09-26T12:00:10.000Z", "drain", "ckpt-1"),
            {
                "status": "queued",
                "detail": {"kind": "yield", "reason": "drain", "notBefore": "2026-09-26T12:00:10.000Z", "checkpointRef": "ckpt-1"},
            },
        ),
        (Complete("needs_input"), {"status": "completed", "outcome": "needs_input"}),
        (Complete(), {"status": "completed", "outcome": "answer"}),
        (Complete(leaves_input=True), {"status": "completed", "outcome": "answer", "leavesInput": True}),
        (
            Fail("model_failed", "no"),
            {"status": "failed", "errorType": "model_failed", "detail": {"kind": "fail", "message": "no"}},
        ),
        (Cancelled(), {"status": "cancelled"}),
        (
            Quarantine(4, "crash_loop"),
            {"status": "failed", "errorType": "crash_loop", "detail": {"kind": "quarantine", "attempts": 4, "lastErrorType": "crash_loop"}},
        ),
    ],
)
def test_d13_transitions_map_onto_node_statuses_and_details(
    transition: Transition, wire: dict[str, Any]
) -> None:
    assert transition_wire(transition) == wire


async def test_claim_presents_the_credential_and_keeps_the_token_out_of_the_claim() -> None:
    node = _Node()
    node.replies.append(httpx.Response(200, json=_claimed()))
    claim = await node.journal().claim(worker_id="w1")
    assert claim is not None
    request = node.requests[0]
    assert request.url.path == "/api/v1/agent-harness/internal/runs/claim"
    assert request.headers["authorization"] == "Bearer phw_worker"
    assert json.loads(request.content) == {
        "workerId": "w1",
        "leaseDurationMs": 60_000,
        "role": "dispatched",
        "workerCapabilities": ADVERTISED,
    }
    assert (claim.attempt, claim.fence, claim.last_seq, claim.input_text) == (1, 1, 3, "hello")
    assert claim.spec == minimal_run_spec()
    assert claim.capabilities == {"journal.durable": True}
    assert "tok-1" not in repr(claim)


async def test_in9_a_claim_carries_the_input_artifacts_the_run_input_names() -> None:
    node = _Node()
    body = _claimed()
    body["run"]["input"]["inputArtifacts"] = [{"artifactId": "art-1"}, {"artifactId": "art-2", "version": 3}]
    node.replies.append(httpx.Response(200, json=body))
    claim = await node.journal().claim(worker_id="w1")
    assert claim is not None
    assert claim.input_artifacts == (InputArtifactRef("art-1"), InputArtifactRef("art-2", 3))
    node.replies.append(httpx.Response(200, json=_claimed()))
    plain = await node.journal().claim(worker_id="w1")
    assert plain is not None and plain.input_artifacts == ()


async def test_tr03_a_claim_carries_the_trigger_context_the_run_input_names() -> None:
    node = _Node()
    body = _claimed()
    trigger = {
        "kind": "connector",
        "scheduleId": "sch-1",
        "firingId": "fir-1",
        "slotAt": "2026-10-30T09:00:00.000Z",
        "window": {"start": "2026-10-30T09:00:00.000Z", "end": "2026-10-30T09:05:00.000Z"},
        "event": {"connectorId": "con-1", "eventType": "newRecord", "at": "2026-10-30T09:01:02.000Z", "recordId": "rec-1"},
    }
    body["run"]["input"]["trigger"] = trigger
    node.replies.append(httpx.Response(200, json=body))
    claim = await node.journal().claim(worker_id="w1")
    assert claim is not None
    assert claim.trigger_context == trigger
    node.replies.append(httpx.Response(200, json=_claimed()))
    plain = await node.journal().claim(worker_id="w1")
    assert plain is not None and plain.trigger_context is None


async def test_ru2b_a_claim_by_id_carries_the_advertisement_and_an_incompatible_run_is_not_claimable() -> None:
    node = _Node()
    node.replies.append(_error(409, "WORKER_INCOMPATIBLE"))
    with pytest.raises(RunNotClaimable, match="WORKER_INCOMPATIBLE"):
        _ = await node.journal().claim("run-1", worker_id="w1", org_id="org-1")
    assert json.loads(node.requests[0].content) == {
        "orgId": "org-1",
        "workerId": "w1",
        "leaseDurationMs": 60_000,
        "workerCapabilities": ADVERTISED,
    }


async def test_wp88_fu2_a_worker_names_its_region_only_when_one_is_set() -> None:
    """OWD-12 and UX2: with no region set the claim says none, as every worker before regions did, so a control plane
    from before regions does not refuse it; a set region is always sent, so placement fails closed."""
    node = _Node()
    node.replies += [httpx.Response(204), httpx.Response(204)]
    assert await node.journal().claim(worker_id="w1") is None
    unset = json.loads(node.requests[-1].content)
    assert "workerRegion" not in unset
    # Fails closed: the claim says the worker knows regions, so Node places it as no region's, never as its own.
    assert "region_aware" in unset["workerCapabilities"]["features"]
    assert await node.journal(region="eu-west").claim(worker_id="w1") is None
    assert json.loads(node.requests[-1].content)["workerRegion"] == "eu-west"
    node.replies.append(_error(409, "RUN_NOT_CLAIMABLE"))
    with pytest.raises(RunNotClaimable):
        _ = await node.journal(region="eu-west").claim("run-1", worker_id="w1", org_id="org-1")
    assert json.loads(node.requests[-1].content)["workerRegion"] == "eu-west"


async def test_an_empty_queue_is_none_and_a_lost_race_is_not_claimable() -> None:
    node = _Node()
    node.replies += [httpx.Response(204), _error(409, "RUN_NOT_CLAIMABLE"), _error(503, "UNAVAILABLE")]
    journal = node.journal()
    assert await journal.claim(worker_id="w1") is None
    with pytest.raises(RunNotClaimable):
        _ = await journal.claim("run-1", worker_id="w1", org_id="org-1")
    with pytest.raises(JournalUnavailable):
        _ = await journal.claim(worker_id="w1")
    with pytest.raises(ValueError, match="org_id"):
        _ = await journal.claim("run-1", worker_id="w1")


@pytest.mark.parametrize(
    "reply",
    [
        httpx.Response(200, content=b"<html>proxy error</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
        httpx.Response(200, json={**_claimed(), "pinned": {"spec": {"schemaVersion": "x"}}}),
        # PERF-B: a Node older than history-by-reference hands over the stored input, which names the history
        # and does not carry it. Refused whole, so the run never starts without its history (QA22-F2).
        httpx.Response(200, json=_claimed_naming_its_history()),
    ],
    ids=["not-json", "not-an-object", "pinned-spec-invalid", "input-names-a-history-it-does-not-carry"],
)
async def test_fx1_1_a_claim_answer_this_worker_cannot_read_is_a_journal_error_and_keeps_no_token(
    reply: httpx.Response,
) -> None:
    node = _Node()
    node.replies.append(reply)
    journal = node.journal()
    with pytest.raises(JournalError) as raised:
        _ = await journal.claim(worker_id="w1")
    assert type(raised.value) is JournalError
    with pytest.raises(OwnershipLost):
        _ = await journal.commit(CommitRequest("c1", AttemptToken("run-1", "att-1", 1)))


async def test_d16_a_harness_v1_run_is_failed_terminally_never_run() -> None:
    node = _Node()
    node.replies += [httpx.Response(200, json=_claimed(major=1)), httpx.Response(200, json={"receipt": {}})]
    with pytest.raises(RunNotClaimable, match=HARNESS_V1_RETIRED):
        _ = await node.journal().claim("run-1", worker_id="w1", org_id="org-1")
    retire = node.requests[1]
    assert retire.headers["authorization"] == "Bearer tok-1"
    assert json.loads(retire.content) == {
        "commandId": f"att-1:{HARNESS_V1_RETIRED}",
        "attemptId": "att-1",
        "fence": 1,
        "expectedRevision": 1,
        "status": "failed",
        "events": [{"type": "run_failed", "data": {"errorType": HARNESS_V1_RETIRED}}],
        "outbox": [],
    }


async def test_renew_adopts_the_fresh_token_for_later_calls() -> None:
    node = _Node()
    node.replies += [
        httpx.Response(200, json=_claimed()),
        httpx.Response(200, json={"run": _run(), "attemptToken": {"token": "tok-2", "expiresAt": 2}}),
        httpx.Response(200, json={"receipt": {}, "events": [], "replayed": False}),
    ]
    journal = node.journal()
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    grant = await journal.renew(claim.token)
    assert grant.lease_ms == 60_000
    _ = await journal.commit(CommitRequest("c1", claim.token, (NewEvent("turn.started", {"turn": 1}),)))
    assert node.requests[2].headers["authorization"] == "Bearer tok-2"
    assert json.loads(node.requests[2].content)["events"] == [
        {"type": "turn.started", "schemaVersion": 1, "critical": True, "visibility": "owner", "data": {"turn": 1}}
    ]


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (_error(409, "STALE_ATTEMPT"), OwnershipLost),
        (_error(401, "ATTEMPT_SUPERSEDED"), OwnershipLost),
        (_error(409, "IDEMPOTENCY_CONFLICT"), CommandConflict),
        (_error(409, "INPUT_PENDING"), InputPending),
        (_error(422, "JOURNAL_WRITER_NOT_ALLOWED"), WriterNotAllowed),
        (_error(502, "BAD_GATEWAY"), JournalUnavailable),
    ],
)
async def test_commit_errors_map_onto_the_port(reply: httpx.Response, error: type[Exception]) -> None:
    node = _Node()
    node.replies += [httpx.Response(200, json=_claimed()), reply]
    journal = node.journal()
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    with pytest.raises(error):
        _ = await journal.commit(CommitRequest("c1", claim.token))


@pytest.mark.parametrize(
    "outage",
    [httpx.ConnectError("connection refused"), _error(503, "UNAVAILABLE")],
    ids=["unreachable", "5xx"],
)
async def test_pa19_a_read_node_cannot_answer_is_transient(
    outage: Exception | httpx.Response, caplog: pytest.LogCaptureFixture
) -> None:
    """A Node restart during the fold read is ``JournalUnavailable`` (the
    kernel retries it), never an arbitrary transport error."""
    node = _Node()
    node.replies.append(httpx.Response(200, json=_claimed()))
    served: list[Exception | httpx.Response] = [outage]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and served:
            reply = served.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return node.handler(request)

    journal = NodeJournal(
        base_url="http://node",
        credentials=_Credential(),
        http_client=httpx.AsyncClient(base_url="http://node", transport=httpx.MockTransport(handler)),
    )
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    caplog.set_level("WARNING")
    with pytest.raises(JournalUnavailable):
        _ = await journal.read("ses-1")
    event = "journal.unreachable" if isinstance(outage, Exception) else "journal.unavailable"
    [line] = [r for r in caplog.records if getattr(r, "harness_event", None) == event]
    assert line.harness["runId"] == "run-1" and line.harness["operation"] == "journal.read"  # type: ignore[attr-defined]


async def test_an_attempt_this_worker_never_claimed_has_no_token() -> None:
    node = _Node()
    node.replies.append(httpx.Response(200, json=_claimed()))
    journal = node.journal()
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    journal.forget(claim.token)
    with pytest.raises(OwnershipLost):
        _ = await journal.commit(CommitRequest("c1", claim.token))


@pytest.mark.parametrize("operation", ["journal.commit", "journal.renew", "journal.release"])
async def test_a_post_node_cannot_answer_is_logged_with_the_attempt(
    operation: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Ground rule 9: a Node restart during a commit, renew or release is
    followed by grepping the run id, like a read (JR-4)."""
    node = _Node()
    node.replies.append(httpx.Response(200, json=_claimed()))
    claimed = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal claimed
        if not claimed:
            claimed = True
            return node.handler(request)
        raise httpx.ConnectError("connection refused")

    journal = NodeJournal(
        base_url="http://node",
        credentials=_Credential(),
        http_client=httpx.AsyncClient(base_url="http://node", transport=httpx.MockTransport(handler)),
    )
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    caplog.set_level("WARNING")
    with pytest.raises(JournalUnavailable):
        if operation == "journal.commit":
            _ = await journal.commit(CommitRequest("c1", claim.token))
        elif operation == "journal.renew":
            _ = await journal.renew(claim.token)
        else:
            await journal.release(claim.token, reason="drain")
    [line] = [r for r in caplog.records if getattr(r, "harness_event", None) == "journal.unreachable"]
    fields = line.harness  # type: ignore[attr-defined]
    assert fields["runId"] == "run-1" and fields["attemptId"] == claim.attempt_id
    assert fields["operation"] == operation


# ---- NJ-1: the ChildRunPort and a child's completing transition (P6.1) ----


async def _claimed_journal(node: _Node) -> tuple[NodeJournal, Any]:
    node.replies.append(httpx.Response(200, json=_claimed()))
    journal = node.journal()
    claim = await journal.claim("run-1", worker_id="w1", org_id="org-1")
    assert claim is not None
    return journal, claim


async def test_spawn_sends_the_request_with_the_attempt_token() -> None:
    node = _Node()
    journal, claim = await _claimed_journal(node)
    node.replies.append(httpx.Response(200, json={"childRunId": "c-1", "created": True}))
    admitted = await journal.spawn(
        claim.token,  # type: ignore[attr-defined]
        ChildRequest(
            "t1.0",
            "find churn",
            ChildGrantRequest(tools=("jira@i/search",), max_turns=4),
            task_id="churn",
        ),
    )
    assert (admitted.child_run_id, admitted.created) == ("c-1", True)
    request = node.requests[-1]
    assert request.url.path == "/api/v1/agent-harness/internal/runs/run-1/children"
    assert request.headers["authorization"] == "Bearer tok-1"
    assert json.loads(request.content) == {
        "attemptId": "att-1",
        "fence": 1,
        "invocationId": "t1.0",
        "goal": "find churn",
        "grants": {"tools": ["jira@i/search"], "maxTurns": 4},
        "taskId": "churn",
    }


async def test_nk1_spawn_sends_the_sub_agent_definition() -> None:
    node = _Node()
    journal, claim = await _claimed_journal(node)
    node.replies.append(httpx.Response(200, json={"childRunId": "c-1", "created": True}))
    definition = {"role": "jira", "domainAgent": "jira_agent", "toolNames": ["jira_agent"]}
    _ = await journal.spawn(
        claim.token,  # type: ignore[attr-defined]
        ChildRequest(
            "t1.0",
            "find churn",
            ChildGrantRequest(max_turns=8, max_tokens=500_000, max_children=0),
            definition=definition,  # pyright: ignore[reportArgumentType]
        ),
    )
    body = json.loads(node.requests[-1].content)
    assert body["subAgent"] == definition
    assert body["grants"] == {"maxTurns": 8, "maxTokens": 500_000, "maxChildren": 0}


@pytest.mark.parametrize("refusal", list(ChildRefusal))
async def test_node_refusals_are_typed(refusal: ChildRefusal) -> None:
    node = _Node()
    journal, claim = await _claimed_journal(node)
    node.replies.append(_error(409, refusal.value.upper()))
    with pytest.raises(ChildRefused) as refused:
        _ = await journal.spawn(claim.token, ChildRequest("t1.0", "x"))  # type: ignore[attr-defined]
    assert refused.value.reason is refusal


async def test_ma1_fb1_a_refused_spawn_carries_nodes_sentence_capped() -> None:
    node = _Node()
    journal, claim = await _claimed_journal(node)
    sentence = "The model set for the role researcher is not allowed by the workspace"
    node.replies.append(
        httpx.Response(409, json={"error": {"code": "HTTP_AGENT_HARNESS_CHILD_GRANT_TOO_LARGE", "message": sentence}})
    )
    with pytest.raises(ChildRefused) as refused:
        _ = await journal.spawn(claim.token, ChildRequest("t1.0", "x"))  # type: ignore[attr-defined]
    assert refused.value.detail == sentence
    node.replies.append(
        httpx.Response(409, json={"error": {"code": "HTTP_AGENT_HARNESS_CHILD_GRANT_TOO_LARGE", "message": "y" * 5000}})
    )
    with pytest.raises(ChildRefused) as long:
        _ = await journal.spawn(claim.token, ChildRequest("t1.1", "x"))  # type: ignore[attr-defined]
    assert long.value.detail == "y" * 300
    node.replies.append(httpx.Response(409, json={"error": {"code": "HTTP_AGENT_HARNESS_CHILD_GRANT_TOO_LARGE"}}))
    with pytest.raises(ChildRefused) as bare:
        _ = await journal.spawn(claim.token, ChildRequest("t1.2", "x"))  # type: ignore[attr-defined]
    assert bare.value.detail == ""


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [(409, "IDEMPOTENCY_CONFLICT", CommandConflict), (409, "STALE_ATTEMPT", OwnershipLost)],
)
async def test_other_refusals_map_like_a_commit(
    status: int, code: str, error: type[Exception]
) -> None:
    node = _Node()
    journal, claim = await _claimed_journal(node)
    node.replies.append(_error(status, code))
    with pytest.raises(error):
        _ = await journal.spawn(claim.token, ChildRequest("t1.0", "x"))  # type: ignore[attr-defined]


async def test_join_ready_is_its_own_refusal() -> None:
    node = _Node()
    journal, claim = await _claimed_journal(node)
    node.replies.append(_error(409, "JOIN_READY"))
    with pytest.raises(JoinReady):
        _ = await journal.commit(
            CommitRequest("att-1:suspend:1", claim.token, (), Suspend("join"))  # type: ignore[attr-defined]
        )


def test_a_child_completion_carries_its_result() -> None:
    ref = inline_ref(b'{"output":"x","success":true}')
    wire = transition_wire(
        Complete("answered", child_result=ChildResultRef(ref, {"integrity": "internal"}))
    )
    assert wire == {
        "status": "completed",
        "outcome": "answer",
        "childResult": {"resultRef": dump_contract(ref), "labels": {"integrity": "internal"}},
    }
    assert "childResult" not in transition_wire(Complete("answered"))


async def test_ux5_a_forgotten_attempt_still_settles_and_hands_over_followups_but_never_commits() -> None:
    node = _Node()
    node.replies.append(httpx.Response(200, json=_claimed()))
    journal = node.journal()
    claim = await journal.claim(worker_id="w1")
    assert claim is not None and claim.followups is not None
    journal.forget(claim.token)
    node.replies.append(httpx.Response(200, json={"followups": [{"text": "a"}, {"text": "b"}]}))
    assert await claim.followups.deliver(["a", "b"]) is True
    sent = node.requests[-1]
    assert sent.url.path == "/api/v1/agent-harness/internal/runs/run-1/followups"
    assert sent.headers["Authorization"] == "Bearer tok-1"
    assert json.loads(sent.content) == {
        "items": [{"text": "a"}, {"text": "b"}],
        "attemptId": claim.attempt_id,
        "fence": claim.fence,
    }
    with pytest.raises(OwnershipLost):
        _ = await journal.commit(CommitRequest("c1", claim.token))


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (403, "ACCOUNT_MISSING"),
        (403, "ORG_POLICY_REFUSED"),
        (400, "ENGINE_PROFILE_WIDENING"),
        (409, "ENGINE_UNAVAILABLE"),
        # WP-111: the engine was turned off after the turn started; the hand-off is refused, the loop answers.
        (409, "ENGINE_OFF"),
    ],
)
async def test_ctl6_a_child_refused_its_access_is_a_typed_refusal_naming_nodes_code(status: int, code: str) -> None:
    """CTL-6: an engine child whose model access or engine Node refuses is a refused call, never a failed run."""
    node = _Node()
    journal, claim = await _claimed_journal(node)
    node.replies.append(httpx.Response(status, json={"error": {"code": f"HTTP_AGENT_HARNESS_{code}", "message": "no"}}))
    with pytest.raises(ChildRefused) as refused:
        _ = await journal.spawn(claim.token, ChildRequest("t1.0", "x"))  # type: ignore[attr-defined]
    assert refused.value.reason is ChildRefusal.ACCESS_REFUSED
    assert refused.value.code == code
