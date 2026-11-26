"""SS2 AU-3, AU-7: a run in a shared session, as the worker assembles it.

The audience Node pinned (``x.pipeshub.audience``) becomes the kernel's
``HistoryAudience``; the conversation history the run's input carries is
projected for its actor; everything the run reads, it reads as that actor.
"""

from __future__ import annotations

from typing import Any

from app.harness.assembly.context import RunContext
from app.harness.worker.capabilities import WORKER_CAPABILITIES
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.kernel.durable.audience import FOREIGN_TURN_STUB, HistoryAudience

from . import world as w

OWNER = "p_owner-1"


def _shared_spec(**audience: Any) -> RunSpec:  # noqa: ANN401
    spec = dump_contract(w.pinned_spec(tool_ids=[]))
    spec["extensions"]["x.pipeshub"]["audience"] = {  # type: ignore[index]
        "sessionRole": "collaborator",
        "ownerRef": OWNER,
        "ownRuns": ["run-0"],
        **audience,
    }
    return validate_contract(RunSpec, spec)


HISTORY: list[dict[str, Any]] = [
    {"role": "user_query", "content": "owner asks", "attachments": [{"recordId": "R"}]},
    {"role": "bot_response", "content": "owner's answer from R", "tool_results": [{"result": "R"}]},
    {"role": "user_query", "content": "my question", "sender": f"p_{w.ACTOR}"},
    {"role": "bot_response", "content": "my answer", "sender": f"p_{w.ACTOR}"},
]


def test_au7_the_run_acts_as_its_sender_and_carries_its_audience() -> None:
    run = RunContext.from_claim(w.claim(_shared_spec(), previous=HISTORY))
    assert run.actor_id == w.ACTOR
    assert run.principal_ref == f"p_{w.ACTOR}"
    assert run.history_audience == HistoryAudience(
        principal=f"p_{w.ACTOR}", owner=OWNER, own_runs=frozenset({"run-0"})
    )


def test_au3_previous_conversations_are_projected_for_the_actor() -> None:
    run = RunContext.from_claim(w.claim(_shared_spec(), previous=HISTORY))
    assert [dict(t) for t in run.previous_conversations] == [
        {"role": "user_query", "content": FOREIGN_TURN_STUB},
        {"role": "bot_response", "content": FOREIGN_TURN_STUB},
        {"role": "user_query", "content": "my question"},
        {"role": "bot_response", "content": "my answer"},
    ]


def test_au4_a_personal_session_s_run_is_unchanged() -> None:
    run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[]), previous=HISTORY))
    assert run.history_audience is None
    assert [dict(t) for t in run.previous_conversations] == HISTORY


def test_ca12_the_worker_advertises_session_roles() -> None:
    assert "session_roles" in [f.root for f in WORKER_CAPABILITIES.features]


def test_s2_8_the_runs_whose_label_passed_are_read_from_the_pin() -> None:
    run = RunContext.from_claim(w.claim(_shared_spec(visibleRuns=["run-a", "run-c"]), previous=HISTORY))
    assert run.history_audience == HistoryAudience(
        principal=f"p_{w.ACTOR}",
        owner=OWNER,
        own_runs=frozenset({"run-0"}),
        visible_runs=frozenset({"run-a", "run-c"}),
    )
    # Conversation history names no run, so it stays projected as SS2 projects it.
    assert [dict(t) for t in run.previous_conversations][:2] == [
        {"role": "user_query", "content": FOREIGN_TURN_STUB},
        {"role": "bot_response", "content": FOREIGN_TURN_STUB},
    ]


def test_s2_8_the_worker_advertises_turn_labels() -> None:
    assert "turn_labels" in [f.root for f in WORKER_CAPABILITIES.features]
