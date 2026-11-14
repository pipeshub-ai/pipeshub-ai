"""Run the file-searching grep stages of a storage command with ripgrep.

Opt-in with ``PATTERN_MATCH_ENGINE=ripgrep``. A pattern match's first stage
walks every record file of a connector, and an ``xargs grep`` stage then reads
each file the stage before it listed. GNU grep does both on one thread, which on
a large knowledge base rarely fits the time limit; ripgrep searches the same
files on several threads.

Commands keep their GNU grep syntax; only execution changes. A stage runs on
``rg`` only where the translation prints what GNU grep prints apart from line
order (paths as given, ``path:count`` for ``-c``, NUL framing for ``-Z``) over
record files, which are UTF-8 text. Everything else stays on GNU grep:
backreferences, word and space classes and ``-w`` (glibc and Unicode disagree
on which characters those hold), POSIX classes other than digits, malformed
patterns, and any flag outside a small set.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import string

from app.services.resource_governor.probe import effective_cpu_count
from app.utils.env_utils import env_int

logger = logging.getLogger(__name__)

PATTERN_MATCH_ENGINE_ENV_VAR = "PATTERN_MATCH_ENGINE"
PATTERN_MATCH_RG_THREADS_ENV_VAR = "PATTERN_MATCH_RG_THREADS"
_DEFAULT_THREADS = 4
_MAX_THREADS = 8
# GNU allows 32767; a long Unicode repetition can pass ripgrep's regex size limit.
_MAX_INTERVAL = 255

_MATCHERS = {"grep": "G", "egrep": "E", "fgrep": "F"}
# Grep flags with an exact ripgrep form. -r is the walk itself (rg recurses into
# any directory it is given). -I skips binary files, which rg does whenever it
# walks; record files are text either way.
_SHORT_FLAGS: dict[str, str | None] = {
    "i": "--ignore-case", "l": "--files-with-matches", "c": "--count", "H": "--with-filename",
    "Z": "--null", "x": "--line-regexp", "s": "--no-messages",
    "r": None, "I": None, "E": None, "F": None,
}
_LONG_FLAGS = {
    "--recursive": "r", "--ignore-case": "i", "--files-with-matches": "l", "--count": "c",
    "--with-filename": "H", "--null": "Z", "--line-regexp": "x", "--no-messages": "s",
    "--extended-regexp": "E", "--fixed-strings": "F",
}
_RUST_META = frozenset("\\.+*?()|[]{}^$")
_CLASS_META = frozenset("\\[]&~-^")
_BRE_OPERATORS = frozenset("|(){}+?")
# \w \s \b \< and the like follow glibc's character classes in GNU grep and
# Unicode's in rg; \` and \' anchor the whole buffer.
_GNU_CLASS_ESCAPES = frozenset("wWsSbB<>`'")
# ASCII digits in glibc and in Rust alike; every other POSIX class differs past ASCII.
_POSIX_CLASSES = frozenset({"digit", "xdigit"})
# A backslash before a letter, digit or space means something different in each
# GNU grep version (a backreference, a class, a literal with a warning).
_LITERAL_ESCAPES = frozenset(string.punctuation) - _GNU_CLASS_ESCAPES
_INTERVAL_OPENERS = frozenset("0123456789,}")
_ERE_INTERVAL = re.compile(r"\{(\d*)(,(\d*))?\}")
_BRE_INTERVAL = re.compile(r"\\\{(\d*)(,(\d*))?\\\}")
_FIND_BRANCHING = frozenset({"-o", "-or", "-not", "!", ","})


@functools.cache
def _rg_on_path() -> str | None:
    """The ``rg`` on PATH, looked up (and its absence logged) once per process."""
    rg = shutil.which("rg")
    if rg is None:
        logger.warning("%s=ripgrep, but rg is not on PATH: grep stages run on GNU grep", PATTERN_MATCH_ENGINE_ENV_VAR)
    return rg


@functools.cache
def _rg_confinable(rg: str) -> bool:
    """Whether Landlock lets a confined stage run *rg*: only from the system directories."""
    from app.agents.actions.storage_search.landlock_exec import SYSTEM_DIRS

    real = os.path.realpath(rg)
    if any(real.startswith(d + os.sep) for d in SYSTEM_DIRS):
        return True
    logger.warning(
        "%s=ripgrep, but %s is outside the directories a confined command may run: "
        "grep stages run on GNU grep", PATTERN_MATCH_ENGINE_ENV_VAR, real,
    )
    return False


@functools.cache
def _utf8_locale(locale: str) -> bool:
    """Whether GNU grep matches characters, as rg does, under *locale*."""
    if "utf-8" in locale.lower() or "utf8" in locale.lower():
        return True
    logger.warning(
        "%s=ripgrep, but the locale %r is not UTF-8 (GNU grep then matches bytes, rg "
        "characters): grep stages run on GNU grep", PATTERN_MATCH_ENGINE_ENV_VAR, locale,
    )
    return False


def ripgrep_binary(*, confined: bool) -> str | None:
    """The ``rg`` to run grep stages with, or None to keep GNU grep.

    *confined*: the stages run under Landlock, which executes programs only
    from the system directories.
    """
    if os.getenv(PATTERN_MATCH_ENGINE_ENV_VAR, "").strip().lower() != "ripgrep":
        return None
    locale = next((v for k in ("LC_ALL", "LC_CTYPE", "LANG") if (v := os.environ.get(k))), "C")
    rg = _rg_on_path()
    if rg is None or not _utf8_locale(locale) or (confined and not _rg_confinable(rg)):
        return None
    return rg


def ripgrep_threads() -> int:
    """Threads per rg process: 4 by default, at most 8 and the CPUs this process may use."""
    wanted = env_int(PATTERN_MATCH_RG_THREADS_ENV_VAR, _DEFAULT_THREADS, lo=1, hi=_MAX_THREADS)
    return min(wanted or _DEFAULT_THREADS, effective_cpu_count())


def lists_files_only(tokens: list[str]) -> bool:
    """Whether a stage prints file names and nothing else (``grep -l``, ``find -type f``),
    so that an ``xargs`` stage after it is handed regular files only."""
    if not tokens:
        return False
    if tokens[0] == "find":
        has_type_f = any(a == "-type" and b == "f" for a, b in zip(tokens, tokens[1:]))
        return has_type_f and not _FIND_BRANCHING.intersection(tokens)
    parsed = _parse_grep(tokens)
    return parsed is not None and "l" in parsed[0]


def grep_to_ripgrep(tokens: list[str], rg: str, *, walk: bool, threads: int) -> list[str] | None:
    """The ``rg`` argv for a grep stage, or None when it has no exact ripgrep form.

    *walk*: the pipeline's first stage, which must recurse over the paths it
    names. Otherwise the stage searches the files ``xargs`` appends.
    """
    parsed = _parse_grep(tokens)
    if parsed is None:
        return None
    letters, patterns, operands, max_count = parsed
    if walk and "r" not in letters:
        return None
    if "l" in letters and "c" in letters:
        return None
    matcher = _MATCHERS[tokens[0]]
    chosen = {matcher, *(m for m in "EF" if m in letters)} - {"G"}
    if len(chosen) > 1:
        return None  # GNU grep refuses -E with -F
    if not patterns:
        if not operands:
            return None
        patterns, operands = [operands[0]], operands[1:]
    if walk and not operands:
        return None  # grep -r with no path walks ".", rg would read the pipe
    if any("\n" in p for p in patterns):
        return None  # GNU grep splits a pattern on newlines; rg refuses a newline in one
    fixed = "F" in chosen
    if fixed:
        exprs: list[str] = patterns
    else:
        translated = [_translate(p, extended="E" in chosen) for p in patterns]
        if any(t is None for t in translated):
            return None
        exprs = [t for t in translated if t is not None]
    argv = [rg, "--no-config", "--no-ignore", "--hidden", "--threads", str(threads)]
    argv += [flag for letter, flag in _SHORT_FLAGS.items() if flag and letter in letters]
    if "c" in letters:
        argv.append("--include-zero")  # grep -c prints a line for every file, zero counts too
    if max_count is not None:
        argv += ["--max-count", max_count]
    if fixed:
        argv.append("--fixed-strings")
    for expr in exprs:
        argv += ["--regexp", expr]
    return [*argv, "--", *operands]


def _parse_grep(tokens: list[str]) -> tuple[set[str], list[str], list[str], str | None] | None:
    """(flag letters, patterns, operands, max count) of a grep stage whose every
    option is in the sets above; None for anything else."""
    if not tokens or tokens[0] not in _MATCHERS:
        return None
    letters: set[str] = set()
    patterns: list[str] = []
    operands: list[str] = []
    max_count: str | None = None
    end_of_options = False
    i = 1
    while i < len(tokens):
        arg = tokens[i]
        # GNU grep reads options after operands too, so only "--" ends them.
        if end_of_options or arg == "-" or not arg.startswith("-"):
            operands.append(arg)
        elif arg == "--":
            end_of_options = True
        elif arg in ("-e", "--regexp", "-m", "--max-count"):
            if i + 1 >= len(tokens):
                return None
            value = tokens[i + 1]
            if arg in ("-m", "--max-count"):
                if not value.isdigit():
                    return None
                max_count = value
            else:
                patterns.append(value)
            i += 1
        elif arg.startswith("--regexp="):
            patterns.append(arg.split("=", 1)[1])
        elif arg.startswith("--"):
            if arg not in _LONG_FLAGS:
                return None
            letters.add(_LONG_FLAGS[arg])
        else:
            if any(ch not in _SHORT_FLAGS for ch in arg[1:]):
                return None
            letters.update(arg[1:])
        i += 1
    return letters, patterns, operands, max_count


def _translate(pattern: str, *, extended: bool) -> str | None:
    """A GNU basic (or, with *extended*, extended) regex in Rust syntax, or None
    when the two engines could disagree on what it matches."""
    out: list[str] = []
    # What came last: "start" of a branch, an "anchor", an "atom" or a "quant"ifier.
    last = "start"
    depth = 0
    i, n = 0, len(pattern)

    def quantifier(text: str) -> bool:
        nonlocal last
        if last in ("start", "anchor"):
            # GNU drops a leading ERE quantifier and reads a leading BRE '*' as a
            # literal; Rust refuses both, so only the literal is kept.
            if extended or text != "*":
                return False
            out.append(r"\*")
            last = "atom"
            return True
        if last == "quant":
            return False  # a repeated repetition: Rust versions differ on these
        out.append(text)
        last = "quant"
        return True

    def literal(ch: str) -> None:
        nonlocal last
        out.append("\\" + ch if ch in _RUST_META else ch)
        last = "atom"

    while i < n:
        ch = pattern[i]
        if ch == "\\":
            if i + 1 == n:
                return None
            nxt = pattern[i + 1]
            if nxt not in _LITERAL_ESCAPES:
                return None
            if not extended and nxt in _BRE_OPERATORS:
                if nxt == "{":
                    interval = _interval(_BRE_INTERVAL.match(pattern, i))
                    if interval is None or not quantifier(interval[0]):
                        return None
                    i = interval[1]
                    continue
                if nxt == "}":
                    return None
                if nxt in "+?":
                    if not quantifier(nxt):
                        return None
                else:
                    depth, last = _group_or_branch(nxt, out, depth)
                    if depth < 0:
                        return None
                i += 2
                continue
            literal(nxt)
            i += 2
            continue
        if ch == "[":
            got = _bracket(pattern, i)
            if got is None:
                return None
            out.append(got[0])
            last = "atom"
            i = got[1]
            continue
        if extended and ch in "|()":
            depth, last = _group_or_branch(ch, out, depth)
            if depth < 0:
                return None
        elif extended and ch == "{" and pattern[i + 1:i + 2] in _INTERVAL_OPENERS:
            # Before a count, ',' or '}', an ERE '{' must open a valid interval:
            # GNU refuses some malformed ones and reads others as text.
            # Before anything else it is a literal.
            interval = _interval(_ERE_INTERVAL.match(pattern, i))
            if interval is None or not quantifier(interval[0]):
                return None
            i = interval[1]
            continue
        elif ch == "*" or (extended and ch in "+?"):
            if not quantifier(ch):
                return None
        elif ch == "^" and (extended or last == "start"):
            out.append("^")
            last = "anchor"
        elif ch == "$" and (extended or _ends_branch(pattern, i + 1)):
            out.append("$")
            last = "anchor"
        elif ch == ".":
            out.append(".")
            last = "atom"
        else:
            literal(ch)
        i += 1
    return "".join(out) if depth == 0 else None


def _group_or_branch(op: str, out: list[str], depth: int) -> tuple[int, str]:
    """Emit a group bracket or an alternation; (new depth, what came last). A
    negative depth is a ")" that closes nothing."""
    out.append(op)
    if op == "(":
        return depth + 1, "start"
    if op == ")":
        return depth - 1, "atom"
    return depth, "start"


def _interval(m: re.Match[str] | None) -> tuple[str, int] | None:
    """The Rust form of a matched ``{m,n}`` interval and the index after it."""
    if m is None:
        return None
    lo, comma, hi = m.group(1), m.group(2), m.group(3)
    if not comma and not lo:
        return None  # "{}" is an error in GNU grep
    low = int(lo or 0)
    high = int(hi) if hi else None
    if max(low, high or 0) > _MAX_INTERVAL or (high is not None and high < low):
        return None
    upper = "" if high is None else str(high)
    return (f"{{{low},{upper}}}" if comma else f"{{{low}}}"), m.end()


def _ends_branch(pattern: str, i: int) -> bool:
    """A basic regex's '$' anchors only at the end of the pattern or of a group or branch."""
    return i == len(pattern) or pattern.startswith(("\\)", "\\|"), i)


def _bracket(pattern: str, start: int) -> tuple[str, int] | None:
    """The POSIX bracket expression at ``pattern[start]`` in Rust syntax, and the index after it."""
    out = ["["]
    n = len(pattern)
    i = start + 1
    if pattern[i:i + 1] == "^":
        out.append("^")
        i += 1
    items = 0
    previous: str | None = None  # the last single character, which a '-' may start a range from
    while i < n:
        ch = pattern[i]
        if ch == "]" and items:
            out.append("]")
            return "".join(out), i + 1
        if ch == "[" and pattern[i + 1:i + 2] in (":", "=", "."):
            end = pattern.find(":]", i + 2)
            if pattern[i + 1] != ":" or end < 0 or pattern[i + 2:end] not in _POSIX_CLASSES:
                return None
            out.append(pattern[i:end + 2])
            i = end + 2
            previous = None
        elif ch == "-" and items and pattern[i + 1:i + 2] not in ("]", ""):
            high = pattern[i + 1]
            # A range past ASCII needs the locale's collation order; C.UTF-8 refuses it.
            if previous is None or high == "[" or not (previous + high).isascii() or high < previous:
                return None
            out.append("-" + _class_char(high))
            i += 2
            previous = None
        else:
            out.append(_class_char(ch))
            previous = ch
            i += 1
        items += 1
    return None


def _class_char(ch: str) -> str:
    """*ch* inside a Rust class: literal in POSIX brackets, but nesting or a set operation in Rust."""
    return "\\" + ch if ch in _CLASS_META else ch
