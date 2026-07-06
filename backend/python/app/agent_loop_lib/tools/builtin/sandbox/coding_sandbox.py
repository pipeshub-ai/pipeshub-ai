from __future__ import annotations

import base64
import logging
import os
from typing import Any

from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.manager import (
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

__all__ = ["CodingSandboxTool", "InstallPackagesTool", "ReadSandboxFileTool"]

_LANGUAGE_ENUM = ["typescript", "python"]
_logger = logging.getLogger(__name__)

_NO_NETWORK_NOTE = (
    "IMPORTANT — this sandbox has NO network access, ever, by design: code that "
    "tries to reach any external host (HTTP requests, API calls, DNS, sockets — "
    "via `requests`/`fetch`/`urllib`/`axios`/anything) will fail or hang, and no "
    "package can change this. Never write code that calls an external API or "
    "fetches a URL. If a task needs live/external data (a REST API, a webpage, "
    "search results, ...), fetch that data FIRST with `web_search` or `fetch_url` "
    "(which do have network access), then pass the already-fetched data into this "
    "tool's `code` as a literal — this tool's job is local computation and file "
    "generation from data you already have, never data retrieval."
)

_NETWORK_NOTE = (
    "This sandbox CAN reach the network: code may call public HTTP/HTTPS APIs "
    "directly (e.g. with `requests`/`httpx` in Python, `fetch`/`axios` in "
    "TypeScript) to pull live data and analyze it in the same program. PREFER "
    "this over `web_search` whenever a well-known, unauthenticated public REST "
    "API serves the data the query needs — API responses are current, while "
    "search results are point-in-time snapshots of articles written about the "
    "topic. `web_search` is still the better choice for discovery/research "
    "questions with no single authoritative API, and `fetch_url` for reading "
    "one specific already-known page. Internal/private hosts (VPN-only "
    "services, org-internal APIs, cloud metadata endpoints) are not reachable "
    "and must not be targeted."
)


class CodingSandboxTool(Tool):
    """`run_code` — write and run a standalone TypeScript/Python program."""

    def __init__(
        self,
        manager: SandboxManager,
        *,
        default_timeout: float = 30.0,
        artifact_output_dir: str | None = None,
        allow_network: bool = False,
    ) -> None:
        self._manager = manager
        self._default_timeout = default_timeout
        self._artifact_output_dir = artifact_output_dir
        self._allow_network = allow_network

    @property
    def app_name(self) -> str:
        return "coding_sandbox"

    @property
    def name(self) -> str:
        return "run_code"

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
            "note why. Failures (syntax/type/runtime errors, timeouts) are returned as "
            "structured data with a category and suggestion for you to fix and retry — they "
            "are not exceptions. To produce a downloadable file (PDF, chart, spreadsheet, "
            "image, document, ...), simply write it to the program's working directory (or "
            "to $OUTPUT_DIR if set) — every file the program writes is captured and returned "
            "in the result's `artifacts` list and delivered to the user automatically; do "
            "NOT print file paths in your answer. Pass the returned sandbox_id on a later "
            "call to reuse the same environment: installed packages and files persist "
            "across calls, but variables/imports do NOT (each call is a fresh process). "
            "Use this tool to run generated programs; use execute_code only when your code "
            "needs to call OTHER agent tools programmatically.\n\n"
            + (_NETWORK_NOTE if self._allow_network else _NO_NETWORK_NOTE)
        )

    @property
    def path(self) -> str:
        return "/toolsets/coding_sandbox/run_code"

    @property
    def tags(self) -> list[Tag]:
        return [Tag("risk", "high"), Tag("category", "execute")]

    @property
    def parameters(self) -> list[ToolParameter]:
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
                description="Maximum seconds to let the program run before it is killed.",
            ),
        ]

    async def execute(
        self,
        code: str,
        language: str = "typescript",
        packages: list[str] | None = None,
        sandbox_id: str | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        try:
            resolved_id, backend = await self._manager.get_or_create(SandboxType.CODING, sandbox_id)
        except UnknownSandboxError as e:
            return ToolOutput(success=False, error=str(e))

        resolved_timeout = timeout if timeout is not None else self._default_timeout
        request = CodeRequest(
            code=code,
            language=language,
            timeout=resolved_timeout,
            packages=packages or [],
            allow_network=self._allow_network,
        )
        result = await backend.execute(request)

        data = {**result.model_dump(), "sandbox_id": resolved_id}

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

    async def execute(
        self,
        packages: list[str],
        language: str = "typescript",
        sandbox_id: str | None = None,
        **kwargs: Any,
    ) -> ToolOutput:
        try:
            resolved_id, backend = await self._manager.get_or_create(SandboxType.CODING, sandbox_id)
        except UnknownSandboxError as e:
            return ToolOutput(success=False, error=str(e))

        result = await backend.install_packages(packages, language)
        return ToolOutput(success=True, data={**result.model_dump(), "sandbox_id": resolved_id})


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
    def short_description(self) -> str:
        return "Read a file (e.g. a run_code artifact) from a coding sandbox."

    @property
    def description(self) -> str:
        return (
            "Read a file from a coding sandbox's working directory — e.g. one of the "
            "artifact paths returned by run_code. Text files are returned as UTF-8; binary "
            "files are base64-encoded. Paths must stay within the sandbox — '..' traversal "
            "outside it is rejected."
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
                description="Path relative to the sandbox's working directory.",
            ),
            ToolParameter(
                name="max_bytes", type=ParameterType.INTEGER, required=False, default=1_000_000,
                description="Maximum number of bytes to read.",
            ),
        ]

    async def execute(
        self,
        sandbox_id: str,
        path: str,
        max_bytes: int = 1_000_000,
        **kwargs: Any,
    ) -> ToolOutput:
        try:
            backend = self._manager.get(SandboxType.CODING, sandbox_id)
        except UnknownSandboxError as e:
            return ToolOutput(success=False, error=str(e))

        try:
            content = await backend.download_file(path)
        except ValueError as e:
            # Path traversal — a rejected request, not an infra failure.
            return ToolOutput(success=True, data={"error": str(e)})
        except (FileNotFoundError, IsADirectoryError) as e:
            return ToolOutput(success=True, data={"error": str(e)})

        truncated = len(content) > max_bytes
        content = content[:max_bytes]
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
