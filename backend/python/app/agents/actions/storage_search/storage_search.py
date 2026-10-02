"""Storage Pattern Match toolset -- run read-only Linux commands on indexed record storage.

Exposes ``run_command`` and ``find_records`` to the agent, scoped to a specific
connector's record directory.  Supports grep, find, rg, and other read-only
filesystem tools.

``find_records`` is the recommended first step: it locates matching record files
and returns structured metadata (record_id, record_name) that the agent can pass
directly to ``fetch_full_record`` for full content retrieval.

The tool only works when storage type is local (no-op / informative error for S3/Azure).
Commands run inside:
  Linux:  ~/.local/{mountName}/{orgId}/PipesHub/records/{connectorId}/
  macOS:  ~/Library/{mountName}/{orgId}/PipesHub/records/{connectorId}/
  Windows: ~/AppData/{mountName}/{orgId}/PipesHub/records/{connectorId}/
"""

from __future__ import annotations

import asyncio
import contextlib
import json as json_mod
import logging
import os
import platform
import re
import shlex
import shutil
import signal
import time
import uuid
from datetime import datetime, timedelta, timezone

import regex
from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.config.constants.service import config_node_constants
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory
from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

_MAX_OUTPUT_CHARS = 8_000
_EXEC_TIMEOUT_SECS = 30
# Per pipeline stage, whatever the caller asks for; bounds worker memory.
_MAX_STAGE_STDOUT_BYTES = 8 * 1024 * 1024
_MAX_STDERR_BYTES = 64 * 1024
# run_command on a connector the user cannot read in full executes against a
# view holding only the files they may read; this bounds the per-call work.
_MAX_VIEW_FILES = 20_000
_VIEW_ACCESS_CHECK_BATCH = 1_000
_VIEWS_DIR_NAME = ".pattern_match_views"
_STORED_FILE_RE = re.compile(r"^(?:record|metadata)_([0-9a-f\-]+)\.json$", re.IGNORECASE)
_MAX_FETCH_RECORD_IDS = 5
_MAX_SCORE_READ_BYTES = 512_000
# Across all candidates of one find_records call; beyond it the rest keep grep order.
_MAX_SCORE_TOTAL_BYTES = 32_000_000
_SNIPPET_RADIUS = 90
# Ranking runs LLM-written patterns in Python's backtracking engine. grep has
# already proved every candidate matches, so a pattern that times out only
# costs that file its rank, never its place in the results.
_RANK_MATCH_TIMEOUT_SECS = 0.25
_RANK_TOTAL_SECS = 5.0
_MAX_RANK_PATTERN_CHARS = 512
_MAX_RANK_ALTERNATIVES = 32

# Binaries the agent is allowed to invoke (read-only, no destructive ops).
# NOTE: sed is deliberately excluded — it can write files (-i), execute
# commands (e flag), and write to arbitrary paths (w command).
# NOTE: awk is deliberately excluded — its system() builtin and `cmd | getline`
# / `print | cmd` forms shell out to arbitrary commands directly from within
# the awk program text, which no argument-level check can reliably catch.
_ALLOWED_BINARIES: frozenset[str] = frozenset({
    "grep", "egrep", "fgrep",
    "rg",
    "find",
    "ls",
    "wc",
    "head",
    "tail",
    "cat",
    "sort",
    "uniq",
    "tr",
    "xargs",
    "file",
    "echo",
})

# Flags on otherwise-safe, allowlisted binaries that nonetheless provide
# arbitrary command execution or arbitrary file writes -- the classic
# "GTFOBins" allowlist-bypass pattern. Rejected even though the binary itself
# is allowed. Checked against each bare flag token (before any `=value`).
_DANGEROUS_FLAGS_BY_BINARY: dict[str, frozenset[str]] = {
    # find -exec/-execdir/-ok/-okdir run an arbitrary command; -fprintf/-fprint/
    # -fprint0/-fls write arbitrary content to an arbitrary file path; -delete
    # removes matched files. Kept as a second layer of defense even though
    # _FIND_ALLOWED_PRIMARIES (an allowlist, checked separately in
    # _validate_command) already rejects all of these.
    "find": frozenset({
        "-exec", "-execdir", "-ok", "-okdir",
        "-fprintf", "-fprint", "-fprint0", "-fls",
        "-delete",
    }),
    # ripgrep --pre / --hostname-bin run an arbitrary command.
    "rg": frozenset({"--pre", "--pre-glob", "--hostname-bin"}),
    # sort -o writes a file; --compress-program executes one; -T writes temp
    # files to a chosen directory; the rest read a caller-chosen file.
    "sort": frozenset({
        "-o", "--output", "--compress-program", "-T", "--temporary-directory",
        "--files0-from", "--random-source",
    }),
    # xargs -a reads its argument list from a caller-chosen file.
    "xargs": frozenset({"-a", "--arg-file"}),
    "wc": frozenset({"--files0-from"}),
    # file -C writes a compiled magic file; -m / -f read caller-chosen files.
    "file": frozenset({"-C", "--compile", "-m", "--magic-file", "-f", "--files-from"}),
}

# Rejects `..` as a whole path segment, including after an option's `=`.
_TRAVERSAL_RE = re.compile(r"(?:^|[/=])\.\.(?:/|$)")

# Allowlist of read-only `find` primaries the agent may use. Checked in
# _validate_command in addition to (not instead of) _DANGEROUS_FLAGS_BY_BINARY
# above -- any "-"-prefixed find argument not in this set is rejected, closing
# off primaries we haven't reasoned about (e.g. any future GNU find addition)
# rather than relying solely on a denylist of known-dangerous ones.
_FIND_ALLOWED_PRIMARIES: frozenset[str] = frozenset({
    "-name", "-iname", "-type", "-mtime", "-newer", "-size", "-path", "-ipath",
    "-print", "-print0", "-maxdepth", "-mindepth", "-not", "-and", "-or",
    "-prune", "-regex", "-iregex", "-regextype", "-empty", "-readable",
    "-perm", "-user", "-group", "-links", "-inum", "-samefile",
    "-newermt", "-newerat", "-newerct",
    "-daystart", "-follow", "-mount", "-xdev",
    "-true", "-false", "-depth", "-noleaf",
    # Parentheses for grouping
    "(", ")",
})

# Shell metacharacters that enable arbitrary command injection. Commands are
# executed via create_subprocess_exec (never a shell), so $VAR/~/glob cannot
# expand; this filter is defense-in-depth, rejecting operators that would only
# ever indicate an injection attempt. The pipe `|` is allowed for pipelines
# (handled by splitting into exec stages); newlines are rejected outright.
_INJECTION_RE = re.compile(r'[;&<>`\n\r]|\$\(|`|\|\|')


def _check_dangerous_flags(binary: str, args: list[str]) -> str | None:
    """Return an error message if args contain a disallowed flag for binary, else None."""
    disallowed = _DANGEROUS_FLAGS_BY_BINARY.get(binary)
    if not disallowed:
        return None
    for arg in args:
        # Match both `--pre foo` (separate token) and `--pre=foo` (joined) forms.
        flag = arg.split("=", 1)[0]
        if flag not in disallowed and flag.startswith("--") and len(flag) > 2:
            # GNU getopt accepts any unambiguous prefix: --out means --output.
            flag = next((d for d in disallowed if d.startswith(flag)), flag)
        elif flag not in disallowed and arg.startswith("-") and not arg.startswith("--"):
            # Short options cluster or carry an attached value: `-uo out`, `-o/tmp/x`.
            for ch in arg[1:]:
                if not ch.isalnum():
                    break
                if f"-{ch}" in disallowed:
                    flag = f"-{ch}"
                    break
        if flag in disallowed:
            return (
                f"Error: '{flag}' is not allowed with '{binary}' "
                "(it can execute commands, write files, or read files outside the "
                "record directory)."
            )
    return None


def _own_args(parts: list[str], index: int) -> list[str]:
    """Arguments belonging to the binary at ``parts[index]`` itself.

    For xargs that stops at its sub-command, whose flags are validated
    against the sub-command's own rules (``xargs grep -a`` is not ``xargs -a``).
    """
    if parts[index] != "xargs":
        return parts[index + 1:]
    sub_index = _find_xargs_sub_binary(parts, index)
    return parts[index + 1:sub_index]


def _option_value(arg: str) -> str | None:
    """The value embedded in an option token (`--opt=value`, `-Xvalue`), if any."""
    if not arg.startswith("-") or arg == "-":
        return None
    if "=" in arg:
        return arg.split("=", 1)[1]
    if not arg.startswith("--") and len(arg) > 2:
        return arg[2:]
    return None


# GNU xargs flags that consume a SEPARATE value token immediately after
# them (e.g. `-I replace-str`, `-n max-args`). That value token can itself
# fail to start with "-" (e.g. `-I cat`), so it must never be mistaken for
# xargs's actual sub-command -- that mistake is exactly what let a
# disallowed sub-command hide a few tokens further down the argument list.
# Includes the deprecated single-letter aliases (-i, -l, -e); GNU xargs
# documents these as taking an optional value, but treating them as always
# value-consuming is the safe direction here (under-skipping is what
# caused the bug, not over-skipping).
_XARGS_VALUE_FLAGS: frozenset[str] = frozenset({
    "-I", "-i", "-L", "-l", "-n", "-P", "-s", "-a", "-d", "-E", "-e",
})


def _find_xargs_sub_binary(parts: list[str], index: int) -> int | None:
    """Return the index in *parts* of the token that xargs at ``parts[index]``
    will actually invoke.

    Walk parts[index+1:] sequentially: a value-consuming flag (see
    ``_XARGS_VALUE_FLAGS``) skips itself AND the token right after it (that
    token is the flag's value, never a sub-command candidate); any other
    "-"-prefixed token is a boolean flag and is skipped alone. The first
    surviving token's index is the real sub-command position. Returns None
    if every token is consumed as a flag or flag-value (e.g. a trailing,
    valueless `-I`) -- callers treat that the same as "no sub-command
    found", i.e. allow.

    Returning the INDEX (not just the token string) matters: a later
    `parts.index(sub_binary, ...)` re-lookup would find the first literal
    match of that string, which can be an earlier flag *value* that
    coincidentally equals the real sub-command's name (e.g. `-I xargs
    xargs find ...`) rather than the position this walk actually landed
    on. Returning the index sidesteps that ambiguity entirely.
    """
    i = index + 1
    n = len(parts)
    while i < n:
        tok = parts[i]
        if tok.startswith("-"):
            i += 2 if tok in _XARGS_VALUE_FLAGS else 1
            continue
        return i
    return None


def _validate_xargs_subcommand(parts: list[str], xargs_index: int) -> str | None:
    """Validate the sub-command invoked by the xargs at ``parts[xargs_index]``.

    xargs's own sub-command can itself be another xargs (e.g.
    ``xargs -0 xargs find . -exec ... +``), and that can nest arbitrarily
    deep. Walk forward through every nesting level -- not just one -- so a
    disallowed sub-command or dangerous flag can't be smuggled in behind
    extra xargs wrapping. Returns an error message, or None if the whole
    chain validates.
    """
    index = xargs_index
    while True:
        sub_index = _find_xargs_sub_binary(parts, index)
        if sub_index is None:
            return None

        sub_binary = parts[sub_index]
        if sub_binary not in _ALLOWED_BINARIES:
            return (
                f"Error: xargs sub-command '{sub_binary}' is not allowed. "
                f"Allowed commands: {', '.join(sorted(_ALLOWED_BINARIES))}"
            )
        # xargs appends file names, and uniq writes to its second operand.
        if sub_binary == "uniq":
            return "Error: 'uniq' is not allowed as an xargs sub-command (it can overwrite files)."

        sub_flag_err = _check_dangerous_flags(sub_binary, _own_args(parts, sub_index))
        if sub_flag_err:
            return sub_flag_err

        if sub_binary != "xargs":
            return None
        index = sub_index


# ──────────────────────────────────────────────────────────────────────────────
# Pydantic input schema
# ──────────────────────────────────────────────────────────────────────────────

class RunCommandInput(BaseModel):
    connector_id: str = Field(
        description=(
            "The connector ID whose records to search. Scopes the command to "
            "that connector's record directory (records/<connector_id>/)."
        )
    )
    command: str = Field(
        description=(
            "A read-only Linux command to run inside the connector's record directory. "
            "Allowed binaries: grep, egrep, fgrep, rg, find, ls, wc, head, tail, "
            "cat, sort, uniq, xargs, file, echo. "
            "All paths must be relative (no leading /). "
            "Pipe (|) is supported; other shell operators (;, &&, ||, >, <, $()) are not. "
            "ALWAYS use double quotes for patterns and arguments. "
            "Examples: "
            "grep -r \"keyword\" .  "
            "find . -name \"*.json\" -mtime -7  "
            "grep -rl \"quarterly report\" . | head -20"
        )
    )
    record_date: str | None = Field(
        default=None,
        description=(
            "Optional ISO date (YYYY-MM-DD) to restrict the search to records "
            "whose stored file was written (indexed) within +/-1 day of that date. "
            "This is indexing time, not the source document's modification time. "
            "When set, a find-based time filter is prepended to the command. "
            "Only supported when the first stage is grep/egrep/fgrep/rg searching '.'. "
            "Example: '2026-06-01'"
        )
    )


_MAX_FIND_RECORDS = 20
# find_records answers every empty outcome identically, so it never reveals
# whether matching content exists in records the user cannot read.
_NO_ACCESSIBLE_MATCH = json_mod.dumps({
    "records": [],
    "total_found": 0,
    "message": (
        "No accessible records matched. The command must list record files "
        "(grep -l or -c, or find) for records to be returned."
    ),
})


def _public_failure(output: str) -> str:
    """A failed command's error without its stdout/stderr, which can carry
    lines of records the user cannot read (e.g. ``sort -c`` echoes a line)."""
    if output.startswith("Command failed"):
        return output.split(":", 1)[0] + ". Its output is withheld."
    return output
_RECORD_FILENAME_RE = re.compile(r"record_([0-9a-f\-]+)\.json$", re.IGNORECASE)
# Matches a record_<virtualRecordId>.json reference anywhere in a text line
# (e.g. grep -r output "grp/doc/sid/record_<vrid>.json:match"), used to gate
# raw command output on per-record access.
_VRID_IN_TEXT_RE = re.compile(r"record_([0-9a-f\-]+)\.json", re.IGNORECASE)
# Matches grep -c output: a file path ending in .json followed by :count.
# Used to parse match counts for relevance ranking.
_GREP_COUNT_RE = re.compile(r"^(.*\.json):(\d+)$", re.IGNORECASE)


class FindRecordsInput(BaseModel):
    connector_id: str = Field(
        description=(
            "The connector ID whose records to search. "
            "Scopes the command to that connector's record directory."
        )
    )
    command: str = Field(
        description=(
            "A read-only Linux command that outputs file paths (one per line). "
            "The tool runs this command, extracts record file paths from the output, "
            "and returns structured metadata (record_id, record_name, virtual_record_id). "
            "Use grep with -l (list files) or find to discover files. "
            "ALWAYS use double quotes for patterns. "
            "Examples: "
            "grep -ril \"keyword\" .  "
            "grep -rilZ \"keyword\" . | xargs -0 grep -il \"term2\"  "
            "find . -name \"*.json\" -mtime -7  "
            "The same allowed binaries and security rules as run_command apply."
        )
    )
    max_results: int = Field(
        default=10,
        description="Maximum number of records to return (1-20). Default 10.",
    )


# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"\n[...output truncated ({len(text)} chars total)]"


def _resolve_mount_root(mount_name: str) -> str:
    """Return OS-specific storage mount root, mirroring local-storage.provider.ts."""
    home = os.path.expanduser("~")
    system = platform.system()
    if system == "Darwin":
        return os.path.join(home, "Library", mount_name)
    if system == "Windows":
        return os.path.join(home, "AppData", mount_name)
    # Linux and everything else
    return os.path.join(home, ".local", mount_name)


def is_local_storage(storage_cfg: dict | None) -> bool:
    """Return True when the configured storage backend is local disk.

    Shared by ``_resolve_connector_path`` (this module) and
    ``Retrieval._execute_parallel_search`` (retrieval.py) so both callers
    agree on what counts as "local" without duplicating the comparison.
    """
    storage_type = (storage_cfg or {}).get("storageType", "local")
    return storage_type in ("local", "LOCAL")


def _split_pipeline_stages(command: str) -> list[str]:
    """Split a command on un-quoted pipe characters.

    ``command.split("|")`` is NOT quote-aware — it breaks patterns like
    ``grep -riE 'term1|term2' .`` into fragments with unmatched quotes.
    This function walks the string character-by-character, tracking whether
    we are inside single or double quotes, and only splits on ``|`` that
    appear outside any quoting context.
    """
    stages: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    escape_next = False

    for ch in command:
        if escape_next:
            current.append(ch)
            escape_next = False
            continue
        if ch == "\\" and not in_single:
            current.append(ch)
            escape_next = True
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
            continue
        if ch == "|" and not in_single and not in_double:
            stages.append("".join(current).strip())
            current = []
            continue
        current.append(ch)

    stages.append("".join(current).strip())
    return stages


def _validate_command(command: str) -> tuple[bool, str]:
    """Validate command string against allowlist and security rules.

    Returns (True, "") on success or (False, error_message) on rejection.
    Validates every pipeline stage; only the pipe character is allowed as
    a shell operator — semicolons, redirects, subshells, etc. are blocked.
    """
    command = command.strip()
    if not command:
        return False, "Error: empty command provided"

    # Block injection metacharacters before any further processing.
    m = _INJECTION_RE.search(command)
    if m:
        return False, (
            f"Error: shell operator '{m.group()}' is not allowed. "
            "Only the pipe character | is permitted."
        )

    stages = _split_pipeline_stages(command)
    for stage in stages:
        if not stage:
            return False, "Error: empty pipeline stage (check for leading/trailing |)"

        try:
            parts = shlex.split(stage)
        except ValueError as exc:
            return False, f"Error: cannot parse command: {exc}"

        if not parts:
            return False, "Error: blank pipeline stage"

        binary = parts[0]
        if binary not in _ALLOWED_BINARIES:
            allowed_sorted = ", ".join(sorted(_ALLOWED_BINARIES))
            return False, (
                f"Error: command '{binary}' is not allowed. "
                f"Allowed commands: {allowed_sorted}"
            )

        flag_err = _check_dangerous_flags(binary, _own_args(parts, 0))
        if flag_err:
            return False, flag_err

        # uniq's second operand is an output file it overwrites.
        if binary == "uniq":
            operands: list[str] = []
            end_of_options = False
            j = 1
            while j < len(parts):
                tok = parts[j]
                if end_of_options or tok == "-" or not tok.startswith("-"):
                    # "-" is stdin; everything after "--" is an operand.
                    operands.append(tok)
                elif tok == "--":
                    end_of_options = True
                elif tok in ("-f", "-s", "-w"):
                    # These take their number as the next token.
                    j += 1
                j += 1
            if len(operands) > 1:
                return False, "Error: uniq accepts at most one input file (a second operand is written to)."

        # find: allowlist of read-only primaries, checked in addition to the
        # denylist above. Any "-"-prefixed argument not in the allowlist is
        # rejected outright (numeric values like the "-1" in "-mtime -1" are
        # exempted, since they are option VALUES, not primaries).
        if binary == "find":
            for arg in parts[1:]:
                if not arg.startswith("-") or arg in _FIND_ALLOWED_PRIMARIES:
                    continue
                try:
                    float(arg)
                    continue
                except ValueError:
                    pass
                return False, (
                    f"Error: find primary '{arg}' is not in the read-only allowlist."
                )

        # xargs takes a sub-command as its argument -- validate that too,
        # walking through any further nested xargs (xargs -0 xargs ...).
        if binary == "xargs":
            xargs_err = _validate_xargs_subcommand(parts, 0)
            if xargs_err:
                return False, xargs_err

        # grep-family: the token after -e / --regexp is a regex PATTERN, not a
        # path. Mark it so a pattern containing a leading '/' (e.g. -e "/foo/")
        # is not misread as an absolute path. This is the only sanctioned way to
        # pass a slash-containing pattern; a bare positional is treated as a
        # path (we cannot tell pattern from path by shape).
        pattern_indices: set[int] = set()
        if binary in ("grep", "egrep", "fgrep", "rg"):
            for idx in range(1, len(parts)):
                if parts[idx] in ("-e", "--regexp") and idx + 1 < len(parts):
                    pattern_indices.add(idx + 1)
                # Joined forms carry the pattern in the same token: --regexp=/x/, -e/x/.
                elif parts[idx].startswith(("--regexp=", "-e")) and not parts[idx].startswith("--e"):
                    pattern_indices.add(idx)

        for idx in range(1, len(parts)):
            if idx in pattern_indices:
                continue
            arg = parts[idx]
            if _TRAVERSAL_RE.search(arg):
                return False, (
                    f"Error: path traversal ('..') is not allowed in argument '{arg}'. "
                    "All paths must be relative to the connector's record directory."
                )
            embedded = _option_value(arg)
            if embedded is not None and (embedded.startswith("/") or _TRAVERSAL_RE.search(embedded)):
                return False, (
                    f"Error: absolute paths and '..' are not allowed in option value '{arg}'. "
                    "Use relative paths only."
                )
            # Reject any absolute path unconditionally (/etc, /etc/, /root/, /proc/…).
            # The tool mandates relative paths; a legitimate slash-containing regex
            # literal must be passed via -e (e.g. grep -e "/foo/" .).
            if arg.startswith("/"):
                return False, (
                    f"Error: absolute paths are not allowed in argument '{arg}'. "
                    "Use relative paths only. If this is a grep regex literal that must "
                    "contain a slash, pass it with -e (e.g. grep -e \"/foo/\" .)."
                )

    return True, ""


def _build_date_filtered_command(command: str, record_date: str) -> tuple[bool, str]:
    """Inject a find-based time filter around the user command.

    Validates the date format and returns the modified shell command string.
    Returns (True, new_command) on success or (False, error_message) on failure.
    """
    try:
        target = datetime.strptime(record_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return False, (
            f"Error: invalid record_date format '{record_date}'. "
            "Expected YYYY-MM-DD (e.g. '2026-06-01')."
        )

    after = (target - timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
    before = (target + timedelta(days=2)).strftime("%Y-%m-%d 00:00:00")

    # When the command has pipes (e.g. `grep -rilZ "x" . | xargs -0
    # grep -i "y"`), only the first stage receives the date-filtered
    # file list; subsequent stages consume the previous stage's output.
    stages = _split_pipeline_stages(command)
    ok, first_stage = _bind_stage_to_file_list(stages[0])
    if not ok:
        return False, first_stage

    date_filter = (
        f"find . -type f -name '*.json' -newermt '{after}' -not -newermt '{before}' "
        # -r: with no file selected, rg would otherwise search the whole directory.
        f"-print0 | xargs -0 -r {first_stage}"
    )
    if len(stages) > 1:
        rest = " | ".join(stages[1:])
        date_filter = f"{date_filter} | {rest}"
    return True, date_filter


_GREP_RECURSIVE_LONG_FLAGS = frozenset({"--recursive", "--dereference-recursive"})
_GREP_PATTERN_FLAGS = frozenset({"-e", "--regexp", "-f", "--file"})
# Short options that take a value (attached or as the next token).
_SHORT_VALUE_LETTERS = {"rg": "efmABCgtTjMErd"}
_GREP_SHORT_VALUE_LETTERS = "efmABCdD"


def _bind_stage_to_file_list(stage: str) -> tuple[bool, str]:
    """Rewrite a grep/rg stage to search only the files xargs hands it.

    A directory operand (`.`) or recursion would make grep search the whole
    connector regardless of the file list, so both are dropped; any other
    explicit path is rejected rather than silently widening the search.
    """
    try:
        tokens = shlex.split(stage)
    except ValueError as exc:
        return False, f"Error: cannot parse command: {exc}"
    binary = tokens[0] if tokens else ""
    if binary not in _GREP_BINARIES:
        return False, (
            "Error: record_date only supports a grep/egrep/fgrep/rg first stage. "
            "For other commands, filter by date in the command itself, e.g. "
            "find . -name \"*.json\" -newermt \"2026-06-01\"."
        )

    value_letters = _SHORT_VALUE_LETTERS.get(binary, _GREP_SHORT_VALUE_LETTERS)
    # -H keeps the file name on every line even when xargs passes a single file.
    out: list[str] = [binary, "-H"]
    positionals: list[int] = []
    has_pattern_flag = False
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GREP_PATTERN_FLAGS:
            has_pattern_flag = True
            out.extend(tokens[i:i + 2])
            i += 2
            continue
        if tok in _GREP_VALUE_FLAGS:
            out.extend(tokens[i:i + 2])
            i += 2
            continue
        if tok in _GREP_RECURSIVE_LONG_FLAGS:
            i += 1
            continue
        if tok.startswith("--"):
            has_pattern_flag = has_pattern_flag or tok.split("=", 1)[0] in _GREP_PATTERN_FLAGS
            out.append(tok)
            i += 1
            continue
        if tok.startswith("-") and len(tok) > 1:
            kept: list[str] = []
            takes_next = False
            for j, ch in enumerate(tok[1:], start=1):
                if ch in value_letters:
                    has_pattern_flag = has_pattern_flag or ch in "ef"
                    kept.append(tok[j:])
                    takes_next = j == len(tok) - 1
                    break
                # rg's -r is --replace, caught as a value letter above.
                if ch not in "rR":
                    kept.append(ch)
            if kept:
                out.append("-" + "".join(kept))
            if takes_next and i + 1 < len(tokens):
                out.append(tokens[i + 1])
                i += 1
            i += 1
            continue
        positionals.append(len(out))
        out.append(tok)
        i += 1

    if not has_pattern_flag and not positionals:
        return False, "Error: no search pattern found in the first stage."
    path_positions = positionals if has_pattern_flag else positionals[1:]
    for pos in path_positions:
        if out[pos] not in (".", "./"):
            return False, (
                f"Error: record_date cannot be combined with the explicit path "
                f"'{out[pos]}'. Search '.' or drop record_date."
            )
    drop = set(path_positions)
    return True, shlex.join(tok for idx, tok in enumerate(out) if idx not in drop)


async def _read_capped(stream: asyncio.StreamReader, max_bytes: int) -> tuple[bytes, bool]:
    """Read *stream* keeping at most *max_bytes*; returns (data, truncated).

    Reaching the cap counts as truncated without waiting for more output, so
    the caller can kill a still-scanning process right away.
    """
    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        chunk = await stream.read(min(65536, max_bytes - total))
        if not chunk:
            return b"".join(chunks), False
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks), True


async def _drain_capped(stream: asyncio.StreamReader, max_bytes: int) -> bytes:
    """Read *stream* to EOF, keeping only the first *max_bytes*.

    Keeps draining past the cap so a chatty stderr cannot fill its pipe and
    stall the process.
    """
    kept = bytearray()
    while chunk := await stream.read(65536):
        if len(kept) < max_bytes:
            kept.extend(chunk[: max_bytes - len(kept)])
    return bytes(kept)


async def _communicate_capped(
    proc: asyncio.subprocess.Process,
    stdin_data: bytes | None,
    max_stdout_bytes: int,
) -> tuple[bytes, bytes, bool]:
    """Like ``proc.communicate`` but with stdout bounded to *max_stdout_bytes*.

    Once the cap is reached the process is killed. Returns
    (stdout, stderr, truncated).
    """
    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None

    async def _feed() -> None:
        try:
            if stdin_data:
                proc.stdin.write(stdin_data)
                await proc.stdin.drain()
        except OSError:
            # The process exited or was killed before reading all its input.
            pass
        finally:
            proc.stdin.close()

    async def _read_stdout() -> tuple[bytes, bool]:
        data, truncated = await _read_capped(proc.stdout, max_stdout_bytes)
        if truncated:
            # Kill before _feed is awaited: a process blocked on full stdout
            # stops reading stdin, and _feed's drain would never return.
            _kill_tree(proc)
        return data, truncated

    stderr_task = asyncio.ensure_future(_drain_capped(proc.stderr, _MAX_STDERR_BYTES))
    try:
        _, (stdout, truncated) = await asyncio.gather(_feed(), _read_stdout())
        if truncated:
            # Anything a killed child still holds is abandoned rather than awaited.
            try:
                stderr = await asyncio.wait_for(asyncio.shield(stderr_task), timeout=2)
            except asyncio.TimeoutError:
                stderr = b""
        else:
            stderr = await stderr_task
    finally:
        if not stderr_task.done():
            stderr_task.cancel()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(proc.wait(), timeout=2)
    return stdout, stderr, truncated


def _kill_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill *proc* and, on POSIX, every process it started (xargs → grep).

    Killing only xargs would leave its grep child holding the stdout pipe.
    """
    if proc.returncode is not None:
        return
    pid = proc.pid
    # Signal a group only when it is the one start_new_session gave this child
    # (pgid == pid). Anything else is someone else's group: a bad pid of 1
    # would be init's, and as root killpg(1) takes down the whole host.
    if os.name == "posix" and isinstance(pid, int) and pid > 1:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            if os.getpgid(pid) == pid and pid != os.getpgrp():
                os.killpg(pid, signal.SIGKILL)
                return
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def _trim_to_last_record(data: bytes) -> bytes:
    """Drop a trailing partial line / NUL-separated path left by truncation."""
    cut = max(data.rfind(b"\n"), data.rfind(b"\0"))
    return data[: cut + 1] if cut >= 0 else b""


async def _run_subprocess(
    command: str,
    *,
    cwd: str,
    timeout: int = _EXEC_TIMEOUT_SECS,
    max_stdout_bytes: int = 0,
    max_output_chars: int = _MAX_OUTPUT_CHARS,
) -> tuple[bool, str]:
    """Execute a validated pipeline without a shell and return (success, output).

    Each pipeline stage is tokenized and run via ``create_subprocess_exec`` --
    never ``create_subprocess_shell`` -- so ``$VAR`` / ``${VAR}`` / ``~`` /
    glob expansion cannot occur (there is no shell to perform it). Stage N's
    stdout is captured and fed as stage N+1's stdin in Python, reproducing pipe
    semantics without a shell. Pipeline exit status follows shell convention:
    the LAST stage's exit code and stderr decide success (no pipefail).

    The whole pipeline shares a single ``timeout`` deadline; every spawned
    process is killed if it is exceeded.

    Every stage's stdout is capped (``_MAX_STAGE_STDOUT_BYTES``) and the
    stage is killed once it passes the cap. When *max_stdout_bytes* > 0 the
    **first** stage uses that smaller cap instead, so a grep over 100K+ files
    stops once the first few hundred matches are in.
    """
    stage_tokens: list[list[str]] = []
    for stage in _split_pipeline_stages(command):
        try:
            tokens = shlex.split(stage)
        except ValueError as exc:
            return False, f"Error: cannot parse command: {exc}"
        if not tokens:
            return False, "Error: blank pipeline stage"
        stage_tokens.append(tokens)

    procs: list[asyncio.subprocess.Process] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    stdin_data: bytes | None = None
    last_proc: asyncio.subprocess.Process | None = None
    last_stderr = b""
    killed_early = max_stdout_bytes > 0

    try:
        for i, tokens in enumerate(stage_tokens):
            proc = await asyncio.create_subprocess_exec(
                *tokens,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                # Own process group, so _kill_tree can reach xargs's children.
                start_new_session=os.name == "posix",
            )
            procs.append(proc)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError

            cap = max_stdout_bytes if (i == 0 and max_stdout_bytes > 0) else _MAX_STAGE_STDOUT_BYTES
            stdout_bytes, stderr_bytes, truncated = await asyncio.wait_for(
                _communicate_capped(proc, stdin_data if i > 0 else None, cap),
                timeout=remaining,
            )
            if truncated:
                killed_early = True
                # A half-written path would reach the next stage as a bogus file name.
                if i < len(stage_tokens) - 1:
                    stdout_bytes = _trim_to_last_record(stdout_bytes)

            stdin_data = stdout_bytes
            last_proc = proc
            last_stderr = stderr_bytes

        stdout = (stdin_data or b"").decode("utf-8", errors="replace")
        stderr = last_stderr.decode("utf-8", errors="replace")
        exit_code = last_proc.returncode if last_proc else 0

        if killed_early and stdout.strip():
            return True, _truncate(stdout, max_output_chars)

        if exit_code == 0:
            output = stdout if stdout.strip() else "No matches found."
            return True, _truncate(output, max_output_chars)

        if exit_code == 1 and not stderr.strip():
            # grep/rg exit 1 means "no matches" — not an error
            return True, "No matches found."

        if exit_code == 123:
            # xargs returns 123 when a child process (grep) exits non-zero.
            # In piped grep chains this means an intermediate grep found no
            # matches — treat as "no matches", not a hard error.
            return True, "No matches found."

        error_detail = stderr.strip() or stdout.strip() or f"exit code {exit_code}"
        return False, f"Command failed (exit {exit_code}): {_truncate(error_detail, 2000)}"

    except TimeoutError:
        return False, f"Error: command timed out after {timeout}s"

    except Exception as exc:
        return False, f"Error executing command: {exc}"

    finally:
        for proc in procs:
            if proc.returncode is None:
                try:
                    _kill_tree(proc)
                    await proc.communicate()
                except Exception:
                    pass


_GREP_BINARIES = frozenset({"grep", "egrep", "fgrep", "rg"})
# Flags whose value is a separate token that must not be read as the pattern.
_GREP_VALUE_FLAGS = frozenset({
    "-m", "-A", "-B", "-C", "-f", "-g", "-t", "-T", "-d", "-D",
    "--max-count", "--include", "--exclude", "--exclude-dir", "--glob",
    "--type", "--type-not", "--context", "--after-context", "--before-context",
})
# A quantified group, e.g. "(a+)+", can backtrack exponentially in Python's
# engine; grep's DFA is immune, so only the Python-side ranking needs this.
_NESTED_QUANTIFIER_RE = re.compile(r"\)[*+?{]")
_UNBOUNDED_DOT_RE = re.compile(r"(?<!\\)\.([*+])")
_POSIX_CLASSES = {
    "[:space:]": r"\s", "[:blank:]": r" \t", "[:digit:]": "0-9",
    "[:alpha:]": "a-zA-Z", "[:alnum:]": "a-zA-Z0-9",
    "[:upper:]": "A-Z", "[:lower:]": "a-z",
}


def _bre_to_python(alternative: str) -> str:
    """Translate one GNU basic-regex alternative into Python regex syntax.

    In BRE, ``\\?`` ``\\+`` ``\\(`` ``\\)`` are operators and the bare
    characters are literals — the reverse of Python.
    """
    out: list[str] = []
    i = 0
    while i < len(alternative):
        ch = alternative[i]
        if ch == "\\" and i + 1 < len(alternative):
            nxt = alternative[i + 1]
            if nxt in "?+(){}":
                out.append(nxt)
            elif nxt in "<>":
                out.append(r"\b")
            else:
                out.append("\\" + nxt)
            i += 2
            continue
        out.append("\\" + ch if ch in "?+(){}|" else ch)
        i += 1
    return "".join(out)


def _grep_search_regexes(command: str) -> list[regex.Pattern[str]]:
    """Compile each search alternative of every non-inverted grep stage.

    ``grep a . | xargs grep "b\\|c"`` yields regexes for a, b and c, so a
    file can be ranked by how many distinct alternatives it contains.
    """
    regexes: list[regex.Pattern[str]] = []
    for stage in _split_pipeline_stages(command):
        try:
            tokens = shlex.split(stage)
        except ValueError:
            continue
        start = next((i for i, t in enumerate(tokens) if t in _GREP_BINARIES), None)
        if start is None:
            continue
        binary, args = tokens[start], tokens[start + 1:]
        short_flags = "".join(a[1:] for a in args if a.startswith("-") and not a.startswith("--"))
        if "v" in short_flags or "--invert-match" in args:
            continue
        fixed = binary == "fgrep" or "F" in short_flags
        extended = binary in ("egrep", "rg") or "E" in short_flags
        patterns: list[str] = []
        positionals: list[str] = []
        i = 0
        while i < len(args):
            arg = args[i]
            if arg in ("-e", "--regexp") and i + 1 < len(args):
                patterns.append(args[i + 1])
                i += 2
            elif arg in _GREP_VALUE_FLAGS:
                i += 2
            else:
                if not arg.startswith("-"):
                    positionals.append(arg)
                i += 1
        if not patterns:
            patterns = positionals[:1]
        for pattern in patterns:
            regexes.extend(_compile_alternatives(pattern, fixed=fixed, extended=extended))
    # A term repeated across stages or alternatives must count once.
    return list({rx.pattern.lower(): rx for rx in regexes}.values())[:_MAX_RANK_ALTERNATIVES]


def _compile_alternatives(pattern: str, *, fixed: bool, extended: bool) -> list[regex.Pattern[str]]:
    """Split a grep pattern into top-level alternatives and compile each.

    A pattern with groups is kept whole, since splitting ``(a|b)c`` on ``|``
    would produce two broken halves. Anything that fails to compile, or could
    backtrack catastrophically, is matched literally instead; an alternative
    longer than ``_MAX_RANK_PATTERN_CHARS`` is not used for ranking at all.
    """
    if fixed:
        alternatives = [regex.escape(p) for p in pattern.split("\n")]
    elif extended:
        alternatives = [pattern] if "(" in pattern else pattern.split("|")
    else:
        parts = [pattern] if "\\(" in pattern else pattern.split("\\|")
        alternatives = [_bre_to_python(p) for p in parts]
    compiled: list[regex.Pattern[str]] = []
    for raw_alt in alternatives[:_MAX_RANK_ALTERNATIVES]:
        if len(raw_alt) > _MAX_RANK_PATTERN_CHARS:
            continue
        alt = raw_alt
        for posix, py in _POSIX_CLASSES.items():
            alt = alt.replace(posix, py)
        if not alt:
            continue
        if _NESTED_QUANTIFIER_RE.search(alt):
            alt = regex.escape(raw_alt)
        else:
            # Unbounded ".*" is quadratic on the long single-line blocks records hold.
            alt = _UNBOUNDED_DOT_RE.sub(lambda m: ".{0,200}" if m.group(1) == "*" else ".{1,200}", alt)
        try:
            compiled.append(regex.compile(alt, regex.IGNORECASE))
        except regex.error:
            compiled.append(regex.compile(regex.escape(raw_alt), regex.IGNORECASE))
    return compiled


# Disjoint alternation, so scanning a truncated file stays linear.
_BLOCK_DATA_RE = re.compile(r'"data"\s*:\s*"((?:[^"\\]|\\.)*)"')


def _record_search_text(raw: str) -> str:
    """The human-readable text of a stored record file (blocks, summary).

    Never falls back to the raw file: it opens with the stored record name,
    which with dedup is the first-indexed owner's, and it would surface in
    snippets. A file that does not parse (e.g. cut at the read cap) yields
    only its block data strings.
    """
    try:
        rec = json_mod.loads(raw).get("record")
    except (ValueError, AttributeError, RecursionError):
        return "\n".join(_unescaped_block_data(raw))
    if not isinstance(rec, dict):
        return ""
    parts: list[str] = []
    blocks = (rec.get("block_containers") or {}).get("blocks") or []
    parts.extend(b["data"] for b in blocks if isinstance(b, dict) and isinstance(b.get("data"), str))
    summary = (rec.get("semantic_metadata") or {}).get("summary")
    if isinstance(summary, str):
        parts.append(summary)
    return "\n".join(p for p in parts if p)


def _unescaped_block_data(raw: str) -> list[str]:
    out: list[str] = []
    for m in _BLOCK_DATA_RE.finditer(raw):
        try:
            out.append(json_mod.loads('"' + m.group(1) + '"'))
        except ValueError:
            continue
    return out


def _score_record_file(
    full_path: str,
    regexes: list[regex.Pattern[str]],
    *,
    timeout: float = _RANK_MATCH_TIMEOUT_SECS,
) -> tuple[int, int, str, int]:
    """Return (distinct alternatives matched, total matches, snippet, bytes read).

    The snippet is taken around the rarest matched alternative — the term
    that most distinguishes this record, e.g. "billion" rather than the
    company name that appears everywhere.
    """
    try:
        with open(full_path, encoding="utf-8", errors="replace") as f:
            raw = f.read(_MAX_SCORE_READ_BYTES)
    except OSError:
        return 0, 0, "", 0
    text = _record_search_text(raw)
    distinct = total = 0
    rarest: tuple[int, regex.Match[str]] | None = None
    try:
        for rx in regexes:
            matches = list(rx.finditer(text, timeout=timeout, concurrent=True))
            if not matches:
                continue
            distinct += 1
            total += len(matches)
            if rarest is None or len(matches) < rarest[0]:
                rarest = (len(matches), matches[0])
    except TimeoutError:
        return 0, 0, "", len(raw)
    if rarest is None:
        return 0, 0, "", len(raw)
    m = rarest[1]
    start, end = max(0, m.start() - _SNIPPET_RADIUS), min(len(text), m.end() + _SNIPPET_RADIUS)
    snippet = " ".join(text[start:end].split())
    snippet = f"{'…' if start else ''}{snippet}{'…' if end < len(text) else ''}"
    return distinct, total, snippet, len(raw)


def _rank_candidates(
    candidates: list[tuple[str, str, int]],
    connector_dir: str,
    regexes: list[regex.Pattern[str]],
) -> list[tuple[str, str, int, int, str]]:
    """Order (path, vrid, grep_count) by real matches in the record text.

    Returns (path, vrid, matched_terms, match_count, snippet), best first.
    ``grep -c`` counts matching lines and each record is one line, so its
    count is 0 or 1 for every file and cannot rank anything. Files beyond the
    read or time budget, or outside the connector directory, keep grep's order.
    """
    real_dir = os.path.realpath(connector_dir)
    budget = _MAX_SCORE_TOTAL_BYTES
    deadline = time.monotonic() + _RANK_TOTAL_SECS
    scored: list[tuple[int, int, int, str, str, str]] = []
    for order, (path, vrid, grep_count) in enumerate(candidates):
        full_path = os.path.realpath(os.path.join(connector_dir, path))
        distinct, total, snippet = 0, grep_count, ""
        remaining = deadline - time.monotonic()
        if regexes and budget > 0 and remaining > 0 and full_path.startswith(real_dir + os.sep):
            distinct, total, snippet, read = _score_record_file(
                full_path, regexes, timeout=min(_RANK_MATCH_TIMEOUT_SECS, remaining),
            )
            budget -= read
        scored.append((distinct, total, -order, path, vrid, snippet))
    scored.sort(reverse=True)
    return [(path, vrid, d, total, snippet) for d, total, _o, path, vrid, snippet in scored]


def _list_stored_files(connector_dir: str) -> list[tuple[str, str]] | None:
    """(relative path, virtualRecordId) of every stored record file, or None past the cap."""
    found: list[tuple[str, str]] = []
    for root, _dirs, files in os.walk(connector_dir):
        for name in files:
            m = _STORED_FILE_RE.match(name)
            if m:
                found.append((os.path.relpath(os.path.join(root, name), connector_dir), m.group(1)))
                if len(found) > _MAX_VIEW_FILES:
                    return None
    return found


def _populate_view(connector_dir: str, view_dir: str, files: list[tuple[str, str]]) -> None:
    """Mirror (relative path, vrid) *files* into *view_dir* as ``<vrid>/<file name>``,
    hard-linked so mtimes match.

    Named by virtualRecordId because a stored path carries the folder and group
    names of containers the user may not be able to see.
    """
    os.makedirs(view_dir, exist_ok=True)
    for rel, vrid in files:
        src = os.path.join(connector_dir, rel)
        dst = os.path.join(view_dir, vrid, os.path.basename(rel))
        if os.path.exists(dst):
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)


def _views_root(connector_dir: str) -> str:
    """Beside ``records/`` (same filesystem, so hard links work), never inside it."""
    return os.path.join(os.path.dirname(os.path.dirname(connector_dir)), _VIEWS_DIR_NAME)


# ──────────────────────────────────────────────────────────────────────────────
# Toolset registration
# ──────────────────────────────────────────────────────────────────────────────

@ToolsetBuilder("Storage Pattern Match")\
    .in_group("Internal Tools")\
    .with_description(
        "Run read-only Linux pattern matching commands on indexed record storage. "
        "Scoped per connector; supports grep, find, rg, and more."
    )\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/storage_search.svg"))\
    .build_decorator()
class StoragePatternMatch:
    """Read-only pattern matching over local indexed record storage, exposed to agents."""

    def __init__(self, state: ChatState) -> None:
        self.state = state
        self._containers: object | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_connector_path(self, connector_id: str) -> tuple[str | None, str | None]:
        """Return (connector_dir, error_message).

        Reads mountName + storageType from etcd via config_service, builds the
        OS-correct scoped path, and verifies it exists on disk.
        Only local storage is supported; cloud providers return an informative error.
        """
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', connector_id):
            return None, f"Error: invalid connector_id '{connector_id}'"

        config_service = self.state.get("config_service")
        org_id = self.state.get("org_id", "")

        if not config_service:
            return None, "Error: config_service not available in agent state"
        if not org_id:
            return None, "Error: org_id not available in agent state"

        try:
            storage_cfg = await config_service.get_config(
                config_node_constants.STORAGE.value
            )
        except Exception as exc:
            return None, f"Error: could not read storage config: {exc}"

        if not is_local_storage(storage_cfg):
            storage_type = (storage_cfg or {}).get("storageType", "local")
            return None, (
                f"Error: storage_pattern_match only works with local storage "
                f"(current type: '{storage_type}'). "
                "Use retrieval.search_internal_knowledge for cloud storage environments."
            )

        mount_name = (storage_cfg or {}).get("mountName", "PipesHub")
        mount_root = _resolve_mount_root(mount_name)
        # Path mirrors getFullDocumentPath() in Node.js: orgId/PipesHub/records/<cid>
        connector_dir = os.path.join(
            mount_root, org_id, "PipesHub", "records", connector_id
        )

        # Defense-in-depth against path traversal: even though connector_id is
        # already validated against a strict charset above, verify the resolved
        # real path never escapes the connector's records base directory.
        base_dir = os.path.realpath(os.path.join(mount_root, org_id, "PipesHub", "records"))
        resolved_dir = os.path.realpath(connector_dir)
        if resolved_dir != base_dir and not resolved_dir.startswith(base_dir + os.sep):
            return None, "Error: connector path escapes base directory"

        if not os.path.isdir(connector_dir):
            return None, (
                f"Error: no records directory found for connector '{connector_id}'. "
                "Verify the connector_id is correct and records have been indexed."
            )

        return connector_dir, None

    def _scope_connector_ids(self) -> frozenset[str] | None:
        """The agent's knowledge scope (apps ∪ KBs), or None when it has none.

        Resolved exactly as retrieval does, so these tools can never reach a
        connector the agent's semantic search could not.
        """
        from app.agents.actions.knowledge_graph.ops.scope import derive_scope

        scope = derive_scope(self.state)
        if scope.is_empty():
            return None
        return frozenset(scope.app_ids) | frozenset(scope.kb_ids)

    def _out_of_scope_error(self, connector_id: str) -> str | None:
        scope = self._scope_connector_ids()
        if scope is not None and connector_id not in scope:
            return (
                f"Error: connector '{connector_id}' is not part of this agent's knowledge. "
                f"Searchable connectors: {', '.join(sorted(scope))}"
            )
        return None

    async def _check_accessible_vrids(self, vrids: list[str]) -> dict[str, str] | None:
        """Resolve which of *vrids* the requesting user may access.

        Returns a {virtual_record_id: record_id} map, or None if the check
        cannot be performed (missing user_id / graph provider / lookup error) —
        callers MUST fail closed on None. Empty ``vrids`` returns an empty map.
        """
        if not vrids:
            return {}
        user_id = self.state.get("user_id", "")
        org_id = self.state.get("org_id", "")
        graph_provider = self.state.get("graph_provider")
        if not user_id or not org_id or graph_provider is None:
            logger.info(
                "[_check_accessible_vrids] BAIL: user_id=%r org_id=%r has_gp=%s",
                user_id, org_id, graph_provider is not None,
            )
            return None
        logger.info(
            "[_check_accessible_vrids] checking %d vrids for user=%s org=%s",
            len(vrids), user_id, org_id,
        )
        logger.debug("[_check_accessible_vrids] vrids=%s", vrids)
        try:
            # The same adjudicator semantic search uses, with no container trusted
            # and the agent's knowledge scope bounding which record may be cited.
            accessible = await graph_provider.filter_accessible_virtual_record_ids(
                list(dict.fromkeys(vrids)), user_id, org_id,
                scope_connector_ids=self._scope_connector_ids(),
            )
        except Exception as exc:
            logger.warning(
                "[storage_pattern_match] permission check failed: %s", exc,
                exc_info=True,
            )
            return None
        logger.info(
            "[_check_accessible_vrids] result: %d/%d accessible",
            len(accessible) if accessible else 0, len(vrids),
        )
        logger.debug("[_check_accessible_vrids] map=%s", accessible)
        return accessible or {}

    async def _can_read_whole_connector(self, connector_id: str) -> bool:
        """Whether the user may read every record under *connector_id*.

        ``run_command`` returns raw output that can carry record content with
        no file name attached (``grep -h``, ``| xargs cat``, ``| tr``), so no
        output-side filter can attribute it to a record. Raw access is
        therefore needs every record readable: true for APP_LEVEL connectors,
        where reaching the connector means reaching all of its records. Any
        other connector runs against ``_build_accessible_view``. Fails closed.
        """
        user_id = self.state.get("user_id", "")
        org_id = self.state.get("org_id", "")
        graph_provider = self.state.get("graph_provider")
        if not user_id or not org_id or graph_provider is None:
            return False
        containers = self._containers
        if containers is None:
            try:
                containers = await graph_provider.get_accessible_containers(
                    user_id=user_id, org_id=org_id,
                )
            except Exception:
                logger.warning(
                    "[storage_pattern_match] container access check failed", exc_info=True,
                )
                return False
            # One lookup per agent turn; the tool instance does not outlive it.
            self._containers = containers
        return (
            containers is not None
            and not containers.fallback_reason
            and connector_id in containers.app_ids_trusted
        )

    async def _build_accessible_view(self, connector_dir: str) -> tuple[str | None, str | None]:
        """Return (view_dir, error): a copy of *connector_dir* holding only readable records.

        The command then cannot read a record the user may not access, however
        its output is shaped. The caller must remove view_dir.
        """
        files = await asyncio.to_thread(_list_stored_files, connector_dir)
        if files is None:
            return None, (
                f"Error: this connector holds more than {_MAX_VIEW_FILES} record files, "
                "too many to check your access to each for run_command. Use "
                "storage_pattern_match.find_records instead; it returns only records "
                "you can access."
            )
        vrids = sorted({vrid for _, vrid in files})
        accessible: set[str] = set()
        for start in range(0, len(vrids), _VIEW_ACCESS_CHECK_BATCH):
            checked = await self._check_accessible_vrids(vrids[start:start + _VIEW_ACCESS_CHECK_BATCH])
            if checked is None:
                return None, (
                    "Error: cannot verify record access (permission service "
                    "unavailable). Refusing to run the command."
                )
            accessible.update(checked)

        view_dir = os.path.join(_views_root(connector_dir), uuid.uuid4().hex)
        try:
            await asyncio.to_thread(
                _populate_view, connector_dir, view_dir,
                [(rel, vrid) for rel, vrid in files if vrid in accessible],
            )
        except OSError:
            logger.warning("[storage_pattern_match] could not prepare the record view", exc_info=True)
            await asyncio.to_thread(shutil.rmtree, view_dir, True)
            return None, "Error: could not prepare the record view."
        return view_dir, None

    async def _filter_output_by_permission(
        self, command: str, output: str
    ) -> str | None:
        """Redact command output belonging to records the user cannot access.

        Defense in depth only: ``_can_read_whole_connector`` is the gate.

        Extracts record virtualRecordIds from both the command's file arguments
        and the output text, resolves accessibility via ``_check_accessible_vrids``,
        and:
          * returns None (→ caller denies) if the command directly targets a
            record the user may not access, or the permission check is unavailable
            while records are referenced (fail closed);
          * drops output lines that reference an inaccessible record;
          * returns output unchanged when no record is referenced (e.g. counts).

        Note: aggregate output that references no record path (e.g. `wc -l`) can
        still reflect inaccessible records in a count; content and file names are
        never surfaced. Callers needing per-record results should use find_records.
        """
        output_vrids = set(_VRID_IN_TEXT_RE.findall(output))
        command_vrids = set(_VRID_IN_TEXT_RE.findall(command))
        all_vrids = output_vrids | command_vrids
        if not all_vrids:
            return output

        accessible = await self._check_accessible_vrids(sorted(all_vrids))
        if accessible is None:
            return None
        accessible_vrids = set(accessible)

        # A record named directly in the command (cat/head/tail/file …) must be
        # accessible, else deny the whole result — output from such reads carries
        # no path prefix to redact line-by-line.
        if command_vrids - accessible_vrids:
            return None

        if not (output_vrids - accessible_vrids):
            return output

        kept: list[str] = []
        for line in output.splitlines():
            line_vrids = set(_VRID_IN_TEXT_RE.findall(line))
            if line_vrids - accessible_vrids:
                continue
            kept.append(line)
        result = "\n".join(kept).strip()
        return result if result else "No matches found."

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    @tool(
        path="/tools/storage_pattern_match/run_command",
        short_description="Run read-only search commands on local blob storage",
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING, description="The connector ID whose records to search. Scopes the command to that connector's record directory (records/<connector_id>/).", required=True),
            ToolParameter(name="command", type=ParameterType.STRING, description="A read-only Linux command to run inside the connector's record directory. Allowed binaries: grep, egrep, fgrep, rg, find, ls, wc, head, tail, cat, sort, uniq, xargs, file, echo. All paths must be relative (no leading /). Pipe (|) is supported; other shell operators (;, &&, ||, >, <, $()) are not.", required=True),
            ToolParameter(name="record_date", type=ParameterType.STRING, description="Optional ISO date (YYYY-MM-DD) to restrict the search to records whose stored file was written (indexed) within +/-1 day of that date; this is indexing time, not source modification time. Only supported when the first stage is grep/egrep/fgrep/rg searching '.'.", required=False),
        ],
        tags=[Tag(key="category", value="search"), Tag(key="type", value="read")],
        display_name="Searched local storage",
        description=(
            "Execute a read-only Linux command on the indexed JSON record files for a specific "
            "connector. Records are stored in a hierarchical folder structure mirroring the data "
            "source (e.g. records/<connector_id>/<space>/<folder>/<docId>/record.json). "
            "The command runs scoped to that connector's directory — all paths are relative.\n\n"
            "**TIP**: If your goal is to identify records and then fetch their full content, "
            "prefer `storage_pattern_match.find_records` which returns structured record_ids "
            "you can pass to `fetch_full_record`. Use `run_command` when you need custom "
            "commands, regex patterns, or raw output.\n\n"
            "Use this for exact-text search, regex matching, file discovery by date/name, or "
            "any pattern-based access that goes beyond semantic search.\n\n"
            "## SEARCH STRATEGY (CRITICAL — follow these rules exactly)\n\n"
            "### Rule 1: ALWAYS use -Z and xargs -0 for piped grep commands\n"
            "File paths contain spaces and special characters. Without null-termination, xargs\n"
            "splits paths on spaces and breaks everything.\n"
            "  - Use grep -rilZ (capital Z = null-terminated output)\n"
            "  - Use xargs -0 (zero = read null-terminated input)\n\n"
            "### Rule 2: Do NOT add a trailing '.' in piped stages\n"
            "When piping through xargs, grep reads files from stdin — do NOT append '.' at the end.\n"
            "  WRONG:   grep -rilZ 'term' . | xargs -0 grep -i 'word' .\n"
            "  CORRECT: grep -rilZ 'term' . | xargs -0 grep -i 'word'\n"
            "Only the FIRST grep in the chain should have '.' (the starting directory).\n\n"
            "### Rule 3: Multi-word queries — break into piped filters\n"
            "Do NOT search for the entire phrase as one string.\n"
            "Break into individual terms, pipe grep to narrow results progressively:\n\n"
            "   WRONG (single phrase match, rarely works):\n"
            "     grep -ri \"Asana Q3 revenue\" .\n\n"
            "   WRONG (spaces break xargs, missing -Z/-0):\n"
            "     grep -ril \"asana\" . | xargs grep -il \"Q3\"\n\n"
            "   CORRECT (null-safe, intermediate filters use -lZ, final stage shows content):\n"
            "     grep -rilZ \"asana\" . | xargs -0 grep -ilZ \"Q3\" | xargs -0 grep -i -C2 \"revenue\"\n\n"
            "### Rule 4: Final stage must show CONTENT, not file paths\n"
            "Use -l only in intermediate filter stages. Final stage omits -l to return matching lines.\n"
            "Add -C2 to show context around matches.\n\n"
            "### Rule 5: Single keyword — direct grep with context\n"
            "     grep -ri -C2 \"revenue\" .\n\n"
            "## QUOTING RULES (CRITICAL — commands fail without correct quoting)\n"
            "- Use DOUBLE QUOTES for all patterns: grep -ri \"keyword\" .\n"
            "- For regex OR, use double quotes: grep -riE \"term1|term2|term3\" .\n"
            "- Do NOT use backslash escapes like \\0 or \\n in arguments.\n"
            "- If you need null-terminated output for tr, just omit the tr stage —\n"
            "  find_records automatically handles path parsing.\n\n"
            "## PATTERN CHEAT SHEET\n"
            "- Multi-term (content):    grep -rilZ \"term1\" . | xargs -0 grep -ilZ \"term2\" | xargs -0 grep -i -C2 \"term3\"\n"
            "- Multi-term (files only): grep -rilZ \"term1\" . | xargs -0 grep -ilZ \"term2\" | xargs -0 grep -l \"term3\"\n"
            "- Single keyword:          grep -ri -C2 \"term\" .\n"
            "- Regex OR (any term):     grep -riE \"term1|term2|term3\" .\n"
            "- Find by date:            find . -name \"*.json\" -mtime -7\n"
            "- Find + grep:             find . -name \"*.json\" -print0 | xargs -0 grep -i \"keyword\"\n"
            "- Count matching files:    grep -rl \"pattern\" . | wc -l\n"
            "- ALWAYS use -i for case-insensitive matching on user queries\n\n"
            "## Examples\n"
            "  grep -rilZ \"asana\" . | xargs -0 grep -ilZ \"Q3\" | xargs -0 grep -i -C2 \"revenue\"\n"
            "  grep -ri -C2 \"deployment error\" .\n"
            "  find . -name \"*.json\" -mtime -7 -print0 | xargs -0 grep -i \"keyword\"\n"
            "  grep -rl \"quarterly report\" . | head -20\n\n"
            "## WHEN TO USE\n"
            "- User asks to search record content with exact text or regex patterns\n"
            "- User wants to find records by filename, modification date, or size\n"
            "- Semantic retrieval returned imprecise results and exact pattern match is needed\n"
            "- User asks 'which files mention X' or 'find all records containing Y'\n"
            "- User wants to list, count, or inspect the raw record files for a connector\n\n"
            "## WHEN NOT TO USE\n"
            "- Semantic or conceptual search -- use retrieval.search_internal_knowledge instead\n"
            "- Searching live data from connected apps -- use connector-specific tools (e.g. drive.search_files)\n"
            "- Storage type is S3 or Azure Blob (local storage only)\n"
            "- User just wants to read one specific known record -- use fetch_full_record\n\n"
            "## TYPICAL QUERIES\n"
            "- Find all records mentioning budget Q3: grep -rilZ \"budget\" . | xargs -0 grep -i -C2 \"Q3\"\n"
            "- Which files were updated last week: find . -name \"*.json\" -mtime -7\n"
            "- Asana Q3 revenue: grep -rilZ \"asana\" . | xargs -0 grep -ilZ \"Q3\" | xargs -0 grep -i -C2 \"revenue\"\n"
            "- Count matching files for API key: grep -rl \"API key\" . | wc -l\n"
        ),
    )
    async def run_command(
        self,
        connector_id: str,
        command: str,
        record_date: str | None = None,
    ) -> tuple[bool, str]:
        """Run a read-only Linux command scoped to the connector's record directory."""
        org_id = self.state.get("org_id", "")
        logger.info(
            "[storage_pattern_match] org_id=%s connector_id=%s command=%r record_date=%s",
            org_id, connector_id, command, record_date,
        )

        # 1. Validate the command (allowlist + security checks) and the scope.
        valid, err = _validate_command(command)
        if not valid:
            return False, err
        scope_err = self._out_of_scope_error(connector_id)
        if scope_err:
            return False, scope_err

        # 2. Inject date filter if requested.
        effective_command = command

        if record_date is not None:
            ok, result = _build_date_filtered_command(command, record_date)
            if not ok:
                return False, result
            effective_command = result

        # 3. Resolve and verify the connector's record directory.
        connector_dir, path_err = await self._resolve_connector_path(connector_id)
        if path_err:
            return False, path_err

        # 4. Execute: against the whole connector when every record is readable,
        # otherwise against a view of only the records the user may read.
        cwd = connector_dir
        view_dir: str | None = None
        if not await self._can_read_whole_connector(connector_id):
            view_dir, view_err = await self._build_accessible_view(connector_dir)
            if view_err:
                return False, view_err
            cwd = view_dir
        logger.debug(
            "[storage_pattern_match] cwd=%s effective_command=%r",
            cwd, effective_command,
        )
        try:
            success, output = await _run_subprocess(effective_command, cwd=cwd)
        finally:
            if view_dir:
                await asyncio.to_thread(shutil.rmtree, view_dir, True)

        # 5. Permission gate: never surface content or paths of records the
        # requesting user is not authorized to access. Mirrors the vrid gating
        # in retrieval._merge_pattern_match_blocks.
        if success:
            filtered = await self._filter_output_by_permission(effective_command, output)
            if filtered is None:
                return False, (
                    "Error: access denied — you do not have permission to read "
                    "one or more records targeted by this command."
                )
            output = filtered

        logger.info(
            "[storage_pattern_match] result: success=%s output_len=%d output=%r",
            success, len(output), output[:500],
        )
        return success, output

    # ------------------------------------------------------------------
    # find_records tool
    # ------------------------------------------------------------------

    @tool(
        path="/tools/storage_pattern_match/find_records",
        short_description="Find records matching a command and return structured metadata",
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING, description="The connector ID whose records to search. Scopes the command to that connector's record directory.", required=True),
            ToolParameter(name="command", type=ParameterType.STRING, description="A read-only Linux command that outputs file paths (one per line). Use grep with -l (list files) or find to discover files. The same allowed binaries and security rules as run_command apply.", required=True),
            ToolParameter(name="max_results", type=ParameterType.INTEGER, description="Maximum number of records to return (1-20). Default 10.", required=False, default=10),
        ],
        tags=[Tag(key="category", value="search"), Tag(key="type", value="read")],
        display_name="Found records in local storage",
        description=(
            "Run a read-only Linux command on the connector's record directory and "
            "automatically extract structured record metadata from any record file paths "
            "in the output. Returns record_id and record_name that you can pass directly "
            "to fetch_full_record to get the complete content.\n\n"
            "This is the RECOMMENDED tool when you need to identify records then fetch them. "
            "Workflow:\n"
            "  1. Call find_records with a command that lists matching files\n"
            "  2. Tool parses file paths and returns [{record_id, record_name, virtual_record_id}]\n"
            "  3. Call fetch_full_record(record_ids=[...]) with the record_id values\n"
            "  4. Answer the user's question from the full content\n\n"
            "The command should output FILE PATHS (use grep -l, find, ls, etc.). "
            "ALWAYS use double quotes for all patterns and arguments. "
            "All rules from run_command apply (allowed binaries, -Z/xargs -0 for pipes, "
            "no trailing dot in piped stages).\n\n"
            "## Command examples for find_records\n"
            "  grep -rilZ \"asana\" . | xargs -0 grep -ilZ \"Q3\"\n"
            "  find . -name \"*.json\" -mtime -7\n"
            "  grep -rl \"revenue\" .\n\n"
            "NOTE: Unlike run_command, find_records does NOT return raw command output. "
            "It parses file paths and returns structured JSON with record metadata.\n\n"
            "## WHEN TO USE\n"
            "- User asks a question that requires fetching full records\n"
            "- You need record_ids to pass to fetch_full_record\n"
            "- You want to identify which records are relevant before reading them in full\n"
            "- You need record names and IDs from file search results\n\n"
            "## WHEN NOT TO USE\n"
            "- You already have record_ids from retrieval context -- use fetch_full_record directly\n"
            "- You need raw command output (grep content, counts, etc.) -- use run_command instead\n"
            "- Semantic or conceptual search -- use retrieval.search_internal_knowledge\n\n"
            "## TYPICAL QUERIES\n"
            "- Find Asana Q3 revenue records: grep -rilZ \"asana\" . | xargs -0 grep -il \"Q3\"\n"
            "- Records modified last week: find . -name \"*.json\" -mtime -7\n"
            "- Files mentioning deployment: grep -rl \"deployment\" .\n"
        ),
    )
    async def find_records(
        self,
        connector_id: str,
        command: str,
        max_results: int = 10,
        max_stdout_bytes: int = 0,
        max_output_chars: int = _MAX_OUTPUT_CHARS,
    ) -> tuple[bool, str]:
        """Run a command and parse record file paths from output into structured metadata."""
        org_id = self.state.get("org_id", "")
        logger.info(
            "[storage_pattern_match.find_records] org_id=%s connector_id=%s command=%r max_results=%d",
            org_id, connector_id, command, max_results,
        )

        max_results = min(max(max_results, 1), _MAX_FIND_RECORDS)

        # Validate the command (same rules as run_command) and the scope.
        valid, err = _validate_command(command)
        if not valid:
            return False, err
        scope_err = self._out_of_scope_error(connector_id)
        if scope_err:
            return False, scope_err

        # Resolve connector directory
        connector_dir, path_err = await self._resolve_connector_path(connector_id)
        if path_err:
            logger.info("[find_records] path error for %s: %s", connector_id, path_err)
            return False, path_err
        logger.info("[find_records] connector_dir=%s", connector_dir)

        # Execute the command
        success, output = await _run_subprocess(
            command, cwd=connector_dir, max_stdout_bytes=max_stdout_bytes,
            max_output_chars=max_output_chars,
        )
        logger.info(
            "[find_records] subprocess result: success=%s output_len=%d output_preview=%r",
            success, len(output), output[:500],
        )

        # The command ran over every record in the connector, readable or not,
        # so its raw stdout/stderr are never returned: only records that pass
        # the permission check below, and one identical answer for "nothing".
        if not success:
            if "No matches found" in output:
                return True, _NO_ACCESSIBLE_MATCH
            return False, _public_failure(output)

        if output.strip() == "No matches found.":
            return True, _NO_ACCESSIBLE_MATCH

        # Extract candidate record file paths (cheap regex, no file opens).
        # Handles both grep -l output (plain paths) and grep -c output
        # (path:count) so results can be ranked by keyword density.
        lines = [
            line.strip() for line in output.splitlines()
            if line.strip() and not line.startswith("[...output truncated")
        ]
        line_vrids: list[tuple[str, str, int]] = []  # (path, vrid, match_count)
        for line in lines:
            path = line
            match_count = 1  # default for grep -l (no count info)

            csm = _GREP_COUNT_RE.match(line)
            if csm:
                candidate_path = csm.group(1)
                count = int(csm.group(2))
                if count == 0:
                    continue
                if _RECORD_FILENAME_RE.match(os.path.basename(candidate_path)):
                    path = candidate_path
                    match_count = count

            fm = _RECORD_FILENAME_RE.match(os.path.basename(path))
            if fm:
                line_vrids.append((path, fm.group(1), match_count))

        # Sort by match count descending so the most relevant files win
        # the max_results cap instead of arbitrary filesystem-order files.
        line_vrids.sort(key=lambda x: x[2], reverse=True)

        logger.info(
            "[find_records] lines=%d line_vrids=%d", len(lines), len(line_vrids),
        )

        if not line_vrids:
            return True, _NO_ACCESSIBLE_MATCH

        # Permission gate: keep only records the requesting user may access.
        # Fail closed if the check cannot be performed.
        accessible = await self._check_accessible_vrids([v for _, v, _c in line_vrids])
        if accessible is None:
            return False, (
                "Error: cannot verify record access (permission service "
                "unavailable). Refusing to return records."
            )

        candidates: list[tuple[str, str, int]] = []
        seen: set[str] = set()
        for path, vrid, match_count in line_vrids:
            if vrid in accessible and vrid not in seen:
                seen.add(vrid)
                candidates.append((path, vrid, match_count))
        paths_found = len(candidates)
        try:
            ranked = await asyncio.to_thread(
                _rank_candidates, candidates, connector_dir, _grep_search_regexes(command),
            )
        except Exception:
            # Ranking only improves order; never lose the results over it.
            logger.warning("[find_records] ranking failed, using grep order", exc_info=True)
            ranked = [(path, vrid, 0, count, "") for path, vrid, count in candidates]

        graph_provider = self.state.get("graph_provider")
        try:
            graph_records = await graph_provider.get_records_by_record_ids(
                record_ids=list(dict.fromkeys(accessible[vrid] for _p, vrid, *_rest in ranked if accessible[vrid])),
                org_id=org_id,
            )
        except Exception:
            logger.warning("[find_records] record lookup failed", exc_info=True)
            return False, (
                "Error: cannot verify record access (permission service "
                "unavailable). Refusing to return records."
            )
        by_id = {
            r.get("_key") or r.get("id"): r for r in graph_records or [] if isinstance(r, dict)
        }

        records: list[dict[str, str]] = []
        for _path, vrid, matched_terms, match_count, snippet in ranked:
            if len(records) >= max_results:
                break
            record_id = accessible[vrid]
            graph_rec = by_id.get(record_id) if record_id else None
            if not graph_rec:
                continue
            # Named from the record the permission check chose, never from the
            # storage path, whose folders can belong to other records or groups.
            meta: dict[str, str] = {
                "record_id": record_id,
                "record_name": graph_rec.get("recordName") or "",
                "virtual_record_id": vrid,
            }
            if matched_terms:
                meta["matched_terms"] = str(matched_terms)
            if match_count > 1:
                meta["match_count"] = str(match_count)
            if snippet:
                meta["match_preview"] = snippet
            records.append(meta)

        if not records:
            return True, _NO_ACCESSIBLE_MATCH

        hint_parts = [
            "IMPORTANT: Do NOT blindly fetch all records. Review each record's "
            "record and match_preview to determine relevance to the query. "
            "Only pass record IDs for records that are clearly relevant. "
            f"fetch_full_record accepts at most {_MAX_FETCH_RECORD_IDS} IDs per call.",
        ]

        result = {
            "records": records,
            "total_found": paths_found,
            "returned": len(records),
            "hint": " ".join(hint_parts),
            "success": True,
        }

        logger.info(
            "[storage_pattern_match.find_records] paths_found=%d returned=%d",
            paths_found, len(records),
        )
        return True, json_mod.dumps(result, indent=2)

