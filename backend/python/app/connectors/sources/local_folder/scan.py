"""Filesystem side of the Folder connector: allowed roots, ignore rules, walking.

Pure standard library so it can be tested without the connector framework.
Every path that reaches the disk goes through ``safe_open`` or ``walk``; both
stay inside a root chosen from the server's allow-list.
"""

from __future__ import annotations  # noqa: I001

import hashlib
import logging
import os
import re
import stat
import sys
from urllib.parse import unquote_to_bytes
from collections.abc import Iterator  # noqa: TC003
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024
HASH_CHUNK = 1024 * 1024

# Gitignore syntax, applied after .gitignore files, so a user pattern wins.
DEFAULT_IGNORES = [
    ".git/", ".hg/", ".svn/", "node_modules/", "venv/", ".venv/", "env/", "__pycache__/",
    ".tox/", ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", ".idea/", ".vscode/",
    "dist/", "build/", "target/", "out/", ".next/", ".nuxt/", ".gradle/", "coverage/",
    "vendor/", ".terraform/", "*.egg-info/", ".DS_Store",
    "*.pyc", "*.pyo", "*.class", "*.o", "*.so", "*.dll", "*.dylib", "*.exe", "*.a", "*.lib",
    "*.min.js", "*.map", "*.lock", "package-lock.json",
    # Binary assets the light parsers cannot read.
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico", "*.bmp", "*.tiff", "*.heic",
    "*.mp3", "*.mp4", "*.mov", "*.avi", "*.wav", "*.zip", "*.tar", "*.gz", "*.tgz", "*.bz2",
    "*.xz", "*.7z", "*.rar", "*.jar", "*.war", "*.bin", "*.iso", "*.dmg", "*.sqlite",
    "*.sqlite3", "*.db", "*.woff", "*.woff2", "*.ttf", "*.otf", "*.eot", "*.pdb", "*.wasm",
]


# For a pushed repository. The client sends what git lists (its .gitignore already applied), so only what git would not know is left out:
# tool caches, dependency folders, compiled and binary files, and artifact folders at the top of the repository. A source folder that merely
# shares a build-output name (`internal/build`, `cmd/out`, `lib/env`, `pkg/dist`) is code.
PUSHED_REPO_IGNORES = [
    ".git/", ".hg/", ".svn/", "node_modules/", "__pycache__/", ".venv/", ".tox/", ".mypy_cache/", ".pytest_cache/", ".ruff_cache/", ".idea/",
    ".vscode/", ".next/", ".nuxt/", ".gradle/", ".terraform/", "*.egg-info/", ".DS_Store",
    "/dist/", "/build/", "/target/", "/out/", "/coverage/", "/vendor/",
    *[p for p in DEFAULT_IGNORES if p.startswith("*.") or p in ("package-lock.json",)],
]


class WalkFailed(OSError):
    """A folder or file could not be read during the walk. Raised, never skipped: the sync would take what the walk did not see for deleted."""


class PathRefused(Exception):
    """The path is outside the allowed roots or is not a regular file."""


def normalize_rel(rel: str) -> str:
    """Validate a relative path (forward slashes, no dots, not absolute) and return it."""
    rel = rel.replace("\\", "/")
    if "\x00" in rel or rel.startswith("/") or any(s in ("..", ".") for s in rel.split("/")):
        raise PathRefused("path traversal refused")
    return rel


def _fd_path(fd: int) -> str | None:
    """Where the kernel says an open fd lives, or None where it cannot tell."""
    if sys.platform.startswith("linux"):
        return os.readlink(f"/proc/self/fd/{fd}")
    try:  # macOS
        import fcntl

        return os.fsdecode(fcntl.fcntl(fd, fcntl.F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0])
    except (ImportError, AttributeError, OSError):
        # shortcut: no fd-path check on other platforms; the dir_fd walk below is the real guard.
        return None


def _open_beneath(root: Path, rel: str) -> int:
    """Open ``rel`` below ``root`` one component at a time with ``O_NOFOLLOW``.

    Each step is relative to the directory fd of the previous one, so a directory
    swapped for a symlink after any check cannot redirect the open.
    """
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    dirflag = getattr(os, "O_DIRECTORY", 0)
    dfd = os.open(root, os.O_RDONLY | dirflag)
    try:
        *parents, leaf = rel.split("/")
        for part in parents:
            nfd = os.open(part, os.O_RDONLY | dirflag | nofollow, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
        return os.open(leaf, os.O_RDONLY | os.O_NONBLOCK | nofollow, dir_fd=dfd)
    finally:
        os.close(dfd)


def _decode_name(rel: str) -> str:
    """Undo ``safe_name``: ``%XX`` bytes back to the on-disk name (invalid UTF-8 becomes surrogates)."""
    return os.fsdecode(unquote_to_bytes(rel))


def safe_open(root: Path, rel: str, max_links: int = 1) -> int:
    """Open ``root/rel`` read-only and return the fd, or raise ``PathRefused``.

    Refuses ``..``, absolute paths, anything whose real path leaves the root
    (symlinks inside the root are followed once, up front), anything that is not
    a regular file (devices, FIFOs) and files with other hard links. The open
    itself walks the resolved path by directory fd with ``O_NOFOLLOW`` (no TOCTOU
    swap), then checks where the fd really points.

    Hard links: a link to a file outside the root cannot be told from a path, so a
    file with ``st_nlink > 1`` is refused (and skipped by ``walk``), even when the
    other link is also inside the root.
    """
    rel = normalize_rel(_decode_name(rel))  # ids are in ``safe_name`` form; check the decoded path
    try:
        real = (root / rel).resolve()
        if not real.is_relative_to(root) or real == root:
            raise PathRefused("path leaves the root")
        fd = _open_beneath(root, real.relative_to(root).as_posix())
    except (OSError, ValueError) as exc:
        raise PathRefused(f"cannot open: {getattr(exc, 'strerror', None) or exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PathRefused("not a regular file")
        if st.st_nlink > max_links:  # a root the server fills itself may hold a few links of its own (see ``max_links`` of ``walk``)
            raise PathRefused("file has other hard links")
        where = _fd_path(fd)  # None on platforms without a way to ask
        if where is not None and not Path(where).is_relative_to(root):
            raise PathRefused("path leaves the root")
    except BaseException:
        os.close(fd)
        raise
    return fd


def safe_name(rel: str) -> str:
    """Store-safe, one-to-one form of a relative path: a literal ``%`` is ``%25``, non-UTF-8 bytes are ``%XX``."""
    rel = rel.replace("%", "%25")
    return re.sub(r"[\udc80-\udcff]", lambda m: f"%{ord(m.group()) - 0xDC00:02X}", rel)


# ---------------------------------------------------------------- gitignore

def _glob_to_regex(pat: str) -> str:
    out, i = [], 0
    while i < len(pat):
        c = pat[i]
        if c == "*":
            if pat[i : i + 2] == "**":
                if pat[i : i + 3] == "**/":
                    out.append("(?:.*/)?")
                    i += 3
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            j = pat.find("]", i + 2)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pat[i + 1 : j]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = j
        elif c == "\\" and i + 1 < len(pat):
            i += 1
            out.append(re.escape(pat[i]))
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


def _glob_tokens(pat: str) -> list[tuple[str, Any]]:
    """The same pattern language as ``_glob_to_regex``, as tokens: ("lit", c), ("one", None) for ``?``, ("cls", compiled one-character regex),
    ("star", None) for ``*`` (any run but "/"), ("dstar", None) for ``**`` (any run), ("opt", None) for ``**/`` (optionally anything and a "/")."""
    out: list[tuple[str, Any]] = []
    i = 0
    while i < len(pat):
        c = pat[i]
        if c == "*":
            if pat[i : i + 3] == "**/":
                out.append(("opt", None))
                i += 3
                continue
            if pat[i : i + 2] == "**":
                out.append(("dstar", None))
                i += 2
                continue
            out.append(("star", None))
        elif c == "?":
            out.append(("one", None))
        elif c == "[" and pat.find("]", i + 2) != -1:
            j = pat.find("]", i + 2)
            body = pat[i + 1 : j]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append(("cls", re.compile("[" + body.replace("\\", "\\\\") + "]")))
            i = j
        elif c == "\\" and i + 1 < len(pat):
            i += 1
            out.append(("lit", pat[i]))
        else:
            out.append(("lit", c))
        i += 1
    return out


class _Glob:
    """Matches a whole name against a gitignore pattern in time linear in the name (times the pattern), however many ``*`` it holds.

    A regular expression that repeats ``[^/]*`` backtracks over every way to split a run of characters between the stars, so one hostile line
    in a .gitignore could stall the walk; this walks all the ways at once instead. ``anywhere`` is the unanchored form (any folders first)."""

    def __init__(self, tokens: list[tuple[str, Any]], *, anywhere: bool) -> None:
        self.tokens = [("opt", None), *tokens] if anywhere else tokens

    def _close(self, states: set[tuple[int, bool]]) -> set[tuple[int, bool]]:
        """Add the states reachable without reading a character: a star or an optional group may match nothing."""
        todo, seen = list(states), set(states)
        while todo:
            i, inside = todo.pop()
            if inside or i >= len(self.tokens) or self.tokens[i][0] not in ("star", "dstar", "opt"):
                continue
            nxt = (i + 1, False)
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
        return seen

    def match(self, name: str) -> bool:
        toks = self.tokens
        states = self._close({(0, False)})
        for ch in name:
            nxt: set[tuple[int, bool]] = set()
            for i, _inside in states:
                if i >= len(toks):
                    continue
                kind, arg = toks[i]
                if kind == "lit":
                    if ch == arg:
                        nxt.add((i + 1, False))
                elif kind == "one":
                    if ch != "/":
                        nxt.add((i + 1, False))
                elif kind == "cls":
                    if arg.fullmatch(ch):
                        nxt.add((i + 1, False))
                elif kind == "star":
                    if ch != "/":
                        nxt.add((i, False))
                elif kind == "dstar":
                    if ch != "\n":
                        nxt.add((i, False))
                elif kind == "opt" and ch != "\n":
                    nxt.add((i, True))  # inside the group: ".*" runs on
                    if ch == "/":
                        nxt.add((i + 1, False))  # and a "/" may end it
            states = self._close(nxt)
            if not states:
                return False
        return any(i == len(toks) for i, _ in states)


@dataclass
class _Rule:
    regex: _Glob
    negate: bool
    dir_only: bool


def parse_ignore(lines: list[str]) -> list[_Rule]:
    """Compile gitignore lines. Supports negation, anchoring, ``**``, dir-only."""
    rules = []
    for line in lines:
        line = line.rstrip("\n").rstrip()  # noqa: PLW2901
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate or line.startswith("\\!") or line.startswith("\\#"):  # noqa: PIE810
            line = line[1:]  # noqa: PLW2901
        dir_only = line.endswith("/")
        line = line.rstrip("/")  # noqa: PLW2901
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")  # noqa: PLW2901
        body = _glob_to_regex(line)
        rx = f"^{body}$" if anchored else f"^(?:.*/)?{body}$"
        try:
            re.compile(rx)  # a line that is not a valid pattern is ignored, as git does; matching itself does not use the expression
            rules.append(_Rule(_Glob(_glob_tokens(line), anywhere=not anchored), negate, dir_only))
        except re.error as exc:  # git ignores a bad line too
            logger.warning("Folder: ignoring malformed ignore pattern %r: %s", line, exc)
    return rules


class IgnoreStack:
    """Rules in force at one directory: defaults, user patterns, then each .gitignore down the path."""

    def __init__(self, layers: list[tuple[str, list[_Rule]]] | None = None) -> None:
        self.layers = layers or []  # (directory relative to root, rules)

    def extended(self, base: str, rules: list[_Rule]) -> IgnoreStack:
        return IgnoreStack([*self.layers, (base, rules)]) if rules else self

    def ignored(self, rel: str, is_dir: bool) -> bool:  # noqa: FBT001
        verdict = False
        for base, rules in self.layers:
            if base and not rel.startswith(base + "/"):
                continue
            sub = rel[len(base) + 1 :] if base else rel
            for r in rules:
                if (not r.dir_only or is_dir) and r.regex.match(sub):
                    verdict = not r.negate
        return verdict


# ------------------------------------------------------------------- walking

@dataclass(frozen=True)
class Entry:
    rel: str
    size: int
    mtime_ns: int


def walk(
    root: Path,
    user_patterns: list[str] | None = None,
    max_bytes: int = DEFAULT_MAX_FILE_BYTES,
    use_gitignore: bool = True,  # noqa: FBT001, FBT002
    defaults: list[str] | None = None,
    max_links: int = 1,
) -> Iterator[Entry]:
    """Yield regular files under ``root`` that survive the ignore rules.

    Skips symlinked directories (loop risk; shortcut: in-root dir links are not
    followed), symlinks whose target leaves the root, non-regular files and files
    over ``max_bytes``.
    """
    base = IgnoreStack().extended("", parse_ignore(DEFAULT_IGNORES if defaults is None else defaults))
    stack = [("", base)]
    user_rules = parse_ignore(user_patterns or [])
    while stack:
        rel_dir, rules = stack.pop()
        here = root / rel_dir if rel_dir else root
        try:
            with os.scandir(here) as it:
                entries = sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            continue  # the folder went away while the walk ran: its files are gone
        except OSError as exc:
            raise WalkFailed(f"cannot read the folder {rel_dir or '.'}: {exc}") from exc
        if use_gitignore:
            gi = here / ".gitignore"
            try:
                if gi.is_file() and not gi.is_symlink():
                    rules = rules.extended(rel_dir, parse_ignore(gi.read_text("utf-8", "replace").splitlines()))
            except OSError:
                pass
        # User patterns last at every level so they override .gitignore.
        eff = rules.extended("", user_rules)
        for e in entries:
            rel = f"{rel_dir}/{e.name}" if rel_dir else e.name
            try:
                link = e.is_symlink()
                is_dir = e.is_dir(follow_symlinks=False)
                if link:
                    target = Path(os.path.realpath(e.path))
                    if not target.is_relative_to(root) or target.is_dir():
                        continue
                    st = os.stat(e.path)
                else:
                    st = e.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue  # removed while the walk ran
            except OSError as exc:
                raise WalkFailed(f"cannot read {rel}: {exc}") from exc
            if eff.ignored(rel, is_dir):
                continue
            if is_dir:
                stack.append((rel, rules))
            elif stat.S_ISREG(st.st_mode) and st.st_size <= max_bytes and st.st_nlink <= max_links:
                yield Entry(safe_name(rel), st.st_size, st.st_mtime_ns)


def sha256_fd(fd: int) -> str:
    h = hashlib.sha256()
    with os.fdopen(fd, "rb", closefd=True) as f:
        while chunk := f.read(HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def looks_binary(fd: int) -> bool:
    """True if the first 4 KiB has a NUL byte. Leaves the file position alone."""
    return b"\x00" in os.pread(fd, 4096, 0)
