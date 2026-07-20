"""agent_loop_lib coding-sandbox adapter layer: composes PipesHub-specific
concerns (versioned artifact registration, code-artifact capture + lineage,
input-artifact staging, the curated package allowlist, host-path redaction)
on top of the generic ``agent_loop_lib`` coding sandbox — entirely through
composition. No ``agent_loop_lib`` file is modified by anything in this
module.

Three responsibilities:

1. ``build_coding_sandbox_manager`` / ``register_coding_sandbox_tools`` —
   construct a per-request ``SandboxManager`` wired to the local or Docker
   backend, selected the same way the legacy ``app/sandbox/manager.py``
   stack is (``SANDBOX_MODE``, ``SANDBOX_DOCKER_IMAGE``, ``SANDBOX_EGRESS_NETWORK``,
   ``SANDBOX_PIP_INDEX_URL``, ``SANDBOX_NPM_REGISTRY``), and register
   ``PipesHubCodingSandboxTool`` (``run_code`` + an ``input_artifacts``
   parameter) plus the other two ready-made ``agent_loop_lib`` sandbox
   tools onto a ``ToolRegistry``.
2. ``register_coding_sandbox_hooks`` — the PRE_TOOL_USE/POST_TOOL_USE
   middleware pair, scoped to ``/toolsets/coding_sandbox/**``:
       - PRE:  the lib's own ``coding_sandbox_safety`` static checks,
         PipesHub's curated package allowlist enforcement, capturing
         ``code`` as a versioned CODE artifact, and resolving+staging any
         ``input_artifacts`` refs into the sandbox filesystem.
       - POST: fetch artifact bytes from the sandbox (inline, before the
         sandbox can be torn down), register them SYNCHRONOUSLY through
         ``ArtifactRegistryService`` (so ``artifact_id``/``version`` are in
         the tool response the model sees THIS turn), record
         ``DERIVED_FROM`` lineage against the code artifact captured in
         PRE, and redact host sandbox paths out of stdout/stderr before
         the model sees them.
       Also registers ``coding_sandbox_result_propagation`` on POST_AGENT
       (event-scoped, no path pattern — it fires once per ``Agent.run()``,
       not per tool call) to copy this run's registered artifacts onto
       ``AgentResult.artifacts`` for a parent/orchestrator to see.
3. ``coding_sandbox_artifact_staging`` composes with
   ``app/services/artifact_registry`` (never touching a signed URL or raw
   bytes at the model-input boundary — see that package's module docstrings)
   and ``app.agent_loop_lib.tools.builtin.sandbox.input_staging`` (the
   existing model-proof parent->child file handoff mechanism, reused here
   unchanged rather than inventing a second staging path).
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import Artifact as LibArtifact
from app.agent_loop_lib.core.types import ArtifactType as LibArtifactType
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.coding_sandbox_safety import (
    coding_sandbox_safety,
)
from app.agent_loop_lib.hooks.middleware.context import (
    AgentLifecycleContext,
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
from app.agent_loop_lib.tools.base import ParameterType, ToolParameter
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import (
    CodingSandboxTool,
    InstallPackagesTool,
    ReadSandboxFileTool,
    detect_language_mismatch,
)
from app.agent_loop_lib.tools.builtin.sandbox.input_staging import (
    set_staged_input_files_for_task,
)
from app.config.constants.arangodb import Connectors
from app.models.entities import ArtifactType
from app.sandbox.artifact_upload import MIME_TO_ARTIFACT_TYPE
from app.sandbox.manager import get_sandbox_mode
from app.sandbox.models import SandboxLanguage, SandboxMode
from app.sandbox.package_policy import (
    PackageNotAllowedError,
    enforce_package_allowlist,
    get_allowlist,
)
from app.sandbox.redact import redact_sandbox_paths
from app.services.artifact_registry import Actor, ArtifactMetadata
from app.services.artifact_registry.access import AccessDeniedError, ArtifactNotFoundError
from app.utils.conversation_tasks import register_task

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.registry import HookRegistry
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = [
    "CODING_SANDBOX_PATH_PATTERN",
    "PipesHubCodingSandboxTool",
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

_CODE_MIME_BY_LANGUAGE = {"python": "text/x-python", "typescript": "application/typescript"}
_CODE_EXT_BY_LANGUAGE = {"python": "py", "typescript": "ts"}

# Per-`RunScope` (NOT the flat, tree-wide `AgentContext.artifacts_registered_
# this_run`) record of artifacts registered during exactly this run — see
# `coding_sandbox_result_propagation`'s docstring for why this must be a
# `StateSlot` rather than the shared context list: concurrent sibling
# `coding_agent` spawns must never see each other's artifacts here.
_REGISTERED_ARTIFACTS_SLOT: StateSlot[list[dict[str, Any]]] = StateSlot(
    key="pipeshub.sandbox_bridge.artifacts_registered", default_factory=list,
)


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
        logger.info(
            "build_coding_sandbox_manager: mode=DOCKER image=%s egress_network=%s "
            "pip_index_url=%s npm_registry=%s network_enabled=%s "
            "allowlist_size=%d",
            image, egress_network, pip_index_url, npm_registry,
            network_enabled, len(allowlist),
        )

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
        logger.info(
            "build_coding_sandbox_manager: mode=LOCAL network_enabled=%s "
            "allowlist_size=%d",
            network_enabled, len(allowlist),
        )

        def _make_local_sandbox() -> LocalCodingSandbox:
            return LocalCodingSandbox(package_allowlist=allowlist)

        manager.register_backend_factory(SandboxType.CODING, _make_local_sandbox, limits=limits)

    return manager


class PipesHubCodingSandboxTool(CodingSandboxTool):
    """`run_code` extended with an `input_artifacts` parameter — declarative
    reuse of previously generated artifacts (a chart from an earlier call,
    a CSV from `save_artifact`, ...) without ever putting a signed URL or
    raw bytes at the model-input boundary. Resolution/permission-check/
    fetch/staging all happen in `coding_sandbox_artifact_staging`'s
    PRE_TOOL_USE hook — this subclass only advertises the parameter so the
    model knows it exists; `execute()` itself is untouched (the extra
    kwarg lands in `**kwargs` and is ignored). The PRE hook re-resolves and
    re-stages `input_artifacts` on EVERY call, fresh sandbox or reused —
    `CodingSandboxTool._upload_staged_files()` runs unconditionally, not
    only at sandbox creation — so passing `input_artifacts` alongside an
    explicit `sandbox_id` works the same as on a fresh sandbox."""

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            *super().parameters,
            ToolParameter(
                name="input_artifacts", type=ParameterType.ARRAY, required=False, default=None,
                items={"type": "string"},
                description=(
                    "Names or artifact IDs of previously generated artifacts from THIS "
                    "conversation (a chart/CSV/document from an earlier run_code call, or "
                    "one saved via artifacts__save_artifact / image generation) to make "
                    "available in this run. Each is staged at input/artifacts/<name> before "
                    "your code runs — read it from there directly; do not regenerate an "
                    "artifact that already exists. Works whether this call creates a fresh "
                    "sandbox or reuses one via sandbox_id — call artifacts__list_artifacts "
                    "first (if available) when unsure of the exact name."
                ),
            ),
        ]

    @property
    def description(self) -> str:
        return (
            super().description
            + "\n\nTo reuse a previously generated artifact (a chart, CSV, or other file "
            "from an earlier call in this conversation) in this run, pass its name in "
            "input_artifacts — it will be staged at input/artifacts/<name>. Do not "
            "regenerate an artifact that already exists. Every file your code writes is "
            "attached to the response automatically as a downloadable artifact — never "
            "re-run code just to attach, verify, or 'provide' files already produced."
        )


def register_coding_sandbox_tools(
    tool_registry: "ToolRegistry",
    manager: SandboxManager,
    *,
    default_timeout: float = 30.0,
    allow_network: bool | None = None,
) -> None:
    """Register `run_code`/`install_packages`/`read_sandbox_file` onto
    `tool_registry`. Deliberately does NOT pass `artifact_output_dir` to
    `PipesHubCodingSandboxTool` — PipesHub's own artifact pipeline (the
    versioned registry) is wired separately via the POST_TOOL_USE hook
    below, not the tool's built-in local-disk save path.

    `allow_network` should be the SAME resolved value passed to
    `build_coding_sandbox_manager()` — it only changes `run_code`'s
    advertised `description`/`CodeRequest.allow_network`; the backend
    itself independently enforces its own `allow_network` ceiling."""
    network_enabled = sandbox_network_enabled() if allow_network is None else allow_network
    tool_registry.register_tool(
        PipesHubCodingSandboxTool(manager, default_timeout=default_timeout, allow_network=network_enabled)
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
    hooks.on(HookEvent.PRE_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_artifact_staging(context),
    )
    hooks.on(HookEvent.POST_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_artifact_bridge(context, manager),
    )
    hooks.on(HookEvent.POST_AGENT).use(coding_sandbox_result_propagation())


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
            # `run_code` may auto-correct a mismatched declared language
            # against the actual code (see `CodingSandboxTool.execute`) —
            # check the allowlist against the language it will ACTUALLY
            # run as, not the (possibly wrong) declared one, so a
            # `reportlab`-with-`language=typescript` call isn't denied
            # for the wrong ecosystem right before the code itself would
            # have been corrected to python.
            code = ctx.tool_input.get("code")
            if isinstance(code, str) and code:
                corrected = detect_language_mismatch(code, language_str)
                if corrected is not None:
                    language_str = corrected
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


def coding_sandbox_result_propagation():
    """POST_AGENT middleware: copies every artifact registered during
    exactly THIS run (tracked in `_REGISTERED_ARTIFACTS_SLOT`, written by
    `coding_sandbox_artifact_bridge` above) onto `AgentResult.artifacts` as
    a proper `agent_loop_lib.core.types.Artifact` — so a parent/orchestrator
    agent that spawned a `coding_agent` child (via `spawn_agent`/`AgentTool`)
    sees exactly the artifacts THAT CHILD produced in its own `AgentResult`,
    without re-querying the registry itself and with zero risk of
    double-counting a concurrently-running sibling's artifacts (the reason
    this is a per-`RunScope` slot, not the flat, tree-wide
    `AgentContext.artifacts_registered_this_run` list — see that field's
    docstring and `StateSlot`'s concurrency contract).

    `content` carries the full compact metadata dict (`artifact_id`, name,
    version, mime_type, ...) rather than a URL — a parent agent that wants
    to reuse the artifact passes its name/id into ITS OWN `run_code` call's
    `input_artifacts`, it never needs a signed URL for this."""

    async def _middleware(ctx: AgentLifecycleContext, next_fn) -> None:
        if ctx.result is not None and ctx.scope is not None:
            for entry in ctx.scope.get(_REGISTERED_ARTIFACTS_SLOT):
                ctx.result.artifacts.append(LibArtifact(
                    name=entry.get("name") or "artifact",
                    type=LibArtifactType.FILE,
                    content=entry,
                    description=entry.get("description") or None,
                ))
        await next_fn()

    return _middleware


def coding_sandbox_artifact_staging(context: "AgentContext"):
    """PRE_TOOL_USE middleware, `run_code` only:

    1. Persists the `code` string as a versioned CODE artifact through
       `ArtifactRegistryService` — hash-deduplicated, so an unchanged
       re-run costs nothing but a lookup. Its `artifact_id`/`version` are
       stashed in `ctx.metadata` for `coding_sandbox_artifact_bridge`'s
       POST hook to link output artifacts to via `DERIVED_FROM`. Identity
       is keyed off `sandbox_id` (stable across calls that reuse the same
       sandbox — i.e. iterating on the same program) when the model passed
       one, else a one-off name (a fresh sandbox has no prior program to
       version against).
    2. Resolves + stages any `input_artifacts` refs into the sandbox
       filesystem via `set_staged_input_files_for_task()` — a bare,
       task-local `ContextVar.set()` rather than `stage_input_files()`'s
       `with` block. That distinction matters here specifically: PRE_TOOL_USE
       middleware's `next_fn()` only advances to the NEXT middleware, never
       into `tool.execute()` (see `ToolExecutor.call_tool()`), so a `with
       stage_input_files(...): await next_fn()` block would unwind and
       reset the var back to `None` before `CodingSandboxTool.execute()`
       ever runs — which is exactly the bug this hook used to have. See
       `set_staged_input_files_for_task`'s docstring for why a bare `.set()`
       is safe here (same-task sequencing with `execute()`, no leakage
       across sibling/later tool calls). Never a signed URL or a
       tool-visible path the model constructs itself. Every ref is
       permission-checked through `ArtifactRegistryService` before its
       bytes are fetched; an unknown or unauthorized ref is reported back
       (not silently dropped) via `ctx.metadata`, surfaced in the tool
       response by the POST hook.
    """

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        registry = context.artifact_registry
        code = ctx.tool_input.get("code")
        if registry is not None and context.conversation_id and isinstance(code, str) and code.strip():
            await _capture_code_artifact(context, registry, ctx)

        refs = ctx.tool_input.get("input_artifacts")
        if not refs or registry is None or not context.conversation_id:
            logger.info(
                "coding_sandbox_artifact_staging: no input_artifacts to stage "
                "(refs=%s registry=%s conversation_id=%s)",
                bool(refs), registry is not None, context.conversation_id,
            )
            # Always call, even with nothing to stage — a falsy `files`
            # is a no-op inside `set_staged_input_files_for_task`, but the
            # call site staying unconditional means this hook never
            # accidentally relies on skipping it for correctness.
            set_staged_input_files_for_task(None)
            await next_fn()
            return

        logger.info(
            "coding_sandbox_artifact_staging: resolving %d input_artifact ref(s): %s",
            len(refs), refs,
        )
        files, resolved, missing = await _resolve_input_artifacts(context, registry, refs)
        logger.info(
            "coding_sandbox_artifact_staging: resolved %d artifact(s) (%s), "
            "missing %d ref(s) (%s), staging %d file(s) totalling %d bytes",
            len(resolved),
            [r["name"] for r in resolved],
            len(missing),
            missing,
            len(files),
            sum(len(v) for v in files.values()),
        )
        if resolved:
            ctx.metadata["staged_input_artifacts"] = resolved
        if missing:
            ctx.metadata["input_artifacts_not_found"] = missing
        set_staged_input_files_for_task(files)
        await next_fn()

    return _middleware


async def _capture_code_artifact(context: "AgentContext", registry: Any, ctx: ToolCallContext) -> None:
    sandbox_id = ctx.tool_input.get("sandbox_id")
    language = ctx.tool_input.get("language") or "typescript"
    ext = _CODE_EXT_BY_LANGUAGE.get(language, "ts")
    logical_name = f"code_{sandbox_id}.{ext}" if sandbox_id else f"code_{ctx.tool_use_id}.{ext}"
    actor = Actor(org_id=context.org_id, user_id=context.user_id)
    try:
        metadata, _version = await registry.register_output(
            actor=actor,
            name=logical_name,
            artifact_type=ArtifactType.CODE,
            mime_type=_CODE_MIME_BY_LANGUAGE.get(language, "text/plain"),
            content=ctx.tool_input["code"].encode("utf-8"),
            conversation_id=context.conversation_id,
            source_tool=ctx.tool_path,
        )
    except Exception:
        logger.warning("Failed to capture code artifact for %s", ctx.tool_path, exc_info=True)
        return
    ctx.metadata["code_artifact_id"] = metadata.artifact_id
    ctx.metadata["code_artifact_version"] = metadata.version


async def _resolve_input_artifacts(
    context: "AgentContext", registry: Any, refs: list[str],
) -> tuple[dict[str, bytes], list[dict[str, Any]], list[str]]:
    """Resolve+fetch every ref in `refs`, permission-checked per-ref through
    the registry. Returns `(sandbox_files, resolved_info, missing_refs)` —
    `sandbox_files` is ready for `stage_input_files()`; `resolved_info` and
    `missing_refs` are model-visible reporting, never raw bytes/URLs."""
    actor = Actor(org_id=context.org_id, user_id=context.user_id)
    files: dict[str, bytes] = {}
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            logger.debug("_resolve_input_artifacts: skipping empty/non-str ref: %r", ref)
            continue
        try:
            metadata: ArtifactMetadata = await registry.resolve(
                actor=actor, ref=ref, conversation_id=context.conversation_id,
            )
            content = await registry.get_content(actor=actor, artifact_id=metadata.artifact_id)
        except (ArtifactNotFoundError, AccessDeniedError) as exc:
            logger.warning(
                "_resolve_input_artifacts: ref %r not found or access denied: %s", ref, exc,
            )
            missing.append(ref)
            continue
        except Exception:
            logger.warning("Failed to stage input artifact %r", ref, exc_info=True)
            missing.append(ref)
            continue
        staged_path = f"input/artifacts/{metadata.name}"
        logger.info(
            "_resolve_input_artifacts: ref %r -> artifact_id=%s name=%s "
            "version=%d content_size=%d staged_path=%s",
            ref, metadata.artifact_id, metadata.name,
            metadata.version, len(content), staged_path,
        )
        files[staged_path] = content
        resolved.append({
            "ref": ref, "artifact_id": metadata.artifact_id, "name": metadata.name,
            "version": metadata.version, "path": staged_path,
        })
    return files, resolved, missing


def coding_sandbox_artifact_bridge(context: "AgentContext", manager: SandboxManager):
    """POST_TOOL_USE middleware: redacts host sandbox paths out of
    `run_code`'s stdout/stderr/error_analysis, and — when the result carries
    `artifacts` + `sandbox_id` — fetches the artifact bytes INLINE (before
    this hook returns, so the sandbox can't be destroyed out from under the
    read) and registers each one SYNCHRONOUSLY through
    `ArtifactRegistryService`, right here in the hook — never as a
    fire-and-forget background task. That is the load-bearing change from
    the legacy `CodingSandbox._schedule_artifact_upload` pipeline: the
    model's OWN tool response now carries `artifact_id`/`name`/`version`
    for every produced file (`data["artifacts"]`, see
    `ArtifactMetadata.to_tool_response`), so a later turn asking "update
    that chart" has a real ID to call `save_artifact`/`run_code`'s
    `input_artifacts` with — it never has to guess a file name back into
    existence from prose.

    Registration is synchronous because it is cheap: bytes are already
    fetched inline for redaction/lineage purposes regardless, and
    `ArtifactRegistryService` enforces the same 25 MiB cap `run_code`'s
    artifact pipeline always has. See `app/services/artifact_registry/`.
    """

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
                # Read by `hooks/completion_gate.py` — a file-generation
                # request is only "done" once this flips true, regardless
                # of which agent in the spawn tree (top-level or a spawned
                # `coding_agent` child) produced the artifact.
                context.artifacts_produced_this_run = True
                _before = len(context.artifacts_registered_this_run)
                await _register_run_code_artifacts(
                    context, manager, sandbox_id, artifacts, data,
                    source_tool=ctx.tool_path,
                    code_artifact_id=ctx.metadata.get("code_artifact_id"),
                    code_artifact_version=ctx.metadata.get("code_artifact_version"),
                )
                if ctx.scope is not None:
                    newly_registered = context.artifacts_registered_this_run[_before:]
                    if newly_registered:
                        ctx.scope.turn.run.get(_REGISTERED_ARTIFACTS_SLOT).extend(newly_registered)
            elif "artifacts" in data:
                # run_code always carries the key — an empty list means the
                # program wrote no files, the #1 reason "no download card
                # appeared" reports come in. Make it explicit in the logs.
                logger.info(
                    "coding sandbox run produced no artifacts (tool=%s sandbox=%s)",
                    ctx.tool_path, sandbox_id,
                )

            staged = ctx.metadata.get("staged_input_artifacts")
            if staged:
                data["input_artifacts"] = staged
            missing = ctx.metadata.get("input_artifacts_not_found")
            if missing:
                data["input_artifacts_not_found"] = missing

        await next_fn()

    return _middleware


async def _register_run_code_artifacts(
    context: "AgentContext",
    manager: SandboxManager,
    sandbox_id: str,
    artifact_paths: list[str],
    data: dict[str, Any],
    *,
    source_tool: str,
    code_artifact_id: str | None,
    code_artifact_version: int | None,
) -> None:
    """Fetch `artifact_paths` from the still-live sandbox and register each
    one through the registry, synchronously, before this POST hook returns.
    Mutates `data["artifacts"]` in place to the model-visible compact block
    (`ArtifactMetadata.to_tool_response`) on success — the ORIGINAL raw
    sandbox-relative path list is only ever consumed here, never returned
    to the model, since a path meaningless outside this sandbox is a worse
    handle than a durable `artifact_id`."""
    registry = context.artifact_registry
    conversation_id = context.conversation_id
    org_id = context.org_id
    if not (registry is not None and conversation_id and org_id):
        logger.warning(
            "coding sandbox artifact registration skipped: registry=%s conversation_id=%s org_id=%s",
            registry is not None, conversation_id, org_id,
        )
        return

    try:
        backend = manager.get(SandboxType.CODING, sandbox_id)
    except UnknownSandboxError:
        logger.warning("coding sandbox artifact registration skipped: unknown sandbox_id=%s", sandbox_id)
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
            "coding sandbox artifact registration skipped: none of %s could be downloaded from sandbox %s",
            artifact_paths, sandbox_id,
        )
        return

    actor = Actor(org_id=org_id, user_id=context.user_id)
    registered: list[ArtifactMetadata] = []
    model_blocks: list[dict[str, Any]] = []
    legacy_entries: list[dict[str, Any]] = []
    for rel_path, content in fetched:
        file_name = os.path.basename(rel_path)
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        try:
            metadata, _version = await registry.register_output(
                actor=actor,
                name=file_name,
                artifact_type=MIME_TO_ARTIFACT_TYPE.get(mime_type, ArtifactType.OTHER),
                mime_type=mime_type,
                content=content,
                conversation_id=conversation_id,
                source_tool=source_tool,
                connector_name=Connectors.CODING_SANDBOX,
            )
        except Exception:
            logger.exception("coding sandbox artifact registration failed for %s", rel_path)
            continue

        if code_artifact_id:
            try:
                await registry.record_derivation(
                    output_artifact_id=metadata.artifact_id,
                    code_artifact_id=code_artifact_id,
                    code_version=code_artifact_version or 1,
                    output_version=metadata.version,
                )
                metadata.derived_from_code_artifact_id = code_artifact_id
                metadata.derived_from_code_version = code_artifact_version
            except Exception:
                logger.warning(
                    "Failed to record lineage for output=%s code=%s",
                    metadata.artifact_id, code_artifact_id, exc_info=True,
                )

        registered.append(metadata)

        # A re-run producing byte-identical content re-registers the same
        # (artifact_id, version) — content-hash dedup means no new version
        # exists, so re-delivering it would just duplicate the download
        # card in the UI (once per re-run). Deliver each version exactly
        # once per request; the model still sees the artifact in its tool
        # response either way, flagged so it knows not to regenerate.
        delivery_key = f"{metadata.artifact_id}:{metadata.version}"
        already_delivered = delivery_key in context.delivered_artifact_versions
        block = metadata.to_tool_response()
        if already_delivered:
            block["already_delivered"] = True
            block["note"] = (
                "This exact version was already attached to the response earlier in "
                "this run — it is downloadable; do NOT regenerate or re-attach it."
            )
        model_blocks.append(block)
        if already_delivered:
            continue
        context.delivered_artifact_versions.add(delivery_key)
        context.artifacts_registered_this_run.append(metadata.model_dump())

        download_url: str | None = None
        try:
            download_url = await registry.get_download_url(actor=actor, artifact_id=metadata.artifact_id)
        except Exception:
            logger.warning("Failed to obtain download URL for artifact %s", metadata.artifact_id, exc_info=True)
        await _emit_artifact_event(context, metadata, download_url)
        legacy_entries.append({
            "documentId": metadata.document_id,
            "fileName": metadata.name,
            "mimeType": metadata.mime_type,
            "sizeBytes": metadata.size_bytes,
            "recordId": metadata.artifact_id,
            "downloadUrl": download_url or "",
            "artifactType": metadata.artifact_type.value,
            "version": metadata.version,
        })

    if not registered:
        return

    logger.info(
        "registered %d artifact(s) for conversation %s: %s (%d newly delivered)",
        len(registered), conversation_id, [m.name for m in registered], len(legacy_entries),
    )
    # Model-visible block — IDs the very next turn (or this same turn's
    # later tool calls) can pass to `run_code`'s `input_artifacts` or to
    # `save_artifact`/`get_artifact_download_url` (see plan section 5).
    data["artifacts"] = model_blocks
    data["artifacts_note"] = (
        "Every file listed in `artifacts` is already attached to your response as a "
        "downloadable artifact — the user can download each one. Do NOT re-run code to "
        "\"provide\", \"attach\", or \"verify\" these files, and do NOT put download "
        "links or file contents in your reply; just reference them by name."
    )

    if not legacy_entries:
        return

    # `::artifact` marker delivery (`streaming.py::_append_task_markers`)
    # still runs off `conversation_tasks.await_and_collect_results` — wrap
    # the ALREADY-COMPUTED result in a trivially-resolved task so that
    # pipeline keeps working unchanged even though registration itself is
    # no longer a background operation.
    async def _immediate() -> dict[str, Any]:
        return {"type": "artifacts", "artifacts": legacy_entries}

    task = asyncio.create_task(_immediate())
    register_task(conversation_id, task)


async def _emit_artifact_event(
    context: "AgentContext", metadata: ArtifactMetadata, download_url: str | None,
) -> None:
    """Push a live SSE `artifact` event so the frontend can render a
    download card WHILE the turn is still streaming (`streaming.ts`'s
    `onArtifact` handler already exists for exactly this). This is a
    nice-to-have, additive UX signal — the authoritative, persisted
    delivery mechanism is still the `::artifact` marker appended into the
    saved answer once the turn completes."""
    if context.event_sink is None or not download_url:
        return
    try:
        artifact_data = {
            "artifactId": metadata.artifact_id,
            "fileName": metadata.name,
            "mimeType": metadata.mime_type,
            "sizeBytes": metadata.size_bytes,
            "downloadUrl": download_url,
            "artifactType": metadata.artifact_type.value,
            "isTemporary": metadata.is_temporary,
            "recordId": metadata.artifact_id,
            "version": metadata.version,
        }
        if metadata.derived_from_code_artifact_id:
            artifact_data["derivedFromCodeArtifactId"] = metadata.derived_from_code_artifact_id
        for evt in context.formatter.artifact(context, artifact_data=artifact_data):
            await context.event_sink.write(evt)
    except Exception:
        logger.warning("failed to emit live artifact SSE event for %s", metadata.name, exc_info=True)
