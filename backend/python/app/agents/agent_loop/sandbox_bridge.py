"""agent_loop_lib coding-sandbox adapter layer: composes PipesHub-specific
concerns (artifact upload to blob storage + ArangoDB, the curated package
allowlist, host-path redaction) on top of the generic ``agent_loop_lib``
coding sandbox — entirely through composition. No ``agent_loop_lib`` file is
modified by anything in this module.

Two responsibilities:

1. ``build_coding_sandbox_manager`` / ``register_coding_sandbox_tools`` —
   construct a per-request ``SandboxManager`` wired to the local or Docker
   backend, selected the same way the legacy ``app/sandbox/manager.py``
   stack is (``SANDBOX_MODE``, ``SANDBOX_DOCKER_IMAGE``, ``SANDBOX_EGRESS_NETWORK``,
   ``SANDBOX_PIP_INDEX_URL``, ``SANDBOX_NPM_REGISTRY``), and register the
   three ready-made ``agent_loop_lib`` sandbox tools onto a ``ToolRegistry``.
2. ``register_coding_sandbox_hooks`` — the PRE_TOOL_USE/POST_TOOL_USE
   middleware pair, scoped to ``/toolsets/coding_sandbox/**``:
       - PRE:  the lib's own ``coding_sandbox_safety`` static checks, plus
         PipesHub's curated package allowlist enforcement.
       - POST: fetch artifact bytes from the sandbox (inline, before the
         sandbox can be torn down) and upload them to blob storage +
         ArangoDB as a background task; redact host sandbox paths out of
         stdout/stderr before the model sees them.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.coding_sandbox_safety import (
    coding_sandbox_safety,
)
from app.agent_loop_lib.hooks.middleware.context import (
    ToolCallContext,
    ToolResultContext,
)
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agent_loop_lib.sandbox.manager import (
    SandboxLimits,
    SandboxManager,
    SandboxType,
    UnknownSandboxError,
)
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    CodingSandboxTool,
    InstallPackagesTool,
    ReadSandboxFileTool,
)
from app.config.constants.arangodb import Connectors
from app.models.entities import ArtifactType
from app.sandbox.artifact_upload import MIME_TO_ARTIFACT_TYPE, upload_bytes_artifact
from app.sandbox.manager import get_sandbox_mode
from app.sandbox.models import SandboxLanguage, SandboxMode
from app.sandbox.package_policy import (
    PackageNotAllowedError,
    enforce_package_allowlist,
    get_allowlist,
)
from app.sandbox.redact import redact_sandbox_paths
from app.utils.conversation_tasks import register_task

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = [
    "CODING_SANDBOX_PATH_PATTERN",
    "build_coding_sandbox_manager",
    "register_coding_sandbox_tools",
    "register_coding_sandbox_hooks",
    "sandbox_network_enabled",
]

CODING_SANDBOX_PATH_PATTERN = "/toolsets/coding_sandbox/**"

# Same env vars app/sandbox/docker_executor.py reads — an operator's
# existing Docker-sandbox configuration therefore applies unchanged to the
# agent-loop path, with no separate set of settings to keep in sync.
_ENV_DOCKER_IMAGE = "SANDBOX_DOCKER_IMAGE"
_ENV_EGRESS_NETWORK = "SANDBOX_EGRESS_NETWORK"
_ENV_PIP_INDEX_URL = "SANDBOX_PIP_INDEX_URL"
_ENV_NPM_REGISTRY = "SANDBOX_NPM_REGISTRY"
_ENV_ALLOW_NETWORK = "SANDBOX_ALLOW_NETWORK"

_DEFAULT_DOCKER_IMAGE = "pipeshub/sandbox:latest"
_DEFAULT_EGRESS_NETWORK = "pipeshub_sandbox_egress"
_DEFAULT_PIP_INDEX_URL = "https://pypi.org/simple"
_DEFAULT_NPM_REGISTRY = "https://registry.npmjs.org"

_FALSY_ENV_VALUES = {"0", "false", "no", "off"}


def sandbox_network_enabled() -> bool:
    """Whether `run_code`'s sandbox may reach the network — read once per
    call so tests/operators can flip `SANDBOX_ALLOW_NETWORK` without a
    process restart. Defaults to enabled: writing code that calls a public
    REST API for live data (then analyzing the response in the same
    program) is the whole point of giving the agent this tool alongside
    `web_search`/`fetch_url` — see `factory.py`, which reads this once per
    request and threads the SAME resolved value into the sandbox manager,
    the `run_code` tool, the package-policy deny message, the planner's
    upfront-plan steering, and the system prompt, so every surface the
    model sees agrees on whether network is on."""
    raw = os.environ.get(_ENV_ALLOW_NETWORK)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY_ENV_VALUES

_LANGUAGE_TO_SANDBOX_LANGUAGE: dict[str, SandboxLanguage] = {
    "python": SandboxLanguage.PYTHON,
    "typescript": SandboxLanguage.TYPESCRIPT,
}


def _curated_package_allowlist() -> list[str]:
    """Python + npm allowlists combined, passed into the backend
    constructor as defense-in-depth (`EnvironmentManager`/`DockerCodingSandbox`
    both accept `package_allowlist`) — mirrors the tool-layer + executor-layer
    double validation the legacy stack already does."""
    return sorted(get_allowlist(SandboxLanguage.PYTHON) | get_allowlist(SandboxLanguage.TYPESCRIPT))


def build_coding_sandbox_manager(
    *, max_concurrent: int = 5, max_lifetime_s: float = 1800.0, allow_network: bool | None = None,
) -> SandboxManager:
    """Create a fresh, per-request `SandboxManager` wired to the local or
    Docker coding-sandbox backend, chosen the same way the legacy
    `app/sandbox/manager.py::get_executor()` stack is (`SANDBOX_MODE`).

    `allow_network` defaults to `sandbox_network_enabled()` when omitted —
    callers that already resolved the flag (see `factory.py`) should pass
    it explicitly so it isn't re-read (and can't drift) mid-request."""
    manager = SandboxManager()
    mode = get_sandbox_mode()
    allowlist = _curated_package_allowlist()
    limits = SandboxLimits(max_concurrent=max_concurrent, max_lifetime_s=max_lifetime_s)
    network_enabled = sandbox_network_enabled() if allow_network is None else allow_network

    if mode == SandboxMode.DOCKER:
        image = os.environ.get(_ENV_DOCKER_IMAGE, _DEFAULT_DOCKER_IMAGE)
        egress_network = os.environ.get(_ENV_EGRESS_NETWORK, _DEFAULT_EGRESS_NETWORK)
        pip_index_url = os.environ.get(_ENV_PIP_INDEX_URL, _DEFAULT_PIP_INDEX_URL)
        npm_registry = os.environ.get(_ENV_NPM_REGISTRY, _DEFAULT_NPM_REGISTRY)

        def _make_docker_sandbox() -> DockerCodingSandbox:
            return DockerCodingSandbox(
                image=image,
                egress_network=egress_network,
                pip_index_url=pip_index_url,
                npm_registry=npm_registry,
                package_allowlist=allowlist,
                image_node_modules="/home/sandbox/node_modules",
                allow_network=network_enabled,
            )

        manager.register_backend_factory(SandboxType.CODING, _make_docker_sandbox, limits=limits)
    else:
        def _make_local_sandbox() -> LocalCodingSandbox:
            return LocalCodingSandbox(package_allowlist=allowlist)

        manager.register_backend_factory(SandboxType.CODING, _make_local_sandbox, limits=limits)

    return manager


def register_coding_sandbox_tools(
    tool_registry: "ToolRegistry",
    manager: SandboxManager,
    *,
    default_timeout: float = 30.0,
    allow_network: bool | None = None,
) -> None:
    """Register `run_code`/`install_packages`/`read_sandbox_file` onto
    `tool_registry`. Deliberately does NOT pass `artifact_output_dir` to
    `CodingSandboxTool` — PipesHub's own artifact pipeline (blob storage +
    ArangoDB) is wired separately via the POST_TOOL_USE hook below, not the
    tool's built-in local-disk save path.

    `allow_network` should be the SAME resolved value passed to
    `build_coding_sandbox_manager()` — it only changes `run_code`'s
    advertised `description`/`CodeRequest.allow_network`; the backend
    itself independently enforces its own `allow_network` ceiling."""
    network_enabled = sandbox_network_enabled() if allow_network is None else allow_network
    tool_registry.register_tool(
        CodingSandboxTool(manager, default_timeout=default_timeout, allow_network=network_enabled)
    )
    tool_registry.register_tool(InstallPackagesTool(manager))
    tool_registry.register_tool(ReadSandboxFileTool(manager))


def register_coding_sandbox_hooks(
    hooks: "HookRegistry",
    context: "AgentContext",
    manager: SandboxManager,
    *,
    max_code_size: int = 50_000,
    allow_network: bool | None = None,
) -> None:
    """Wire the coding-sandbox PRE/POST hooks onto `hooks`. Explicit here
    (rather than relying on `ControlPlane.start()`'s auto-add) because the
    agent-loop adapter path builds its own `HookRegistry` directly — see
    `PipesHubAgentFactory._build_hooks`.

    `allow_network` should be the SAME resolved value passed to
    `build_coding_sandbox_manager()`/`register_coding_sandbox_tools()` — it
    only changes the package-policy deny message's wording."""
    network_enabled = sandbox_network_enabled() if allow_network is None else allow_network
    hooks.on(HookEvent.PRE_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_safety(max_code_size=max_code_size),
    )
    hooks.on(HookEvent.PRE_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_package_policy(allow_network=network_enabled),
    )
    hooks.on(HookEvent.POST_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_artifact_bridge(context, manager),
    )


def coding_sandbox_package_policy(*, allow_network: bool = False):
    """PRE_TOOL_USE middleware: enforce PipesHub's curated package allowlist
    (`app/sandbox/package_policy.py`) for `run_code`/`install_packages`
    calls. `ToolCallContext.deny()` only carries a plain-text reason (no
    structured payload reaches the model on a PRE_TOOL_USE denial — see
    `ToolExecutor.call_tool`), so the reason string itself is built to
    contain both the rejected package and the full allowed list, giving the
    LLM everything the legacy `_package_rejection` dict conveyed.

    The allowlist itself is unaffected by `allow_network` — only the deny
    message's closing note changes, since "no package can give this
    sandbox network access" would be actively wrong once the sandbox has
    network access some other way (see `sandbox_network_enabled()`)."""

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        packages = ctx.tool_input.get("packages")
        if packages:
            language_str = ctx.tool_input.get("language") or "typescript"
            sandbox_language = _LANGUAGE_TO_SANDBOX_LANGUAGE.get(language_str)
            if sandbox_language is not None:
                try:
                    enforce_package_allowlist(packages, sandbox_language)
                except PackageNotAllowedError as exc:
                    ctx.metadata["rejected_package"] = exc.package
                    ctx.metadata["allowed_packages"] = exc.allowed
                    network_note = (
                        "Note: this sandbox has network access, but the package "
                        "allowlist still applies regardless — pick an allowed "
                        "package instead of retrying with a different one."
                        if allow_network else
                        "Note: no package can give this sandbox network access — it "
                        "has none, ever, regardless of package. Do not retry with a "
                        "different HTTP/network library. For live or external data, "
                        "call web_search/fetch_url first, then pass the "
                        "already-fetched data into run_code."
                    )
                    ctx.deny(
                        f"Package {exc.package!r} is not on the {exc.language.value} sandbox "
                        f"allowlist and will not be installed. Allowed {exc.language.value} "
                        f"packages: {', '.join(exc.allowed)}. {network_note}"
                    )
                    return
        await next_fn()

    return _middleware


def coding_sandbox_artifact_bridge(context: "AgentContext", manager: SandboxManager):
    """POST_TOOL_USE middleware: redacts host sandbox paths out of
    `run_code`'s stdout/stderr/error_analysis, and — when the result carries
    `artifacts` + `sandbox_id` — fetches the artifact bytes INLINE (before
    this hook returns, so the sandbox can't be destroyed out from under the
    read) and schedules the blob-storage/ArangoDB upload as a background
    conversation task, mirroring the legacy `CodingSandbox._schedule_artifact_upload`.

    That background task's return value is what wires this into the SAME
    `conversation_tasks.await_and_collect_results` -> `_append_task_markers`
    pipeline the legacy path uses (`app/utils/streaming.py`) — it must
    return `{"type": "artifacts", "artifacts": [...]}` (never bare `None`
    on success), or the frontend never receives `::artifact` markers even
    though the files were uploaded successfully."""

    async def _middleware(ctx: ToolResultContext, next_fn) -> None:
        response = ctx.tool_response
        if response.success and isinstance(response.data, dict):
            data = response.data
            if "stdout" in data:
                data["stdout"] = redact_sandbox_paths(data.get("stdout"))
            if "stderr" in data:
                data["stderr"] = redact_sandbox_paths(data.get("stderr"))
            error_analysis = data.get("error_analysis")
            if isinstance(error_analysis, dict):
                for key in ("message", "stack_trace", "suggestion"):
                    if error_analysis.get(key):
                        error_analysis[key] = redact_sandbox_paths(error_analysis[key])

            artifacts = data.get("artifacts")
            sandbox_id = data.get("sandbox_id")
            if artifacts and sandbox_id:
                logger.info(
                    "coding sandbox produced %d artifact(s): %s (tool=%s sandbox=%s)",
                    len(artifacts), artifacts, ctx.tool_path, sandbox_id,
                )
                await _fetch_and_schedule_artifact_upload(
                    context, manager, sandbox_id, artifacts, source_tool=ctx.tool_path,
                )
            elif "artifacts" in data:
                # run_code always carries the key — an empty list means the
                # program wrote no files, the #1 reason "no download card
                # appeared" reports come in. Make it explicit in the logs.
                logger.info(
                    "coding sandbox run produced no artifacts (tool=%s sandbox=%s)",
                    ctx.tool_path, sandbox_id,
                )

        await next_fn()

    return _middleware


async def _fetch_and_schedule_artifact_upload(
    context: "AgentContext",
    manager: SandboxManager,
    sandbox_id: str,
    artifact_paths: list[str],
    *,
    source_tool: str,
) -> None:
    blob_store = context.blob_store
    conversation_id = context.conversation_id
    org_id = context.org_id
    if not (blob_store and conversation_id and org_id):
        logger.warning(
            "coding sandbox artifact upload skipped: blob_store=%s conversation_id=%s org_id=%s",
            bool(blob_store), conversation_id, org_id,
        )
        return

    try:
        backend = manager.get(SandboxType.CODING, sandbox_id)
    except UnknownSandboxError:
        logger.warning("coding sandbox artifact upload skipped: unknown sandbox_id=%s", sandbox_id)
        return

    fetched: list[tuple[str, bytes]] = []
    for rel_path in artifact_paths:
        try:
            content = await backend.download_file(rel_path)
        except Exception:
            logger.warning(
                "artifact download failed for %r in sandbox %s", rel_path, sandbox_id, exc_info=True,
            )
            continue
        fetched.append((rel_path, content))

    if not fetched:
        logger.warning(
            "coding sandbox artifact upload skipped: none of %s could be downloaded from sandbox %s",
            artifact_paths, sandbox_id,
        )
        return

    logger.info(
        "scheduling background upload of %d artifact(s) for conversation %s: %s",
        len(fetched), conversation_id, [p for p, _ in fetched],
    )
    user_id = context.user_id
    graph_provider = context.graph_provider

    async def _upload() -> dict[str, Any] | None:
        uploaded: list[dict[str, Any]] = []
        for rel_path, content in fetched:
            file_name = os.path.basename(rel_path)
            mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            try:
                result_entry = await upload_bytes_artifact(
                    file_name=file_name,
                    file_bytes=content,
                    mime_type=mime_type,
                    blob_store=blob_store,
                    org_id=org_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    graph_provider=graph_provider,
                    connector_name=Connectors.CODING_SANDBOX,
                    source_tool=source_tool,
                )
            except Exception:
                logger.exception("coding sandbox artifact upload failed for %s", rel_path)
                continue
            if result_entry is None:
                # Oversized or save failure — already logged inside
                # upload_bytes_artifact; skip it like the legacy path does.
                continue
            uploaded.append(result_entry)
            await _emit_artifact_event(context, result_entry)

        if not uploaded:
            return None
        # This return value is drained by conversation_tasks.await_and_collect_results
        # and turned into ::artifact markers by streaming.py::_append_task_markers —
        # see this function's docstring.
        return {"type": "artifacts", "artifacts": uploaded}

    task = asyncio.create_task(_upload())
    register_task(conversation_id, task)


async def _emit_artifact_event(context: "AgentContext", result_entry: dict[str, Any]) -> None:
    """Push a live SSE `artifact` event so the frontend can render a
    download card WHILE the turn is still streaming (`streaming.ts`'s
    `onArtifact` handler already exists for exactly this on the frontend,
    it's just never been fed by this path before). This is a nice-to-have,
    additive UX signal — the authoritative, persisted delivery mechanism is
    still the `::artifact` marker `_upload()` returns above, appended into
    the saved answer once the turn completes."""
    if context.event_sink is None:
        return
    download_url = result_entry.get("signedUrl") or result_entry.get("downloadUrl") or ""
    if not download_url:
        return
    mime_type = result_entry.get("mimeType", "application/octet-stream")
    record_id = result_entry.get("recordId")
    try:
        await context.event_sink.write({
            "event": "artifact",
            "data": {
                "artifactId": record_id or result_entry.get("documentId"),
                "fileName": result_entry.get("fileName", "Download"),
                "mimeType": mime_type,
                "sizeBytes": result_entry.get("sizeBytes", 0),
                "downloadUrl": download_url,
                "artifactType": MIME_TO_ARTIFACT_TYPE.get(mime_type, ArtifactType.OTHER).value,
                "isTemporary": False,
                "recordId": record_id,
            },
        })
    except Exception:
        logger.warning(
            "failed to emit live artifact SSE event for %s",
            result_entry.get("fileName"), exc_info=True,
        )
