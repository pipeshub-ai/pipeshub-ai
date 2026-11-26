"""What an engine run a limit cut short leaves for the run that continues it (SPENDCONT).

A Codex ``code_task`` child made 131 Jira calls, its budget stopped it, and its wrap-up wrote an
8.3 KB partial inventory as its answer; nothing was saved. The person's "continue" started a child whose workspace
held none of it, so it searched Jira again from scratch and stopped at the same limit, twice.

A cut-short run now commits a handoff to its workspace before it completes, as the worker of a long task keeps a
progress file (Anthropic's long-running harnesses: a progress log and a feature list the next session reads first;
Codex's compaction prompt: a handoff summary for the model that resumes):

* ``notes/handoff.json``: the request, why it stopped, the deliverable, and every tool call that returned, each with
  its result saved under ``notes/fetched/`` (named by the call's arguments digest, so a repeated call is one file);
  a continuation's handoff keeps the earlier runs' calls;
* ``notes/partial-answer.md``: the run's partial answer;
* the deliverable, when the request asked for a file the engine did not write: the partial answer under ``out/``,
  named as requested, with a banner that says it is partial. It is published like any file the engine writes.

A ``code_task`` child's fork merges into the session's workspace when its parent joins it, so the next engine run of
the conversation finds all of it; ``briefing`` tells it to read the handoff first and do only what is missing.
Product-neutral: no tool or connector is named; executor-neutral: every engine reads files.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from harness_core.artifacts.types import OutputFile
from harness_core.engines.outcome.deliverable import (
    TEXT_TYPES,
    Deliverable,
    resolve_deliverable,
)
from harness_core.engines.outcome.ledger import (
    PLACEHOLDER_MAX_BYTES,
    is_placeholder_text,
)
from harness_core.engines.outcome.presenter import PRESENTER
from harness_core.security.token_patterns import redact_secrets_in
from harness_core.workspace.changes import ChangeSet
from harness_core.workspace.types import ObjectScope, WorkspaceFile

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.artifacts.ports import ArtifactPublisher
    from harness_core.artifacts.types import Derivation, PublishReport
    from harness_core.engines.outcome.outcome import PartialReason
    from harness_core.interactions.ports import RunBinding
    from harness_core.workspace.ports import BinaryObjectStore, WorkspaceStore
    from harness_core.workspace.types import WorkspaceManifest

HANDOFF_PATH = "notes/handoff.json"
PARTIAL_ANSWER_PATH = "notes/partial-answer.md"
FETCHED_DIR = "notes/fetched"
OUTPUTS_DIR = "out"
HANDOFF_SCHEMA = 1
STOPPED_EARLY = "stopped_early"
COMPLETED = "completed"
# Three such children fetched 3.5 MB, 4.4 MB and 1.9 MB of tool results; past this a call is listed without its file.
MAX_FETCHED_BYTES = 8 * 1024 * 1024
MAX_REQUEST_CHARS = 4_000
MAX_ARGUMENT_CHARS = 2_000
# What the continuing run's first prompt shows of the handoff; the file holds the rest.
MAX_SUMMARY_CHARS = 6_000
MAX_SUMMARY_ARGUMENT_CHARS = 160
# The earlier partial result a wrap-up extends, at most (the rest stays in the workspace).
MAX_EARLIER_CHARS = 24_000
_SLUG = re.compile(r"[^A-Za-z0-9]+")


@dataclass(frozen=True, slots=True)
class DoneCall:
    """One tool call that returned: the tool, its arguments when this attempt saw them, the result's text."""

    tool: str
    args_digest: str
    text: str
    arguments: Mapping[str, JsonValue] | None = None


@dataclass(frozen=True, slots=True)
class HandoffSaved:
    """What the handoff committed: the workspace revision, the deliverable's name (``None``: none written)."""

    manifest: WorkspaceManifest
    deliverable: str | None
    publication: PublishReport | None


def source_of(call: DoneCall) -> tuple[str | None, str | None]:
    """Where a call's result came from, as ``(url, title)`` when its arguments or its result name one (a web page,
    a search's own query is no source); ``(None, None)`` otherwise. Product-neutral: any tool keyed ``url``."""
    url = call.arguments.get("url") if call.arguments is not None else None
    title: str | None = None
    try:
        document = json.loads(call.text)
    except ValueError:
        document = None
    if isinstance(document, dict):
        found = cast("dict[str, JsonValue]", document)
        if not isinstance(url, str) and isinstance(found.get("url"), str):
            url = found["url"]
        if isinstance(found.get("title"), str):
            title = cast("str", found["title"]).strip() or None
    return (url if isinstance(url, str) and url.startswith(("http://", "https://")) else None), title


# WP-70: the engine's own notes a wrap-up reads (text files it kept outside in/, out/ and the handoff's files), each
# at most NOTE_MAX_BYTES and all of them NOTES_MAX_BYTES; the prompt then cuts them to its share.
NOTE_MAX_BYTES = 64 * 1024
NOTES_MAX_BYTES = 256 * 1024
_NOTE_SUFFIXES = (".md", ".markdown", ".txt")


def is_engine_note(path: str) -> bool:
    """Whether ``path`` is a note the engine kept: a text file outside in/, out/ and what the handoff writes."""
    if not path.lower().endswith(_NOTE_SUFFIXES):
        return False
    if path.startswith(("in/", f"{OUTPUTS_DIR}/", f"{FETCHED_DIR}/", ".")):
        return False
    return path not in (HANDOFF_PATH, PARTIAL_ANSWER_PATH)


def fetched_path(tool: str, args_digest: str) -> str:
    digest = args_digest.split(":", 1)[-1][:12]
    return f"{FETCHED_DIR}/{_SLUG.sub('-', tool).strip('-')[:60] or 'tool'}-{digest}.txt"


def handoff_document(
    *,
    request: str,
    reason: PartialReason,
    run_id: str,
    calls: Sequence[DoneCall],
    saved: Mapping[str, str],
    deliverable: str | None,
    earlier: Mapping[str, JsonValue] | None,
    pinned: Deliverable | None = None,
) -> dict[str, JsonValue]:
    """The handoff: this run's calls after the earlier runs' (one entry per tool and arguments digest), and the
    deliverable the task was pinned to, which a continuation reads instead of the request's words."""
    done: list[JsonValue] = []
    seen: set[str] = set()
    earlier_done = earlier.get("done") if earlier is not None else None
    for entry in cast("list[JsonValue]", earlier_done) if isinstance(earlier_done, list) else []:
        key = entry.get("argsDigest") if isinstance(entry, dict) else None
        if isinstance(key, str) and isinstance(entry, dict) and f"{entry.get('tool')}:{key}" not in seen:
            seen.add(f"{entry.get('tool')}:{key}")
            done.append(entry)
    for call in calls:
        key = f"{call.tool}:{call.args_digest}"
        if key in seen:
            continue
        seen.add(key)
        item: dict[str, JsonValue] = {"tool": call.tool, "argsDigest": call.args_digest}
        if call.arguments is not None:
            item["arguments"] = _bounded_arguments(call.arguments)
        item["result"] = saved.get(key)
        done.append(item)
    earlier_runs = earlier.get("runs") if earlier is not None else None
    runs: list[JsonValue] = (
        [r for r in cast("list[JsonValue]", earlier_runs) if isinstance(r, str)] if isinstance(earlier_runs, list) else []
    )
    earlier_deliverable = earlier.get("deliverable") if earlier is not None else None
    document: dict[str, JsonValue] = {
        "schema": HANDOFF_SCHEMA,
        "request": original_request(earlier, request),
        "status": STOPPED_EARLY,
        "stoppedAt": reason,
        "runs": [*runs, run_id] if run_id not in runs else runs,
        "deliverable": f"{OUTPUTS_DIR}/{deliverable}"
        if deliverable is not None
        else (earlier_deliverable if isinstance(earlier_deliverable, str) else None),
        "partialAnswer": PARTIAL_ANSWER_PATH,
        "done": done,
        "howToContinue": (
            "Read the deliverable and the partial answer first. Every call under done already returned: read its "
            "result file instead of calling the tool again with the same arguments. Do only what the request still "
            "needs, extend the deliverable in place, and keep this file current."
        ),
    }
    if pinned is not None:
        document["pinned"] = pinned.wire()
    return document


def original_request(earlier: Mapping[str, JsonValue] | None, request: str) -> str:
    """The request the task started from: a continuation's own message is often just "continue"."""
    first = earlier.get("request") if earlier is not None else None
    if isinstance(first, str) and first.strip():
        latest = request.strip()
        return first if not latest or latest in first else f"{first}\n\nLatest message: {latest}"[:MAX_REQUEST_CHARS]
    return request.strip()[:MAX_REQUEST_CHARS]


def handoff_deliverable(document: Mapping[str, JsonValue] | None) -> Deliverable | None:
    """The file an open handoff's task owes: the deliverable it was pinned to; a handoff saved before the pin resolves
    it once from the request it kept. ``None`` when the task owes no file."""
    if document is None:
        return None
    pinned = Deliverable.from_wire(document.get("pinned"))
    if pinned is None:
        request = document.get("request")
        pinned = resolve_deliverable(request) if isinstance(request, str) else None
    return pinned if pinned is not None and pinned.is_file else None


def _earlier_deliverable(earlier: Mapping[str, JsonValue] | None) -> str | None:
    path = earlier.get("deliverable") if earlier is not None else None
    prefix = f"{OUTPUTS_DIR}/"
    return path[len(prefix):] if isinstance(path, str) and path.startswith(prefix) else None


def _bounded_arguments(arguments: Mapping[str, JsonValue]) -> JsonValue:
    clean = cast("JsonValue", redact_secrets_in(dict(arguments)))
    text = json.dumps(clean, ensure_ascii=False, sort_keys=True)
    return clean if len(text) <= MAX_ARGUMENT_CHARS else text[:MAX_ARGUMENT_CHARS] + "…"


def handoff_summary(document: Mapping[str, JsonValue]) -> str:
    """The handoff as the continuing run's first prompt shows it: the deliverable so far and each call already
    made, one line each, bounded; the file has every call and where its result is."""
    lines: list[str] = []
    deliverable = document.get("deliverable")
    if isinstance(deliverable, str):
        lines.append(f"Deliverable so far (partial): {deliverable}")
    done = document.get("done")
    entries = [e for e in cast("list[JsonValue]", done) if isinstance(e, dict)] if isinstance(done, list) else []
    if entries:
        lines.append("Already done (each result is saved; do not call these again):")
    used = sum(len(line) for line in lines)
    shown = 0
    for entry in entries:
        arguments = entry.get("arguments")
        args = json.dumps(arguments, ensure_ascii=False, sort_keys=True) if arguments is not None else "(arguments not recorded)"
        if len(args) > MAX_SUMMARY_ARGUMENT_CHARS:
            args = args[:MAX_SUMMARY_ARGUMENT_CHARS] + "…"
        result = entry.get("result")
        line = f"- {entry.get('tool')} {args}" + (f" -> {result}" if isinstance(result, str) else "")
        if used + len(line) > MAX_SUMMARY_CHARS:
            break
        lines.append(line)
        used += len(line)
        shown += 1
    if shown < len(entries):
        lines.append(f"- … and {len(entries) - shown} more in {HANDOFF_PATH}")
    return "\n".join(lines)


async def earlier_partial(
    objects: BinaryObjectStore, scope: ObjectScope, head: WorkspaceManifest, document: Mapping[str, JsonValue] | None
) -> str | None:
    """The partial result an earlier cut-short run left (its deliverable, else its partial answer), bounded; the
    wrap-up of the run that continues it extends this rather than answering from its own results alone."""
    candidates: list[str] = []
    deliverable = document.get("deliverable") if document is not None else None
    if isinstance(deliverable, str):
        candidates.append(deliverable)
    candidates.append(PARTIAL_ANSWER_PATH)
    for path in candidates:
        entry = head.files.get(path)
        if entry is None:
            continue
        try:
            text = (await objects.get(scope, entry.content)).decode("utf-8", errors="replace").strip()
        except LookupError:
            continue
        text = PRESENTER.without_banner(text)
        if text:
            return text[:MAX_EARLIER_CHARS]
    return None


async def read_handoff(
    objects: BinaryObjectStore, scope: ObjectScope, head: WorkspaceManifest
) -> dict[str, JsonValue] | None:
    """The handoff an earlier run left in ``head``; ``None`` when there is none or it cannot be read."""
    entry = head.files.get(HANDOFF_PATH)
    if entry is None:
        return None
    try:
        document = json.loads(await objects.get(scope, entry.content))
    except (ValueError, LookupError):
        return None
    return cast("dict[str, JsonValue]", document) if isinstance(document, dict) else None


def is_open(document: Mapping[str, JsonValue]) -> bool:
    """Whether the handoff's task still waits for a run to finish it."""
    return document.get("status") == STOPPED_EARLY


async def close_handoff(
    store: WorkspaceStore,
    objects: BinaryObjectStore,
    object_scope: ObjectScope,
    tenant_scope: ObjectScope,
    head: WorkspaceManifest,
    document: Mapping[str, JsonValue],
    *,
    run_id: str,
) -> WorkspaceManifest | None:
    """Mark an open handoff finished by ``run_id`` (its calls and results stay for reference); ``None`` when it was
    not open. Raises what the store raises."""
    if not is_open(document):
        return None
    closed = {**document, "status": COMPLETED, "completedBy": run_id}
    encoded = json.dumps(closed, ensure_ascii=False, indent=1).encode("utf-8")
    entry = WorkspaceFile(HANDOFF_PATH, await objects.put(object_scope, encoded), "application/json")
    return await store.commit(
        tenant_scope, head.workspace_id, base_revision=head.revision, changes=ChangeSet.of(entry), reason="turn_end"
    )


async def save_handoff(
    *,
    store: WorkspaceStore,
    objects: BinaryObjectStore,
    publisher: ArtifactPublisher,
    binding: RunBinding,
    object_scope: ObjectScope,
    workspace_id: str,
    request: str,
    answer: str,
    reason: PartialReason,
    calls: Sequence[DoneCall],
    wrote_outputs: bool,
    derivation: Derivation,
    deliverable: Deliverable,
    banner: str,
) -> HandoffSaved:
    """Commit the handoff to the run's workspace in one revision and publish the deliverable it wrote, if any.

    ``wrote_outputs``: the engine published a file of its own, which stays the deliverable (a placeholder is not
    one: the caller passes False for it, WP-70). ``deliverable``: what the task owes, as pinned; the partial answer is
    saved as its file under ``banner`` (the presenter's). A file the engine wrote there is never replaced: the handoff
    writes only what is missing (ADR-RUN-01 D1). Raises what the store raises (a moved head is
    ``WorkspaceRevisionConflict``); the caller treats any failure as no handoff."""
    head = await store.head(ObjectScope(binding.tenant), workspace_id)
    found = await read_handoff(objects, object_scope, head)
    # A finished task's handoff belongs to that task: a new one starts its own.
    earlier = found if found is not None and is_open(found) else None
    files: list[WorkspaceFile] = []
    saved: dict[str, str] = {}
    budget = MAX_FETCHED_BYTES
    for call in calls:
        key = f"{call.tool}:{call.args_digest}"
        if key in saved or not call.text:
            continue
        data = call.text.encode("utf-8")
        if len(data) > budget:
            continue
        budget -= len(data)
        path = fetched_path(call.tool, call.args_digest)
        files.append(WorkspaceFile(path, await objects.put(object_scope, data), "text/plain"))
        saved[key] = path
    name = (
        None
        if wrote_outputs or not answer.strip()
        else deliverable.partial_name() or _earlier_deliverable(earlier)
    )
    if name is not None and await _engine_wrote(objects, object_scope, head.files.get(f"{OUTPUTS_DIR}/{name}")):
        name = None
    output: OutputFile | None = None
    if answer.strip():
        files.append(
            WorkspaceFile(PARTIAL_ANSWER_PATH, await objects.put(object_scope, answer.encode("utf-8")), "text/markdown")
        )
    if name is not None:
        mime = TEXT_TYPES.get("." + name.rsplit(".", 1)[-1].lower(), "text/markdown")
        content = await objects.put(object_scope, f"{banner}\n\n{answer.strip()}\n".encode())
        files.append(WorkspaceFile(f"{OUTPUTS_DIR}/{name}", content, mime))
        output = OutputFile(name, content, mime)
    document = handoff_document(
        request=request, reason=reason, run_id=binding.run_id, calls=calls, saved=saved, deliverable=name,
        earlier=earlier, pinned=deliverable,
    )
    encoded = json.dumps(document, ensure_ascii=False, indent=1).encode("utf-8")
    files.append(WorkspaceFile(HANDOFF_PATH, await objects.put(object_scope, encoded), "application/json"))
    changed = [f for f in files if not f.same_content(head.files.get(f.path))]
    manifest = (
        await store.commit(
            ObjectScope(binding.tenant), workspace_id, base_revision=head.revision, changes=ChangeSet.of(*changed),
            reason="turn_end",
        )
        if changed
        else head
    )
    report = (
        await publisher.publish_changes(binding, manifest, [output], draft=False, derivation=derivation)
        if output is not None
        else None
    )
    return HandoffSaved(manifest, name if report is not None and report.published else None, report)


async def _engine_wrote(objects: BinaryObjectStore, scope: ObjectScope, entry: WorkspaceFile | None) -> bool:
    """Whether ``entry`` is a file the engine wrote, larger than a placeholder: not one an earlier handoff saved."""
    if entry is None or entry.is_symlink or entry.content.size_bytes < PLACEHOLDER_MAX_BYTES:
        return False
    try:
        text = (await objects.get(scope, entry.content)).decode("utf-8", errors="replace")
    except LookupError:
        return True
    return PRESENTER.without_banner(text) == text


__all__ = [
    "NOTES_MAX_BYTES",
    "NOTE_MAX_BYTES",
    "is_engine_note",
    "is_placeholder_text",
    "source_of",
    "FETCHED_DIR",
    "HANDOFF_PATH",
    "MAX_FETCHED_BYTES",
    "PARTIAL_ANSWER_PATH",
    "DoneCall",
    "HandoffSaved",
    "earlier_partial",
    "handoff_summary",
    "close_handoff",
    "is_open",
    "original_request",
    "fetched_path",
    "handoff_deliverable",
    "handoff_document",
    "read_handoff",
    "save_handoff",
]
