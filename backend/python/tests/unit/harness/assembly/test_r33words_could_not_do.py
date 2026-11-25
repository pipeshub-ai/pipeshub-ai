"""R32REPO-FU-1 (ADR-RUN-03 D5): the answer itself says what the run could not do, from the journal only.

Before, a clone the person declined reached them only through the parent model's ``couldNotDo`` note and the job's
step line: a parent model that left it out still showed a complete-looking answer above the step. Now:

* CND-1: a refused clone keeps what it could not read (``target``) and how many (``count``); the typed items name only
  what the run met while it worked, never a limit it started with and did not meet (no web access on a task that
  needed none).
* CND-2: the view's English line, one sentence per item, with the host hint for a host the org does not allow.
* CND-3: ``code_task``'s step frame carries the typed items, valid against ``x.pipeshub.tool.result_presented``.
* CND-4: the loop's presenter leads the answer with the line (after the not-found line) and carries ``limits``, valid
  against ``x.pipeshub.answer.finalized``; equal items from two jobs once; nothing met, nothing added.
* CND-5: an engine run's own answer reads its journal the same way (a job's, or a person's engine run).
"""

from __future__ import annotations

import dataclasses
from typing import Any, cast

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.engines.capability_limits import (
    LIMITS_KEY,
    CapabilityLimit,
    could_not_do_view,
    met_items,
    refused_clones,
)
from app.harness.repositories import TOOL_ID
from harness_core.contracts import validate_contract

ANSWER = "x.pipeshub.answer.finalized"
RESULT = "x.pipeshub.tool.result_presented"


@dataclasses.dataclass
class _Loose:
    type: str
    data: dict[str, Any]
    runId: str = "run-1"


def _declined_card(label: str, card: str = "ix.1") -> list[_Loose]:
    return [
        _Loose("interaction.explained", {"interactionId": card, "toolId": TOOL_ID,
                                         "destination": {"key": "sha256:x", "label": f"{label} (public, read-only)"}}),
        _Loose("interaction.resolved", {"interactionId": card, "decision": "denied"}),
    ]


def _valid(event_type: str, data: dict[str, Any]) -> None:
    model = EVENT_MODELS[event_type]
    _ = validate_contract(cast("Any", model.model_fields["data"].annotation), data)


def test_cnd_1_refused_clones_keep_what_they_could_not_read_and_only_met_limits_are_items() -> None:
    events = [
        *_declined_card("github.com/onyx-dot-app/onyx"),
        _Loose("tool.intent", {"invocationId": "i2", "toolId": TOOL_ID}),
        _Loose("tool.denied", {"invocationId": "i2", "rule": "flow"}),
        _Loose("tool.intent", {"invocationId": "i3", "toolId": TOOL_ID}),
        _Loose("tool.denied", {"invocationId": "i3", "rule": "flow"}),
    ]
    limits = refused_clones(events, TOOL_ID)  # type: ignore[arg-type]
    assert [(limit.kind, limit.target, limit.count) for limit in limits] == [
        ("repository.declined", "github.com/onyx-dot-app/onyx", 1),
        ("repository.refused", "", 2),
    ]
    started = CapabilityLimit("web", "Search or read the web", "this task has no web access")
    assert met_items([*limits, started]) == [
        {"kind": "repository.declined", "repository": "github.com/onyx-dot-app/onyx"},
        {"kind": "repository.refused", "count": 2},
    ]
    # The hand-back round-trips the new fields; a document from before them still reads.
    assert [CapabilityLimit.from_json(limit.to_json()) for limit in limits] == list(limits)
    old = CapabilityLimit.from_json({"kind": "repository.failed", "cannot": "Read it", "why": "the clone failed"})
    assert old is not None and (old.target, old.count) == ("", 1)


def test_cnd_2_the_line_is_one_plain_sentence_per_thing() -> None:
    view = could_not_do_view([
        {"kind": "repository.declined", "repository": "github.com/onyx-dot-app/onyx"},
        {"kind": "repository.refused", "count": 2},
        {"kind": "repository.not_allowed", "repository": "git.example.com/acme/widgets"},
        {"kind": "repository.failed"},
        {"kind": "repository.declined", "repository": "github.com/onyx-dot-app/onyx"},
        {"kind": "web"},
    ])
    assert view is not None
    assert view["line"] == (
        "I couldn't read github.com/onyx-dot-app/onyx: the clone was declined. "
        "I couldn't read 2 code repositories: a rule of your organization refused the clone. "
        "I couldn't read git.example.com/acme/widgets: its host is not one this server may clone from. "
        "Its host is not one this server clones from. "
        "I couldn't read a code repository: the clone failed."
    )
    assert len(cast("list[object]", view["items"])) == 4
    assert could_not_do_view([]) is None and could_not_do_view([{"kind": "web"}]) is None


async def test_cnd_3_the_job_step_frame_carries_the_typed_items() -> None:
    from app.harness.assembly.code_task import RESULT_PRESENTED, CodeTaskDelegation
    from harness_core.kernel.core.messages import ToolCall

    declined = refused_clones(_declined_card("github.com/onyx-dot-app/onyx"), TOOL_ID)  # type: ignore[arg-type]
    started = CapabilityLimit("web", "Search or read the web", "this task has no web access")
    document = {"success": True, "output": "Onyx's docs say X.", LIMITS_KEY: [d.to_json() for d in (*declined, started)]}
    outcome = await CodeTaskDelegation().outcome(ToolCall(id="c1", name="code_task", arguments={}), document)
    frame = next(e for e in outcome.events if e.type == RESULT_PRESENTED)
    data = cast("dict[str, Any]", frame.data)
    assert data["limits"] == [{"kind": "repository.declined", "repository": "github.com/onyx-dot-app/onyx"}]
    _valid(RESULT, data)
    plain = await CodeTaskDelegation().outcome(ToolCall(id="c2", name="code_task", arguments={}), {"success": True, "output": "x"})
    assert "limits" not in cast("dict[str, Any]", next(e for e in plain.events if e.type == RESULT_PRESENTED).data)


def test_cnd_4_the_answer_leads_with_the_line_after_the_not_found_line() -> None:
    from app.harness.grounding import Grounding
    from app.harness.presentation import _grounded, run_could_not_do, with_limits  # pyright: ignore[reportPrivateUsage]

    item = {"kind": "repository.declined", "repository": "github.com/onyx-dot-app/onyx"}
    events = [
        _Loose(RESULT, {"callId": "c1", "status": "completed", "content": "", "limits": [item]}),
        _Loose(RESULT, {"callId": "c2", "status": "completed", "content": "", "limits": [item]}),
        _Loose(RESULT, {"callId": "c3", "status": "completed", "content": ""}),
    ]
    said = "Onyx documents its connectors."
    data: dict[str, Any] = {"outcome": "answered", "answer": said, "partTexts": ["", said], "citations": [],
                            "confidence": None}
    with_limits(data, run_could_not_do(events))  # type: ignore[arg-type]
    line = "I couldn't read github.com/onyx-dot-app/onyx: the clone was declined."
    assert data["limits"] == {"items": [item], "line": line}
    assert data["answer"] == f"{line}\n\n{said}"
    assert data["partTexts"] == ["", data["answer"]]
    _valid(ANSWER, data)

    _grounded(data, Grounding("not_found", "Onyx roadmap", 1, 0))
    not_found = data["notFound"]["line"]
    assert data["answer"] == f"{not_found}\n\n{line}\n\n{said}"
    _valid(ANSWER, data)

    untouched: dict[str, Any] = {"outcome": "answered", "answer": said, "partTexts": [said], "citations": []}
    with_limits(untouched, run_could_not_do(events[2:]))  # type: ignore[arg-type]
    assert untouched == {"outcome": "answered", "answer": said, "partTexts": [said], "citations": []}


def test_cnd_5_an_engine_runs_own_answer_reads_its_journal() -> None:
    from app.harness.engines.presenter import ANSWER as ENGINE_ANSWER
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from harness_core.engines.durable.ports import ProgressStamp

    stamp = ProgressStamp("run-1", "trn_1", iter(range(100)).__next__)
    journal = _declined_card("github.com/onyx-dot-app/onyx")
    events = PipesHubEngineRunPresenter().finished(
        stamp, success=True, reason=None, output="Here is the report.", journal=journal  # type: ignore[arg-type]
    )
    answer = cast("dict[str, Any]", next(e for e in events if e.type == ENGINE_ANSWER).data)
    assert answer["answer"].startswith("I couldn't read github.com/onyx-dot-app/onyx: the clone was declined.\n\n")
    assert answer["limits"]["items"] == [{"kind": "repository.declined", "repository": "github.com/onyx-dot-app/onyx"}]
    plain = PipesHubEngineRunPresenter().finished(stamp, success=True, reason=None, output="Here is the report.")
    assert "limits" not in cast("dict[str, Any]", next(e for e in plain if e.type == ENGINE_ANSWER).data)
