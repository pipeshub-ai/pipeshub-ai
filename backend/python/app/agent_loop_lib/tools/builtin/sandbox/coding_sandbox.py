from __future__ import annotations

import asyncio
import base64
import logging
import math
import os
import re
import time
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.sandbox.coding.base import (
    CodeRequest,
    CodeResult,
    CodingSandboxBackend,
    ErrorCategory,
)
from app.agent_loop_lib.sandbox.manager import (
    SandboxCallStopped,
    SandboxLimitExceeded,
    SandboxManager,
    SandboxType,
    UnknownSandboxError,
)
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    PARENT_RESULTS_INPUT_PATH,
    peek_staged_input_files,
    peek_staged_skill_resources,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.core.types import ToolResult

"""The coding sandbox's three tools — `run_code`, `install_packages`,
`read_sandbox_file` — all constructor-injected with the (generic, typed)
`SandboxManager` and operating on `SandboxType.CODING`.

Error-propagation contract (see `sandbox/coding/base.py::CodeResult`): a
failed RUN (syntax/type/runtime error, timeout) is NOT a tool failure —
`run_code`/`install_packages` return `ToolOutput(success=True, data=...)`
with the failure represented as data (`error_analysis`, `exit_code`) so the
model sees it as something to reflect on and retry. Only infrastructure
failures — an unrecognized `sandbox_id`, or the sandbox type not being
registered/enabled — surface as `ToolOutput(success=False, ...)`; any other
unexpected exception (missing runtime, foundational env setup failure) is
caught generically by `ToolExecutor._run` and surfaced the same way.
"""

__all__ = [
    "CodingSandboxTool",
    "InstallPackagesTool",
    "ReadSandboxFileTool",
    "detect_language_mismatch",
    "stopped_output",
]

_LANGUAGE_ENUM = ["typescript", "python"]
_logger = logging.getLogger(__name__)

# A weaker model asked to write "PDF-generation code" will often reach for
# `reportlab`/`fpdf2` (Python) but leave `language` at its declared default
# of "typescript" — the sandbox then tries to run Python source as
# TypeScript and fails on the first line. These signals are deliberately
# unambiguous syntax (not vocabulary that could appear in either language's
# strings/comments) so a real mismatch is caught without ever "correcting"
# code that's actually fine as declared.
_PYTHON_SIGNALS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*def\s+\w+\s*\([^)]*\)\s*:\s*$", re.MULTILINE),
    re.compile(r"^\s*import\s+[\w.]+\s*$", re.MULTILINE),
    re.compile(r"^\s*from\s+[\w.]+\s+import\s+", re.MULTILINE),
    re.compile(r"^\s*if\s+__name__\s*==\s*[\"']__main__[\"']\s*:", re.MULTILINE),
    re.compile(r"^\s*elif\s+.+:\s*$", re.MULTILINE),
    re.compile(r"^\s*print\s*\(", re.MULTILINE),
)
_TYPESCRIPT_SIGNALS: tuple[re.Pattern, ...] = (
    re.compile(r"^\s*(const|let|var)\s+\w+\s*[:=]", re.MULTILINE),
    re.compile(r"^\s*import\s+.+\s+from\s+['\"].+['\"]\s*;?\s*$", re.MULTILINE),
    re.compile(r"^\s*(export\s+)?function\s+\w+\s*\(", re.MULTILINE),
    re.compile(r"^\s*console\.(log|error|warn)\(", re.MULTILINE),
    re.compile(r"=>\s*\{", re.MULTILINE),
)
_LANGUAGE_MISMATCH_MIN_SIGNALS = 2


def _reuse_id(sandbox_id: str | None) -> str | None:
    """Blank means "no sandbox", not "a sandbox named blank".

    `sandbox_id` is optional, but models routinely fill an optional string
    parameter with `""` instead of omitting it. Passing that through makes
    the manager look up an id that can never exist, so a first-ever
    `run_code` fails with `UnknownSandboxError` and the guidance text tells
    the model to do what it already did.
    """
    if sandbox_id is None:
        return None
    stripped = sandbox_id.strip()
    return stripped or None


def _resolve_timeout(timeout: Any, default: float, cap: float | None) -> tuple[float, float, bool]:
    """`(requested, effective, capped)` for the model's `timeout` argument.

    Missing, non-numeric or NaN → `default`; zero or negative → 1 s; above
    `cap` → `cap`. The cap holds for every backend because it is applied
    here, before the backend sees the request.
    """
    try:
        requested = float(default if timeout is None else timeout)
    except (TypeError, ValueError):
        requested = float(default)
    if math.isnan(requested):
        requested = float(default)
    if requested <= 0:
        requested = 1.0
    if cap is not None and requested > cap:
        return requested, cap, True
    return requested, requested, False


def _at_cap_suggestion(cap: float) -> str:
    return (
        f"The run hit the maximum run time of {cap:g}s. A larger timeout will not be "
        "accepted: make the code faster, process less data per run, or split the work "
        "across several runs (files written to the sandbox persist between runs with "
        "the same sandbox_id)."
    )


_STOPPED_ERROR = (
    "Stopped by the user before this run finished. The run was ended; files it "
    "was writing may be incomplete."
)
_STOPPED_BEFORE_START_ERROR = "Stopped by the user before this call started. Nothing ran."


def stopped_output(*, started: bool = True) -> ToolOutput:
    """The result of a sandbox call the user stopped mid-way. The run then
    ends as cancelled at its next turn, so the model does not act on it in
    this run; the wording is safe for it to read in a later turn.
    `started` is False when the stop came while the call was still waiting
    for a sandbox or a slot, so nothing ran."""
    return ToolOutput(
        success=False,
        error=_STOPPED_ERROR if started else _STOPPED_BEFORE_START_ERROR,
        end_fields={"reason": "stopped", "error_class": "cancelled"},
    )


def _run_outcome(result: CodeResult) -> str:
    """The `outcome=` value of the `run_code finished` log line."""
    if result.success:
        return "ok"
    category = result.error_analysis.category if result.error_analysis else None
    if category == ErrorCategory.TIMEOUT:
        return "timeout"
    if category == ErrorCategory.OUT_OF_MEMORY:
        return "out_of_memory"
    return "error"


def _log_run_finished(
    outcome: str, *, exit_code: int, duration_ms: float, timeout_s: float,
    timeout_capped: bool, backend: Any,
) -> None:
    """One line per `run_code` call, whatever its outcome: the record that
    run-outcome counts (timeouts, out-of-memory kills, stops) are read from."""
    _logger.info(
        "run_code finished: outcome=%s exit_code=%d duration_ms=%.0f timeout_s=%g "
        "timeout_capped=%s backend=%s",
        outcome, exit_code, duration_ms, timeout_s, timeout_capped,
        "-" if backend is None else getattr(backend, "backend_name", type(backend).__name__),
    )


def unknown_sandbox_guidance(error: Exception) -> str:
    """Turn a bare `UnknownSandboxError` into something the model can act on.

    A sandbox lives only for the request that created it, so any id carried
    over from an earlier turn is always dead. Worse, ids appear inside
    filenames the model can see later, so it guesses one out of a filename
    and burns a turn on a call that could never succeed. Saying so costs
    one sentence and saves the retry.
    """
    return (
        f"{error}. Sandboxes exist only for the turn that created them, so a "
        "sandbox_id from an earlier turn or one inferred from a filename is never "
        "valid. Do not retry with another id: start a fresh sandbox by calling "
        "run_code with no sandbox_id, and re-stage any file you need as an input "
        "artifact rather than reading it out of a previous sandbox."
    )


def sandbox_limit_guidance(error: Exception, *, reuse_helps: bool = True) -> str:
    """Turn a capacity denial into a retryable instruction.

    Capacity is transient — another request finishing frees a slot — so the
    model should wait and retry rather than treat this as a permanent
    failure and abandon the task. `reuse_helps` is True when the denial was
    for a new sandbox (a backend that keeps its slot for life, or the
    sandbox count): an existing sandbox then avoids it. It is False when
    every call takes a slot while it runs, reused sandbox or not.
    """
    if reuse_helps:
        return (
            f"{error}. This is a temporary capacity limit, not a problem with your "
            "code. Reuse an existing sandbox_id from earlier in this turn if you "
            "have one, or retry this call shortly."
        )
    return (
        f"{error}. This is a temporary capacity limit, not a problem with your "
        "code. Every call needs a free slot while it runs, even on an existing "
        "sandbox, so retry this call shortly and run one call at a time."
    )


def detect_language_mismatch(code: str, declared: str) -> str | None:
    """High-confidence-only language detection: returns the corrected
    language string when `code` clearly matches the OTHER language's
    syntax and shows none of `declared`'s own signals, `None` otherwise
    (including any ambiguous case — a false "no mismatch" costs nothing
    beyond the status quo, while a false correction would run the model's
    actual code as the wrong language)."""
    python_score = sum(1 for pattern in _PYTHON_SIGNALS if pattern.search(code))
    ts_score = sum(1 for pattern in _TYPESCRIPT_SIGNALS if pattern.search(code))

    if declared == "typescript" and python_score >= _LANGUAGE_MISMATCH_MIN_SIGNALS and ts_score == 0:
        return "python"
    if declared == "python" and ts_score >= _LANGUAGE_MISMATCH_MIN_SIGNALS and python_score == 0:
        return "typescript"
    return None


_NO_CREDENTIALS_NOTE = (
    "This sandbox has NO credentials of any kind and cannot reach any connected "
    "or authenticated system — no Jira, Slack, Confluence, Google, internal "
    "knowledge base, database, or any other service the calling agent may have "
    "access to. Never write code that tries to log in, authenticate, or call a "
    "private/internal API. If you are missing data you need, say so in your "
    "final answer and ask the caller to provide it (or check the input file "
    "noted below) — never fabricate placeholder data and never attempt to fetch "
    "it yourself with credentials you don't have."
)

def _web_tools_md(names: tuple[str, ...]) -> str:
    return "/".join(f"`{n}`" for n in names)


def _no_network_note(web_tool_names: tuple[str, ...]) -> str:
    """`web_tool_names` is whichever web-fetching tool(s) are ACTUALLY
    granted alongside this one this turn — see `set_web_tool_names()`.
    Naming a tool that was never granted is a dead end the model cannot
    resolve, so the fetch-first escape hatch below is only offered when
    there is a real tool to name."""
    if web_tool_names:
        escape = (
            " If a task needs live/external data (a REST API, a webpage, search "
            f"results, ...), fetch that data FIRST with {_web_tools_md(web_tool_names)} "
            "(which do have network access), then pass the already-fetched data into "
            "this tool's `code` as a literal — this tool's job is local computation "
            "and file generation from data you already have, never data retrieval."
        )
    else:
        escape = (
            " This tool cannot retrieve external data itself — any live/external "
            "data a task needs must already be available in this conversation "
            "before you pass it into this tool's `code` as a literal."
        )
    return (
        "IMPORTANT — this sandbox has NO network access, ever, by design: code that "
        "tries to reach any external host (HTTP requests, API calls, DNS, sockets — "
        "via `requests`/`fetch`/`urllib`/`axios`/anything) will fail or hang, and no "
        "package can change this. Never write code that calls an external API or "
        "fetches a URL." + escape + "\n\n" + _NO_CREDENTIALS_NOTE
    )


def _network_note(web_tool_names: tuple[str, ...]) -> str:
    if web_tool_names:
        joined = _web_tools_md(web_tool_names)
        prefer = (
            f" PREFER this over {joined} whenever a well-known, unauthenticated "
            "public REST API serves the data the query needs — API responses are "
            f"current, while {joined} only surfaces point-in-time snapshots of "
            "articles about the topic. Still better for discovery/research "
            "questions with no single authoritative source, or for reading one "
            "specific already-known page."
        )
    else:
        prefer = ""
    return (
        "This sandbox CAN reach the network, but ONLY public, unauthenticated "
        "HTTP/HTTPS APIs — code may call them directly (e.g. with `requests`/"
        "`httpx` in Python, `fetch`/`axios` in TypeScript) to pull live data and "
        "analyze it in the same program." + prefer + " Internal/private hosts "
        "(VPN-only services, org-internal APIs, cloud metadata endpoints) are not "
        "reachable and must not be targeted.\n\n" + _NO_CREDENTIALS_NOTE
    )


# Library default: assume the caller also granted the flat `web_search`/
# `fetch_url` pair unless told otherwise via `set_web_tool_names()` — matches
# this tool's pre-existing behavior for callers that never call the setter.
_DEFAULT_WEB_TOOL_NAMES: tuple[str, ...] = ("web_search", "fetch_url")

# `True` (default) branch: shown ONLY when this exact tool instance is
# reachable through a `share_parent_results=True` `AgentTool.handle()` (see
# `coordination/agent_tool.py`) — i.e. a delegated child, not a flat
# top-level grant. Directive rather than suggestive ("ALWAYS guard", not
# "check before assuming") because even on this path the file can
# legitimately be absent (the parent shared nothing this leg) — a model that
# merely "checks before assuming it is missing" still, empirically, skips
# the check and crashes on an unguarded `open()`.
_PARENT_RESULTS_NOTE = (
    "If your task depends on data the calling agent already fetched, this "
    f"sandbox MAY have it pre-loaded at `{PARENT_RESULTS_INPUT_PATH}` the moment "
    "you create your first sandbox — but only when the caller actually shared "
    "something; it is NOT guaranteed to exist. ALWAYS guard this read (e.g. "
    "`os.path.exists(...)` or a try/except `FileNotFoundError`) rather than "
    "opening it unconditionally — a missing file simply means no such data "
    "was shared this run, not an error. "
)

# `False` branch: shown when this exact tool instance is granted directly to
# a flat, non-delegated top-level agent (e.g. quick mode — see
# `factory.py`'s `set_advertise_parent_results` call site) — the
# parent-results handoff mechanism never runs for that grant, so promising
# the file would be actively false, not merely optimistic.
_NO_PARENT_RESULTS_NOTE = (
    f"This sandbox never has `{PARENT_RESULTS_INPUT_PATH}` or any other "
    "automatic pre-loading of data the calling agent already fetched — do "
    "NOT write code that opens that path; it will never exist here. If your "
    "task needs data gathered elsewhere in this conversation, embed it "
    "directly as a literal in `code`. "
)


async def _upload_staged_files(backend: Any) -> list[str]:
    """Upload every currently-staged input file and skill resource.

    Called on EVERY `run_code`/`install_packages` call, fresh sandbox or
    reused — not just at sandbox creation. Two independent reasons feed
    into `peek_staged_input_files()`, each with its own re-upload story:

    - Parent->child handoff (`AgentTool.handle()`) and skill resources
      (`load_skill`) are staged for the FULL span of a `with
      stage_input_files(...):`-wrapped child run / the rest of the agent
      run respectively — re-uploading the same bytes to the same path on
      every call (fresh or reused) is a harmless, idempotent no-op cost,
      not a correctness issue.
    - A PipesHub `input_artifacts` ref (`coding_sandbox_artifact_staging`
      in `sandbox_bridge.py`) is resolved fresh PER CALL via
      `set_staged_input_files_for_task()` — a bare, task-local set, not
      the `with`-block above — precisely because the model can (and does)
      pass `input_artifacts` alongside an explicit `sandbox_id` to reuse
      an existing sandbox. That staged content only exists for the
      duration of THIS call's task, so it MUST be uploaded here
      regardless of `is_fresh_sandbox`, or those bytes are resolved by
      the PRE hook and then silently dropped on the floor.

    Also covers the pre-existing case this replaces: a child that
    pre-warms with `install_packages` first, then reuses that
    `sandbox_id` for every `run_code`, still gets
    `input/parent_tool_results.json` — that promise ("pre-loaded as soon
    as you create a sandbox") no longer depends on which call happens to
    be the first to see `is_fresh_sandbox=True`."""
    staged_input = peek_staged_input_files()
    staged_skill = peek_staged_skill_resources()
    _logger.info(
        "_upload_staged_files: staged_input_files=%d (%s) staged_skill_resources=%d (%s) "
        "backend=%s",
        len(staged_input) if staged_input else 0,
        sorted(staged_input.keys()) if staged_input else [],
        len(staged_skill) if staged_skill else 0,
        sorted(staged_skill.keys()) if staged_skill else [],
        type(backend).__name__,
    )
    to_upload: dict[str, bytes] = {}
    to_upload.update(staged_input or {})
    to_upload.update(staged_skill or {})
    uploaded: list[str] = []
    for rel_path, content in to_upload.items():
        _logger.info(
            "_upload_staged_files: uploading %s (%d bytes) to %s",
            rel_path, len(content), type(backend).__name__,
        )
        await backend.upload_file(rel_path, content)
        uploaded.append(rel_path)
    if not uploaded:
        _logger.info("_upload_staged_files: nothing to upload (no staged files in context)")
    return uploaded


_READ_MAX_BYTES = 1_000_000


def _clamp_read_bytes(value: Any) -> int:
    """`max_bytes` from the model, held to 0..`_READ_MAX_BYTES`; anything
    that is not a number gets the maximum."""
    try:
        n = int(value)
    except (TypeError, ValueError, OverflowError):
        return _READ_MAX_BYTES
    return min(max(n, 0), _READ_MAX_BYTES)


async def _read_head(backend: Any, path: str, max_bytes: int) -> tuple[bytes, bool]:
    """At most `max_bytes` of `path`, and whether the file has more."""
    if isinstance(backend, CodingSandboxBackend):
        return await backend.read_file_head(path, max_bytes)
    content = await backend.download_file(path)
    return content[:max_bytes], len(content) > max_bytes


class CodingSandboxTool(Tool):
    """`run_code` — write and run a standalone TypeScript/Python program."""

    def __init__(
        self,
        manager: SandboxManager,
        *,
        default_timeout: float = 30.0,
        artifact_output_dir: str | None = None,
        allow_network: bool = False,
        advertise_parent_results: bool = True,
    ) -> None:
        self._manager = manager
        self._default_timeout = default_timeout
        self._artifact_output_dir = artifact_output_dir
        self._allow_network = allow_network
        self._advertise_parent_results = advertise_parent_results
        self._web_tool_names: tuple[str, ...] = _DEFAULT_WEB_TOOL_NAMES

    def set_web_tool_names(self, names: tuple[str, ...]) -> None:
        """Flip which web-fetching tool(s) `description` names as the
        fetch-first / prefer-over-search escape hatch, e.g. `("web_search",
        "fetch_url")` flat, `("web_agent",)` when composed, or `()` when
        this agent has no web access at all this turn.

        Same post-construction-setter reasoning as `set_advertise_parent_
        results`: this tool is registered before the caller (`factory.py`)
        knows the granted tool surface for this request. Left at the
        library default (`_DEFAULT_WEB_TOOL_NAMES`) when never called."""
        self._web_tool_names = names

    def set_advertise_parent_results(self, advertise: bool) -> None:
        """Flip whether `description` promises `PARENT_RESULTS_INPUT_PATH`.

        Must be a post-construction setter, not a constructor-only flag:
        this tool is registered onto the shared per-request `ToolRegistry`
        BEFORE the caller (`factory.py`) knows whether domain-agent
        composition will end up wrapping it in a `share_parent_results=True`
        `AgentTool` (a delegated child, where the promise holds) or granting
        it directly to a flat top-level agent (quick mode, or the
        composition kill-switch — where it never will). The registry holds
        one instance per request, so mutating it in place here is safe and
        does not affect any other request."""
        self._advertise_parent_results = advertise

    @property
    def app_name(self) -> str:
        return "coding_sandbox"

    @property
    def name(self) -> str:
        return "run_code"

    @property
    def display_name(self) -> str | None:
        return "Ran code"

    @property
    def short_description(self) -> str:
        return "Write and run a standalone TypeScript or Python program in an isolated sandbox."

    @property
    def description(self) -> str:
        return (
            "Write and run a standalone TypeScript or Python program, with npm/pip package "
            "management, in a kernel-confined sandbox. Prefer TypeScript for all code "
            "generation unless a Python library would produce significantly better results "
            "(e.g. data science with numpy/pandas, ML with torch/sklearn, image processing "
            "with Pillow, scientific computing with scipy) — when Python is chosen, briefly "
            "note why. TypeScript has no top-level await: put async code in an "
            "`async function main()` and call `main()`. Failures (syntax/type/runtime errors, timeouts) are returned as "
            "structured data with a category and suggestion for you to fix and retry — they "
            "are not exceptions. $OUTPUT_DIR is always set: to produce a downloadable file "
            "(PDF, chart, spreadsheet, image, document, ...), write ONLY that final file to "
            "$OUTPUT_DIR (e.g. os.path.join(os.environ['OUTPUT_DIR'], 'report.pdf')) — those "
            "are captured, returned in the result's `artifacts` list, and delivered to the "
            "user automatically; do NOT print file paths or download links in your answer. "
            "Write intermediates (page renders, scratch data, anything the program itself "
            "doesn't consider the deliverable) anywhere OUTSIDE $OUTPUT_DIR — those stay "
            "sandbox-local and are never shown to the user. Pass the returned sandbox_id on a later "
            "call to reuse the same environment: installed packages and files persist "
            "across calls, but variables/imports do NOT (each call is a fresh process). "
            + (_PARENT_RESULTS_NOTE if self._advertise_parent_results else _NO_PARENT_RESULTS_NOTE)
            + "Use this tool to run generated programs; use execute_code only when your code "
            "needs to call OTHER agent tools programmatically.\n\n"
            + (
                _network_note(self._web_tool_names)
                if self._allow_network
                else _no_network_note(self._web_tool_names)
            )
        )

    @property
    def path(self) -> str:
        return "/toolsets/coding_sandbox/run_code"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "high"), Tag("category", "execute")]

    @property
    def parameters(self) -> list[ToolParameter]:
        cap = self._manager.run_timeout_cap(SandboxType.CODING)
        timeout_description = "Maximum seconds to let the program run before it is killed"
        if cap is not None:
            timeout_description += (
                f" (default {self._default_timeout:g}, at most {cap:g}; "
                f"larger values are lowered to {cap:g})"
            )
        return [
            ToolParameter(
                name="code", type=ParameterType.STRING, required=True,
                description="The full program source code to run.",
            ),
            ToolParameter(
                name="language", type=ParameterType.STRING, required=False, default="typescript",
                enum=_LANGUAGE_ENUM,
                description="Language to run the code as. Prefer 'typescript' unless a Python library is clearly the better tool for the job.",
            ),
            ToolParameter(
                name="packages", type=ParameterType.ARRAY, required=False, default=None,
                items={"type": "string"},
                description="npm (typescript) or PyPI (python) package names to ensure installed before running. Already-installed packages are skipped.",
            ),
            ToolParameter(
                name="sandbox_id", type=ParameterType.STRING, required=False, default=None,
                description="Sandbox to reuse, from a previous run_code/install_packages call's result. Omit to create a fresh sandbox.",
            ),
            ToolParameter(
                name="timeout", type=ParameterType.FLOAT, required=False, default=self._default_timeout,
                description=timeout_description + ".",
            ),
        ]

    def result_failed(self, args: dict[str, Any], result: ToolResult) -> bool:
        return isinstance(result.content, dict) and result.content.get("exit_code", 0) != 0

    async def execute(
        self,
        code: str,
        language: str = "typescript",
        packages: list[str] | None = None,
        sandbox_id: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        sandbox_id = _reuse_id(sandbox_id)
        is_fresh_sandbox = sandbox_id is None
        _logger.info(
            "CodingSandboxTool.execute: language=%s packages=%s sandbox_id=%s "
            "is_fresh=%s allow_network=%s code_len=%d",
            language, packages, sandbox_id, is_fresh_sandbox,
            self._allow_network, len(code),
        )
        # Raced against Stop: for a metered backend this is where the slot
        # wait and the VM creation happen.
        try:
            resolved_id, backend = await self._manager.unless_stopped(
                lambda: self._manager.get_or_create(SandboxType.CODING, sandbox_id, tool=self.name),
                tool=self.name,
            )
        except UnknownSandboxError as e:
            _logger.error("CodingSandboxTool.execute: unknown sandbox %s: %s", sandbox_id, e)
            return ToolOutput(success=False, error=unknown_sandbox_guidance(e))
        except SandboxLimitExceeded as e:
            _logger.warning("CodingSandboxTool.execute: capacity denied: %s", e)
            return ToolOutput(success=False, error=sandbox_limit_guidance(e))
        except SandboxCallStopped:
            _log_run_finished(
                "stopped", exit_code=-1, duration_ms=0.0, timeout_s=0.0,
                timeout_capped=False, backend=None,
            )
            return stopped_output(started=False)

        _logger.info(
            "CodingSandboxTool.execute: resolved sandbox_id=%s backend=%s "
            "working_dir=%s",
            resolved_id, type(backend).__name__,
            getattr(backend, "working_dir", "N/A"),
        )

        # Uploaded on EVERY call, fresh or reused — see `_upload_staged_files`
        # for why a reused sandbox can still have per-call staged bytes
        # (a PipesHub `input_artifacts` ref resolved fresh for THIS call).
        uploaded_inputs: list[str] = await _upload_staged_files(backend)

        language_correction: str | None = None
        detected = detect_language_mismatch(code, language)
        if detected is not None:
            language_correction = (
                f"Note: requested language={language!r}, but the code is unambiguously "
                f"{detected} — ran it as {detected} instead of failing outright."
            )
            _logger.info("run_code: corrected declared language %r -> %r", language, detected)
            language = detected

        cap = self._manager.run_timeout_cap(SandboxType.CODING)
        requested_timeout, resolved_timeout, timeout_capped = _resolve_timeout(
            timeout, self._default_timeout, cap,
        )
        request = CodeRequest(
            code=code,
            language=language,
            timeout=resolved_timeout,
            packages=packages or [],
            allow_network=self._allow_network,
        )
        ran = False

        async def _run() -> CodeResult:
            nonlocal ran
            async with self._manager.slot(SandboxType.CODING, resolved_id, tool=self.name):
                ran = True
                return await backend.execute(request)

        started = time.monotonic()
        try:
            # Raced against Stop: covers the slot wait and the run itself.
            result = await self._manager.unless_stopped(_run, tool=self.name)
        except SandboxLimitExceeded as e:
            _logger.warning("CodingSandboxTool.execute: no free slot: %s", e)
            await _drop_unused_sandbox(self._manager, resolved_id, is_fresh_sandbox)
            return ToolOutput(success=False, error=sandbox_limit_guidance(e, reuse_helps=False))
        except SandboxCallStopped:
            _log_run_finished(
                "stopped", exit_code=-1, duration_ms=(time.monotonic() - started) * 1000,
                timeout_s=request.timeout, timeout_capped=timeout_capped, backend=backend,
            )
            return stopped_output(started=ran)
        except asyncio.CancelledError:
            _log_run_finished(
                "cancelled", exit_code=-1, duration_ms=(time.monotonic() - started) * 1000,
                timeout_s=request.timeout, timeout_capped=timeout_capped, backend=backend,
            )
            raise
        _log_run_finished(
            _run_outcome(result), exit_code=result.exit_code, duration_ms=result.duration_ms,
            timeout_s=request.timeout, timeout_capped=timeout_capped, backend=backend,
        )
        analysis = result.error_analysis
        if (
            cap is not None and resolved_timeout >= cap and result.exit_code == -1
            and analysis is not None and analysis.category == ErrorCategory.TIMEOUT
        ):
            # A wall-clock timeout at the cap (exit -1). The local backend's
            # CPU-time kill is also TIMEOUT but has its own suggestion. A
            # larger timeout is refused, so the same call cannot succeed.
            result = result.model_copy(update={
                "error_analysis": analysis.model_copy(
                    update={"suggestion": _at_cap_suggestion(cap), "is_retryable": False},
                ),
            })
        _logger.info(
            "CodingSandboxTool.execute: result artifacts=%s stderr_len=%d",
            result.artifacts,
            len(result.stderr) if result.stderr else 0,
        )

        data = {**result.model_dump(), "sandbox_id": resolved_id}
        if uploaded_inputs:
            data["input_files"] = sorted(uploaded_inputs)
        if language_correction is not None:
            data["language_correction"] = language_correction
        if timeout_capped:
            data["timeout_note"] = (
                f"Requested timeout of {requested_timeout:g}s is above this deployment's "
                f"maximum of {cap:g}s; the run was given {cap:g}s."
            )

        if self._artifact_output_dir and result.artifacts:
            saved = await _save_artifacts(backend, resolved_id, result.artifacts, self._artifact_output_dir)
            data["saved_artifacts"] = saved

        return ToolOutput(success=True, data=data)


class InstallPackagesTool(Tool):
    """`install_packages` — explicit package management for a coding sandbox."""

    def __init__(self, manager: SandboxManager) -> None:
        self._manager = manager

    @property
    def app_name(self) -> str:
        return "coding_sandbox"

    @property
    def name(self) -> str:
        return "install_packages"

    @property
    def display_name(self) -> str | None:
        return "Installed packages"

    @property
    def short_description(self) -> str:
        return "Install npm or PyPI packages into a coding sandbox ahead of running code."

    @property
    def description(self) -> str:
        return (
            "Install npm (typescript) or PyPI (python) packages into a coding sandbox. "
            "Usually unnecessary — run_code's own 'packages' argument installs on demand — "
            "but useful to pre-warm an environment or see full installer output after a "
            "failed install. Returns the sandbox_id to reuse on subsequent run_code/"
            "install_packages calls."
        )

    @property
    def path(self) -> str:
        return "/toolsets/coding_sandbox/install_packages"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "medium"), Tag("category", "execute")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="packages", type=ParameterType.ARRAY, required=True,
                items={"type": "string"},
                description="npm or PyPI package names, optionally with a version (e.g. 'lodash@4.17.21', 'numpy==1.26.0').",
            ),
            ToolParameter(
                name="language", type=ParameterType.STRING, required=False, default="typescript",
                enum=_LANGUAGE_ENUM,
                description="Which package ecosystem to install into.",
            ),
            ToolParameter(
                name="sandbox_id", type=ParameterType.STRING, required=False, default=None,
                description="Sandbox to reuse. Omit to create a fresh sandbox.",
            ),
        ]

    def result_failed(self, args: dict[str, Any], result: ToolResult) -> bool:
        return isinstance(result.content, dict) and result.content.get("success") is False

    async def execute(
        self,
        packages: list[str],
        language: str = "typescript",
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        sandbox_id = _reuse_id(sandbox_id)
        is_fresh_sandbox = sandbox_id is None
        try:
            resolved_id, backend = await self._manager.unless_stopped(
                lambda: self._manager.get_or_create(SandboxType.CODING, sandbox_id, tool=self.name),
                tool=self.name,
            )
        except UnknownSandboxError as e:
            return ToolOutput(success=False, error=unknown_sandbox_guidance(e))
        except SandboxLimitExceeded as e:
            return ToolOutput(success=False, error=sandbox_limit_guidance(e))
        except SandboxCallStopped:
            return stopped_output(started=False)

        # Same staged-input upload `run_code` does on a fresh sandbox —
        # a child that pre-warms its environment with install_packages
        # FIRST creates its sandbox HERE, and every later run_code call
        # reuses it (non-fresh, upload skipped), so skipping the upload
        # here means `input/parent_tool_results.json` never arrives at
        # all despite the child's goal promising it. See
        # `_upload_staged_files`'s docstring.
        uploaded_inputs: list[str] = []
        if is_fresh_sandbox:
            uploaded_inputs = await _upload_staged_files(backend)

        ran = False

        async def _install() -> Any:
            nonlocal ran
            async with self._manager.slot(SandboxType.CODING, resolved_id, tool=self.name):
                ran = True
                return await backend.install_packages(packages, language)

        try:
            result = await self._manager.unless_stopped(_install, tool=self.name)
        except SandboxLimitExceeded as e:
            await _drop_unused_sandbox(self._manager, resolved_id, is_fresh_sandbox)
            return ToolOutput(success=False, error=sandbox_limit_guidance(e, reuse_helps=False))
        except SandboxCallStopped:
            return stopped_output(started=ran)
        data = {**result.model_dump(), "sandbox_id": resolved_id}
        if uploaded_inputs:
            data["input_files"] = sorted(uploaded_inputs)
        return ToolOutput(success=True, data=data)


async def _drop_unused_sandbox(manager: SandboxManager, sandbox_id: str, fresh: bool) -> None:
    """Tear down a sandbox this call created but could not run on (no free
    slot). Its id is not returned to the model, so nothing could reuse it,
    and it would hold one of this request's sandboxes until the request ends."""
    if fresh:
        await manager.destroy(SandboxType.CODING, sandbox_id)


class ReadSandboxFileTool(Tool):
    """`read_sandbox_file` — artifact retrieval; without this, `run_code`'s
    `artifacts` list would name files the agent could never actually read."""

    def __init__(self, manager: SandboxManager) -> None:
        self._manager = manager

    @property
    def app_name(self) -> str:
        return "coding_sandbox"

    @property
    def name(self) -> str:
        return "read_sandbox_file"

    @property
    def display_name(self) -> str | None:
        return "Read sandbox file"

    @property
    def short_description(self) -> str:
        return "Read a file (e.g. a run_code artifact) from a coding sandbox."

    @property
    def description(self) -> str:
        return (
            "Read a file from a coding sandbox's working directory — e.g. one of the "
            "artifact paths returned by run_code. Pass the path exactly as run_code "
            "reported it (deliverables are 'output/<name>'); '$OUTPUT_DIR/<name>' is "
            "accepted too, though nothing shell-expands a tool argument. Text files are "
            "returned as UTF-8; binary files are base64-encoded. Paths must stay within "
            "the sandbox — '..' traversal outside it is rejected."
        )

    @property
    def path(self) -> str:
        return "/toolsets/coding_sandbox/read_sandbox_file"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "low"), Tag("category", "read")]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="sandbox_id", type=ParameterType.STRING, required=True,
                description="Sandbox to read from, returned by a previous run_code/install_packages call.",
            ),
            ToolParameter(
                name="path", type=ParameterType.STRING, required=True,
                description=(
                    "Path relative to the sandbox's working directory, e.g. "
                    "'output/report.pdf' as returned by run_code."
                ),
            ),
            ToolParameter(
                name="max_bytes", type=ParameterType.INTEGER, required=False, default=_READ_MAX_BYTES,
                description=f"Maximum number of bytes to read (at most {_READ_MAX_BYTES}).",
            ),
        ]

    async def execute(
        self,
        sandbox_id: str,
        path: str,
        max_bytes: int = _READ_MAX_BYTES,
        **kwargs: Any,
    ) -> ToolOutput:
        max_bytes = _clamp_read_bytes(max_bytes)
        try:
            backend = self._manager.get(SandboxType.CODING, sandbox_id)
        except UnknownSandboxError as e:
            return ToolOutput(success=False, error=unknown_sandbox_guidance(e))
        except SandboxLimitExceeded as e:
            return ToolOutput(success=False, error=sandbox_limit_guidance(e))

        try:
            # Reads only `max_bytes` (plus one) where the backend can, so a
            # large file is never read whole to return its first bytes.
            content, truncated = await _read_head(backend, path, max_bytes)
        except ValueError as e:
            # Path traversal — a rejected request, not an infra failure.
            return ToolOutput(success=True, data={"error": str(e)})
        except (FileNotFoundError, IsADirectoryError) as e:
            return ToolOutput(success=True, data={"error": str(e)})

        try:
            return ToolOutput(success=True, data={
                "content": content.decode("utf-8"), "encoding": "utf-8", "truncated": truncated,
            })
        except UnicodeDecodeError:
            return ToolOutput(success=True, data={
                "content": base64.b64encode(content).decode("ascii"), "encoding": "base64", "truncated": truncated,
            })


async def _save_artifacts(
    backend: Any,
    sandbox_id: str,
    artifact_paths: list[str],
    output_dir: str,
) -> list[dict[str, str]]:
    """Download each artifact from the sandbox and write it to
    `<output_dir>/<sandbox_id>/<relative_path>` on the host filesystem.
    Returns a list of `{"sandbox_path": ..., "local_path": ...}` dicts
    for every file successfully saved; failures are logged and skipped
    (a single broken artifact should never tank the whole tool result)."""
    saved: list[dict[str, str]] = []
    sandbox_dir = os.path.join(output_dir, sandbox_id)
    os.makedirs(sandbox_dir, exist_ok=True)

    for rel_path in artifact_paths:
        try:
            content = await backend.download_file(rel_path)
        except Exception:
            _logger.warning("artifact download failed for %r in sandbox %s", rel_path, sandbox_id, exc_info=True)
            continue

        local_path = os.path.join(sandbox_dir, rel_path)
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, "wb") as f:
            f.write(content)

        saved.append({"sandbox_path": rel_path, "local_path": local_path})

    return saved
