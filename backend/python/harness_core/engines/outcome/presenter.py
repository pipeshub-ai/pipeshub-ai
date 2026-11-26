"""``OutcomePresenter``: the only producer of the words a person reads about a run's outcome (ADR-RUN-01 D1).

It turns a ``RunOutcome`` into the answer's opening and closing block, the banner over a partial file, the status
line and the action offered ("continue" only when there is material to continue from; "retry" when nothing was
gathered). Every word comes from the locale catalog (``words``). Kernel branches record facts and ask it; they
compose no text of their own, so the words cannot claim more than the ledger holds ("everything it
gathered is saved" when nothing had been gathered).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from harness_core.engines.outcome.outcome import UNFINISHED, Completed, Failed, Partial
from harness_core.engines.outcome.words import ENGLISH

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from harness_core.engines.outcome.ledger import SourceGathered
    from harness_core.engines.outcome.outcome import Action, PartialReason, RunOutcome
    from harness_core.engines.outcome.words import Words

# WP-70: an answer longer than this is the saved file's; the chat gets its opening section and the file's name.
ANSWER_SUMMARY_CHARS: Final = 8_000
# What the answer of a run that owes its file lists of the sources it gathered, at most.
SOURCES_SHOWN: Final = 20
TOOLS_SHOWN: Final = 8
LIMITS: Final = ("turns", "budget", "time", "stall", "provider", "engine_error", "secret_blocked")
_SECTION = re.compile(r"^#{1,2} ", re.MULTILINE)
_BANNER_PREFIX: Final = "> **Partial**: "


@dataclass(frozen=True, slots=True)
class Framed:
    """An outcome's answer in two parts (WP-89): ``prose``, the model's own words (or the opening of the file it
    wrote, ``opening``), and ``closing``, this presenter's words about the outcome after it. The web chat shows the
    prose and words the outcome itself from the settled outcome, in the reader's language; a surface with no UI
    (the Slack bot, an API text consumer, a parent's model) reads ``text``, both joined, in English."""

    prose: str
    closing: str = ""
    opening: bool = False

    @property
    def text(self) -> str:
        if not self.closing:
            return self.prose
        return f"{self.prose}\n\n{self.closing}" if self.prose else self.closing


@dataclass(frozen=True, slots=True)
class Presentation:
    """What the person reads: the chat's ``answer`` (``framed``'s text), the run's one-line ``status`` ("" when it
    completed) and the ``action`` the chat offers."""

    answer: str
    status: str
    action: Action
    framed: Framed


class OutcomePresenter:
    def __init__(self, words: Words = ENGLISH, *, handoff_path: str = "notes/handoff.json") -> None:
        self._w = words
        self._handoff = handoff_path

    def present(self, outcome: RunOutcome, *, body: str = "") -> Presentation:
        """``body``: the result text the outcome frames (the engine's answer, its file's text, or the wrap-up's)."""
        framed = self.frame(outcome, body=body)
        return Presentation(framed.text, self.status(outcome), outcome.action, framed)

    def status(self, outcome: RunOutcome) -> str:
        if isinstance(outcome, Partial) and outcome.reason in LIMITS:
            return self._w(f"outcome.reason.{outcome.reason}.notice")
        return ""

    def answer(self, outcome: RunOutcome, *, body: str = "") -> str:
        return self.frame(outcome, body=body).text

    def frame(self, outcome: RunOutcome, *, body: str = "") -> Framed:
        text = body.strip()
        match outcome:
            case Completed() | Failed():
                return Framed(text)
            case Partial():
                return self._partial(outcome, text)

    def _partial(self, outcome: Partial, text: str) -> Framed:
        file = outcome.delivered.file
        if file is not None and outcome.reason == UNFINISHED:
            return self._opened(text, file.name, f"---\n{self._w('outcome.unfinished.written', name=file.name)}")
        if file is not None:
            by_engine = file.by == "engine"
            closing = self._closing(
                outcome.reason, written=file.name if by_engine else None, saved=None if by_engine else file.name
            )
            return self._opened(text, file.name, closing)
        if outcome.missing and (outcome.reason == UNFINISHED or not text):
            # Nothing stands in for the file: a text the run could not save as it is not the file.
            return Framed("", self._unwritten(outcome, outcome.missing[0]))
        if text:
            return Framed(text, self._closing(outcome.reason))
        return Framed("", self.no_answer(outcome.reason))

    def _opened(self, text: str, saved_file: str, closing: str) -> Framed:
        prose, cut = self._opening(text, saved_file)
        if not cut:
            return Framed(prose, closing)
        return Framed(prose, f"{self._w('outcome.opening.full_report', name=saved_file)}\n\n{closing}", opening=True)

    def failure(self, outcome: Failed) -> str:
        """The English sentence of a run that ends failed with a code of its own words (``outcome.failed.<code>``):
        Node's run error message for surfaces with no UI; the web chat words the same code and servers itself
        (WP-84, WP-89)."""
        return self._w(f"outcome.failed.{outcome.reason}", servers=self._listed(outcome.servers))

    def _listed(self, names: Sequence[str]) -> str:
        if len(names) <= 2:
            return self._w("outcome.list.and").join(names)
        return self._w("outcome.list.comma").join(names[:-1]) + self._w("outcome.list.and") + names[-1]

    def _closing(self, reason: PartialReason, *, written: str | None = None, saved: str | None = None) -> str:
        lines = ["---", f"{self._w('outcome.partial.title')} {self._w(f'outcome.reason.{reason}.notice')}"]
        if saved:
            lines.append(self._w("outcome.partial.saved", name=saved))
        elif written:
            lines.append(self._w("outcome.partial.written", name=written))
        lines.append(self._w(f"outcome.reason.{reason}.resume"))
        return "\n".join(lines)

    def opening(self, text: str, saved_file: str | None) -> str:
        """The chat's part of a result saved as ``saved_file``: all of it, or when it is long, its opening up to its
        second section (bounded) and a line naming the file."""
        prose, cut = self._opening(text, saved_file)
        if not cut or saved_file is None:
            return prose
        return f"{prose}\n\n{self._w('outcome.opening.full_report', name=saved_file)}"

    @staticmethod
    def _opening(text: str, saved_file: str | None) -> tuple[str, bool]:
        """``text``, or when it is a long result saved as ``saved_file``, its opening (and ``True``)."""
        text = text.strip()
        if saved_file is None or len(text) <= ANSWER_SUMMARY_CHARS:
            return text, False
        starts = [m.start() for m in _SECTION.finditer(text)]
        # Past a leading title, the opening is the first section (an executive summary, as reports start).
        skip = 2 if starts and starts[0] == 0 and text.startswith("# ") else 1
        cut = starts[skip] if len(starts) > skip else None
        if cut is not None and cut <= ANSWER_SUMMARY_CHARS:
            opening = text[:cut]
        else:
            opening = text[:ANSWER_SUMMARY_CHARS]
            paragraph = opening.rfind("\n\n")
            opening = opening[:paragraph] if paragraph > ANSWER_SUMMARY_CHARS // 2 else opening
        return opening.rstrip(), True

    def _unwritten(self, outcome: Partial, name: str) -> str:
        """The answer of a run that owes ``name`` and has nothing to stand in for it: that it is not written, what was
        gathered and whether it was kept, and the action that matches."""
        cut = outcome.reason != UNFINISHED
        because = {"because": self._w(f"outcome.reason.{outcome.reason}.because")} if cut else {}
        delivered = outcome.delivered
        if not delivered.material:
            lead = self._w(f"outcome.unwritten.lead.nothing.{'cut' if cut else 'end'}", name=name, **because)
            return f"{lead}\n\n{self._w('outcome.unwritten.retry')}"
        lines = [self._w(f"outcome.unwritten.lead.{'cut' if cut else 'end'}", name=name, **because)]
        tools = self._tool_counts(delivered.sources)
        if tools:
            lines.extend(["", self._w("outcome.unwritten.gathered", tools=tools)])
        lines.extend(self._sources(delivered.sources))
        closing = (
            self._w("outcome.unwritten.kept", handoff=self._handoff, name=name)
            if delivered.kept
            else self._w("outcome.unwritten.lost")
        )
        lines.extend(["", closing])
        return "\n".join(lines)

    def _tool_counts(self, sources: Sequence[SourceGathered]) -> str:
        ranked = _ranked(plain_tool_label(s.tool) for s in sources)
        return ", ".join(
            self._w("outcome.unwritten.calls.one", tool=tool)
            if count == 1
            else self._w("outcome.unwritten.calls.many", tool=tool, count=count)
            for tool, count in ranked[:TOOLS_SHOWN]
        )

    def _sources(self, sources: Sequence[SourceGathered]) -> list[str]:
        pages = [s for s in sources if s.url is not None]
        if not pages:
            return []
        shown = pages[:SOURCES_SHOWN]
        lines = ["", self._w("outcome.unwritten.sources"), *(f"- [{s.title or s.url}]({s.url})" for s in shown)]
        if len(pages) > len(shown):
            lines.append(self._w("outcome.unwritten.more", count=len(pages) - len(shown)))
        return lines

    def banner(self, reason: PartialReason, *, synthesized: bool) -> str:
        """The first line of a partial file the run saves for a task ``reason`` stopped. ``synthesized``: its
        wrap-up wrote it from what was gathered, so it is whole as far as the sources go."""
        limit = self._w(f"outcome.reason.{reason}.limit")
        return self._w("outcome.banner.synthesized" if synthesized else "outcome.banner.cut", limit=limit)

    def partial_file(self, reason: PartialReason, body: str, *, synthesized: bool) -> str:
        return f"{self.banner(reason, synthesized=synthesized)}\n\n{body.strip()}\n"

    @staticmethod
    def without_banner(text: str) -> str:
        """A partial file's text without the banner ``partial_file`` put over it."""
        if not text.startswith(_BANNER_PREFIX):
            return text
        return text.split("\n", 1)[1].strip() if "\n" in text else ""

    def no_answer(
        self,
        reason: PartialReason,
        tool_names: Iterable[str] = (),
        *,
        saved: Sequence[str] = (),
        sources: Sequence[SourceGathered] = (),
    ) -> str:
        """The answer of a run whose limit ran out when no model call could write one: that it stopped, what it had
        done (``gathered``) and how to carry on. Deterministic, so it needs no budget."""
        done = self.gathered(tool_names, saved=saved, sources=sources)
        lines = [self._w(f"outcome.reason.{reason}.stopped"), *(["", done] if done else [])]
        lines.extend(["", self._w(f"outcome.reason.{reason}.continue")])
        return "\n".join(lines)

    def gathered(
        self, tool_names: Iterable[str] = (), *, saved: Sequence[str] = (), sources: Sequence[SourceGathered] = ()
    ) -> str:
        """What a run cut short before its answer had done, in plain words (R36COVER-FU-5, R50JFIX-1): the files it
        saved, what it had gathered (each tool by a plain label, never its id) and the pages it read, as an engine
        job's cut names them (``_sources``); "" when it did nothing. The body of ``no_answer``, and the prose a
        settled cut-short loop answer carries, so every surface shows the same facts (``loop_settled``)."""
        lines: list[str] = []
        named = list(dict.fromkeys(name for name in saved if name))
        if named:
            lines.append(self._w("outcome.fallback.saved", names=", ".join(f"**{name}**" for name in named)))
        ranked = _ranked(plain_tool_label(name) for name in tool_names if name)
        if ranked:
            lines.extend([*([""] if lines else []), self._w("outcome.fallback.gathered")])
            lines.extend(
                self._w("outcome.fallback.tool.one", tool=tool)
                if count == 1
                else self._w("outcome.fallback.tool.many", tool=tool, count=count)
                for tool, count in ranked[:TOOLS_SHOWN]
            )
            if len(ranked) > TOOLS_SHOWN:
                lines.append(self._w("outcome.fallback.more", count=len(ranked) - TOOLS_SHOWN))
        pages = self._sources(sources)
        lines.extend(pages if lines else pages[1:])
        return "\n".join(lines)

    def is_no_answer(self, text: str) -> bool:
        """Whether ``text`` is an answer this presenter wrote for a run that stopped before its answer was written:
        ``no_answer``'s, or one that owes its file (a parent recognises it and writes the file itself)."""
        lead = text.lstrip()
        if any(lead.startswith(self._w(f"outcome.reason.{r}.stopped")) for r in LIMITS):
            return True
        first = lead.split("\n", 1)[0]
        return first.startswith("**") and self._w("outcome.unwritten.mark") in first


def plain_tool_label(name: str) -> str:
    """A tool as a person reads it (R36COVER-FU-5): its own name without the toolset prefix the model sees and without
    underscores, so ``artifacts__save_artifact`` reads "Save artifact" and ``web_page`` "Web page"."""
    words = name.rsplit("__", 1)[-1].rsplit("/", 1)[-1].replace("_", " ").replace("-", " ").strip()
    return (words[:1].upper() + words[1:]) if words else name


def _ranked(names: Iterable[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


PRESENTER: Final = OutcomePresenter()

__all__ = [
    "ANSWER_SUMMARY_CHARS",
    "PRESENTER",
    "SOURCES_SHOWN",
    "Framed",
    "OutcomePresenter",
    "Presentation",
    "plain_tool_label",
]
