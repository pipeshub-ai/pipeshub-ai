#!/usr/bin/env python3
"""ph: PipesHub's knowledge and tools from an engine sandbox's shell (contract 1: OWD-6, recs audit R3).

    ph search <words...>                       search company knowledge
    ph fetch <record id...>                    read whole records a search showed
    ph tools list                              every tool of this run
    ph tools search <words...> [--limit N]     find a tool of this run by what it does
    ph tools describe <tool>                   a tool's description and input schema
    ph tools call <tool> --json '<arguments>' [--save | --print]
                                               call a tool (--json - reads them from stdin); a large result is
                                               saved under notes/tool-results/ and its path printed
    ph ask <question...> [--option O ...]      ask the person a question; end your turn after it
    ph publish <file> [--as NAME]              publish a file as a deliverable of this task, now
    ph skill list                              the skills this run has
    ph skill show <name>                       a skill's instructions (its SKILL.md) and its files
    ph policy check <tool> [--path P ...]      whether the run's policy lets the engine use its own tool
    ph policy check --hook                     the same for a PreToolUse hook's input on stdin (exit 2: refused)
    ph git clone <https url> [DIR] [--ref R] [--full]
                                               clone a public repository, after the person allows it (read-only)
    ph git fetch [DIR]                         fetch the latest of a repository ph git clone made (asks again)
    ph git push                                push the run's repository to its session branch (asks the person)
    ph git adopt DIR                           move an in-workspace checkout onto the references mount (PipesHub runs it)
    ph --json <command> ...                    one JSON object on stdout: {ok, command, data | error}
    ph --version

``ph describe`` and ``ph call`` are the older names of ``ph tools describe`` and ``ph tools call`` and still work.
Before contract 1, ``ph search`` found tools; it is now the knowledge search (``ph tools search`` finds tools).

A ``ph tools call`` result past ``SAVE_ABOVE_CHARS`` is saved by ``ph`` itself under the workspace's
``notes/tool-results/`` (``TOOL_RESULTS_DIR``), and ``ph`` prints its path and an excerpt (WP98-FU-4): that folder is
the run's scratch, so the save never counts a person's own ``notes/*.json`` as a tool dump. ``--save`` saves any
result; ``--print``, or stdout redirected to a file, prints it whole as before.

``ph ask`` is the run's ``ask_user`` tool: the question is shown as the chat's question card and the answer arrives as
the engine's next message. ``ph publish`` copies the file into the outputs folder (``/pipeshub/outputs``, the
workspace's ``out/``) and asks the gateway to publish it now (``pipeshub.artifact_publish``): it appears in the chat as a
draft, as any file written there does, and becomes final when the turn ends.
``ph skill`` reads the run's read-only skills folder, for an engine without a skills tool of its own (SK-05).
``ph git clone`` and ``ph git fetch`` call the run's ``repository_fetch`` (WP-63 part A): PipesHub fetches the repository
outside the sandbox once the person allows it, and hands it over read-only under the session folder; ``ph`` then clones
or fetches from that copy with hooks, submodules and LFS off. Git hosts are never reachable from the sandbox itself,
and nothing is ever pushed this way.
``ph policy check`` asks the tool gateway, never decides itself; with ``--hook`` it is the managed policy hook the
controller configures in engines that run hooks (G-25), and every failure to get an answer refuses the call.

``ph`` speaks MCP (streamable HTTP, JSON-RPC) to the same tool gateway, with the same per-attempt placeholder,
as the engine's own MCP client. The egress proxy swaps the placeholder for the run's short-lived gateway token, so
no token is in this file, the image or the sandbox. A ``ph tools call`` is the MCP ``tools/call`` the engine would
send: the gateway applies the same grant, argument checks, approvals, flow guard and journal to it. The gateway and
the placeholder come from the session folder the harness writes at each turn (``/pipeshub/session/gateway.json``),
or from ``PIPESHUB_TOOLS_URL`` and ``HARNESS_TOOLS_TOKEN``.

Standard library only: the engine images run it on their own CPython 3.12.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any, NoReturn, cast

# Called with the seconds a call has been held or running.
Progress = Callable[[float], None]

PH_VERSION = "2.3.0"
# The knowledge tools ``ph search`` and ``ph fetch`` call (the knowledgegraph toolset's).
KNOWLEDGE_SEARCH = "knowledgegraph__search"
KNOWLEDGE_FETCH = "knowledgegraph__fetch_record"
FOLDER = os.environ.get("PIPESHUB_FOLDER", "/pipeshub")
URL_ENV = "PIPESHUB_TOOLS_URL"
TOKEN_ENV = "HARNESS_TOOLS_TOKEN"
SEARCH_TOOLS = "search_tools"
CALL_TOOL = "call_tool"
PROTOCOL_VERSION = "2025-06-18"
# The MCP `_meta` key asking the gateway for a tool's whole result (harness_core.gateway.result_cap.FULL_RESULT_META).
FULL_RESULT_META = "pipeshub/fullResult"
TIMEOUT_S = float(os.environ.get("PH_TIMEOUT_S", "900"))
# The gateway's policy check (harness_core.engines.native_policy.POLICY_CHECK_TOOL). It answers at once, and a hook
# that waits past the engine's own hook timeout lets the call run, so ph gives up long before (HOOK_TIMEOUT_S, 30 s).
POLICY_CHECK = "pipeshub.policy_check"
# The gateway's ask_user, under either name it lists it by (harness_core.gateway.asking.ASK_USER_NAMES).
ASK_USER_NAMES = ("ask_user", "pipeshub_ask_user")
# The engine endpoint's publish (harness_core.engines.publish_tool.ARTIFACT_PUBLISH_TOOL): not listed, named by ph.
ARTIFACT_PUBLISH = "pipeshub.artifact_publish"
OUTPUTS_DIR = "outputs"
# The image's link to the workspace (install.sh: /pipeshub/workspace -> /workspace).
WORKSPACE_DIR = "workspace"
# WP98-FU-4: where ph saves a large result, the one folder the sync keeps as scratch
# (harness_core.workspace.sync.storage_classes.TOOL_RESULTS_DIR, pinned by test_wp98_fu4_tool_results).
TOOL_RESULTS_DIR = "notes/tool-results"
# A result longer than the engine's inline cap (harness_core.gateway.result_cap.ENGINE_RESULT_CAP_CHARS) is saved.
SAVE_ABOVE_CHARS = 24_000
EXCERPT_CHARS = 2_000
POLICY_TIMEOUT_S = float(os.environ.get("PH_POLICY_TIMEOUT_S", "10"))
# The most of a hook's input read: a Write's content rides it, but the check needs only the path.
MAX_HOOK_INPUT = 8 * 1024 * 1024
SKILLS_ENV = "HARNESS_SKILLS_DIR"
SKILL_FILE = "SKILL.md"
MAX_SKILL_BYTES = 256 * 1024
# A PreToolUse hook's tool names (Claude Code's; Codex 0.160 reports its shell as Bash too) -> the native tool class
# and the input fields that name the files an edit writes or Codex's image viewer reads.
HOOK_TOOLS: dict[str, tuple[str, tuple[str, ...]]] = {
    "Bash": ("shell", ()),
    "Write": ("edit", ("file_path",)),
    "Edit": ("edit", ("file_path",)),
    "MultiEdit": ("edit", ("file_path",)),
    "NotebookEdit": ("edit", ("notebook_path",)),
    "WebFetch": ("web", ()),
    "WebSearch": ("web", ()),
    "Read": ("read", ()),
    "Grep": ("search", ()),
    "Glob": ("search", ()),
    "view_image": ("read", ("path",)),
}

# WP-63 part A: the tool ``ph git`` calls, and the git config every checkout of a handed-over copy runs with (the copy
# is a local folder, so only the file transport is opened; hooks, submodules and LFS never run).
REPOSITORY_FETCH = "repository_fetch"
# The controller's own tool placing a large input on its first read (harness_core.engines.input_staging, C5.01).
INPUT_FETCH = "pipeshub.input_fetch"
REPOSITORY_PUSH = "repository_push"
GIT_CHECKOUT_CONFIG = (
    "protocol.allow=never",
    "protocol.file.allow=always",
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "submodule.recurse=false",
    "credential.helper=",
    "filter.lfs.process=",
    "filter.lfs.smudge=",
    "filter.lfs.required=false",
)
GIT_TIMEOUT_S = 600
# WP98-FU-1 (ADR-RUN-02): where ``ph git clone`` checks a repository out, a mount of its own outside the saved
# workspace (harness_core.workspace.sync.storage_classes.REFERENCES_DIR, pinned by test_wp98_fu1_references_mount);
# the folder the engine asked for becomes a link to it. Without the mount (an older controller) it checks out in place.
REFERENCES_DIR = "/pipeshub/references"

# Exit codes (sysexits): what a script can branch on.
EX_OK = 0
EX_TOOL_ERROR = 1
EX_USAGE = 64
EX_DATAERR = 65
EX_UNAVAILABLE = 69
EX_SOFTWARE = 70
EX_TEMPFAIL = 75
EX_NOPERM = 77
EX_CONFIG = 78
# ``ph policy check``: the run's policy refuses the call. It is the status a PreToolUse hook blocks with.
EX_REFUSED = 2


class PhError(Exception):
    """A failure told in plain words, with the exit code it ends ``ph`` with."""

    def __init__(self, message: str, code: int, *, reason: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        # The gateway's refusal code (its x-gateway-refusal header) when it refused the credentials.
        self.reason = reason


def _read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def gateway(folder: str | None = None, env: dict[str, str] | None = None) -> tuple[str, str]:
    """The tool gateway's URL and the placeholder to send, from the environment first, then the session folder."""
    folder = FOLDER if folder is None else folder
    env = dict(os.environ) if env is None else env
    session: dict[str, Any] = {}
    raw = _read(os.path.join(folder, "session", "gateway.json"))
    if raw is not None:
        try:
            loaded = json.loads(raw)
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            session = loaded
    url = env.get(URL_ENV) or session.get("url")
    token = env.get(TOKEN_ENV) or session.get("token")
    if not isinstance(url, str) or not url:
        raise PhError(
            "ph: this sandbox has no PipesHub tool gateway: "
            f"{os.path.join(folder, 'session', 'gateway.json')} is missing and {URL_ENV} is not set. "
            "ph works inside a PipesHub engine run.",
            EX_CONFIG,
        )
    if not isinstance(token, str) or not token:
        raise PhError(
            f"ph: no gateway placeholder for this run ({TOKEN_ENV} is not set and the session folder has none).",
            EX_CONFIG,
        )
    return url, token


class Gateway:
    """One MCP client over streamable HTTP, stateless: every request stands alone, as the gateway serves it."""

    def __init__(self, url: str, token: str, *, opener: Any = None, timeout_s: float = TIMEOUT_S) -> None:  # noqa: ANN401
        self.url = url
        self.token = token
        self._open = opener or urllib.request.urlopen
        self._timeout_s = timeout_s
        self._next = 0

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        on_progress: Progress | None = None,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        """One JSON-RPC request; with ``on_progress`` it asks for progress (``_meta.progressToken``), as an MCP
        client with a progress callback does, and is told the seconds a held or running call has taken.
        ``timeout_s`` bounds this request below the client's own bound."""
        timeout = self._timeout_s if timeout_s is None else min(timeout_s, self._timeout_s)
        self._next += 1
        params = dict(params or {})
        if on_progress is not None:
            params["_meta"] = {**params.get("_meta", {}), "progressToken": self._next}
        body = {"jsonrpc": "2.0", "id": self._next, "method": method, "params": params}
        http = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Authorization": f"Bearer {self.token}",
                "MCP-Protocol-Version": PROTOCOL_VERSION,
                "User-Agent": f"pipeshub-ph/{PH_VERSION}",
            },
        )
        try:
            with self._open(http, timeout=timeout) as response:
                kind = response.headers.get("Content-Type", "")
                if "text/event-stream" in kind:
                    message = _streamed(response, self._next, on_progress)
                else:
                    message = _answer(response.read().decode("utf-8", "replace"), self._next)
        except urllib.error.HTTPError as refused:
            raise _http_error(refused) from None
        except urllib.error.URLError as down:
            raise PhError(
                f"ph: could not reach the PipesHub tool gateway at {self.url}: {down.reason}. "
                "Try again; if it keeps failing, the run may have ended.",
                EX_UNAVAILABLE,
            ) from None
        except TimeoutError:
            raise PhError(
                f"ph: the PipesHub tool gateway did not answer within {int(timeout)} s.", EX_UNAVAILABLE
            ) from None
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise PhError(
                f"ph: the tool gateway refused the request: {error.get('message') or 'no reason given'}.",
                EX_SOFTWARE,
            )
        result = message.get("result")
        if not isinstance(result, dict):
            raise PhError("ph: the tool gateway's answer had no result.", EX_SOFTWARE)
        return result

    def tools(self) -> list[dict[str, Any]]:
        listed = self.request("tools/list").get("tools")
        return [t for t in listed if isinstance(t, dict) and isinstance(t.get("name"), str)] if isinstance(listed, list) else []

    def call(
        self, name: str, arguments: dict[str, Any], *, on_progress: Progress | None = None, full: bool = False
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"name": name, "arguments": arguments}
        if full:
            params["_meta"] = {FULL_RESULT_META: True}
        return self.request("tools/call", params, on_progress=on_progress)


def _http_error(refused: urllib.error.HTTPError) -> PhError:
    reason = refused.headers.get("x-gateway-refusal") if refused.headers else None
    if refused.code == 401:
        return PhError(
            "ph: the tool gateway refused this sandbox's credentials"
            + (f" ({reason})" if reason else "")
            + ". The run may have ended; ph only works during the turn that started it.",
            EX_NOPERM,
            reason=reason,
        )
    if refused.code == 403:
        return PhError("ph: the sandbox's network policy does not allow this request.", EX_NOPERM)
    if refused.code == 404:
        return PhError("ph: the tool gateway has no tools for this run any more (the run may have ended).", EX_UNAVAILABLE)
    return PhError(f"ph: the tool gateway answered HTTP {refused.code}. Try again.", EX_UNAVAILABLE)


def _answer(payload: str, wanted: int) -> dict[str, Any]:
    """The JSON-RPC response to request ``wanted`` when the gateway answered with plain JSON."""
    try:
        message = json.loads(payload)
    except ValueError:
        message = None
    if isinstance(message, dict) and message.get("id") == wanted:
        return message
    raise PhError("ph: the tool gateway's answer could not be read.", EX_SOFTWARE)


def _streamed(response: Any, wanted: int, on_progress: Progress | None) -> dict[str, Any]:  # noqa: ANN401
    """The response to request ``wanted`` from an SSE stream, read as it arrives: progress notifications before it
    go to ``on_progress`` while the call is held for approval or running."""
    data: list[str] = []
    for raw in [*response, b""]:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("data:"):
            data.append(line[5:].lstrip())
            continue
        if line.strip() or not data:
            continue
        text, data = "\n".join(data), []
        try:
            message = json.loads(text)
        except ValueError:
            continue
        if not isinstance(message, dict):
            continue
        if message.get("id") == wanted:
            return message
        if message.get("method") == "notifications/progress" and on_progress is not None:
            progress = (message.get("params") or {}).get("progress")
            if isinstance(progress, int | float):
                on_progress(float(progress))
    raise PhError("ph: the tool gateway's answer could not be read.", EX_SOFTWARE)


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    return flat.split(". ")[0].rstrip(".")


def _words(text: str) -> set[str]:
    return {w for w in re.split(r"[^a-z0-9]+", text.lower()) if len(w) > 1}


def _ranked(listed: list[dict[str, Any]], query: str, limit: int) -> list[dict[str, Any]]:
    """The listed tools (never the meta tools) ranked by how many of the query's words their name and description
    share; those sharing none are left out."""
    wanted = _words(query)
    scored = []
    for tool in listed:
        if tool["name"] in (SEARCH_TOOLS, CALL_TOOL):
            continue
        score = len(wanted & _words(f"{tool['name']} {tool.get('description', '')}"))
        if score:
            scored.append((-score, tool["name"], tool))
    return [tool for _, _, tool in sorted(scored)[:limit]]


def search(gw: Gateway, query: str, limit: int) -> list[dict[str, Any]]:
    """The listed tools that match the words, then what the gateway's own ``search_tools`` finds when it offers it.

    WP-70: the gateway's ``search_tools`` ranks the tools it holds back from the list, so a listed tool it does not
    rank (``repository_fetch``, which ``ph git clone`` calls) was never found: ``ph tools search
    repository`` printed nothing while the tool was in the run's list."""
    listed = gw.tools()
    found = _ranked(listed, query, limit)
    if any(t["name"] == SEARCH_TOOLS for t in listed):
        result = gw.call(SEARCH_TOOLS, {"query": query, "limit": limit})
        try:
            more = json.loads(_text(result))
        except ValueError:
            more = []
        if isinstance(more, list):
            names = {t["name"] for t in found}
            found.extend(t for t in cast("list[Any]", more) if isinstance(t, dict) and t.get("name") not in names)
    return found[:limit]


# WP-70: how an engine may spell a gateway tool: its MCP client's server prefix (Codex's ``pipeshub__``, Claude Code's
# ``mcp__pipeshub__``) or a dotted form. Codex has asked for ``pipeshub__repository_fetch``.
_SERVER_PREFIXES = ("mcp__pipeshub__", "mcp__pipeshub_", "pipeshub__", "pipeshub.", "pipeshub/", "pipeshub:")
_SPEC_PREFIX = "pipeshub_"


def name_forms(name: str) -> list[str]:
    """``name`` and the names it may stand for: without a server prefix, and in the gateway's spec and legacy forms
    (``pipeshub_<toolset>_<tool>``, ``<toolset>__<tool>``), most literal first."""
    forms = [name]
    bare = next((name[len(p):] for p in _SERVER_PREFIXES if name.startswith(p) and len(name) > len(p)), name)
    spec = bare if bare.startswith(_SPEC_PREFIX) else f"{_SPEC_PREFIX}{bare}"
    for form in (bare, spec, bare.removeprefix(_SPEC_PREFIX)):
        if form and form not in forms:
            forms.append(form)
    return forms


def _resolved(listed: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    by_name = {t["name"]: t for t in listed}
    return next((by_name[form] for form in name_forms(name) if form in by_name), None)


def describe(gw: Gateway, name: str) -> dict[str, Any]:
    listed = gw.tools()
    found = _resolved(listed, name)
    if found is not None:
        return found
    if any(t["name"] == SEARCH_TOOLS for t in listed):
        for form in name_forms(name):
            match = _resolved(search(gw, form, 20), form)
            if match is not None:
                return match
    hint = [t["name"] for t in search(gw, " ".join(_words(name)), 3)]
    near = f" Did you mean: {', '.join(hint)}?" if hint else ""
    raise PhError(
        f"ph: this run has no tool named {name!r}.{near} Find one with: ph tools search <what it should do>", EX_DATAERR
    )


def schema_file(name: str, folder: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """The tool's ``tools/<toolset>/<tool>.schema.json`` this turn's session folder holds (its JSON Schema 2020-12
    document, titled with the tool's name), as ``(path, document)``; ``None`` outside a seeded session."""
    root = os.path.join(FOLDER if folder is None else folder, "session", "tools")
    for directory, _, files in sorted(os.walk(root, followlinks=True)):
        for file in sorted(files):
            if not file.endswith(".schema.json"):
                continue
            path = os.path.join(directory, file)
            raw = _read(path)
            try:
                document: Any = json.loads(raw) if raw is not None else None
            except ValueError:
                continue
            if isinstance(document, dict) and cast("dict[str, Any]", document).get("title") == name:
                return path, cast("dict[str, Any]", document)
    return None


def call(gw: Gateway, name: str, arguments: dict[str, Any], *, on_progress: Progress | None = None) -> dict[str, Any]:
    """The call the engine's MCP client would make: the tool itself when it is listed, else through ``call_tool``
    (the gateway unwraps it into the same call, under the tool's own name)."""
    tools = gw.tools()
    listed = {t["name"] for t in tools}
    if name in (SEARCH_TOOLS, CALL_TOOL):
        raise PhError(f"ph: {name} is how ph finds and calls tools; name the tool itself.", EX_USAGE)
    resolved = _resolved(tools, name)
    if resolved is not None:
        name = resolved["name"]
    elif any(name.startswith(prefix) for prefix in _SERVER_PREFIXES):
        # A held-back tool, named with the engine's server prefix: the gateway answers the bare name in either form.
        name = name_forms(name)[1]
    # `ph call` prints or redirects the result: the gateway sends it whole, not cut for a conversation.
    if name not in listed and CALL_TOOL in listed:
        return gw.call(CALL_TOOL, {"name": name, "arguments": arguments}, on_progress=on_progress, full=True)
    return gw.call(name, arguments, on_progress=on_progress, full=True)


def _text(result: dict[str, Any]) -> str:
    parts = result.get("content")
    if not isinstance(parts, list):
        return ""
    return "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict) and p.get("type") == "text")


def _writes_to_file(out: Any) -> bool:  # noqa: ANN401
    """Whether stdout is a regular file: ``ph tools call … > x.json`` chose where the result goes."""
    try:
        return stat.S_ISREG(os.fstat(out.fileno()).st_mode)
    except (AttributeError, OSError, ValueError):
        return False


def save_result(tool: str, text: str, folder: str | None = None) -> str | None:
    """Writes ``text`` as the next ``notes/tool-results/<tool>-<n>.json`` (``.txt`` when it is not JSON) of the
    workspace; its path relative to the workspace, or ``None`` outside a run (no workspace link)."""
    workspace = os.path.join(FOLDER if folder is None else folder, WORKSPACE_DIR)
    if not os.path.isdir(workspace):
        return None
    directory = os.path.join(workspace, TOOL_RESULTS_DIR)
    os.makedirs(directory, exist_ok=True)
    try:
        json.loads(text)
        suffix = ".json"
    except ValueError:
        suffix = ".txt"
    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", tool.rpartition("__")[2])[:80] or "result"
    for n in range(1, 100_000):
        name = f"{stem}-{n}{suffix}"
        try:
            # O_EXCL: two calls at once never write one file, and a link planted at the name is never followed.
            fd = os.open(os.path.join(directory, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as saved:
            saved.write(text)
        return f"{TOOL_RESULTS_DIR}/{name}"
    return None


def _excerpt(text: str) -> str:
    head = text[:EXCERPT_CHARS]
    cut = head.rfind("\n")
    return head[:cut] if cut > EXCERPT_CHARS // 2 else head


def saved_outcome(outcome: Outcome, path: str, folder: str | None = None) -> Outcome:
    """The call's outcome once its result is saved at ``path``: the path and an excerpt instead of the whole text."""
    text = outcome.data["text"]
    where = os.path.join(FOLDER if folder is None else folder, WORKSPACE_DIR, path)
    note = (
        f"[ph: this result is {len(text)} characters. The whole of it is saved in {path} ({where}); read the parts "
        "you need from that file rather than all of it. Only the opening is shown here.]"
    )
    data = {**outcome.data, "text": _excerpt(text), "path": path, "chars": len(text)}
    return Outcome(outcome.command, data, f"{_excerpt(text)}\n{note}")


def _arguments(raw: str | None, stdin: Any) -> dict[str, Any]:  # noqa: ANN401
    if raw is None:
        return {}
    text = stdin.read() if raw == "-" else raw
    try:
        value = json.loads(text)
    except ValueError as bad:
        raise PhError(f"ph: --json is not valid JSON ({bad.msg} at character {bad.pos}).", EX_USAGE) from None
    if not isinstance(value, dict):
        raise PhError("ph: --json must be a JSON object of the tool's arguments, like '{\"query\": \"q3\"}'.", EX_USAGE)
    return value


def _index(folder: str) -> list[dict[str, str]] | None:
    """The run's whole grant from the session index the controller wrote, or ``None`` outside a seeded session."""
    raw = _read(os.path.join(folder, "session", "tools.txt"))
    if raw is None:
        return None
    tools = []
    for line in raw.splitlines():
        name, _, summary = line.partition(": ")
        if name.strip():
            tools.append({"name": name.strip(), "summary": summary.strip()})
    return tools


def list_tools(connect: Callable[[], Gateway], folder: str | None = None) -> list[dict[str, str]]:
    """Every tool of this run: the session index (no gateway call), else what the gateway lists."""
    indexed = _index(FOLDER if folder is None else folder)
    if indexed is not None:
        return indexed
    listed = connect().tools()
    return [
        {"name": t["name"], "summary": _first_sentence(str(t.get("description", "")))}
        for t in sorted(listed, key=lambda t: t["name"])
        if t["name"] not in (SEARCH_TOOLS, CALL_TOOL)
    ]


def _granted(gw: Gateway, name: str, folder: str | None = None) -> bool:
    indexed = _index(FOLDER if folder is None else folder)
    if indexed is not None:
        return any(t["name"] == name for t in indexed)
    listed = {t["name"] for t in gw.tools()}
    return name in listed or CALL_TOOL in listed


def version_info(folder: str | None = None) -> dict[str, Any]:
    folder = FOLDER if folder is None else folder
    image = (_read(os.path.join(folder, "VERSION")) or "").strip()
    session = (_read(os.path.join(folder, "session", "VERSION")) or "").strip()
    return {"ph": PH_VERSION, "contract": _contract(image), "session": _contract(session)}


def _contract(raw: str) -> int | str | None:
    return int(raw) if raw.isdigit() else (raw or None)


def version_text(folder: str | None = None) -> str:
    info = version_info(folder)
    line = f"ph {PH_VERSION} (/pipeshub contract {info['contract'] or 'unknown'}"
    if info["session"] is not None and info["session"] != info["contract"]:
        line += f"; this turn's session folder was written for contract {info['session']}"
    return line + ")"


def skills_dir(env: dict[str, str] | None = None, folder: str | None = None) -> str:
    """The run's read-only skills folder: the engine's (``HARNESS_SKILLS_DIR``), else ``/pipeshub/skills``."""
    env = dict(os.environ) if env is None else env
    return env.get(SKILLS_ENV) or os.path.join(FOLDER if folder is None else folder, "skills")


def _frontmatter(text: str) -> dict[str, str]:
    """The ``key: value`` lines of a SKILL.md's leading ``---`` block (name, description); quotes dropped."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    found: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() and not key.startswith((" ", "\t")):
            found[key.strip()] = value.strip().strip("'\"")
    return found


def _skill_text(root: str, name: str) -> str | None:
    path = os.path.join(root, name, SKILL_FILE)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read(MAX_SKILL_BYTES)
    except OSError:
        return None


def list_skills(root: str) -> list[dict[str, str]]:
    """Every skill in the folder (a folder holding a SKILL.md), by name, with the description it gives."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return []
    skills: list[dict[str, str]] = []
    for name in names:
        text = _skill_text(root, name)
        if text is None:
            continue
        meta = _frontmatter(text)
        skills.append({"name": name, "description": " ".join(meta.get("description", "").split())})
    return skills


def show_skill(root: str, name: str) -> dict[str, Any]:
    """A skill's SKILL.md and the paths of its other files, relative to its folder."""
    if not name or name != os.path.basename(name) or name in (".", ".."):
        raise PhError(f"ph: {name!r} is not a skill name. See: ph skill list", EX_USAGE)
    text = _skill_text(root, name)
    if text is None:
        raise PhError(f"ph: this run has no skill named {name!r}. See: ph skill list", EX_DATAERR)
    base = os.path.join(root, name)
    files: list[str] = []
    for directory, _, found in sorted(os.walk(base)):
        for file in sorted(found):
            relative = os.path.relpath(os.path.join(directory, file), base)
            if relative != SKILL_FILE:
                files.append(relative)
    meta = _frontmatter(text)
    return {"name": name, "description": meta.get("description", ""), "path": base, "files": files, "content": text}


def _skill_lines(skills: list[dict[str, str]]) -> str:
    return "\n".join(f"{s['name']}: {s['description']}" if s["description"] else s["name"] for s in skills)


def hook_call(payload: dict[str, Any]) -> dict[str, Any]:
    """A PreToolUse hook's input as the gateway's policy check reads it: ``{tool, name, paths}``. A relative path is
    the engine's working folder's. A tool it does not know is sent as such, and the gateway refuses it.

    Each path is sent as it resolves here, in the sandbox, through every symlink: the gateway judges where the call
    lands, and a link in the workspace to a file outside it is outside. A tool that names its files and names none is
    refused, as a check that cannot say what it is checking."""
    name = payload.get("tool_name")
    if not isinstance(name, str) or not name:
        raise PhError("ph: the hook's input names no tool.", EX_DATAERR)
    tool, fields = HOOK_TOOLS.get(name, ("unknown", ()))
    raw = payload.get("tool_input")
    given = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    folder = payload.get("cwd")
    cwd = folder if isinstance(folder, str) else ""
    paths: list[str] = []
    for field in fields:
        value = given.get(field)
        if isinstance(value, str) and value:
            path = value if value.startswith("/") or not cwd else os.path.join(cwd, value)
            paths.append(os.path.realpath(path) if path.startswith("/") else path)
    if fields and not paths:
        raise PhError(f"ph: the hook's input names no file for {name}.", EX_DATAERR)
    return {"tool": tool, "name": name, "paths": paths}


def read_hook_input(stdin: Any) -> dict[str, Any]:  # noqa: ANN401
    raw = stdin.read(MAX_HOOK_INPUT + 1)
    if len(raw) > MAX_HOOK_INPUT:
        raise PhError("ph: the hook's input is too large to check.", EX_DATAERR)
    try:
        payload = json.loads(raw)
    except ValueError:
        raise PhError("ph: the hook's input is not JSON.", EX_DATAERR) from None
    if not isinstance(payload, dict):
        raise PhError("ph: the hook's input is not a JSON object.", EX_DATAERR)
    return cast("dict[str, Any]", payload)


def policy_check(gw: Gateway, call: dict[str, Any]) -> dict[str, Any]:
    """The gateway's verdict on one native call: ``{decision, reason, code}``. Anything but a clear allow or refusal
    is an error, which a hook turns into a refusal."""
    result = gw.request("tools/call", {"name": POLICY_CHECK, "arguments": call}, timeout_s=POLICY_TIMEOUT_S)
    raw = result.get("structuredContent")
    structured = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    disposition, code = structured.get("disposition"), structured.get("reasonCode")
    if disposition == "executed" and not result.get("isError"):
        return {"decision": "allow", "reason": "", "code": call.get("tool")}
    if disposition == "denied":
        return {"decision": "deny", "reason": _text(result) or "refused by the run's policy", "code": code}
    raise PhError("ph: the tool gateway's policy answer could not be read.", EX_SOFTWARE)


def git_command(*args: str) -> list[str]:
    """``git`` with the checkout config ahead of ``args``."""
    argv = ["git"]
    for pair in GIT_CHECKOUT_CONFIG:
        argv += ["-c", pair]
    return [*argv, *args]


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    return env


# Lines of git's that advise a person at a terminal; on their own they read as the reason (a person saw only "and the
# repository exists."), and the safe.directory they suggest is not the fix.
_GIT_ADVICE = (
    "hint:", "To add an exception", "git config --global", "Please make sure you have the correct access rights",
    "and the repository exists.", "Cloning into",
)
_GIT_REASONS = 3
_GIT_LINE_CHARS = 160
# PipesHub's hand-off owns the copy as the sandbox user (WP-110): git refusing it means the hand-off went wrong.
_NOT_TRUSTED = "detected dubious ownership"


def git_failure(stderr: str, code: int) -> str:
    """What a failed git said: its ``fatal:`` and ``error:`` lines, each once, else its last words; never its advice."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    reasons = [line for line in lines if line.startswith(("fatal:", "error:"))]
    if not reasons:
        reasons = [line for line in lines if not line.startswith(_GIT_ADVICE)][-1:]
    kept = [line[:_GIT_LINE_CHARS] for line in dict.fromkeys(reasons)][:_GIT_REASONS]
    told = "; ".join(kept) or f"git exited {code}"
    if _NOT_TRUSTED in stderr:
        told = told.rstrip(".") + (
            ". The copy PipesHub handed over belongs to another user, so git will not read it: a PipesHub fault, "
            "not the repository's. Say the clone failed rather than changing git's settings."
        )
    return told


def run_git(*args: str, runner: Any = None) -> str:  # noqa: ANN401
    """One git command; its output, or a ``PhError`` with what git said went wrong."""
    import subprocess

    run = runner or subprocess.run
    done = run(git_command(*args), env=_git_env(), capture_output=True, text=True, timeout=GIT_TIMEOUT_S, check=False)
    if done.returncode != 0:
        rest = list(args)
        while rest[:1] == ["-C"]:
            rest = rest[2:]
        verb = rest[0] if rest else "command"
        raise PhError(f"ph: git {verb} failed: {git_failure(done.stderr or '', done.returncode)}", EX_TOOL_ERROR)
    return str(done.stdout or "").strip()


def checkout_clone(
    source: str,
    directory: str,
    url: str,
    ref: str | None,
    *,
    full: bool,
    commit: str | None = None,
    runner: Any = None,  # noqa: ANN401
) -> None:
    """Clones the handed-over copy ``source`` into ``directory`` and names the real repository as ``origin``. The
    ``pipeshub.*`` keys are the checkout's registration as a reference (ADR-RUN-01 D2): the sync records its address
    and commit, and never saves its files."""
    run_git("clone", "--quiet", "--no-hardlinks", "--no-recurse-submodules", "--", source, directory, runner=runner)
    run_git("-C", directory, "remote", "set-url", "origin", url, runner=runner)
    run_git("-C", directory, "config", "pipeshub.fetched", "true", runner=runner)
    if commit:
        run_git("-C", directory, "config", "pipeshub.commit", commit, runner=runner)
    if ref:
        run_git("-C", directory, "config", "pipeshub.ref", ref, runner=runner)
    if full:
        run_git("-C", directory, "config", "pipeshub.full", "true", runner=runner)


def checkout_fetch(source: str, directory: str, *, runner: Any = None) -> None:  # noqa: ANN401
    """Fetches the handed-over copy ``source`` into ``directory``'s ``origin/*`` branches."""
    run_git(
        "-C", directory, "fetch", "--quiet", "--update-shallow", "--no-tags", "--no-recurse-submodules",
        source, "+refs/heads/*:refs/remotes/origin/*", runner=runner,
    )


def _repository_answer(result: dict[str, Any]) -> dict[str, Any]:
    try:
        answer = json.loads(_text(result))
    except ValueError:
        answer = None
    if not isinstance(answer, dict) or not isinstance(answer.get("path"), str) or not isinstance(answer.get("url"), str):
        raise PhError("ph: the repository fetch's answer could not be read.", EX_SOFTWARE)
    return cast("dict[str, Any]", answer)


def _fetched_line(answer: dict[str, Any], verb: str, directory: str, *, linked_to: str | None = None) -> str:
    size = answer.get("bytes")
    mb = f"{max(1, round(size / (1024 * 1024)))} MB, " if isinstance(size, int) else ""
    link = (
        f" {directory} is a link to the checkout in {linked_to}: give tools that do not follow links a trailing "
        f"slash ({directory}/) or their follow option (find -L, rg -L)."
        if linked_to
        else ""
    )
    return (
        f"{verb} {answer.get('label') or answer['url']} into {directory} ({mb}read-only: "
        "PipesHub fetched it; pushing from here is not possible). It is a reference copy, not saved with the "
        f"session's files: write your notes and outputs outside it.{link}"
    )


def _fetch_arguments(url: str, ref: str | None, *, full: bool) -> dict[str, Any]:
    arguments: dict[str, Any] = {"url": url}
    if ref:
        arguments["ref"] = ref
    if full:
        arguments["full"] = True
    return arguments


def _our_link(path: str, references: str) -> bool:
    """A link ``ph git clone`` left at ``path`` into the references mount (its target may be gone with an old
    sandbox): replaced by a new clone."""
    if not os.path.islink(path):
        return False
    target = os.readlink(path)
    return target.startswith(references.rstrip("/") + "/")


def reference_checkout(target: str, references: str) -> str:
    """Where ``target``'s checkout goes under the references mount: one folder per workspace path, so cloning the same
    folder again replaces its checkout and two folders never share one."""
    absolute = os.path.abspath(target)
    readable = re.sub(r"[^A-Za-z0-9._-]+", "_", absolute.strip("/"))[-80:].strip("._") or "repo"
    digest = hashlib.sha256(absolute.encode()).hexdigest()[:10]
    return os.path.join(references, f"{readable}-{digest}")


def git_clone(
    gw: Gateway,
    url: str,
    directory: str | None,
    ref: str | None,
    *,
    full: bool,
    on_progress: Progress | None = None,
    runner: Any = None,  # noqa: ANN401
    references: str = REFERENCES_DIR,
) -> Outcome:
    target = directory or url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") or "repo"
    linked = _our_link(target, references)
    if not linked and os.path.exists(target) and (not os.path.isdir(target) or os.listdir(target)):
        raise PhError(f"ph: {target} already exists and is not empty; name another folder.", EX_USAGE)
    result = call(gw, REPOSITORY_FETCH, _fetch_arguments(url, ref, full=full), on_progress=on_progress)
    held = _called("git.clone", REPOSITORY_FETCH, result)
    if held.code != EX_OK:
        return held
    answer = _repository_answer(result)
    commit = answer.get("commit") if isinstance(answer.get("commit"), str) else None
    if not (os.path.isdir(references) and os.access(references, os.W_OK)):
        checkout_clone(answer["path"], target, answer["url"], ref, full=full, commit=commit, runner=runner)
        return Outcome("git.clone", {**answer, "directory": target}, _fetched_line(answer, "Cloned", target))
    # ADR-RUN-02: the checkout lives on the references mount, never in the saved workspace; the folder asked for is a
    # link to it, so the engine's paths and commands stay the same.
    checkout = reference_checkout(target, references)
    if os.path.lexists(checkout):
        shutil.rmtree(checkout)
    checkout_clone(answer["path"], checkout, answer["url"], ref, full=full, commit=commit, runner=runner)
    if linked:
        os.unlink(target)
    elif os.path.isdir(target):
        os.rmdir(target)
    parent = os.path.dirname(os.path.abspath(target))
    os.makedirs(parent, exist_ok=True)
    os.symlink(checkout, target)
    data = {**answer, "directory": target, "checkout": checkout}
    return Outcome("git.clone", data, _fetched_line(answer, "Cloned", target, linked_to=checkout))


def git_adopt(directory: str, *, references: str = REFERENCES_DIR, runner: Any = None) -> Outcome:  # noqa: ANN401
    """Moves a checkout ``ph git clone`` made inside the workspace (before the references mount, ADR-RUN-02) onto the
    mount and leaves a link where it was; anything else is left as it is. The controller runs it at a turn's start."""
    if os.path.islink(directory) or not os.path.isdir(os.path.join(directory, ".git")):
        return Outcome("git.adopt", {"directory": directory, "moved": False}, f"{directory}: nothing to move")
    if not (os.path.isdir(references) and os.access(references, os.W_OK)):
        return Outcome("git.adopt", {"directory": directory, "moved": False}, f"{directory}: no references mount")
    try:
        fetched = run_git("-C", directory, "config", "--default", "", "pipeshub.fetched", runner=runner)
    except PhError:
        fetched = ""
    if fetched != "true":
        return Outcome("git.adopt", {"directory": directory, "moved": False}, f"{directory}: not a ph git clone checkout")
    checkout = reference_checkout(directory, references)
    if os.path.lexists(checkout):
        shutil.rmtree(checkout)
    shutil.move(directory, checkout)
    os.symlink(checkout, directory)
    return Outcome("git.adopt", {"directory": directory, "moved": True, "checkout": checkout}, f"Moved {directory} to {checkout}")


def git_fetch(gw: Gateway, directory: str | None, *, on_progress: Progress | None = None, runner: Any = None) -> Outcome:  # noqa: ANN401
    target = directory or "."
    try:
        url = run_git("-C", target, "remote", "get-url", "origin", runner=runner)
        fetched = run_git("-C", target, "config", "--default", "", "pipeshub.fetched", runner=runner)
    except PhError:
        raise PhError(f"ph: {target} is not a repository ph git clone made.", EX_USAGE) from None
    if fetched != "true":
        raise PhError(f"ph: {target} is not a repository ph git clone made; ph git fetch only refreshes those.", EX_USAGE)
    ref = run_git("-C", target, "config", "--default", "", "pipeshub.ref", runner=runner) or None
    full = run_git("-C", target, "config", "--default", "", "pipeshub.full", runner=runner) == "true"
    result = call(gw, REPOSITORY_FETCH, _fetch_arguments(url, ref, full=full), on_progress=on_progress)
    held = _called("git.fetch", REPOSITORY_FETCH, result)
    if held.code != EX_OK:
        return held
    answer = _repository_answer(result)
    checkout_fetch(answer["path"], target, runner=runner)
    if isinstance(answer.get("commit"), str) and answer["commit"]:
        run_git("-C", target, "config", "pipeshub.commit", answer["commit"], runner=runner)
    return Outcome("git.fetch", {**answer, "directory": target}, _fetched_line(answer, "Fetched", target))


def inputs_get(gw: Gateway, name: str) -> Outcome:
    """Places a large input under ``in/`` (the task names it as not copied in yet); one already there answers too."""
    # Straight to the controller's tool: it is in no tool list, so never through call_tool.
    result = gw.call(INPUT_FETCH, {"name": name}, full=True)
    held = _called("inputs.get", INPUT_FETCH, result)
    if held.code != EX_OK:
        return held
    try:
        answer = json.loads(_text(result))
    except ValueError:
        raise PhError("ph: the input's answer was not JSON.", EX_SOFTWARE) from None
    placed = cast("dict[str, Any]", answer) if isinstance(answer, dict) else {}
    path = str(placed.get("path", ""))
    verb = "Placed" if placed.get("staged") else "Already in place:"
    return Outcome("inputs.get", placed, f"{verb} {path} ({placed.get('bytes', 0)} bytes).")


def git_push(gw: Gateway, *, on_progress: Progress | None = None) -> Outcome:
    """The run's repository's commit to its session branch, through ``repository_push`` (the person is asked)."""
    result = call(gw, REPOSITORY_PUSH, {}, on_progress=on_progress)
    held = _called("git.push", REPOSITORY_PUSH, result)
    if held.code != EX_OK:
        return held
    try:
        answer = json.loads(_text(result))
    except ValueError:
        answer = {}
    pushed = cast("dict[str, Any]", answer) if isinstance(answer, dict) else {}
    line = f"Pushed {pushed.get('commit', 'the commit')} to {pushed.get('branch', 'the session branch')} of {pushed.get('repository', 'the repository')}."
    return Outcome("git.push", pushed, line)


class Outcome:
    """What a command ends with: its ``--json`` data, the text it prints otherwise, and its exit code. A code other
    than 0 is a failure whose ``message`` goes to stderr (``--json``: the error's message)."""

    def __init__(self, command: str, data: dict[str, Any], text: str, code: int = EX_OK, message: str = "") -> None:
        self.command = command
        self.data = data
        self.text = text
        self.code = code
        self.message = message


# The --json error code for each exit code: stable names a script can branch on.
ERROR_CODES = {
    EX_TOOL_ERROR: "tool_error",
    EX_USAGE: "usage",
    EX_DATAERR: "not_found",
    EX_UNAVAILABLE: "unavailable",
    EX_SOFTWARE: "bad_answer",
    EX_TEMPFAIL: "approval_pending",
    EX_NOPERM: "not_permitted",
    EX_CONFIG: "not_in_run",
    EX_REFUSED: "refused",
}


def _called(command: str, name: str, result: dict[str, Any]) -> Outcome:
    text = _text(result)
    structured = result.get("structuredContent")
    disposition = structured.get("disposition") if isinstance(structured, dict) else None
    data = {"tool": name, "text": text}
    if result.get("isError"):
        return Outcome(command, data, "", EX_TOOL_ERROR, text or f"ph: {name} failed.")
    # Held for a person's approval: the gateway's text says to keep working on the rest meanwhile (ADR-RUN-06 D1).
    if disposition == "deferred":
        return Outcome(command, data, text, EX_TEMPFAIL, text)
    return Outcome(command, data, text)


def ask(gw: Gateway, question: str, options: list[str], *, on_progress: Progress | None = None) -> Outcome:
    """The run's ``ask_user``: the question goes to the person and the engine ends its turn."""
    listed = {t["name"] for t in gw.tools()}
    name = next((n for n in ASK_USER_NAMES if n in listed), None)
    if name is None:
        raise PhError(
            "ph: this run cannot ask the person: nobody is in this chat to answer (a scheduled, API or sub-agent "
            "run). Decide for yourself and say what you assumed.",
            EX_UNAVAILABLE,
        )
    arguments: dict[str, Any] = {"question": question}
    if options:
        arguments["options"] = options
    outcome = _called("ask", name, gw.call(name, arguments, on_progress=on_progress))
    if outcome.code == EX_OK:
        outcome.data.update({"asked": True, "question": question, "options": options})
    return outcome


def outputs_dir(folder: str | None = None) -> str:
    return os.path.join(FOLDER if folder is None else folder, OUTPUTS_DIR)


def output_name(source: str, name: str | None) -> str:
    """Where under the outputs folder the file goes: ``name``, else the file's own name, in NFC. Only a path the
    gateway would publish: plain relative segments, each a name every computer can hold (``unsafe_output_reason``)."""
    wanted = name if name is not None else os.path.basename(source)
    reason = unsafe_output_reason(wanted)
    if reason is not None:
        raise PhError(
            f"ph: {wanted!r} is not a name in the outputs folder ({_UNSAFE_WORDS[reason]}); use a plain relative "
            "name like report.md or charts/q3.png.",
            EX_USAGE,
        )
    return unicodedata.normalize("NFC", wanted)


# SEC-FS-02: the rules of harness_core.workspace.safe_names.check_name_path, which ph cannot import (it runs in the
# sandbox on the standard library alone). contracts/harness/vectors/safe-names.json pins the two to the same answers.
_UNSAFE_WORDS: dict[str, str] = {
    "empty": "it is empty",
    "too_long": "it is too long",
    "separator": "it has a backslash or a colon",
    "traversal": "it is absolute or climbs out with ..",
    "nul": "it has a NUL character",
    "control": "it has a control character",
    "encoded_separator": "it hides a folder separator or a dot folder",
    "drive": "it names a drive",
    "reserved": "it is a device name on Windows",
    "trailing_dot_or_space": "a part of it ends with a dot or a space",
    "not_a_path": "it has an empty part",
}
_DRIVE = re.compile(r"^[A-Za-z]:")
_ENCODED = re.compile(r"%[0-9A-Fa-f]{2}")
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"{device}{digit}" for device in ("com", "lpt") for digit in "0123456789\u00b9\u00b2\u00b3"}
)
_MAX_PATH_BYTES = 1024
_MAX_PATH_DEPTH = 32
_MAX_NAME_BYTES = 255


def _hides_separator(segment: str) -> bool:
    forms = {segment, unicodedata.normalize("NFKC", segment)}
    current = segment
    for _ in range(4):
        if not _ENCODED.search(current):
            break
        current = urllib.parse.unquote(current, errors="surrogateescape")
        forms.update({current, unicodedata.normalize("NFKC", current)})
    return any("/" in form or "\\" in form or "\x00" in form or form in (".", "..") for form in forms - {segment})


def _unsafe_segment(name: str) -> str | None:
    if "\x00" in name:
        return "nul"
    if "/" in name or "\\" in name:
        return "traversal" if name.startswith(("/", "\\")) else "separator"
    if name in (".", ".."):
        return "traversal"
    if any(unicodedata.category(char) == "Cc" for char in name):
        return "control"
    if _hides_separator(name):
        return "encoded_separator"
    if ":" in name:
        return "drive" if _DRIVE.match(name) else "separator"
    if name[-1] in ". ":
        return "trailing_dot_or_space"
    if name.split(".", 1)[0].rstrip(" ").lower() in _RESERVED_STEMS:
        return "reserved"
    if len(name.encode("utf-8", "surrogatepass")) > _MAX_NAME_BYTES:
        return "too_long"
    return None


def unsafe_output_reason(path: str) -> str | None:
    """Why ``path`` is no deliverable's path under the outputs folder (a reason code of safe_names), or ``None``."""
    if not path:
        return "empty"
    if path.startswith(("/", "\\")):
        return "traversal"
    if _DRIVE.match(path):
        return "drive"
    if "\x00" in path:
        return "nul"
    if "\\" in path:
        return "separator"
    normalized = unicodedata.normalize("NFC", path)
    parts = normalized.split("/")
    if any(part in (".", "..") for part in parts):
        return "traversal"
    if len(normalized.encode("utf-8", "surrogatepass")) > _MAX_PATH_BYTES or len(parts) > _MAX_PATH_DEPTH:
        return "not_a_path"
    if "" in parts:
        return "not_a_path"
    for part in parts:
        if _DRIVE.match(part):
            return "drive"
        if _hides_separator(part):
            return "encoded_separator"
    for part in parts:
        reason = _unsafe_segment(part)
        if reason is not None:
            return reason
    return None


def place_output(source: str, name: str, folder: str | None = None) -> str:
    """Copies ``source`` to ``name`` under the outputs folder (nothing when it is that file already); the path."""
    root = outputs_dir(folder)
    if not os.path.lexists(root):
        raise PhError(f"ph: this sandbox has no outputs folder ({root}). ph works inside a PipesHub engine run.", EX_CONFIG)
    # The image's link to the workspace's out/, which a new workspace does not have yet.
    os.makedirs(os.path.realpath(root), exist_ok=True)
    if os.path.islink(source) or not os.path.isfile(source):
        raise PhError(f"ph: {source} is not a regular file; publish a file, not a folder or a link.", EX_DATAERR)
    target = os.path.join(root, name)
    _inside(root, os.path.dirname(target), name)
    if os.path.exists(target) and os.path.samefile(source, target):
        return target
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.islink(target):
        os.unlink(target)
    shutil.copyfile(source, target)
    return target


def _inside(root: str, directory: str, name: str) -> None:
    """The nearest folder of ``directory`` that exists is the outputs folder or inside it: a link in the outputs
    folder never takes a published file elsewhere."""
    real_root = os.path.realpath(root)
    existing = directory
    while not os.path.exists(existing) and existing != root:
        existing = os.path.dirname(existing)
    real = os.path.realpath(existing)
    if real != real_root and not real.startswith(real_root + os.sep):
        raise PhError(f"ph: {name} would leave the outputs folder.", EX_USAGE)


def publish(gw: Gateway, source: str, name: str | None, *, folder: str | None = None) -> Outcome:
    """The file as a deliverable of this task now: copied into the outputs folder, then published by the gateway."""
    wanted = output_name(source, name)
    place_output(source, wanted, folder)
    result = gw.request("tools/call", {"name": ARTIFACT_PUBLISH, "arguments": {"path": wanted}})
    outcome = _called("publish", ARTIFACT_PUBLISH, result)
    if outcome.code != EX_OK:
        return outcome
    try:
        answer = json.loads(outcome.data["text"])
    except ValueError:
        answer = None
    if not isinstance(answer, dict):
        raise PhError("ph: the gateway's answer to the publish could not be read.", EX_SOFTWARE)
    published = cast("dict[str, Any]", answer)
    if published.get("unchanged"):
        text = f"{wanted} is already published and has not changed since."
    else:
        text = (
            f"Published {wanted} (version {published.get('version')}, a draft until this task's turn ends). "
            "It is in the chat now."
        )
    return Outcome("publish", {"path": wanted, **published}, text)


def _tool_lines(tools: list[dict[str, str]]) -> str:
    return "\n".join(f"{t['name']}: {t['summary']}" if t["summary"] else t["name"] for t in tools)


def _found(tools: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"name": str(t.get("name")), "summary": _summary(t)} for t in tools]


def _summary(tool: dict[str, Any]) -> str:
    """A found tool in one line; a repository tool names the ``ph git`` command that calls it (WP-70)."""
    summary = _first_sentence(str(tool.get("description", "")))
    forms = name_forms(str(tool.get("name")))
    if REPOSITORY_FETCH in forms:
        return f"{summary} (run: ph git clone <https url>)"
    if REPOSITORY_PUSH in forms:
        return f"{summary} (run: ph git push)"
    return summary


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise PhError(f"ph: {message}. See: ph --help", EX_USAGE)

    def _check_value(self, action: argparse.Action, value: object) -> None:
        # argparse's own wording changed in a 3.12 patch release (choices lost their quotes); the --json envelope
        # is a contract, so ph words it the same whatever Python the image ships.
        if action.choices is not None and value not in action.choices:
            choices = ", ".join(map(str, action.choices))
            raise argparse.ArgumentError(action, f"invalid choice: {value!r} (choose from {choices})")


def _json_flag(command: argparse.ArgumentParser) -> None:
    command.add_argument("--json", dest="json_here", action="store_true", help="print one JSON object (as ph --json)")


def parser() -> argparse.ArgumentParser:
    return _parsers()[0]


def _parsers() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
    """``ph``'s parser, and the parsers of its nouns with verbs (whose help ``ph <noun>`` prints)."""
    root = _Parser(
        prog="ph",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "PipesHub's knowledge and tools from the shell (contract 1). Guide: /pipeshub/AGENTS.md\n\n"
            "Canonical commands: search, fetch, tools {list,search,describe,call}, ask, publish, skill {list,show},\n"
            "policy check, git {clone,fetch,push,adopt}, inputs get\n"
            "Older names that still work: describe (tools describe), call (tools call)\n"
            "ph search searches company knowledge; ph tools search finds a tool."
        ),
    )
    root.add_argument("--version", action="store_true", help="print ph's version and the /pipeshub contract's")
    root.add_argument(
        "--json", dest="json_output", action="store_true",
        help="print exactly one JSON object on stdout: {ok, command, data} or {ok, command, error}",
    )
    commands = root.add_subparsers(dest="command", parser_class=_Parser, metavar="COMMAND")
    knowledge = commands.add_parser("search", help="search company knowledge (find a tool: ph tools search)")
    knowledge.add_argument("query", nargs="+", help="what to look for, in a few words")
    _json_flag(knowledge)
    fetched = commands.add_parser("fetch", help="read whole records a search showed you, by record id")
    fetched.add_argument("record_ids", nargs="+", metavar="RECORD_ID")
    _json_flag(fetched)
    tools = commands.add_parser("tools", help="list, find, describe and call this run's tools")
    verbs = tools.add_subparsers(dest="verb", parser_class=_Parser, metavar="VERB")
    listed = verbs.add_parser("list", help="every tool of this run, one line each")
    _json_flag(listed)
    found = verbs.add_parser("search", help="find a tool of this run by what it does")
    found.add_argument("query", nargs="+", help="what the tool should do, in a few words")
    found.add_argument("--limit", type=int, default=8, help="the most tools to show (1-20, default 8)")
    _json_flag(found)
    for parent, name, help_text in (
        (verbs, "describe", "a tool's description and input schema"),
        (commands, "describe", "older name of: ph tools describe"),
    ):
        shown = parent.add_parser(name, help=help_text)
        shown.add_argument("tool")
        _json_flag(shown)
    for parent, name, help_text in (
        (verbs, "call", "call a tool with JSON arguments"),
        (commands, "call", "older name of: ph tools call"),
    ):
        called = parent.add_parser(name, help=help_text)
        called.add_argument("tool")
        called.add_argument("--json", dest="arguments", metavar="JSON", help="the arguments as a JSON object; - reads stdin")
        saving = called.add_mutually_exclusive_group()
        saving.add_argument(
            "--save", action="store_const", const="save", dest="saving",
            help=f"save the result under {TOOL_RESULTS_DIR}/ and print its path (default past {SAVE_ABOVE_CHARS} characters)",
        )
        saving.add_argument("--print", action="store_const", const="print", dest="saving", help="print the result whole")
    asked = commands.add_parser("ask", help="ask the person a question; their answer is your next message")
    asked.add_argument("question", nargs="+", help="the question, in plain words")
    asked.add_argument(
        "--option", action="append", default=[], metavar="ANSWER", help="an answer they can pick (repeatable, up to 7)"
    )
    _json_flag(asked)
    published = commands.add_parser("publish", help="publish a file as a deliverable of this task, now")
    published.add_argument("file", help="the file to publish")
    published.add_argument("--as", dest="name", help="its name in the outputs folder (default: the file's own name)")
    _json_flag(published)
    skill = commands.add_parser("skill", help="the skills of this run: list them, show one")
    skill_verbs = skill.add_subparsers(dest="verb", parser_class=_Parser, metavar="VERB")
    _json_flag(skill_verbs.add_parser("list", help="every skill of this run, one line each"))
    shown_skill = skill_verbs.add_parser("show", help="a skill's instructions (SKILL.md) and its other files")
    shown_skill.add_argument("name")
    _json_flag(shown_skill)
    policy = commands.add_parser("policy", help="ask the run's policy about one of the engine's own tool calls")
    policy_verbs = policy.add_subparsers(dest="verb", parser_class=_Parser, metavar="VERB")
    checked = policy_verbs.add_parser("check", help="allowed (exit 0) or refused (exit 2), with the reason")
    checked.add_argument("tool", nargs="?", help="shell, edit, read, search or web")
    checked.add_argument("--path", action="append", default=[], help="a file an edit writes (repeatable)")
    checked.add_argument("--hook", action="store_true", help="read a PreToolUse hook's input from stdin")
    _json_flag(checked)
    git = commands.add_parser("git", help="clone a public repository after the person allows it (read-only)")
    git_verbs = git.add_subparsers(dest="verb", parser_class=_Parser, metavar="VERB")
    cloned = git_verbs.add_parser("clone", help="clone https://<allowed git host>/<owner>/<repo>, asking the person first")
    cloned.add_argument("url", help="the repository's https URL")
    cloned.add_argument("directory", nargs="?", help="the folder to clone into (default: the repository's name)")
    cloned.add_argument("--ref", help="a branch or tag (default: the repository's default branch)")
    cloned.add_argument("--full", action="store_true", help="the whole history (default: the latest commit only)")
    _json_flag(cloned)
    refreshed = git_verbs.add_parser("fetch", help="fetch the latest of a repository ph git clone made (asks again)")
    refreshed.add_argument("directory", nargs="?", help="the clone's folder (default: the current one)")
    _json_flag(refreshed)
    _json_flag(git_verbs.add_parser("push", help="push the run's repository to its session branch, asking the person first"))
    adopted = git_verbs.add_parser(
        "adopt", help="move a checkout ph git clone made inside the workspace onto the references mount (run by PipesHub)"
    )
    adopted.add_argument("directory", help="the checkout's folder")
    _json_flag(adopted)
    inputs = commands.add_parser("inputs", help="the task's input files: get a large one the task says is not copied in yet")
    input_verbs = inputs.add_subparsers(dest="verb", parser_class=_Parser, metavar="VERB")
    got = input_verbs.add_parser("get", help="place a large input under in/ (/pipeshub/inputs) before reading it")
    got.add_argument("name", help="the input's name, as the task lists it")
    _json_flag(got)
    return root, {"tools": tools, "skill": skill, "policy": policy, "git": git, "inputs": inputs}


def _command_name(args: argparse.Namespace) -> str:
    if args.command in ("tools", "skill", "policy", "git", "inputs"):
        return f"{args.command}.{args.verb}"
    # The older names run the canonical commands and answer as them.
    return {"describe": "tools.describe", "call": "tools.call"}.get(args.command, args.command)


def _maybe_saved(outcome: Outcome, tool: str, saving: str | None, out: Any) -> Outcome:  # noqa: ANN401
    """``--save``, or by default a result past ``SAVE_ABOVE_CHARS`` whose stdout is not a file: saved, with its path."""
    if outcome.code != EX_OK or saving == "print":
        return outcome
    if saving != "save" and (len(outcome.data["text"]) <= SAVE_ABOVE_CHARS or out is None or _writes_to_file(out)):
        return outcome
    path = save_result(tool, outcome.data["text"])
    return outcome if path is None else saved_outcome(outcome, path)


def _run(
    args: argparse.Namespace, command: str, connect: Callable[[], Gateway], stdin: Any, err: Any, out: Any = None  # noqa: ANN401
) -> Outcome:
    def waiting(name: str) -> Progress:
        return lambda s: print(f"ph: {name} is still waiting ({int(s)} s)", file=err)

    if command == "version":
        return Outcome(command, version_info(), version_text())
    if command == "tools.list":
        tools = list_tools(connect)
        return Outcome(command, {"tools": tools}, _tool_lines(tools))
    if command == "tools.search":
        query = " ".join(args.query)
        tools = _found(search(connect(), query, max(1, min(int(args.limit), 20))))
        text = _tool_lines(tools) or f"No tool of this run matches {query!r}. Try other words."
        return Outcome(command, {"tools": tools}, text)
    if command == "tools.describe":
        tool = describe(connect(), args.tool)
        schema = tool.get("inputSchema") or tool.get("input_schema") or {}
        description = str(tool.get("description", ""))
        data: dict[str, Any] = {"name": tool["name"], "description": description, "inputSchema": schema}
        # The session's schema file is what `ph tools describe` shows when there is one: the same document a script
        # reads from /pipeshub/session/tools (TG-04).
        found = schema_file(tool["name"])
        if found is not None:
            data["schemaPath"], data["schema"] = found
        shown = data.get("schema", schema)
        text = f"{tool['name']}\n\n{description}\n\nArguments (JSON schema):\n" + json.dumps(shown, indent=2, ensure_ascii=False)
        return Outcome(command, data, text)
    if command == "tools.call":
        arguments = _arguments(args.arguments, stdin)
        gw = connect()
        outcome = _called(command, args.tool, call(gw, args.tool, arguments, on_progress=waiting(args.tool)))
        return _maybe_saved(outcome, args.tool, args.saving, out)
    if command == "ask":
        return ask(connect(), " ".join(args.question), list(args.option), on_progress=waiting("the question"))
    if command == "publish":
        return publish(connect(), args.file, args.name)
    if command == "skill.list":
        skills = list_skills(skills_dir())
        return Outcome(command, {"skills": skills}, _skill_lines(skills) or "This run has no skills.")
    if command == "skill.show":
        shown = show_skill(skills_dir(), args.name)
        files = "".join(f"\n  {f}" for f in shown["files"])
        footer = f"\n\nIts other files, under {shown['path']}:{files}" if files else ""
        return Outcome(command, shown, shown["content"].rstrip("\n") + footer)
    if command == "policy.check":
        return _policy_outcome(args, command, connect, stdin)
    if command == "git.clone":
        return git_clone(connect(), args.url, args.directory, args.ref, full=args.full, on_progress=waiting("the clone"))
    if command == "git.fetch":
        return git_fetch(connect(), args.directory, on_progress=waiting("the fetch"))
    if command == "git.push":
        return git_push(connect(), on_progress=waiting("the push"))
    if command == "git.adopt":
        return git_adopt(args.directory)
    if command == "inputs.get":
        return inputs_get(connect(), args.name)
    gw = connect()
    if command == "search":
        name, arguments, what = KNOWLEDGE_SEARCH, {"query": " ".join(args.query)}, "knowledge search"
    else:
        name, arguments, what = KNOWLEDGE_FETCH, {"record_ids": list(args.record_ids)}, "record fetch"
    if not _granted(gw, name):
        raise PhError(
            f"ph: this run has no {what} tool ({name}). See what it has with: ph tools search <what you need>",
            EX_DATAERR,
        )
    return _called(command, name, call(gw, name, arguments, on_progress=waiting(name)))


def _policy_outcome(args: argparse.Namespace, command: str, connect: Callable[[], Gateway], stdin: Any) -> Outcome:  # noqa: ANN401
    if args.hook:
        checked = hook_call(read_hook_input(stdin))
    elif args.tool:
        checked = {"tool": args.tool, "name": args.tool, "paths": list(args.path)}
    else:
        raise PhError("ph: name the tool to check (shell, edit, read, search, web), or pass --hook.", EX_USAGE)
    verdict = policy_check(connect(), checked)
    if verdict["decision"] == "allow":
        # A hook says nothing on stdout: an engine reads a hook's stdout as its JSON answer.
        return Outcome(command, verdict, "" if args.hook else "allowed")
    return Outcome(command, verdict, "", EX_REFUSED, f"PipesHub policy refused this {checked['name']} call: {verdict['reason']}")


def _wants_json(argv: list[str]) -> bool:
    """``--json`` as the output mode, read before parsing so a usage error is answered in JSON too: given before the
    command, or after any command but ``call``, whose ``--json`` is its arguments."""
    leading = next((i for i, token in enumerate(argv) if not token.startswith("-")), len(argv))
    return "--json" in argv[:leading] or ("--json" in argv and "call" not in argv[leading : leading + 2])


def _envelope(command: str | None, outcome: Outcome | None = None, failed: PhError | None = None) -> str:
    if failed is not None:
        error = {"code": ERROR_CODES.get(failed.code, "failed"), "exit": failed.code, "message": str(failed)}
        return json.dumps({"ok": False, "command": command, "error": error}, ensure_ascii=False)
    assert outcome is not None
    if outcome.code != EX_OK:
        error = {"code": ERROR_CODES.get(outcome.code, "failed"), "exit": outcome.code, "message": outcome.message}
        return json.dumps({"ok": False, "command": command, "error": error}, ensure_ascii=False)
    return json.dumps({"ok": True, "command": command, "data": outcome.data}, ensure_ascii=False)


def main(argv: list[str] | None = None, *, out: Any = None, err: Any = None, stdin: Any = None, gw: Gateway | None = None) -> int:  # noqa: ANN401
    out, err, stdin = out or sys.stdout, err or sys.stderr, stdin or sys.stdin
    argv = sys.argv[1:] if argv is None else list(argv)
    as_json = _wants_json(argv)
    command: str | None = None
    connected: list[Gateway] = [gw] if gw is not None else []

    def connect() -> Gateway:
        if not connected:
            connected.append(Gateway(*gateway()))
        return connected[0]

    try:
        root, nouns = _parsers()
        try:
            with contextlib.redirect_stdout(out):
                args = root.parse_args(argv)
        except SystemExit as helped:
            # --help (a usage error raises PhError instead): printed, and nothing else to do.
            return int(helped.code or 0)
        as_json = bool(args.json_output or getattr(args, "json_here", False))
        if args.version:
            command = "version"
        elif args.command is None or (args.command in nouns and args.verb is None):
            if not as_json:
                (root if args.command is None else nouns[args.command]).print_help(out)
            raise PhError("ph: name a command. See: ph --help", EX_USAGE)
        else:
            command = _command_name(args)
        outcome = _run(args, command, connect, stdin, err, None if as_json else out)
    except PhError as failed:
        if as_json:
            print(_envelope(command, failed=failed), file=out)
        else:
            print(str(failed), file=err)
        # The managed hook fails closed: a check that got no answer refuses the call (G-25).
        if command == "policy.check" and "--hook" in argv:
            if not as_json:
                print("PipesHub could not check this call against the run's policy, so it is refused.", file=err)
            return EX_REFUSED
        return failed.code
    except KeyboardInterrupt:
        return 130
    if as_json:
        print(_envelope(command, outcome), file=out)
    else:
        if outcome.text or (outcome.code == EX_OK and command != "policy.check"):
            print(outcome.text, file=out)
        if outcome.code not in (EX_OK, EX_TEMPFAIL):
            print(outcome.message, file=err)
    return outcome.code


if __name__ == "__main__":
    sys.exit(main())
