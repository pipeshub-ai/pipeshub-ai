"""The workspace note an engine task starts with (G2C).

An engine that is told only "analyse the attached sales.csv" first lists its folder, then reads the file into
its conversation to see what it holds, and only then computes (G2C: the read alone was 37% of the engine's spend on
data analysis, and listing and folder creation were further whole model calls). The note tells it what the folder
holds before it starts: each input's path and size, and for delimited text its columns, row count and first rows,
so it can go straight to code. It also says what else the workspace already holds (a session's workspace outlives a
task) and where deliverables go.

``engine_task_prompt`` is the one place an engine's first prompt is assembled (a durable engine run's first turn,
``durable.kernel``).

Pure: built from the files' names and bytes, bounded whatever they hold.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.durable.handoff import FETCHED_DIR, HANDOFF_PATH
from harness_core.engines.durable.inputs import TRIGGER_INPUT_NAME
from harness_core.engines.input_staging import on_read_note
from harness_core.engines.types import INPUTS_DIR
from harness_core.workspace.sync.storage_classes import (
    is_storage_record,
    reference_line,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

    from harness_core.workspace.sync.storage_classes import Reference
    from harness_core.workspace.types import WorkspaceFile

_DELIMITERS = {".csv": ",", ".tsv": "\t"}
_MAX_FILES = 20
_PREVIEW_ROWS = 3
_MAX_COLUMNS = 40
_MAX_CELL = 40
_MAX_LINE = 240
# Rows are counted by parsing; past this size only the head is parsed and the count is left out.
_COUNT_LIMIT = 16 * 1024 * 1024
_HEAD_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class BriefedFile:
    """An input file as the note describes it: its name under ``in/`` and its bytes; ``on_read``: a large input the
    sandbox gets on its first read (C5.01), described by its size alone (``content`` is empty)."""

    name: str
    content: bytes
    on_read: bool = False
    size_bytes: int | None = None

    @property
    def size(self) -> int:
        return self.size_bytes if self.size_bytes is not None else len(self.content)


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """A file already in the workspace that is not one of this task's inputs: its path and size."""

    path: str
    size_bytes: int


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _table(content: bytes, delimiter: str) -> str | None:
    counted = len(content) <= _COUNT_LIMIT
    data = content if counted else content[:_HEAD_BYTES].rsplit(b"\n", 1)[0]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        return None
    preview: list[list[str]] = []
    rows = 0
    try:
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue
            rows += 1
            if len(preview) < _PREVIEW_ROWS:
                preview.append(row)
            elif not counted:
                break
    except csv.Error:
        return None
    columns = ", ".join(_clip(c.strip(), _MAX_CELL) for c in header[:_MAX_COLUMNS])
    if len(header) > _MAX_COLUMNS:
        columns += f", … ({len(header)} columns)"
    lines = [f"{rows} data rows; columns: {columns}" if counted else f"columns: {columns}"]
    if preview:
        lines.append("first rows:")
        lines.extend(
            "    " + _clip(delimiter.join(_clip(c, _MAX_CELL) for c in row[:_MAX_COLUMNS]), _MAX_LINE) for row in preview
        )
    return "\n".join(lines)


# TR-03: a scheduled run's trigger file holds ids and times only; what a record holds is read with the tools.
TRIGGER_NOTE = (
    "what started this scheduled run: its trigger, the slot time and the ids of what fired it. "
    "Read any record it names with your tools."
)


def _describe(file: BriefedFile) -> str:
    path = f"{INPUTS_DIR}/{file.name}"
    head = f"- {path} ({_size(file.size)})"
    if file.on_read:
        return f"{head}: {on_read_note(file.name)}"
    if file.name == TRIGGER_INPUT_NAME:
        return f"{head}: {TRIGGER_NOTE}"
    suffix = "." + file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
    delimiter = _DELIMITERS.get(suffix)
    table = _table(file.content, delimiter) if delimiter is not None else None
    return f"{head}: {table}" if table is not None else head


# SPENDCONT: "continue" children searched Jira again from scratch; an earlier run cut short now leaves its
# handoff, and the run that continues it is told to start there.
RESUME_FROM_HANDOFF = (
    f"An earlier task in this conversation stopped before it finished and left {HANDOFF_PATH}. If this task continues "
    "it, read that file first: it names the request, the deliverable so far and every tool call that already "
    "returned, with each result saved under notes/fetched/. Do not call those tools again with the same arguments: "
    "read the saved results. Extend the deliverable in place, do only what is still missing, and when you finish, "
    "say in your answer what this run added."
)


def skipped_note(skipped: Sequence[tuple[str | None, str]]) -> str:
    """R36COVER-FU-3: the files handed to the task that it could not take, each ``(name, why)``, and what to do: the
    rest of the work without them, and say so in the answer (the answer contract: say plainly what could not be read)."""
    if not skipped:
        return ""
    lines = [f"Files handed to this task that are not under {INPUTS_DIR}/ (it could not take them):"]
    lines.extend(f"- {name or 'one of the files handed to it'}: {why}" for name, why in skipped[:_MAX_FILES])
    lines.append(
        "Do the rest of the task without them, and say plainly in your answer which file you could not use and why. "
        "Never present your answer as covering a file you did not read."
    )
    return "\n".join(lines)


def workspace_briefing(
    files: Sequence[BriefedFile],
    outputs_dir: str = "out",
    others: Sequence[WorkspaceEntry] = (),
    handoff: str | None = None,
    references: Sequence[Reference] = (),
    skipped: Sequence[tuple[str | None, str]] = (),
) -> str:
    """The note appended to an engine task: its inputs, what else the workspace holds, how to work on them, and
    where deliverables go; ``handoff``: an earlier cut-short run's handoff as ``handoff_summary`` shows it;
    ``references``: the repositories earlier turns fetched (ADR-RUN-01 D2), and how to have one again."""
    out = outputs_dir.strip("/")
    lines = ["About your workspace (the current directory):"]
    if files:
        lines.append(f"This task's input files, under {INPUTS_DIR}/:")
        lines.extend(_describe(f) for f in files[:_MAX_FILES])
        if len(files) > _MAX_FILES:
            lines.append(f"- … and {len(files) - _MAX_FILES} more under {INPUTS_DIR}/")
    elif not others:
        lines.append("This task has no input files: start the work without listing the folder.")
    else:
        lines.append("This task has no input files.")
    if others:
        # A handoff's saved results are one line, so they never push the deliverables out of the list.
        fetched = [e for e in others if e.path.lstrip("/").startswith(FETCHED_DIR + "/")]
        listed = [e for e in others if e not in fetched]
        lines.append("Also in the workspace from earlier work:")
        lines.extend(f"- {e.path.lstrip('/')} ({_size(e.size_bytes)})" for e in listed[:_MAX_FILES])
        if len(listed) > _MAX_FILES:
            lines.append(f"- … and {len(listed) - _MAX_FILES} more")
        if fetched:
            lines.append(f"- {FETCHED_DIR}/: {len(fetched)} saved tool results ({_size(sum(e.size_bytes for e in fetched))})")
    if references:
        lines.append("Fetched earlier in this conversation:")
        lines.extend(reference_line(ref) for ref in references[:_MAX_FILES])
    if handoff:
        lines.extend((RESUME_FROM_HANDOFF, handoff))
    if skipped:
        lines.append(skipped_note(skipped))
    if files:
        lines.append(
            "This list is complete: start the work without listing the folder. Work on data files with code (for "
            "example pandas); do not read a whole data file into the conversation."
        )
    elif others:
        lines.append("This list is complete: start the work without listing the folder.")
    lines.append(
        f"Deliverables go under {out}/: create it in the same command that writes the first one "
        f"(for example `mkdir -p {out} && python3 make.py`), not as a step of its own. A file the task or the notes "
        f"in the workspace name by a path (wiki/index.md) goes at exactly that path, not under {out}/."
    )
    return "\n".join(lines)


def earlier_files(files: Mapping[str, WorkspaceFile], inputs: Iterable[str]) -> tuple[WorkspaceEntry, ...]:
    """The files of a workspace revision that are not the task's inputs (named under ``in/``), by path."""
    given = {f"{INPUTS_DIR}/{name}" for name in inputs}
    return tuple(
        WorkspaceEntry(path, entry.content.size_bytes)
        for path, entry in sorted(files.items())
        if path.lstrip("/") not in given and not is_storage_record(path)
    )


def engine_task_prompt(
    goal: str,
    inputs: Sequence[BriefedFile],
    others: Sequence[WorkspaceEntry] = (),
    outputs_dir: str = "out",
    *,
    handoff: str | None = None,
    references: Sequence[Reference] = (),
    skipped: Sequence[tuple[str | None, str]] = (),
) -> str:
    """An engine's first prompt for a task: the caller's goal, then the workspace note."""
    return f"{goal}\n\n{workspace_briefing(inputs, outputs_dir, others, handoff, references, skipped)}"


__all__ = [
    "RESUME_FROM_HANDOFF",
    "TRIGGER_NOTE",
    "BriefedFile",
    "WorkspaceEntry",
    "earlier_files",
    "engine_task_prompt",
    "skipped_note",
    "workspace_briefing",
]
