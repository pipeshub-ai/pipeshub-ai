"""The engine's answer, with its citations in the one form the answer pipeline resolves (S1b, G2-F1/F2).

An engine run reads knowledge through the run's own tools (the tool gateway), so
every block it read reached it labelled ``[index|refN]`` inside a ``<record>``
element that names the record. Engines cite in many forms (the label itself, numbered
footnotes with a sources list, titles, record ids, links to the record); today's
answer pipeline resolves only ``[label](refN)``. ``rewrite_engine_citations``
maps every form it recognises to the ``refN`` of a record the engine read in
this run, and removes (and counts) every citation that names no such record,
including a ``refN`` the request's mapper holds but this engine never saw.

Pure functions over text; the read set comes from what the run's calls returned (``EngineAnswer``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.utils.citations import extract_tiny_ref, normalize_malformed_citations
from app.utils.text_fragments import split_fragment_directive
from harness_core.gateway.memory import refs_in

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

_RECORD = re.compile(r"<record>(.*?)(?:</record>|(?=<record>)|\Z)", re.DOTALL)
# Every pattern here reads an engine's answer on the worker's loop, so each is linear (SEC-C-18): no two
# whitespace runs that can split one run between them, and a value ends at its last non-space by a greedy
# `.*\S`, not a lazy `.*?` that rescans the trailing spaces at every step.
_HEADER = re.compile(r"^[ \t]*(?:[-*][ \t]*)?([A-Za-z][A-Za-z ]{0,30}?)[ \t]*:[ \t]*(\S(?:.*\S)?)[ \t\r]*$", re.MULTILINE)
_TITLE_KEYS = frozenset({"record", "name", "title", "record name"})
_ID_KEYS = frozenset({"record id", "external id", "id"})
_URL_KEYS = frozenset({"web url", "url"})
_RECORD_IN_URL = re.compile(r"/record/([^/?#\s]+)")
_URL = re.compile(r"https?://[^\s<>\]\)\"']+")
_ID_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*[A-Za-z0-9]")
# A title shorter than this matches too much ordinary text to be read as a citation.
_MIN_TITLE = 4

_BLOCK_LABEL = re.compile(r"\[\d+\|(ref\d+)\]")
# A label holds no `[` and a link target none: otherwise a run of `[` (or of `[](https://`) is rescanned from each
# one (SEC-C-18).
_REF_LINK = re.compile(r"(?P<space>[ \t]?)\[(?P<label>[^\[\]]*)\]\((?P<target>ref\d+|https?://ref\d+\.xyz/?)\)")
_URL_LINK = re.compile(r"\[(?P<label>[^\[\]]*)\]\((?P<target>https?://[^)\s\[]+)\)")
_MARKER = re.compile(r"(?P<space>[ \t]?)(?<![\w\]\[])\[\^?(?P<nums>\d{1,3}(?:[ \t]*,[ \t]*\^?\d{1,3})*)\](?![(:])")
_DEFINITION = re.compile(r"^[ \t]*(?:[-*][ \t]+)?\[\^?(?P<num>\d{1,3})\]:?[ \t]+(?P<body>\S(?:.*\S)?)[ \t\r]*$")
_NUMBERED = re.compile(r"^[ \t]*(?P<num>\d{1,3})[.)][ \t]+(?P<body>\S(?:.*\S)?)[ \t\r]*$")
_BULLET = re.compile(r"^[ \t]*[-*][ \t]+(?P<body>\S(?:.*\S)?)[ \t\r]*$")
_SOURCES_HEADING = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__)?(?:sources|references|citations|footnotes)(?:\*\*|__)?(?:[ \t]*:)?(?:[ \t]*(?:\*\*|__))?[ \t]*$",
    re.IGNORECASE,
)
# "… Sources: [1] Title A; [2] Title B" on one line, as engines also write it.
_INLINE_SOURCES = re.compile(
    r"(?<![ \t])[ \t]*(?:\*\*|__)?\b(?:sources|references|citations|footnotes)(?:\*\*|__)?[ \t]*:[ \t]*(?=\[\^?\d)",
    re.IGNORECASE,
)
# The body runs to the next item, tempered on the item's own opening; its padding is stripped in code (SEC-C-18).
_INLINE_ITEM = re.compile(r"\[\^?(\d{1,3})\]:?((?:(?!\[\^?\d{1,3}\]).)*)")
_MENTION = re.compile(r"[\(\[][ \t]*(?:sources?|record(?:[ \t]+id)?|references?|ref)[ \t]*:[ \t]*", re.IGNORECASE)
_CLOSE = {"(": ")", "[": "]"}


def _norm(text: str) -> str:
    return " ".join(text.replace("*", " ").replace("`", " ").replace("_", " ").casefold().split())


@dataclass(frozen=True, slots=True)
class ReadRecord:
    """One record the engine read: its refs in the order it saw them, and what names it."""

    refs: tuple[str, ...]
    titles: frozenset[str] = frozenset()
    ids: frozenset[str] = frozenset()
    urls: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ReadRecords:
    records: tuple[ReadRecord, ...] = ()

    @property
    def refs(self) -> frozenset[str]:
        return frozenset(ref for record in self.records for ref in record.refs)

    def ref_for(self, fragment: str) -> str | None:
        """The ref a citation fragment names: a read ref in it, else the first ref of the record its URL, id or
        (longest) title names."""
        read = self.refs
        for ref in refs_in(fragment):
            if ref in read:
                return ref
        record = self._by_url(fragment) or self._by_id(fragment) or self._by_title(fragment)
        return record.refs[0] if record is not None else None

    def _by_url(self, fragment: str) -> ReadRecord | None:
        for raw in _URL.findall(fragment):
            url = raw.rstrip(".,;:")
            inner = _RECORD_IN_URL.search(url)
            for record in self.records:
                if url in record.urls or (inner is not None and inner.group(1) in record.ids):
                    return record
        return None

    def _by_id(self, fragment: str) -> ReadRecord | None:
        tokens = set(_ID_TOKEN.findall(fragment))
        for record in self.records:
            if any(i in tokens for i in record.ids if not (i.isdigit() and len(i) < 4)):
                return record
        return None

    def _by_title(self, fragment: str) -> ReadRecord | None:
        text = _norm(fragment)
        best: tuple[int, ReadRecord] | None = None
        for record in self.records:
            for title in record.titles:
                if len(title) >= _MIN_TITLE and title in text and (best is None or len(title) > best[0]):
                    best = (len(title), record)
        return best[1] if best is not None else None


def _unescaped(text: str) -> str:
    # A structured tool result reaches the engine as JSON text; its record headers are on escaped lines.
    return text.replace("\\n", "\n") if "\\n" in text and "\n" not in text else text


def _record(chunk: str, ref_to_url: Mapping[str, str]) -> ReadRecord | None:
    refs = refs_in(chunk)
    if not refs:
        return None
    header = re.split(r"Record blocks", chunk, maxsplit=1)[0]
    titles: set[str] = set()
    ids: set[str] = set()
    urls: set[str] = set()
    for match in _HEADER.finditer(header):
        key, value = match.group(1).strip().casefold(), match.group(2).strip()
        if key in _TITLE_KEYS:
            titles.add(_norm(value))
        elif key in _ID_KEYS and value.upper() != "N/A":
            ids.add(value)
        elif key in _URL_KEYS:
            urls.add(value)
    for ref in refs:
        inner = _RECORD_IN_URL.search(ref_to_url.get(ref, ""))
        if inner is not None:
            ids.add(inner.group(1))
    return ReadRecord(refs, frozenset(titles), frozenset(ids), frozenset(urls))


def _loose(ref: str, url: str) -> ReadRecord:
    """A ref outside a ``<record>`` (a web block): named by its record id, and by its link with and without the
    text fragment, which is what an engine shown the page's ``url:`` field writes (ENGCITE)."""
    inner = _RECORD_IN_URL.search(url)
    urls = frozenset(u for u in (url, split_fragment_directive(url)[0]) if u)
    return ReadRecord((ref,), ids=frozenset([inner.group(1)] if inner else []), urls=urls)


def records_read(texts: Iterable[str], ref_to_url: Mapping[str, str]) -> ReadRecords:
    """The records behind the refs in ``texts`` (what a tool returned to the engine), in first-seen order."""
    records: list[ReadRecord] = []
    for raw in texts:
        text = _unescaped(raw)
        inside: set[str] = set()
        for match in _RECORD.finditer(text):
            record = _record(match.group(1), ref_to_url)
            if record is not None:
                records.append(record)
                inside.update(record.refs)
        records.extend(_loose(ref, ref_to_url.get(ref, "")) for ref in refs_in(text) if ref not in inside)
    return ReadRecords(tuple(records))


@dataclass(frozen=True, slots=True)
class CitationRewrite:
    text: str
    kept: int = 0
    mapped: int = 0
    dropped: int = 0


@dataclass
class _Counts:
    mapped: int = 0
    dropped: int = 0


def _link(ref: str) -> str:
    return f"[source]({ref})"


def _links(fragments: Iterable[str], read: ReadRecords, counts: _Counts) -> str:
    refs: list[str] = []
    for fragment in fragments:
        ref = read.ref_for(fragment)
        if ref is None:
            counts.dropped += 1
        elif ref not in refs:
            refs.append(ref)
            counts.mapped += 1
    return " ".join(_link(r) for r in refs)


def _sources_section(lines: list[str]) -> tuple[int, int] | None:
    """The last sources section: its heading line and the end (exclusive) of its items."""
    for start in range(len(lines) - 1, -1, -1):
        if not _SOURCES_HEADING.match(lines[start]):
            continue
        end = start + 1
        while end < len(lines) and (
            not lines[end].strip() or _DEFINITION.match(lines[end]) or _NUMBERED.match(lines[end]) or _BULLET.match(lines[end])
        ):
            end += 1
        if any(lines[i].strip() for i in range(start + 1, end)):
            return start, end
    return None


def _split_inline_sources(text: str) -> str:
    lines: list[str] = []
    for line in text.split("\n"):
        match = _INLINE_SOURCES.search(line)
        if match is None:
            lines.append(line)
            continue
        found = ((n, raw.lstrip(" \t").rstrip(" \t;,")) for n, raw in _INLINE_ITEM.findall(line[match.end() :]))
        items = [f"[{n}] {body}" for n, body in found if body]
        lines.extend([line[: match.start()].rstrip(), "", "Sources:", *items])
    return "\n".join(lines)


def _footnotes(text: str, read: ReadRecords, counts: _Counts) -> str:
    lines = _split_inline_sources(text).split("\n")
    section = _sources_section(lines)
    definitions: dict[str, str] = {}
    definition_lines: set[int] = set()
    bullets: dict[int, str] = {}
    for index, line in enumerate(lines):
        in_section = section is not None and section[0] < index < section[1]
        match = _DEFINITION.match(line) or (_NUMBERED.match(line) if in_section else None)
        if match is not None:
            definitions.setdefault(match.group("num"), match.group("body"))
            definition_lines.add(index)
        elif in_section and (bullet := _BULLET.match(line)) is not None:
            bullets[index] = bullet.group("body")
    if not definitions and not bullets:
        return text
    used: set[str] = set()

    def marker(match: re.Match[str]) -> str:
        nums = [n.strip().lstrip("^") for n in match.group("nums").split(",")]
        if not all(n in definitions for n in nums):
            return match.group(0)
        used.update(nums)
        links = _links((definitions[n] for n in nums), read, counts)
        return match.group("space") + links if links else ""

    heading = section[0] if section is not None else -1
    body_lines = [line for i, line in enumerate(lines) if i not in definition_lines and i not in bullets and i != heading]
    body = _MARKER.sub(marker, "\n".join(body_lines))
    # A sources entry no marker pointed at is a citation of the whole answer: kept as a link at the end.
    trailing = [b for n, b in definitions.items() if n not in used] + list(bullets.values())
    tail = _links(trailing, read, counts)
    return body.rstrip() + ("\n\n" + tail if tail else "") + ("\n" if text.endswith("\n") else "")


def _mentions(text: str, read: ReadRecords, counts: _Counts) -> str:
    out: list[str] = []
    cursor = 0
    for match in _MENTION.finditer(text):
        if match.start() < cursor:
            continue
        opener = text[match.start()]
        depth, end = 1, match.end()
        while end < len(text) and depth:
            if text[end] == opener:
                depth += 1
            elif text[end] == _CLOSE[opener]:
                depth -= 1
            end += 1
        if depth:
            continue
        inner = text[match.end() : end - 1]
        out.append(text[cursor : match.start()].rstrip(" \t"))
        links = _links((part for part in inner.split(";") if part.strip()), read, counts)
        out.append((" " + links) if links else "")
        cursor = end
    out.append(text[cursor:])
    return "".join(out)


def _url_links(text: str, read: ReadRecords, counts: _Counts) -> str:
    def replace(match: re.Match[str]) -> str:
        target = match.group("target")
        if extract_tiny_ref(target) is not None:
            return match.group(0)
        record = read._by_url(target)  # pyright: ignore[reportPrivateUsage]
        if record is None:
            return match.group(0)
        counts.mapped += 1
        return f"[{match.group('label')}]({record.refs[0]})"

    return _URL_LINK.sub(replace, text)


def _ref_links(text: str) -> int:
    return len(_REF_LINK.findall(text))


def rewrite_engine_citations(text: str, read: ReadRecords, *, known: frozenset[str] = frozenset()) -> CitationRewrite:
    """``known``: refs the engine was given outside ``read`` (a durable run's refs from its earlier attempts, whose
    texts this attempt did not see); a ``[label](refN)`` naming one is kept, never mapped to."""
    if not text:
        return CitationRewrite(text)
    counts = _Counts()
    labelled = _BLOCK_LABEL.subn(lambda m: _link(m.group(1)), text)
    out, counts.mapped = labelled
    out = _footnotes(out, read, counts)
    out = _url_links(out, read, counts)
    out = _mentions(out, read, counts)
    before = _ref_links(out)
    out = normalize_malformed_citations(out)
    counts.mapped += max(0, _ref_links(out) - before)
    citable = read.refs | known

    def keep(match: re.Match[str]) -> str:
        ref = extract_tiny_ref(match.group("target")) or match.group("target")
        if ref in citable:
            # The old https://refN.xyz alias as its ref: the one form a parent renumbering this run's refs reads.
            return f"{match.group('space')}[{match.group('label')}]({ref})"
        counts.dropped += 1
        return ""

    out = _REF_LINK.sub(keep, out)
    return CitationRewrite(out, _ref_links(out), counts.mapped, counts.dropped)


__all__ = ["CitationRewrite", "ReadRecord", "ReadRecords", "records_read", "rewrite_engine_citations"]
