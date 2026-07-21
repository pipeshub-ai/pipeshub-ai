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
import json as json_mod
import logging
import os
import platform
import re
import shlex
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
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
_MAX_FETCH_RECORD_IDS = 5
_MATCH_PREVIEW_LINES = 3

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
    # find -exec/-execdir/-ok/-okdir run an arbitrary command; -fprintf/-fprint
    # write arbitrary content to an arbitrary file path.
    "find": frozenset({"-exec", "-execdir", "-ok", "-okdir", "-fprintf", "-fprint"}),
    # ripgrep --pre runs an arbitrary preprocessor command per matched file.
    "rg": frozenset({"--pre", "--pre-glob"}),
}

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
        if flag in disallowed:
            return (
                f"Error: '{flag}' is not allowed with '{binary}' "
                "(it can execute arbitrary commands or write arbitrary files)."
            )
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

        sub_flag_err = _check_dangerous_flags(sub_binary, parts[sub_index + 1:])
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
            "modified within +/-1 day of that date. "
            "When set, a find-based time filter is prepended to the command. "
            "Example: '2026-06-01'"
        )
    )


_MAX_FIND_RECORDS = 20
_RECORD_FILENAME_RE = re.compile(r"record_([0-9a-f\-]+)\.json$", re.IGNORECASE)
# Matches a record_<virtualRecordId>.json reference anywhere in a text line
# (e.g. grep -r output "grp/doc/sid/record_<vrid>.json:match"), used to gate
# raw command output on per-record access.
_VRID_IN_TEXT_RE = re.compile(r"record_([0-9a-f\-]+)\.json", re.IGNORECASE)


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

        flag_err = _check_dangerous_flags(binary, parts[1:])
        if flag_err:
            return False, flag_err

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

        for idx in range(1, len(parts)):
            if idx in pattern_indices:
                continue
            arg = parts[idx]
            if ".." in arg:
                return False, (
                    f"Error: path traversal ('..') is not allowed in argument '{arg}'. "
                    "All paths must be relative to the connector's record directory."
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
    first_stage = stages[0]

    date_filter = (
        f"find . -newermt '{after}' -not -newermt '{before}' "
        f"-name '*.json' -print0 | xargs -0 {first_stage}"
    )
    if len(stages) > 1:
        rest = " | ".join(stages[1:])
        date_filter = f"{date_filter} | {rest}"
    return True, date_filter


async def _run_subprocess(
    command: str,
    *,
    cwd: str,
    timeout: int = _EXEC_TIMEOUT_SECS,
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

    try:
        for i, tokens in enumerate(stage_tokens):
            proc = await asyncio.create_subprocess_exec(
                *tokens,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            procs.append(proc)
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data if i > 0 else None),
                timeout=remaining,
            )
            stdin_data = stdout_bytes
            last_proc = proc
            last_stderr = stderr_bytes

        stdout = (stdin_data or b"").decode("utf-8", errors="replace")
        stderr = last_stderr.decode("utf-8", errors="replace")
        exit_code = last_proc.returncode if last_proc else 0

        if exit_code == 0:
            output = stdout if stdout.strip() else "No matches found."
            return True, _truncate(output, _MAX_OUTPUT_CHARS)

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
                    proc.kill()
                    await proc.communicate()
                except Exception:
                    pass


async def _extract_record_metadata(
    file_path: str,
    cwd: str,
    graph_provider: object | None = None,
) -> dict[str, str] | None:
    """Parse record metadata from a matched file path.

    File path structure relative to connector dir:
        <group>/<path_segments...>/<record_name>/<storage_doc_id>/record_<virtual_record_id>.json

    Resolves record_id via graph_provider (virtualRecordId → record _key).
    Returns dict with record_id, record_name, virtual_record_id, and relative_path,
    or None if the path doesn't match the expected structure.
    """
    filename = os.path.basename(file_path)
    m = _RECORD_FILENAME_RE.match(filename)
    if not m:
        return None

    virtual_record_id = m.group(1)

    # Path segments: everything between the connector dir and filename
    # e.g. "group/My Doc.pdf/abc123/record_xyz.json"
    # parent = "group/My Doc.pdf/abc123"
    parent = os.path.dirname(file_path)
    parts = parent.replace("\\", "/").split("/")
    # storage_doc_id is the last segment of the parent path
    storage_doc_id = parts[-1] if parts else ""
    # record_name is the second-to-last segment
    record_name = parts[-2] if len(parts) >= 2 else ""

    # Resolve the actual record_id (ArangoDB _key) from graph DB using virtual_record_id.
    record_id = ""
    if graph_provider and hasattr(graph_provider, "get_records_by_virtual_record_id"):
        try:
            record_keys = await graph_provider.get_records_by_virtual_record_id(
                virtual_record_id
            )
            if record_keys:
                record_id = record_keys[0]
        except Exception as exc:
            logger.debug(
                "[_extract_record_metadata] graph lookup failed for vrid=%s: %s",
                virtual_record_id, exc,
            )

    # Fallback: try reading id from the JSON file header
    if not record_id:
        full_path = os.path.join(cwd, file_path)
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                head = f.read(8192)
            # Try parsing the head; for large files it may be truncated JSON
            try:
                data = json_mod.loads(head)
            except (json_mod.JSONDecodeError, ValueError):
                # Truncated — use regex to find "id" near the top
                id_match = re.search(
                    r'"(?:id|_key|record_id)"\s*:\s*"([0-9a-f]{24})"', head
                )
                data = None
                if id_match:
                    record_id = id_match.group(1)
            if data and not record_id:
                record_id = (
                    data.get("id") or data.get("_key")
                    or data.get("record_id") or ""
                )
        except Exception:
            pass

    return {
        "record_id": record_id,
        "record_name": record_name,
        "virtual_record_id": virtual_record_id,
        "storage_doc_id": storage_doc_id,
        "relative_path": file_path,
    }


async def _get_match_preview(
    file_path: str,
    cwd: str,
    search_terms: list[str],
    max_lines: int = _MATCH_PREVIEW_LINES,
) -> str:
    """Extract a short preview of why this file matched the grep query.

    Runs a quick grep for each search term against the file and returns
    up to *max_lines* distinct matching lines (trimmed).  Returns ""
    if nothing useful can be extracted.
    """
    if not search_terms:
        return ""

    full_path = os.path.realpath(os.path.join(cwd, file_path))
    real_cwd = os.path.realpath(cwd)
    if not full_path.startswith(real_cwd + os.sep) or not os.path.isfile(full_path):
        return ""

    pattern = "|".join(re.escape(t) for t in search_terms)
    try:
        proc = await asyncio.create_subprocess_exec(
            "grep", "-i", "-m", str(max_lines), "-E", "--", pattern, full_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
        previews = []
        for line in lines[:max_lines]:
            trimmed = line.strip()[:200]
            if trimmed:
                previews.append(trimmed)
        return " | ".join(previews)
    except Exception:
        return ""


def _extract_search_terms(command: str) -> list[str]:
    """Pull quoted search terms from a grep command for match-preview extraction."""
    return re.findall(r'"([^"]+)"', command)


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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _resolve_connector_path(self, connector_id: str) -> tuple[str | None, str | None]:
        """Return (connector_dir, error_message).

        Reads mountName + storageType from etcd via config_service, builds the
        OS-correct scoped path, and verifies it exists on disk.
        Only local storage is supported; cloud providers return an informative error.
        """
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

        if not os.path.isdir(connector_dir):
            return None, (
                f"Error: no records directory found for connector '{connector_id}'. "
                f"Expected path: {connector_dir}. "
                "Verify the connector_id is correct and records have been indexed."
            )

        return connector_dir, None

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
        if not user_id or not graph_provider or not hasattr(
            graph_provider, "check_vrids_accessible"
        ):
            return None
        try:
            accessible = await graph_provider.check_vrids_accessible(
                user_id=user_id,
                org_id=org_id,
                virtual_record_ids=list(dict.fromkeys(vrids)),
            )
        except Exception as exc:
            logger.warning(
                "[storage_pattern_match] permission check failed: %s", exc
            )
            return None
        return accessible or {}

    async def _filter_output_by_permission(
        self, command: str, output: str
    ) -> str | None:
        """Redact command output belonging to records the user cannot access.

        Extracts record virtualRecordIds from both the command's file arguments
        and the output text, resolves accessibility via ``check_vrids_accessible``,
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
        app_name="storage_pattern_match",
        tool_name="run_command",
        description="Run a read-only Linux command (grep, find, rg…) on connector record storage",
        args_schema=RunCommandInput,
        llm_description=(
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
            "  grep -rl \"quarterly report\" . | head -20\n"
        ),
        category=ToolCategory.UTILITY,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User asks to search record content with exact text or regex patterns",
            "User wants to find records by filename, modification date, or size",
            "Semantic retrieval returned imprecise results and exact pattern match is needed",
            "User asks 'which files mention X' or 'find all records containing Y'",
            "User wants to list, count, or inspect the raw record files for a connector",
        ],
        when_not_to_use=[
            "Semantic or conceptual search -- use retrieval.search_internal_knowledge instead",
            "Searching live data from connected apps -- use connector-specific tools (e.g. drive.search_files)",
            "Storage type is S3 or Azure Blob (local storage only)",
            "User just wants to read one specific known record -- use fetch_full_record",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find all records mentioning budget Q3: grep -rilZ \"budget\" . | xargs -0 grep -i -C2 \"Q3\"",
            "Which files were updated last week: find . -name \"*.json\" -mtime -7",
            "Asana Q3 revenue: grep -rilZ \"asana\" . | xargs -0 grep -ilZ \"Q3\" | xargs -0 grep -i -C2 \"revenue\"",
            "Count matching files for API key: grep -rl \"API key\" . | wc -l",
        ],
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

        # 1. Validate the command (allowlist + security checks).
        valid, err = _validate_command(command)
        if not valid:
            return False, err

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

        # 4. Execute.
        logger.debug(
            "[storage_pattern_match] cwd=%s effective_command=%r",
            connector_dir, effective_command,
        )
        success, output = await _run_subprocess(
            effective_command,
            cwd=connector_dir,
        )

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
        app_name="storage_pattern_match",
        tool_name="find_records",
        description="Run a command that lists files, then return structured record metadata (record_id, record_name)",
        args_schema=FindRecordsInput,
        llm_description=(
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
            "It parses file paths and returns structured JSON with record metadata."
        ),
        category=ToolCategory.UTILITY,
        is_essential=False,
        requires_auth=False,
        when_to_use=[
            "User asks a question that requires fetching full records",
            "You need record_ids to pass to fetch_full_record",
            "You want to identify which records are relevant before reading them in full",
            "You need record names and IDs from file search results",
        ],
        when_not_to_use=[
            "You already have record_ids from retrieval context — use fetch_full_record directly",
            "You need raw command output (grep content, counts, etc.) — use run_command instead",
            "Semantic or conceptual search — use retrieval.search_internal_knowledge",
        ],
        primary_intent=ToolIntent.SEARCH,
        typical_queries=[
            "Find Asana Q3 revenue records: grep -rilZ \"asana\" . | xargs -0 grep -il \"Q3\"",
            "Records modified last week: find . -name \"*.json\" -mtime -7",
            "Files mentioning deployment: grep -rl \"deployment\" .",
        ],
    )
    async def find_records(
        self,
        connector_id: str,
        command: str,
        max_results: int = 10,
    ) -> tuple[bool, str]:
        """Run a command and parse record file paths from output into structured metadata."""
        org_id = self.state.get("org_id", "")
        logger.info(
            "[storage_pattern_match.find_records] org_id=%s connector_id=%s command=%r max_results=%d",
            org_id, connector_id, command, max_results,
        )

        max_results = min(max(max_results, 1), _MAX_FIND_RECORDS)

        # Validate the command (same rules as run_command)
        valid, err = _validate_command(command)
        if not valid:
            return False, err

        # Resolve connector directory
        connector_dir, path_err = await self._resolve_connector_path(connector_id)
        if path_err:
            return False, path_err

        # Execute the command
        success, output = await _run_subprocess(command, cwd=connector_dir)

        if not success:
            if "No matches found" in output:
                return True, json_mod.dumps({
                    "records": [],
                    "total_found": 0,
                    "message": "No records match the command criteria.",
                })
            return False, output

        if output.strip() == "No matches found.":
            return True, json_mod.dumps({
                "records": [],
                "total_found": 0,
                "message": "No records match the command criteria.",
            })

        # Extract candidate record file paths (cheap regex, no file opens).
        lines = [
            line.strip() for line in output.splitlines()
            if line.strip() and not line.startswith("[...output truncated")
        ]
        line_vrids: list[tuple[str, str]] = []
        for line in lines:
            fm = _RECORD_FILENAME_RE.match(os.path.basename(line))
            if fm:
                line_vrids.append((line, fm.group(1)))

        if not line_vrids:
            return True, json_mod.dumps({
                "records": [],
                "total_found": 0,
                "raw_output_lines": len(lines),
                "message": (
                    "Command ran successfully but no record file paths (record_*.json) "
                    "were found in the output. Ensure your command uses -l flag (list files) "
                    "or outputs file paths. Use run_command if you need raw output instead."
                ),
            })

        # Permission gate: keep only records the requesting user may access.
        # Fail closed if the check cannot be performed.
        accessible = await self._check_accessible_vrids([v for _, v in line_vrids])
        if accessible is None:
            return False, (
                "Error: cannot verify record access (permission service "
                "unavailable). Refusing to return records."
            )

        records: list[dict[str, str]] = []
        paths_found = 0
        seen: set[str] = set()
        for line, vrid in line_vrids:
            if vrid not in accessible or vrid in seen:
                continue
            seen.add(vrid)
            paths_found += 1
            if len(records) >= max_results:
                continue
            meta = await _extract_record_metadata(line, connector_dir, graph_provider=None)
            if meta:
                # Authoritative record_id from the permission check.
                meta["record_id"] = accessible[vrid] or meta.get("record_id", "")
                records.append(meta)

        if not records:
            return True, json_mod.dumps({
                "records": [],
                "total_found": 0,
                "raw_output_lines": len(lines),
                "message": (
                    "Matching record files were found but none are accessible to you, "
                    "or no record metadata could be resolved."
                ),
            })

        search_terms = _extract_search_terms(command)
        if search_terms:
            for rec in records:
                preview = await _get_match_preview(
                    rec["relative_path"], connector_dir, search_terms,
                )
                if preview:
                    rec["match_preview"] = preview

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

