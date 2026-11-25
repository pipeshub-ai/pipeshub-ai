"""The links and markers an answer may carry (CTL-2): what the loop's finalizer and an engine's presenter both run.

Imports only ``re``: an engine worker runs it on its event loop at the end of a run, where loading the legacy
streaming module (langchain and every provider SDK) once starved the lease renewal.
"""

from __future__ import annotations

import re

# Markers the frontend interprets as artifact / download cards. These are
# authored ONLY by the backend (this function) — if an answer from the LLM
# happens to contain the same syntax (directly or via prompt injection from
# RAG content), the frontend would render attacker-controlled download cards
# pointing at arbitrary URLs. Strip any pre-existing matches before we append
# our own, trusted markers.
_LLM_ARTIFACT_MARKER_RE = re.compile(
    r"::artifact\[[^\]]+\]\([^)]+\)\{[^}]*\}",
)
# Mid-form: `::artifact[name]{meta}` — has the `{meta}` block but no `(url)`.
_LLM_ARTIFACT_NO_URL_MARKER_RE = re.compile(
    r"::artifact\[[^\]]+\]\{[^}]*\}",
)
# Short-form variant LLMs sometimes hallucinate (no `(url){meta}` block).
_LLM_ARTIFACT_SHORT_MARKER_RE = re.compile(
    r"::artifact\[[^\]]+\](?:\([^)]*\))?(?!\{)",
)
_LLM_DOWNLOAD_MARKER_RE = re.compile(
    r"::download_conversation_task\[[^\]]+\]\([^)]+\)",
)

# A real, clickable download is ALWAYS delivered via the `::artifact`
# marker this module appends below — never authored by the model itself.
# When a generation step doesn't actually deliver a file (e.g. `run_code`
# wrote outside `$OUTPUT_DIR`, or failed silently) a model can still hand-
# write an ordinary markdown link like "[Download the report]
# (sandbox://report.pdf)" claiming success anyway. The frontend's
# `rehype-sanitize` schema only allows `http`/`https`/`mailto` hrefs
# (`answer-content.tsx`) and drops anything else outright, so such a link
# renders styled (colored, underlined) but is inert — worse than plain
# text since it visually promises something that doesn't work. Flatten
# markdown links to plain text (dropping the link, keeping the words) only
# when the URL has an EXPLICIT non-allowed scheme, or no scheme and is not an
# app route (`/...`) or an in-page anchor (`#...`) — a genuine citation or
# search-result link the model quotes always carries a real absolute
# http(s) URL.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
_URL_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")
_ALLOWED_LINK_SCHEMES = frozenset({"http", "https", "mailto"})
_CITATION_REF = re.compile(r"ref\d+\b")


def _flatten_unusable_markdown_links(answer: str) -> str:
    if not answer or "](" not in answer:
        return answer

    def _flatten_if_unusable(match: "re.Match[str]") -> str:
        text, url = match.group(1), match.group(2).strip()
        scheme_match = _URL_SCHEME_RE.match(url)
        if scheme_match is None:
            # An app route, an in-page anchor or a citation ref (`refN`, resolved after this runs) is kept; any other
            # relative target (a sandbox path such as `out/report.pdf`) resolves against the chat page and is dead.
            keep = url.startswith("#") or (url.startswith("/") and not url.startswith("//")) or _CITATION_REF.match(url)
            return match.group(0) if keep else text
        if scheme_match.group(1).lower() in _ALLOWED_LINK_SCHEMES:
            return match.group(0)
        return text

    return _MARKDOWN_LINK_RE.sub(_flatten_if_unusable, answer)


# A file under an engine workspace's `out/` or `in/` (instructions.py), named by its sandbox path. A bare path needs
# an extension so prose such as "log in/out" is not taken for one.
_WORKSPACE_PATH = r"(?:\./)?(?:out|in)/(?:[\w.\-]+/)*([\w.\-]+)"
# R36LIVE-FU-3: a link an engine wrote to its own sandbox (Codex's ``sandbox:/mnt/data/out/f.py``, a ``file://`` URL, an
# absolute path such as ``/workspace/out/f.py``) names the same file; only the reader's link to it differs.
_SANDBOX_TARGET = r"(?:(?:sandbox|file):(?://)?(?:/[\w.\-]+)*/|(?:/[\w.\-]+)+/(?=(?:out|in)/))?"
_WORKSPACE_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*" + _SANDBOX_TARGET + _WORKSPACE_PATH + r"\s*\)")
# A sandbox or file link outside out/ and in/ (``sandbox:/mnt/data/f.py``): the file by its name.
_SANDBOX_LINK_RE = re.compile(r"\[([^\]]*)\]\(\s*(?:sandbox|file):(?://)?(?:[^)\s]*/)?([\w.\-]+)\s*\)")
_WORKSPACE_CODE_RE = re.compile(r"`" + _WORKSPACE_PATH + r"`")
_WORKSPACE_BARE_RE = re.compile(r"(?<![\w/.\-\[(`])(?:\./)?(?:out|in)/(?:[\w.\-]+/)*([\w\-]+(?:\.[\w\-]+)*\.\w{1,8})\b")


def link_workspace_files(answer: str, files: dict[str, str]) -> str:
    """Name each sandbox file the answer mentions by its file name: linked to its artifact's record when the run
    published one (``files``: name -> record id), plain text otherwise. A sandbox path never resolves for the
    reader. Then the loop's own sanitizer runs, so an engine answer gets the same treatment as a loop answer."""
    if not answer:
        return answer

    def named(name: str) -> str:
        record_id = files.get(name)
        return f"[{name}](/record/{record_id})" if record_id else name

    def link(match: "re.Match[str]") -> str:
        label, name = match.group(1).strip(), match.group(2)
        record_id = files.get(name)
        shown = name if not label or re.fullmatch(_SANDBOX_TARGET + _WORKSPACE_PATH, label) else label
        return f"[{shown}](/record/{record_id})" if record_id else shown

    answer = _WORKSPACE_LINK_RE.sub(link, answer)
    answer = _SANDBOX_LINK_RE.sub(link, answer)
    answer = _WORKSPACE_CODE_RE.sub(lambda m: named(m.group(1)), answer)
    answer = _WORKSPACE_BARE_RE.sub(lambda m: named(m.group(1)), answer)
    return _strip_llm_authored_markers(answer)


def _strip_llm_authored_markers(answer: str) -> str:
    """Remove any LLM-authored artifact / download markers from *answer*,
    and flatten any hand-written markdown link the model composed with a
    URL scheme the frontend would render as dead (see
    `_flatten_unusable_markdown_links`).

    The frontend's marker parser runs against the full saved message, so a
    marker anywhere in the stream is a URL-spoofing primitive. We drop them
    before the backend appends its own known-good markers.
    """
    if not answer:
        return answer
    stripped = _LLM_ARTIFACT_MARKER_RE.sub("", answer)
    stripped = _LLM_ARTIFACT_NO_URL_MARKER_RE.sub("", stripped)
    stripped = _LLM_ARTIFACT_SHORT_MARKER_RE.sub("", stripped)
    stripped = _LLM_DOWNLOAD_MARKER_RE.sub("", stripped)
    return _flatten_unusable_markdown_links(stripped)


def strip_llm_authored_markers_in_parts(parts: list | None) -> None:
    """Sanitize every `text` part of a transcript in place, recursing into
    `sub_agent` children.

    :func:`_append_task_markers` only sanitizes the answer string, but the
    activity timeline renders every OTHER text part as raw markdown with no
    marker parsing (`agent-activity.tsx`). A marker the model copied out of
    an earlier turn — they are persisted in message content, so they are in
    its context — therefore surfaces there verbatim: literal
    ``::artifact[...]`` text plus an anchor whose ``record:`` href no
    browser can follow.
    """
    for part in parts or []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "sub_agent":
            strip_llm_authored_markers_in_parts(part.get("parts"))
        elif part.get("type") == "text" and part.get("content"):
            part["content"] = _strip_llm_authored_markers(part["content"])
