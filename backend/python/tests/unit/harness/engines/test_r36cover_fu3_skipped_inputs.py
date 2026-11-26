"""R36COVER-FU-3 (answer contract): a file handed to a job that the job could not take is named, with why, to the
parent's model, on the job's step and at the head of the answer; never only "The coding engine stopped with an error".

* SKIP-1: ``skipped_inputs`` reads the job's ``turn.started`` (``skippedInputs``); the hand-back carries them.
* SKIP-2: the job's step names the file and why, and its frame carries the typed item (``attachment.unreadable`` with
  ``file``), valid against ``x.pipeshub.tool.result_presented``; the parent's model reads it in its ``couldNotDo`` note.
* SKIP-3: the parent's answer leads with the file's line; an engine run's own answer reads its journal the same way.
* SKIP-4: PipesHub's input source names a gone input only when it is the run's own conversation's.
* R46FIX-2: a file over the cap travels as ``attachment.too_large`` (unreadable for a Node without
  ``attachment_too_large``); the step says its verb once; unnamed files are counted and read "one of the attached files".
* R46FIX-FU-2: several files it may not name are one item with their ``count`` (a Node with ``attachment_count``),
  so the answer and the chat say "2 of the attached files" as the step does; a Node without it gets no count.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any, cast

from app.harness.contracts.generated.pipeshub.event_catalog import EVENT_MODELS
from app.harness.engines.capability_limits import (
    LIMITS_KEY,
    LIMITS_NOTE_KEY,
    InputsAccepted,
    limits_note,
    met_items,
    skipped_inputs,
)
from harness_core.contracts import validate_contract

RESULT = "x.pipeshub.tool.result_presented"
ANSWER = "x.pipeshub.answer.finalized"
TOO_LARGE = "it is too large to give to a task: 140.0 MB, and the limit is 100.0 MB"
GONE = "it is no longer available to the task (deleted, or no longer shared with you)"


@dataclasses.dataclass
class _Loose:
    type: str
    data: dict[str, Any]
    runId: str = "run-1"


def _started(*, gone: bool = True) -> list[_Loose]:
    skipped = [{"artifactId": "art-1", "reason": "too_large", "words": TOO_LARGE, "name": "plan.csv"}]
    if gone:
        skipped.append({"artifactId": "art-2", "reason": "unavailable", "words": GONE})
    return [_Loose("turn.started", {"turn": 1, "inputs": [], "skippedInputs": skipped})]


def _valid(event_type: str, data: dict[str, Any]) -> None:
    model = EVENT_MODELS[event_type]
    _ = validate_contract(cast("Any", model.model_fields["data"].annotation), data)


def test_skip_1_the_jobs_skipped_files_are_limits_it_met() -> None:
    limits = skipped_inputs(_started())  # type: ignore[arg-type]
    assert [(limit.kind, limit.cannot, limit.why, limit.target) for limit in limits] == [
        ("attachment.too_large", "Read plan.csv", TOO_LARGE, "plan.csv"),
        ("attachment.unreadable", "Read one of the files handed to it", GONE, ""),
    ]
    assert met_items(limits) == [{"kind": "attachment.too_large", "file": "plan.csv"}, {"kind": "attachment.unreadable"}]
    assert skipped_inputs([_Loose("turn.started", {"turn": 1, "inputs": []})]) == ()  # type: ignore[list-item]
    assert limits_note(limits).startswith(f"The coding engine could not do all of this: Read plan.csv: {TOO_LARGE}; ")


async def test_skip_2_the_job_step_names_the_file_and_carries_the_typed_item() -> None:
    from app.harness.assembly.code_task import RESULT_PRESENTED, CodeTaskDelegation
    from harness_core.kernel.core.messages import ToolCall

    limits = skipped_inputs(_started(gone=False))  # type: ignore[arg-type]
    document = {"success": True, "output": "Done with sales.csv.", LIMITS_KEY: [limit.to_json() for limit in limits]}
    outcome = await CodeTaskDelegation().outcome(ToolCall(id="c1", name="code_task", arguments={}), document)
    data = cast("dict[str, Any]", next(e for e in outcome.events if e.type == RESULT_PRESENTED).data)
    assert data["limits"] == [{"kind": "attachment.too_large", "file": "plan.csv"}]
    assert data["resultSummary"] == f"The coding engine finished the task. It could not read plan.csv ({TOO_LARGE})."
    _valid(RESULT, data)
    shown = cast("dict[str, Any]", outcome.content)
    assert "plan.csv" in shown[LIMITS_NOTE_KEY] and TOO_LARGE in shown[LIMITS_NOTE_KEY]


def test_skip_3_the_answer_leads_with_the_file_and_an_engine_runs_own_answer_too() -> None:
    from app.harness.engines.presenter import ANSWER as ENGINE_ANSWER
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from app.harness.presentation import run_could_not_do, with_limits
    from harness_core.engines.durable.ports import ProgressStamp

    item = {"kind": "attachment.too_large", "file": "plan.csv"}
    said = "Sales grew 4%."
    data: dict[str, Any] = {"outcome": "answered", "answer": said, "partTexts": [said], "citations": [], "confidence": None}
    with_limits(data, run_could_not_do([_Loose(RESULT, {"callId": "c1", "status": "completed", "content": "",  # type: ignore[list-item]
                                                         "limits": [item]})]))
    line = "I couldn't read the attached file plan.csv: it is over the size limit for files given to a task."
    assert data["answer"] == f"{line}\n\n{said}"
    _valid(ANSWER, data)

    stamp = ProgressStamp("run-1", "trn_1", iter(range(100)).__next__)
    events = PipesHubEngineRunPresenter().finished(
        stamp, success=True, reason=None, output=said, journal=_started(gone=False)  # type: ignore[arg-type]
    )
    answer = cast("dict[str, Any]", next(e for e in events if e.type == ENGINE_ANSWER).data)
    assert answer["answer"].startswith(line)
    assert answer["limits"]["items"] == [item]


async def test_skip_4_a_gone_input_is_named_only_when_it_is_the_conversations_own() -> None:
    from app.harness.engines.inputs import RegistryInputSource
    from app.services.artifact_registry.access import ArtifactNotFoundError
    from harness_core.journal.types import InputArtifactRef

    own = SimpleNamespace(conversation_id="conv-1", logical_name=None, name="Q3 plan (final).csv")
    other = SimpleNamespace(conversation_id="conv-2", logical_name=None, name="secret.csv")

    class _Registry:
        max_bytes = 1 << 20

        async def resolve(self, *, actor: object, ref: str) -> object:
            del actor
            if ref == "missing":
                raise ArtifactNotFoundError(ref)
            return own if ref == "own" else other

    source = RegistryInputSource(cast("Any", _Registry()), cast("Any", SimpleNamespace(org_id="org")),
                                 cast("Any", None), object_scope=lambda b: cast("Any", b))
    named = source._own_name  # pyright: ignore[reportPrivateUsage]
    assert await named(InputArtifactRef("own"), "conv-1") == "Q3_plan_final_.csv"
    assert await named(InputArtifactRef("other"), "conv-1") is None
    assert await named(InputArtifactRef("missing"), "conv-1") is None


def test_r46fix_2a_a_node_without_attachment_too_large_reads_the_big_file_as_unreadable() -> None:
    limits = skipped_inputs(_started(gone=False), accepted=InputsAccepted(too_large=False))  # type: ignore[arg-type]
    assert met_items(limits) == [{"kind": "attachment.unreadable", "file": "plan.csv"}]
    from app.harness.engines.presenter import ANSWER as ENGINE_ANSWER
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from harness_core.engines.durable.ports import ProgressStamp

    presenter = PipesHubEngineRunPresenter()
    presenter.expect_inputs(InputsAccepted(too_large=False))
    stamp = ProgressStamp("run-1", "trn_1", iter(range(100)).__next__)
    events = presenter.finished(stamp, success=True, reason=None, output="ok", journal=_started(gone=False))  # type: ignore[arg-type]
    answer = cast("dict[str, Any]", next(e for e in events if e.type == ENGINE_ANSWER).data)
    assert answer["limits"]["items"] == [{"kind": "attachment.unreadable", "file": "plan.csv"}]
    _valid(ENGINE_ANSWER, answer)


async def test_r46fix_2b_the_step_says_its_verb_once_and_counts_files_it_may_not_name() -> None:
    """MR-16: the step read "could not read one of the files…; read one of the files…"."""
    from app.harness.assembly.code_task import RESULT_PRESENTED, CodeTaskDelegation
    from harness_core.kernel.core.messages import ToolCall

    started = [_Loose("turn.started", {"turn": 1, "inputs": [], "skippedInputs": [
        {"artifactId": "art-1", "reason": "too_large", "words": TOO_LARGE, "name": "plan.csv"},
        {"artifactId": "art-2", "reason": "unavailable", "words": GONE},
        {"artifactId": "art-3", "reason": "unavailable", "words": GONE},
    ]})]
    limits = skipped_inputs(started)  # type: ignore[arg-type]
    document = {"success": True, "output": "Done.", LIMITS_KEY: [limit.to_json() for limit in limits]}
    outcome = await CodeTaskDelegation().outcome(ToolCall(id="c1", name="code_task", arguments={}), document)
    data = cast("dict[str, Any]", next(e for e in outcome.events if e.type == RESULT_PRESENTED).data)
    assert data["resultSummary"] == (
        f"The coding engine finished the task. It could not read plan.csv ({TOO_LARGE}) and 2 of the files handed to "
        f"it ({GONE})."
    )
    assert data["resultSummary"].count("read ") == 1
    _valid(RESULT, data)


def test_r46fix_2c_the_answer_line_names_an_unnamed_file_in_words_never_a_placeholder() -> None:
    from app.harness.engines.capability_limits import could_not_do_view

    view = could_not_do_view([{"kind": "attachment.unreadable"}, {"kind": "attachment.too_large", "file": "big.txt"}])
    assert view is not None
    assert view["line"] == (
        "I couldn't read one of the attached files: its content could not be read. "
        "I couldn't read the attached file big.txt: it is over the size limit for files given to a task."
    )
    assert "(unnamed)" not in str(view["line"])


def _unnamed_two() -> list[_Loose]:
    return [_Loose("turn.started", {"turn": 1, "inputs": [], "skippedInputs": [
        {"artifactId": "art-2", "reason": "unavailable", "words": GONE},
        {"artifactId": "art-3", "reason": "unavailable", "words": GONE},
    ]})]


async def test_r46fix_fu2a_two_files_it_may_not_name_are_one_item_counting_both_on_the_step_and_the_answer() -> None:
    """MR-16: the step said "2 of the files" while the chat said "one of the attached files"."""
    from app.harness.assembly.code_task import RESULT_PRESENTED, CodeTaskDelegation
    from app.harness.presentation import run_could_not_do, with_limits
    from harness_core.kernel.core.messages import ToolCall

    limits = skipped_inputs(_unnamed_two())  # type: ignore[arg-type]
    assert met_items(limits) == [{"kind": "attachment.unreadable", "count": 2}]
    document = {"success": True, "output": "Done.", LIMITS_KEY: [limit.to_json() for limit in limits]}
    outcome = await CodeTaskDelegation().outcome(ToolCall(id="c1", name="code_task", arguments={}), document)
    frame = next(e for e in outcome.events if e.type == RESULT_PRESENTED)
    data = cast("dict[str, Any]", frame.data)
    assert "2 of the files handed to it" in data["resultSummary"]
    assert data["limits"] == [{"kind": "attachment.unreadable", "count": 2}]
    _valid(RESULT, data)
    answer: dict[str, Any] = {"outcome": "answered", "answer": "Done.", "partTexts": ["Done."], "citations": []}
    with_limits(answer, run_could_not_do([cast("Any", _Loose(RESULT, data))]))
    line = "I couldn't read 2 of the attached files: their content could not be read."
    assert answer["limits"] == {"items": [{"kind": "attachment.unreadable", "count": 2}], "line": line}
    assert answer["answer"].startswith(line)
    _valid(ANSWER, answer)


def test_r46fix_fu2b_an_engine_runs_own_answer_counts_them_and_a_node_without_attachment_count_gets_none() -> None:
    from app.harness.engines.presenter import ANSWER as ENGINE_ANSWER
    from app.harness.engines.presenter import PipesHubEngineRunPresenter
    from harness_core.engines.durable.ports import ProgressStamp

    def items(accepted: InputsAccepted) -> list[dict[str, Any]]:
        presenter = PipesHubEngineRunPresenter()
        presenter.expect_inputs(accepted)
        stamp = ProgressStamp("run-1", "trn_1", iter(range(100)).__next__)
        events = presenter.finished(stamp, success=True, reason=None, output="ok", journal=_unnamed_two())  # type: ignore[arg-type]
        answer = cast("dict[str, Any]", next(e for e in events if e.type == ENGINE_ANSWER).data)
        _valid(ENGINE_ANSWER, answer)
        return cast("list[dict[str, Any]]", answer["limits"]["items"])

    assert items(InputsAccepted()) == [{"kind": "attachment.unreadable", "count": 2}]
    assert items(InputsAccepted(counted=False)) == [{"kind": "attachment.unreadable"}]
    assert InputsAccepted.of(lambda feature: feature == "attachment_too_large") == InputsAccepted(counted=False)


def test_r46fix_fu2c_the_line_words_each_kind_for_several_files_and_the_count_stays_in_the_schemas_bound() -> None:
    from app.harness.engines.capability_limits import attachment_item, could_not_do_view

    view = could_not_do_view([
        attachment_item("attachment.not_accessible", None, count=3),
        attachment_item("attachment.too_large", None, count=2),
        attachment_item("attachment.unreadable", "named.txt", count=4),
    ])
    assert view is not None
    assert view["items"] == [
        {"kind": "attachment.not_accessible", "count": 3},
        {"kind": "attachment.too_large", "count": 2},
        {"kind": "attachment.unreadable", "file": "named.txt"},
    ]
    assert view["line"] == (
        "I couldn't read 3 of the attached files: access to them was refused. "
        "I couldn't read 2 of the attached files: they are over the size limit for files given to a task. "
        "I couldn't read the attached file named.txt: its content could not be read."
    )
    assert attachment_item("attachment.unreadable", None, count=500) == {"kind": "attachment.unreadable", "count": 100}
