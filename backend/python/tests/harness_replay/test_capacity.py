"""Projection rules of the capacity model (P0.9) against REPORT §6.1.4."""

from __future__ import annotations

import pytest

from harness_core.testing.replay import (
    Manifest,
    ModelStep,
    RecordedResponse,
    RecordedToolCall,
    Recording,
    ToolStep,
)
from harness_core.testing.replay.recording import FinalOutcome
from tests.harness_replay import capacity
from tests.harness_replay.capacity import (
    INLINE_LIMIT_BYTES,
    PAYLOAD_REF_BYTES,
    Event,
    load_corpus,
    project,
    slo_turn_bound,
)


def _model(*calls: tuple[str, str], text: str = "") -> ModelStep:
    return ModelStep(
        response=RecordedResponse(
            text=text,
            tool_calls=[RecordedToolCall(id=cid, name=name) for cid, name in calls],
        )
    )


def _tool(cid: str, name: str, result: str = "ok") -> ToolStep:
    return ToolStep(call_id=cid, name=name, status="succeeded", result=result)


def _run(*steps, status: str = "completed") -> Recording:
    return Recording(
        manifest=Manifest(scenario="t"),
        steps=list(steps),
        final=FinalOutcome(answer="done", status=status),
    )


def _kinds(recording: Recording) -> list[str]:
    return [c.kind for c in project(recording).commits]


def test_final_answer_completes_the_run_in_its_model_commit() -> None:
    p = project(_run(_model(text="hi")))
    assert [c.kind for c in p.commits] == ["admit", "claim", "model.response"]
    assert p.commits[-1].types[-1] == "run.completed"
    assert p.commits_by("node") == 2


def test_read_intent_rides_with_its_outcome() -> None:
    p = project(
        _run(
            _model(("c1", "knowledge__search")),
            _tool("c1", "knowledge__search"),
            _model(text="a"),
        )
    )
    assert [c.kind for c in p.commits] == [
        "admit",
        "claim",
        "model.response",
        "outcomes",
        "model.response",
    ]
    assert p.commits[3].types == ["tool.intent", "tool.outcome", "turn.completed"]


def test_write_intent_is_its_own_commit_before_the_outcome() -> None:
    p = project(
        _run(
            _model(("c1", "slack__send_message")),
            _tool("c1", "slack__send_message"),
            _model(text="posted"),
        )
    )
    kinds = [c.kind for c in p.commits]
    assert kinds == [
        "admit",
        "claim",
        "model.response",
        "intent",
        "outcomes",
        "model.response",
    ]
    assert p.commits[3].types == ["tool.intent"]
    assert "tool.intent" not in p.commits[4].types
    assert p.write_intents == 1


def test_parallel_batch_outcomes_share_one_commit() -> None:
    p = project(
        _run(
            _model(("c1", "jira__get_issue"), ("c2", "slack__read_channel")),
            _tool("c1", "jira__get_issue"),
            _tool("c2", "slack__read_channel"),
            _model(text="a"),
        )
    )
    outcomes = [c for c in p.commits if c.kind == "outcomes"]
    assert len(outcomes) == 1
    assert outcomes[0].types.count("tool.outcome") == 2
    assert p.turn_commits == [(2, 2)]


def test_execute_counts_as_a_write_for_commit_rules() -> None:
    assert _kinds(
        _run(_model(("c1", "run_code")), _tool("c1", "run_code"), _model(text="4"))
    )[3] == ("intent")


def test_interaction_suspends_without_an_outcome() -> None:
    p = project(
        _run(
            _model(("c1", "internaltools__ask_user_question")),
            _tool("c1", "internaltools__ask_user_question"),
            status="needs_input",
        )
    )
    assert [c.kind for c in p.commits] == [
        "admit",
        "claim",
        "model.response",
        "suspend",
    ]
    assert p.commits[-1].types == ["interaction.requested", "run.suspended"]


def test_resumed_interaction_adds_node_resolve_and_reclaim() -> None:
    p = project(
        _run(
            _model(("c1", "internaltools__ask_user_question")),
            _tool("c1", "internaltools__ask_user_question"),
            _model(text="thanks"),
        )
    )
    assert [c.kind for c in p.commits][3:] == [
        "suspend",
        "resolve",
        "claim",
        "model.response",
    ]
    assert p.commits_by("node") == 4


def test_payload_over_inline_limit_is_claim_checked() -> None:
    small = Event("tool.outcome", (INLINE_LIMIT_BYTES,))
    big = Event("tool.outcome", (INLINE_LIMIT_BYTES + 1,))
    assert small.blob_bytes == 0
    assert big.blob_bytes == INLINE_LIMIT_BYTES + 1
    assert big.inline_bytes == capacity.ENVELOPE_BYTES + PAYLOAD_REF_BYTES


def test_large_tool_result_lands_in_cas() -> None:
    big = "x" * (INLINE_LIMIT_BYTES + 10)
    p = project(
        _run(
            _model(("c1", "drive__read_file")),
            _tool("c1", "drive__read_file", big),
            _model(text="a"),
        )
    )
    assert p.blob_bytes > INLINE_LIMIT_BYTES
    assert p.inline_bytes < INLINE_LIMIT_BYTES


def test_code_mode_batches_read_outcomes() -> None:
    p = capacity.project_code_mode(calls=250, batch=100)
    assert sum(c.kind == "outcomes" for c in p.commits) == 3 + 1
    assert p.duration_s == pytest.approx(
        2 * capacity.MODEL_CALL_S + 250 / capacity.CODE_MODE_CALLS_PER_S
    )


@pytest.mark.parametrize("p", load_corpus(), ids=lambda p: p.name)
def test_corpus_turns_stay_within_the_commit_slo(p) -> None:
    for tools, commits in p.turn_commits:
        assert commits <= slo_turn_bound(tools), (p.name, tools, commits)


def test_committed_baseline_is_current() -> None:
    assert capacity.DOC_PATH.read_text(encoding="utf-8") == capacity.build(), (
        "run `python -m tests.harness_replay.capacity --write` from backend/python"
    )


@pytest.mark.parametrize("reads", [50, 51, 100, 101, 500])
def test_perfa_12_a_read_batch_is_as_many_outcome_commits_as_the_journal_limit_makes(reads: int) -> None:
    """JRN-30 (QA22-F1): the model assumed one commit for a batch of any size; the kernel splits at
    ``journal.maxCommitEvents`` and keeps a read's two events together."""
    from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS

    calls = [(f"c{i}", "knowledge__search") for i in range(reads)]
    p = project(_run(_model(*calls), *[_tool(cid, name) for cid, name in calls], _model(text="a")))
    outcomes = [c for c in p.commits if c.kind == "outcomes"]
    assert len(outcomes) == -(-2 * reads // MAX_COMMIT_EVENTS), "as many commits as the kernel makes"
    assert all(len(c.events) <= MAX_COMMIT_EVENTS for c in outcomes)
    for commit in outcomes:
        assert commit.types.count("tool.intent") == commit.types.count("tool.outcome"), "a read is never cut"
    assert sum(c.types.count("tool.outcome") for c in outcomes) == reads
    full = 2 * reads % MAX_COMMIT_EVENTS == 0
    assert (outcomes[-1].types[-1] == "turn.completed") is not full, "a full last commit leaves the turn's end to the next"
    assert sum(c.types.count("turn.completed") for c in p.commits) == 2
    [(tools, commits)] = p.turn_commits
    assert (tools, commits) == (reads, 1 + len(outcomes)) and commits <= slo_turn_bound(tools)


def test_perfa_12_code_mode_batches_outcomes_by_the_journal_limit() -> None:
    from harness_core.journal.commit_parts import MAX_COMMIT_EVENTS

    assert capacity.CODE_MODE_OUTCOME_BATCH == MAX_COMMIT_EVENTS
    batches = [c for c in capacity.project_code_mode(calls=250).commits if c.kind == "outcomes"]
    assert [len(c.events) for c in batches] == [MAX_COMMIT_EVENTS, MAX_COMMIT_EVENTS, 50, 2]


@pytest.mark.parametrize(("calls", "commits"), [(50, 1), (51, 2), (100, 2), (500, 10)])
def test_perfa_12_the_loop_projection_of_wide_read_turns_counts_the_kernels_commits(calls: int, commits: int) -> None:
    """QA-22's SLO #10 baseline (``project_loop_reads``) assumed one outcomes commit a turn."""
    p = capacity.project_loop_reads(turns=3, calls=calls)
    outcomes = [c for c in p.commits if c.kind == "outcomes"]
    assert len(outcomes) == 3 * commits and p.turn_commits == [(calls, 1 + commits)] * 3
    assert sum(c.types.count("tool.outcome") for c in outcomes) == 3 * calls
    assert sum(c.types.count("turn.completed") for c in p.commits) == 4
