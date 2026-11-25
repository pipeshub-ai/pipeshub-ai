"""``OutcomeBriefing``: what a parent's model is told when an engine child ends (ADR-RUN-01 D1).

It reads the child's result once, typed (``ChildReport``: its outcome as the child's kernel settled it, and the
work its fork kept), and returns a ``Briefing``: the situation, the instruction content for the parent's model
(rendered as a harness-channel message by the hand-off's caller, ADR-RUN-01 D5) and, when the parent has nothing of
its own to answer from, the answer written without a model. Its words come from the outcome catalog; it replaces
``ENGINE_FAILED_INSTRUCTION`` and its variants, and the parent's reading of the child's answer text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, cast

from harness_core.engines.outcome.ledger import OUTCOME_SETTLED
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.engines.outcome.words import ENGLISH
from harness_core.journal.types import NewEvent

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.engines.outcome.presenter import OutcomePresenter
    from harness_core.engines.outcome.words import Words

# The key a child's result carries its settled outcome under (``outcome_wire`` plus ``answered``).
OUTCOME_KEY: Final = "outcome"
KEPT_FILES_SHOWN: Final = 10
Situation = Literal["answered", "partial", "unanswered", "failed"]


def _object(value: object) -> dict[str, object]:
    return cast("dict[str, object]", value) if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class ChildReport:
    """A ``code_task`` child's result, read once: ``failed`` (the call failed or the child's result says so), its
    ``output``, its settled ``outcome`` (``None`` from a child that predates the ledger), the cut that stopped it,
    and the work its fork kept: the folder and up to ``REPORT_PATHS`` of its files, or the fork's revisions."""

    failed: bool
    output: str = ""
    outcome: dict[str, object] | None = None
    truncated_by: str | None = None
    kept_folder: str | None = None
    kept_files: tuple[str, ...] = ()
    kept_total: int = 0
    revisions: int = 0

    @classmethod
    def read(cls, content: object, *, call_ok: bool = True) -> ChildReport:
        body = _object(content)
        output = body.get("output")
        outcome = body.get(OUTCOME_KEY)
        stopped = body.get("truncatedBy")
        workspace = _object(body.get("workspace"))
        unfinished = _object(workspace.get("unfinished"))
        folder = unfinished.get("folder")
        listed = unfinished.get("files")
        files = tuple(f for f in cast("list[object]", listed) if isinstance(f, str)) if isinstance(listed, list) else ()
        count = unfinished.get("count")
        # A report from before the count was kept lists every file it kept, up to its 50.
        total = count if isinstance(count, int) and not isinstance(count, bool) and count >= len(files) else len(files)
        revision = _object(workspace.get("fork")).get("revision")
        return cls(
            failed=not call_ok or body.get("success") is False,
            output=output if isinstance(output, str) else "",
            outcome=cast("dict[str, object]", outcome) if isinstance(outcome, dict) else None,
            truncated_by=stopped if isinstance(stopped, str) else None,
            kept_folder=folder if isinstance(folder, str) and folder else None,
            kept_files=files,
            kept_total=total,
            revisions=revision if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0 else 0,
        )

    @property
    def has_work(self) -> bool:
        """Whether a failed child left work the parent can point to."""
        return self.kept_folder is not None or self.revisions > 0

    @property
    def closing(self) -> str | None:
        """WP-89: the presenter's English closing the child's settled outcome carries; ``None`` from a child that
        predates it."""
        presented = _object(self.outcome.get("presented")) if self.outcome is not None else {}
        closing = presented.get("closing")
        return closing if isinstance(closing, str) else None

    @property
    def prose(self) -> str:
        """The child's answer without the presenter's closing: what a chat that words the outcome shows. A child
        that predates the framing has only its whole text."""
        closing = self.closing
        text = self.output.strip()
        if not closing or not text.endswith(closing):
            return text
        return text[: len(text) - len(closing)].rstrip()

    def settled_event(self) -> NewEvent | None:
        """WP-89: the child's settled outcome, journaled again on the parent that presents the child's answer as
        its own, so the parent's chat words it; ``None`` without the child's framing."""
        outcome = self.outcome
        if outcome is None or self.closing is None:
            return None
        deliverable = outcome.get("deliverable")
        presented = _object(outcome.get("presented"))
        wire = {k: v for k, v in outcome.items() if k not in ("answered", "presented", "deliverable")}
        data = {
            "outcome": cast("JsonValue", wire),
            "deliverable": cast("JsonValue", deliverable if isinstance(deliverable, dict) else {"kind": "answer"}),
            "presented": {"closing": self.closing, "opening": presented.get("opening") is True},
        }
        return NewEvent(OUTCOME_SETTLED, cast("dict[str, JsonValue]", data), critical=False)


@dataclass(frozen=True, slots=True)
class Briefing:
    """What the parent does with a child's end. ``instruction``: the content of the harness's instruction to the
    parent's model (``None``: none is needed); ``answer``: the run's answer, written without a model, when the
    parent has nothing of its own to answer from; ``reason``: the limit that cut the child, when one did."""

    situation: Situation
    instruction: str | None = None
    answer: str | None = None
    reason: str | None = None


class OutcomeBriefing:
    def __init__(self, words: Words = ENGLISH, presenter: OutcomePresenter = PRESENTER) -> None:
        self._w = words
        self._presenter = presenter

    def brief(self, report: ChildReport, *, upfront: bool = False) -> Briefing:
        """``upfront``: the parent handed the task off before it gathered anything of its own (WP-65)."""
        if report.failed:
            answer = self.failed_answer(report) if upfront and report.has_work else None
            return Briefing("failed", None if answer is not None else self.failed_instruction(report), answer)
        if not report.output.strip():
            return Briefing("unanswered", reason=report.truncated_by)
        if not self._answered(report):
            return Briefing("unanswered", reason=report.truncated_by)
        return Briefing("partial" if report.truncated_by else "answered", reason=report.truncated_by)

    def _answered(self, report: ChildReport) -> bool:
        if report.outcome is not None and isinstance(answered := report.outcome.get("answered"), bool):
            return answered
        # A child settled before the ledger: its answer says so in the presenter's own words.
        return not self._presenter.is_no_answer(report.output)

    def failed_instruction(self, report: ChildReport) -> str:
        if report.kept_folder is not None:
            return self._w("briefing.failed.kept", folder=report.kept_folder)
        if report.revisions > 0:
            return self._w("briefing.failed.unkept", revisions=report.revisions)
        return self._w("briefing.failed.nothing")

    def failed_answer(self, report: ChildReport) -> str:
        """The answer of a run that handed its task off up front and whose engine failed: that it is partial, why,
        what the engine saved and where, and how to try again."""
        return "\n\n".join(
            part
            for part in (self._w("briefing.answer.lead"), self._work(report), self._w("briefing.answer.retry"))
            if part
        )

    def _work(self, report: ChildReport) -> str:
        if report.kept_folder is not None:
            line = self._w("briefing.answer.kept", folder=report.kept_folder)
            if not report.kept_files:
                return line + "."
            shown = report.kept_files[:KEPT_FILES_SHOWN]
            more = report.kept_total - len(shown)
            return f"{line}: {', '.join(shown)}" + (self._w("briefing.answer.more", count=more) if more > 0 else "") + "."
        if report.revisions > 0:
            return self._w("briefing.answer.unkept", revisions=report.revisions)
        return ""


BRIEFING: Final = OutcomeBriefing()

__all__ = ["BRIEFING", "OUTCOME_KEY", "Briefing", "ChildReport", "OutcomeBriefing", "Situation"]
