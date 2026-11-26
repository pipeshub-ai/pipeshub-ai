"""ADR-RUN-03: whether the answer is about what the person asked, as a typed fact the harness builds.

PipesHub's answer contract holds on every path: ask back when the subject isn't found, say plainly when it
couldn't find information, and show a confidence that reflects grounding. The classic chat keeps it in the
model's prompt; a hand-off to an engine lost all three (ANSWER-CONTRACT-1: "Prepare a risk report for 'SAAS
launch'" became a full report built from a product FAQ and an on-call runbook). This module is the one place that
decides it, from what the searches returned, never from what a model wrote:

* ``named_subject`` reads the subject the request names (a quoted phrase, else a capitalised name).
* ``assess`` matches it against the hits: found when one hit holds all its words. Relevance scores are kept for
  ordering only; they are not comparable across vector stores and search modes, the subject's own words are.
* ``Grounding`` is the typed result, journaled in a slot and carried in a job's hand-back (``GROUNDING_KEY``).
* ``not_found_line``, ``grounding_question`` and ``confidence_for`` turn it into the person's words, the question
  card and the confidence; the presenter and the hand-off read only these.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final, Literal, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from pydantic import JsonValue

# Where the typed summary travels: a job's result document, the goal slot, the hand-off's slot.
GROUNDING_KEY: Final = "grounding"
# The parent run's record of its job's grounding, written by the hand-off director (``deep_route``).
JOB_GROUNDING_SLOT: Final = "deepRoute.jobGrounding"

Status = Literal["found", "not_found", "unchecked", "failed"]
Choice = Literal["use_found", "picked", "typed"]

NEAR_MAX: Final = 3
# A subject longer than this is a sentence, not a name.
SUBJECT_MAX_WORDS: Final = 8
SUBJECT_MAX_CHARS: Final = 80
# A hit's text is matched within this many characters (a block is at most a few thousand).
HIT_TEXT_MAX_CHARS: Final = 8_000

# Both kinds of quote mark a person uses for a name; a straight single quote only as a pair around words (an
# apostrophe is not one).
_QUOTED = re.compile(r"[\"“”„«»]([^\"“”„«»\n]{1,80})[\"“”„«»]|(?<![\w'])'([^'\n]{1,80})'(?![\w'])")
# A run of capitalised words (``Project Atlas``, ``SAAS``), the subject of a request with no quotes.
_CAPITALISED = re.compile(r"\b(?:[A-Z][A-Za-z0-9&.-]*[A-Za-z0-9])(?:\s+[A-Z][A-Za-z0-9&.-]*[A-Za-z0-9])*\b")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


def _at_sentence_start(prefix: str) -> bool:
    """Whether ``prefix`` ends where a sentence or a list item starts: nothing before it, a closing mark or a line
    break and any space, or a bullet and a space. A loop over the end, not an end-anchored regex searched from every
    start position, which cost the square of a whitespace run for every capitalised word (SEC-C-18)."""
    if not prefix:
        return True
    stripped = prefix.rstrip()
    trailing = prefix[len(stripped) :]
    if "\n" in trailing:
        return True
    if not stripped:
        return False
    last = stripped[-1]
    return last in ".!?:;\n" or (last in "-*•" and trailing != "")
# Words a subject is not matched on.
_STOPWORDS: Final = frozenset(
    "a an and the of for to in on at by with from about into our my your their its this that these those is are "
    "be or as vs".split()
)
# Capitalised words that name a format, a role or a date, not something in the knowledge base.
_NOT_SUBJECTS: Final = frozenset(
    w.lower()
    for w in (
        "I", "PDF", "CSV", "XLSX", "XLS", "DOCX", "DOC", "PPTX", "JSON", "YAML", "XML", "HTML", "CSS", "SQL", "MD",
        "README", "API", "APIs", "URL", "URLs", "PR", "PRs", "AI", "LLM", "UI", "UX", "FAQ", "OK", "ASAP", "FYI",
        "TLDR", "ETA", "KPI", "KPIs", "OKR", "OKRs", "CEO", "CTO", "CFO", "COO", "VP", "HR", "IT", "QA", "PM",
        "US", "UK", "EU", "USD", "EUR", "GBP", "UTC", "Q1", "Q2", "Q3", "Q4", "H1", "H2", "TODO", "N/A",
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January", "February",
        "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
        "English", "PipesHub",
    )
)
# Directive lines a scripted model reads (``harness_core.testing.engines.script``) and JSON: never a subject.
_JSON_BEFORE = frozenset("{[,:")
_JSON_AFTER = frozenset(":,}]")


def _stem(word: str) -> str:
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def subject_terms(subject: str) -> tuple[str, ...]:
    """The words a hit must hold to be about ``subject``: its own words, without the little ones, lightly stemmed."""
    words = [_stem(w) for w in _WORDS.findall(subject)]
    terms = [w for w in words if w not in _STOPWORDS]
    return tuple(dict.fromkeys(terms or words))


def _is_name(text: str) -> bool:
    words = _WORDS.findall(text)
    return 0 < len(words) <= SUBJECT_MAX_WORDS and len(text) <= SUBJECT_MAX_CHARS and any(c.isalpha() for c in text)


def _quoted(text: str) -> str | None:
    for match in _QUOTED.finditer(text):
        before = text[: match.start()].rstrip()
        after = text[match.end() :].lstrip()
        if (before and before[-1] in _JSON_BEFORE) or (after and after[0] in _JSON_AFTER):
            continue
        span = (match.group(1) or match.group(2) or "").strip()
        if _is_name(span):
            return span
    return None


def _capitalised(text: str) -> str | None:
    for match in _CAPITALISED.finditer(text):
        words = match.group(0).split()
        if _at_sentence_start(text[: match.start()]):
            # The first word of a sentence is capitalised whatever it is; a name after it still counts.
            words = words[1:]
        words = [w for w in words if w.lower() not in _NOT_SUBJECTS]
        candidate = " ".join(words)
        if candidate and _is_name(candidate):
            return candidate
    return None


def named_subject(request: str) -> str | None:
    """The subject the request names: the first quoted phrase, else the first capitalised name that does not start a
    sentence. ``None``: the request names nothing to look for, and nothing is checked (no question is asked)."""
    lines = [line for line in request.splitlines() if line.strip() and not _directive(line)]
    text = "\n".join(lines)
    return _quoted(text) or _capitalised(text)


CARD_REPLY_PREFIX: Final = "User selections:"


def request_subject(request: str) -> str | None:
    """The subject a request is about, the answer to a grounding question included: a hand-off's request is the
    person's request followed by their card reply (``hand_off_request``), whose quotes are the card's, not theirs."""
    head, sep, reply = request.partition(CARD_REPLY_PREFIX)
    answer = grounding_answer(sep + reply) if sep else None
    if answer is not None:
        return answer.subject if answer.choice == "use_found" else answer.value
    return named_subject(head)


def _directive(line: str) -> bool:
    """A line of a scripted turn (``say …``, ``tool name {…}``): the e2e lane's model reads them, people do not."""
    head = line.strip().split(" ", 1)[0]
    bare = {"hold", "overflow"}
    worded = {"say", "tool", "tools", "shell", "write", "spawn", "skill", "todo", "usage", "fail"}
    return head in bare or (head in worded and " " in line.strip())


# The toolsets that search the organization's knowledge (``tool_loader._KNOWLEDGE_TOOLSETS``).
KNOWLEDGE_TOOLSETS: Final = ("knowledgegraph", "retrieval", "knowledgehub")


def is_knowledge_search(tool_id: str) -> bool:
    """A knowledge search by its tool id (``<source>@<instance>/<tool>``) or name: one of the knowledge toolsets'
    search tools (``knowledgegraph__search``, ``retrieval__search_internal_knowledge``, entity search)."""
    lowered = tool_id.lower()
    tool = lowered.rpartition("/")[2]
    return any(ts in lowered for ts in KNOWLEDGE_TOOLSETS) and "search" in tool


def is_web_read(tool_id: str) -> bool:
    """A web search or page read by its tool id or name (``dynamic__web_search``, ``dynamic__fetch_url``)."""
    tool = tool_id.lower().rpartition("/")[2]
    return tool.endswith(("web_search", "fetch_url"))


@dataclass(frozen=True, slots=True)
class Hit:
    """One search result as grounding reads it: the record it came from and the words it showed."""

    name: str
    text: str
    score: float = 0.0
    record_id: str = ""


def hits_from_state(tool_state: Mapping[str, Any]) -> list[Hit]:
    """The hits a run's searches showed, from its citation state (``final_results``: the blocks shown, with their
    record's name; ``tool_records``: the records shown), which the citation journal restores on every attempt."""
    hits: list[Hit] = []
    for block in cast("list[Any]", tool_state.get("final_results") or []):
        if not isinstance(block, dict):
            continue
        meta = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
        name = str(meta.get("recordName") or meta.get("origin") or "")
        connector = str(meta.get("connector") or "")
        text = " ".join(str(part) for part in (name, connector, block.get("content") or "") if part)
        score = block.get("score")
        hits.append(
            Hit(
                name=name,
                text=text[:HIT_TEXT_MAX_CHARS],
                score=float(score) if isinstance(score, int | float) else 0.0,
                record_id=str(meta.get("recordId") or meta.get("virtualRecordId") or ""),
            )
        )
    shown = {h.record_id for h in hits if h.record_id}
    for record in cast("list[Any]", tool_state.get("tool_records") or []):
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("_id") or record.get("_key") or record.get("id") or "")
        if record_id and record_id in shown:
            continue
        name = str(record.get("recordName") or record.get("name") or "")
        if name:
            hits.append(Hit(name=name, text=name, record_id=record_id))
    return hits


# At most this many connected sources are searched for a subject, and named.
PLACES_MAX: Final = 4


@dataclass(frozen=True, slots=True)
class Place:
    """A connected source whose search found the subject: its name (the MCP server's or toolset's) and the tool."""

    place: str
    tool: str


@dataclass(frozen=True, slots=True)
class Grounding:
    """Whether the run's searches found the subject the request names.

    ``status``: ``found`` (a hit holds every word of it), ``not_found`` (searches ran and none does), ``unchecked``
    (no subject named, or nothing searched) or ``failed`` (the search could not run, so nothing is known to be
    missing). ``near``: the names of hits that hold some of its words, most relevant first. ``proceeded``: the person
    was asked and chose to go on with what was found. ``places``: the connected sources searched besides the
    person's PipesHub knowledge (an MCP server's or a toolset's name), so the words name every place looked in;
    ``found_in``: those of them whose search found it (``Place``), which the job is told."""

    status: Status
    subject: str | None = None
    searches: int = 0
    hits: int = 0
    relevant: int = 0
    near: tuple[str, ...] = ()
    proceeded: bool = False
    sources: tuple[str, ...] = field(default=())
    places: tuple[str, ...] = ()
    found_in: tuple[Place, ...] = ()

    @property
    def not_found(self) -> bool:
        return self.status == "not_found"

    def to_json(self) -> dict[str, JsonValue]:
        out: dict[str, JsonValue] = {
            "status": self.status,
            "searches": self.searches,
            "hits": self.hits,
            "relevant": self.relevant,
            "near": list(self.near),
        }
        if self.subject is not None:
            out["subject"] = self.subject
        if self.proceeded:
            out["proceeded"] = True
        if self.sources:
            out["sources"] = list(self.sources)
        if self.places:
            out["places"] = list(self.places)
        if self.found_in:
            out["foundIn"] = [{"place": f.place, "tool": f.tool} for f in self.found_in]
        return out

    @classmethod
    def from_json(cls, value: object) -> Grounding | None:
        if not isinstance(value, dict):
            return None
        data = cast("dict[str, object]", value)
        status = data.get("status")
        if status not in ("found", "not_found", "unchecked", "failed"):
            return None
        subject = data.get("subject")
        near = data.get("near")
        sources = data.get("sources")
        places = data.get("places")
        found_in = data.get("foundIn")

        def count(key: str) -> int:
            raw = data.get(key)
            return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else 0

        return cls(
            status=cast("Status", status),
            subject=subject if isinstance(subject, str) and subject else None,
            searches=count("searches"),
            hits=count("hits"),
            relevant=count("relevant"),
            near=tuple(str(n) for n in cast("list[object]", near) if isinstance(n, str))[:NEAR_MAX]
            if isinstance(near, list)
            else (),
            proceeded=data.get("proceeded") is True,
            sources=tuple(str(s) for s in cast("list[object]", sources) if isinstance(s, str))
            if isinstance(sources, list)
            else (),
            places=tuple(p for p in cast("list[object]", places) if isinstance(p, str) and p)[:PLACES_MAX]
            if isinstance(places, list)
            else (),
            found_in=tuple(
                Place(str(f.get("place")), str(f.get("tool") or ""))
                for f in cast("list[Any]", found_in)
                if isinstance(f, dict) and isinstance(f.get("place"), str) and f.get("place")
            )[:PLACES_MAX]
            if isinstance(found_in, list)
            else (),
        )


UNCHECKED: Final = Grounding("unchecked")


def assess(subject: str | None, hits: Sequence[Hit], *, searches: int, failed: bool = False) -> Grounding:
    """``subject`` against ``hits``: found when one hit holds every word of it."""
    if subject is None:
        return Grounding("unchecked", searches=searches, hits=len(hits))
    if failed and not hits:
        return Grounding("failed", subject=subject, searches=searches)
    if searches == 0 and not hits:
        return Grounding("unchecked", subject=subject)
    terms = subject_terms(subject)
    if not terms:
        return Grounding("unchecked", subject=subject, searches=searches, hits=len(hits))
    relevant: list[Hit] = []
    partial: list[tuple[int, float, Hit]] = []
    for hit in hits:
        words = {_stem(w) for w in _WORDS.findall(hit.text)}
        held = sum(1 for t in terms if t in words)
        if held == len(terms):
            relevant.append(hit)
        elif held:
            partial.append((held, hit.score, hit))
    records = _distinct(h.name for h in relevant)
    partial.sort(key=lambda item: (-item[0], -item[1]))
    near = tuple(n for n in _distinct(h.name for _, _, h in partial) if n not in records)[:NEAR_MAX]
    return Grounding(
        "found" if relevant else "not_found",
        subject=subject,
        searches=max(searches, 1),
        hits=len(_distinct(h.name or h.record_id for h in hits)),
        relevant=len(records) or len(relevant),
        near=() if relevant else near,
        sources=tuple(records[:NEAR_MAX]),
    )


def _distinct(names: Iterable[str]) -> list[str]:
    return [n for n in dict.fromkeys(n.strip() for n in names) if n]


def combined(before: Grounding | None, job: Grounding | None) -> Grounding | None:
    """The run's grounding when it handed off: the job's, which searched further, unless it checked nothing; a
    person's choice to go on with what was found stays with it. A job reads only its knowledge searches, so a
    subject found before it in a connected source stays found, and the places looked in stay named."""
    if job is None or job.status in ("unchecked", "failed"):
        return before if before is not None and before.status != "unchecked" else job
    if before is not None and before.found_in and job.not_found:
        return before
    if before is not None and job.not_found and (before.proceeded or before.places):
        return dataclasses.replace(job, proceeded=job.proceeded or before.proceeded, places=before.places)
    return job


# ---- the person's words ----
# English, as the outcome catalog's are: the answer text and the card carry them for a surface with no UI, and the
# web chat words the same typed fields in the reader's language (``not_found_view``, ``grounding_card``; FU-2).

USE_FOUND_LABEL: Final = "Use what you can find"
NAME_IT_LABEL: Final = "Name a document or folder"
_QUESTION_PREFIX: Final = "I couldn't find anything about “"
_QUESTION_NEAR = _QUESTION_PREFIX + "{subject}” in your knowledge. Did you mean one of these?"
_QUESTION_NONE = _QUESTION_PREFIX + "{subject}” in your knowledge. How should I go on?"
# With connected sources searched too, the words name every place looked in: never "in your knowledge" alone.
KNOWLEDGE_PLACE: Final = "your PipesHub knowledge"
_LOOKED_IN = "I looked in {places} and couldn't find anything about “{subject}”"
_QUESTION_NEAR_IN = _LOOKED_IN + ". Did you mean one of these?"
_QUESTION_NONE_IN = _LOOKED_IN + ". How should I go on?"
_NOT_FOUND_NOTHING_IN = "**" + _LOOKED_IN + "**, so this answer is not based on any document about it."
_NOT_FOUND_TANGENTIAL_IN = (
    "**" + _LOOKED_IN + ".** What follows draws on {count} related {documents} that {verb} not mention it, so check "
    "it before you rely on it."
)


def places_words(places: Sequence[str]) -> str:
    """The places looked in, the person's PipesHub knowledge first: "your PipesHub knowledge and Atlassian Rovo"."""
    every = [KNOWLEDGE_PLACE, *places]
    return every[0] if len(every) == 1 else ", ".join(every[:-1]) + " and " + every[-1]
_INTENT = "Before starting the work, I looked for “{subject}” in what you can reach."
_NOT_FOUND_NOTHING = (
    "**I couldn't find anything about “{subject}” in your knowledge**, so this answer is not based on any of your "
    "documents about it."
)
_NOT_FOUND_TANGENTIAL = (
    "**I couldn't find anything about “{subject}” in your knowledge.** What follows draws on {count} related "
    "{documents} that {verb} not mention it, so check it before you rely on it."
)
_RESULT_NOT_FOUND = " It found nothing about “{subject}” in your knowledge."


def shown_near(grounding: Grounding) -> tuple[str, ...]:
    """The near matches the web chat names under the not-found line: the question the run could not ask (a
    schedule's run, or a turn with no card left), none when the person was asked and chose to go on. They are record
    names from the person's own search, so they travel in the typed field only (which Node keeps from anyone else
    viewing the chat), never in the answer's text."""
    return () if grounding.proceeded else grounding.near[:NEAR_MAX]


def not_found_line(grounding: Grounding | None) -> str | None:
    """The answer's opening when the subject was not found: composed here, never by a model."""
    if grounding is None or not grounding.not_found or not grounding.subject:
        return None
    places = places_words(grounding.places)
    if grounding.hits == 0:
        nothing = _NOT_FOUND_NOTHING_IN if grounding.places else _NOT_FOUND_NOTHING
        return nothing.format(subject=grounding.subject, places=places)
    plural = grounding.hits != 1
    tangential = _NOT_FOUND_TANGENTIAL_IN if grounding.places else _NOT_FOUND_TANGENTIAL
    return tangential.format(
        subject=grounding.subject,
        places=places,
        count=grounding.hits,
        documents="documents" if plural else "document",
        verb="do" if plural else "does",
    )


def not_found_view(grounding: Grounding | None) -> dict[str, JsonValue] | None:
    """The answer's typed not-found fact (``x.pipeshub.answer.finalized`` ``notFound``): what the web chat words in
    the reader's language, with ``line``, the English the answer starts with, which it leaves out (FU-2)."""
    line = not_found_line(grounding)
    if grounding is None or line is None or grounding.subject is None:
        return None
    view: dict[str, JsonValue] = {
        "subject": grounding.subject[:SUBJECT_MAX_CHARS],
        "hits": grounding.hits,
        "line": line,
    }
    near = shown_near(grounding)
    if near:
        view["near"] = [n[:_OPTION_MAX_CHARS] for n in near]
    if grounding.proceeded:
        view["proceeded"] = True
    if grounding.places:
        view["places"] = list(grounding.places)
    return view


def result_note(grounding: Grounding | None) -> str:
    """What a job's step adds to its one-line result when it found nothing about the subject."""
    if grounding is None or not grounding.not_found or not grounding.subject:
        return ""
    return _RESULT_NOT_FOUND.format(subject=grounding.subject)


_OPTION_MAX_CHARS: Final = 120
# Where a grounding question card names its typed fields, beside the tool's own payload (``toolData``).
GROUNDING_CARD_KEY: Final = "grounding"


def grounding_card(grounding: Grounding) -> dict[str, JsonValue]:
    """The grounding question's typed fields, which the web chat words in the reader's language (FU-2): the subject,
    the near matches it offers, and the labels of the card's own options, so a localized label still sends the
    English the reply is read by (``grounding_answer``)."""
    return {
        "subject": (grounding.subject or "")[:SUBJECT_MAX_CHARS],
        "near": [name[:_OPTION_MAX_CHARS] for name in grounding.near[:NEAR_MAX]],
        "useFound": USE_FOUND_LABEL,
        "nameIt": NAME_IT_LABEL,
        **({"places": cast("JsonValue", list(grounding.places))} if grounding.places else {}),
    }


def grounding_question(grounding: Grounding) -> tuple[str, dict[str, JsonValue]]:
    """The question card asked before delegating when the subject was not found: the user intent and one question,
    the near matches as options, then going on with what was found. The app adds its own 'Something else'."""
    subject = grounding.subject or ""
    options: list[dict[str, JsonValue]] = [{"label": name[:_OPTION_MAX_CHARS]} for name in grounding.near[:NEAR_MAX]]
    options.append({"label": USE_FOUND_LABEL})
    if len(options) < 2:
        options.append({"label": NAME_IT_LABEL, "isUserInput": True})
    if grounding.places:
        asked = (_QUESTION_NEAR_IN if grounding.near else _QUESTION_NONE_IN).format(
            subject=subject, places=places_words(grounding.places)
        )
    else:
        asked = (_QUESTION_NEAR if grounding.near else _QUESTION_NONE).format(subject=subject)
    question: dict[str, JsonValue] = {
        "question": asked,
        "header": "Not found",
        "options": cast("JsonValue", options),
        "multiSelect": False,
    }
    return _INTENT.format(subject=subject), question


_ANSWER_LINE = re.compile(r'^\s*\d+\.\s*"(.*)"\s*→\s*(.*)$', re.MULTILINE)
# Either wording of the grounding question ("I couldn't find anything about “X” in your knowledge." or "I looked in
# … and couldn't find anything about “X”."): the subject between the quote marks.
_ASKED_SUBJECT = re.compile(r"couldn't find anything about “(.+?)”", re.IGNORECASE)
# What the card sends for a question the person skipped (the web app's ``NO_PREFERENCE_LABEL``).
SKIPPED_ANSWER: Final = "[No preference]"


def asked_subject(question: str) -> str | None:
    """The subject a grounding question asked about, from its text (ours, composed by ``grounding_question``)."""
    asked = _ASKED_SUBJECT.search(question or "")
    return asked.group(1) if asked is not None else None


def same_subject(a: str, b: str) -> bool:
    """Two namings of one subject: the same words, whatever their case or spacing."""
    return subject_terms(a) == subject_terms(b)


def card_answers(text: str) -> dict[str, str]:
    """The answers a card reply in ``text`` holds, by ``question_key`` of their question: what a job handed the reply
    with its request answers from, instead of asking again."""
    from harness_core.gateway.asking import question_key

    return {question_key(q): a.strip() for q, a in _ANSWER_LINE.findall(text or "") if a.strip()}


# The words above a job's question in the chat: the card is the job's, asked by the chat for it (D2).
JOB_ASK_INTENT: Final = "The job working on your request needs an answer before it can go on."


@dataclass(frozen=True, slots=True)
class GroundingAnswer:
    """The person's answer to a grounding question: the subject asked about and what they chose."""

    subject: str
    choice: Choice
    value: str


def grounding_answer(reply: str) -> GroundingAnswer | None:
    """The answer to a grounding question in a card reply (``User selections:`` lines); ``None``: not one."""
    for question, answer in _ANSWER_LINE.findall(reply or ""):
        asked = _ASKED_SUBJECT.search(question)
        if asked is None:
            continue
        value = answer.strip()
        # Skipping the question is an answer too: go on with what was found, and never ask it again.
        if not value or value.lower() in (USE_FOUND_LABEL.lower(), SKIPPED_ANSWER.lower()):
            return GroundingAnswer(asked.group(1), "use_found", USE_FOUND_LABEL)
        return GroundingAnswer(asked.group(1), "picked" if _is_name(value) else "typed", value)
    return None


# ---- confidence ----

def confidence_for(grounding: Grounding | None, *, cited: int) -> str | None:
    """The answer's confidence from grounding (ADR-RUN-03 D3): ``Low`` when the subject was not found or nothing
    searched was cited; ``High`` when several records about it were found and cited; ``Medium`` between. ``None``
    when there is nothing to judge from (no search ran, nothing cited): never a default value."""
    if grounding is None:
        return None
    if grounding.not_found:
        return "Low"
    if grounding.status == "failed":
        return "Low" if cited == 0 else "Medium"
    if grounding.status == "found":
        if cited == 0:
            return "Low"
        return "High" if grounding.relevant >= 2 and cited >= 2 else "Medium"
    # Unchecked: no subject was named; the citations are the only grounding.
    if cited == 0:
        return "Low" if grounding.searches > 0 else None
    return "High" if cited >= 3 else "Medium"


_ORDER: Final = ("Low", "Medium", "High", "Very High")


def capped(level: str | None, cap: str | None) -> str | None:
    """``level`` no higher than ``cap``: a model's own rating never claims more than its grounding allows."""
    if cap is None:
        return level
    if level not in _ORDER:
        return cap
    return level if _ORDER.index(level) <= _ORDER.index(cap) else cap


__all__ = [
    "CARD_REPLY_PREFIX",
    "GROUNDING_CARD_KEY",
    "GROUNDING_KEY",
    "JOB_GROUNDING_SLOT",
    "KNOWLEDGE_PLACE",
    "NAME_IT_LABEL",
    "PLACES_MAX",
    "UNCHECKED",
    "USE_FOUND_LABEL",
    "Grounding",
    "GroundingAnswer",
    "Hit",
    "Place",
    "JOB_ASK_INTENT",
    "SKIPPED_ANSWER",
    "asked_subject",
    "assess",
    "capped",
    "card_answers",
    "combined",
    "confidence_for",
    "grounding_answer",
    "grounding_card",
    "grounding_question",
    "hits_from_state",
    "is_knowledge_search",
    "is_web_read",
    "named_subject",
    "not_found_line",
    "not_found_view",
    "places_words",
    "request_subject",
    "result_note",
    "same_subject",
    "shown_near",
    "subject_terms",
]
