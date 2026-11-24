"""Unified diffs (``git diff`` or plain ``diff -u``) into the lines each file changed.

The code graph holds the old side, the indexed commit, so a change is located there: by the old-side lines it
removed, and by the old-side line each run of added lines follows. Context lines are not changes. A hunk ends when
its line counts are used up, never at a line that merely looks like a header (``--- x`` is the removed line ``-- x``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

__all__ = ["FileDiff", "Hunk", "parse_unified_diff"]

Status = Literal["added", "deleted", "modified", "renamed"]

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_BINARY_HEAD, _BINARY_TAIL, _BINARY_AND = "Binary files ", " differ", " and "
_NULL = "/dev/null"


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_len: int
    new_start: int
    new_len: int


@dataclass(frozen=True)
class FileDiff:
    path: str                           # the new-side path; the old one for a deleted file
    old_path: str | None                # where the graph has it; None for an added file
    status: Status
    binary: bool = False
    hunks: tuple[Hunk, ...] = ()
    removed: tuple[int, ...] = ()       # old-side lines removed
    inserted_after: tuple[int, ...] = ()  # old-side line each run of added lines follows (0: the top)
    added: tuple[int, ...] = ()         # new-side lines added


@dataclass
class _File:
    git: bool
    old: str | None = None
    new: str | None = None
    renamed: bool = False
    added_file: bool = False
    deleted_file: bool = False
    binary: bool = False
    hunks: list[Hunk] = field(default_factory=list)
    removed: list[int] = field(default_factory=list)
    inserted_after: list[int] = field(default_factory=list)
    added: list[int] = field(default_factory=list)

    def path(self, raw: str, prefix: str) -> str | None:
        raw = raw.split("\t", 1)[0].strip()
        if len(raw) > 1 and raw[0] == raw[-1] == '"':
            raw = raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        if raw == _NULL:
            return None
        return raw[len(prefix):] if self.git and raw.startswith(prefix) else raw

    def done(self) -> FileDiff | None:
        old = None if self.added_file else self.old
        new = None if self.deleted_file else self.new
        if old is None and new is None:
            return None
        if not self.git and old and new:
            # `diff -ruN old/ new/` names both sides under their tree; a first component that alone differs is
            # the tree, not the file (as `patch -p1` reads it).
            old_parts, new_parts = old.split("/", 1), new.split("/", 1)
            if len(old_parts) == len(new_parts) == 2 and old_parts[0] != new_parts[0] and old_parts[1] == new_parts[1]:
                old = new = old_parts[1]
        if old is None:
            status: Status = "added"
        elif new is None:
            status = "deleted"
        else:
            status = "renamed" if self.renamed or old != new else "modified"
        return FileDiff(path=new or old, old_path=old, status=status, binary=self.binary, hunks=tuple(self.hunks),
                        removed=tuple(self.removed), inserted_after=tuple(dict.fromkeys(self.inserted_after)),
                        added=tuple(self.added))


def _git_header_paths(rest: str) -> tuple[str, str] | None:
    """``a/x b/y`` from a ``diff --git`` line; only used when nothing later names the paths (a pure mode change)."""
    if rest.startswith('"'):
        end = rest.find('" ', 1)
        first, second = (rest[:end + 1], rest[end + 2:]) if end > 0 else ("", "")
    else:
        first, _, second = rest.partition(" ")
    if first and second and (_quoted(second) or " " not in second):
        return first, second
    half = (len(rest) - 1) // 2  # unquoted paths with spaces: the two halves name the same file
    return (rest[:half], rest[half + 1:]) if rest[half:half + 1] == " " else None


def _quoted(path: str) -> bool:
    return len(path) > 1 and path[0] == path[-1] == '"'


def _binary_paths(line: str) -> tuple[str, str] | None:
    """``Binary files a/x and b/y differ``: the two paths, split at the last `` and `` (linear time, unlike a
    backtracking pattern on a long line)."""
    if not (line.startswith(_BINARY_HEAD) and line.endswith(_BINARY_TAIL)):
        return None
    old, sep, new = line[len(_BINARY_HEAD):-len(_BINARY_TAIL)].rpartition(_BINARY_AND)
    return (old, new) if sep and old and new else None


def _lines(text: str) -> list[str]:
    """Lines split on newlines only: ``str.splitlines`` also splits on form feeds and Unicode separators, which
    are content inside a diff line."""
    lines = [line.removesuffix("\r") for line in text.split("\n")]
    return lines[:-1] if lines and not lines[-1] else lines


def parse_unified_diff(text: str) -> list[FileDiff]:
    """Every file *text* changes, in order. Text that names no file gives []."""
    files: list[FileDiff] = []
    cur: _File | None = None
    old_left = new_left = 0  # lines still owed by the open hunk
    old_line = new_line = anchor = 0
    # Added lines right after removed ones replace them, and the removed lines locate the change: anchoring the
    # added run on the line before as well would blame whatever holds that line.
    replacing = False

    def close() -> None:
        if cur is not None and (done := cur.done()) is not None:
            files.append(done)

    for line in _lines(text):
        if old_left > 0 or new_left > 0:
            tag, body = (line[:1], line[1:]) if line else (" ", "")
            if tag == "\\":
                continue
            if tag == " ":
                anchor, old_line, new_line = old_line, old_line + 1, new_line + 1
                old_left, new_left, replacing = old_left - 1, new_left - 1, False
                continue
            if tag == "-" and old_left > 0:
                cur.removed.append(old_line)
                old_line, old_left, replacing = old_line + 1, old_left - 1, True
                continue
            if tag == "+" and new_left > 0:
                if not replacing:
                    cur.inserted_after.append(anchor)
                cur.added.append(new_line)
                new_line, new_left = new_line + 1, new_left - 1
                continue
            old_left = new_left = 0  # a malformed hunk: stop counting and read the line as a header
        if line.startswith("diff --git "):
            close()
            cur = _File(git=True)
            if paths := _git_header_paths(line[len("diff --git "):]):
                cur.old, cur.new = cur.path(paths[0], "a/"), cur.path(paths[1], "b/")
        elif line.startswith("--- "):
            if cur is None or cur.hunks or not cur.git:  # without `diff --git`, each file starts here
                close()
                cur = _File(git=False)
            cur.old = cur.path(line[4:], "a/")
            cur.added_file = cur.added_file or cur.old is None
        elif line.startswith("+++ ") and cur is not None:
            cur.new = cur.path(line[4:], "b/")
            cur.deleted_file = cur.deleted_file or cur.new is None
        elif cur is None:
            continue
        elif line.startswith("new file mode"):
            cur.added_file = True
        elif line.startswith("deleted file mode"):
            cur.deleted_file = True
        elif line.startswith("rename from "):
            cur.renamed, cur.old = True, line[len("rename from "):]
        elif line.startswith("rename to "):
            cur.renamed, cur.new = True, line[len("rename to "):]
        elif binary := _binary_paths(line):
            cur.binary = True
            cur.old = cur.path(binary[0], "a/") if cur.old is None else cur.old
            cur.new = cur.path(binary[1], "b/") if cur.new is None else cur.new
        elif hunk := _HUNK.match(line):
            old_start, new_start = int(hunk.group(1)), int(hunk.group(3))
            old_left = int(hunk.group(2)) if hunk.group(2) is not None else 1
            new_left = int(hunk.group(4)) if hunk.group(4) is not None else 1
            cur.hunks.append(Hunk(old_start, old_left, new_start, new_left))
            old_line, new_line = old_start, new_start
            # `-5,0` inserts after line 5; otherwise the hunk starts at old_start
            anchor, replacing = (old_start if old_left == 0 else old_start - 1), False
    close()
    return files
