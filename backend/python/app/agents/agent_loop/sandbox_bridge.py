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
       ``AgentResult.artifacts`` for a parent/orchestrator to see, and — if
       the run produced zero ``$OUTPUT_DIR`` deliverables — to rescue its
       recorded scratch files as a last-resort delivery.
3. ``coding_sandbox_artifact_staging`` composes with
   ``app/services/artifact_registry`` (never touching a signed URL or raw
   bytes at the model-input boundary — see that package's module docstrings)
   and ``app.agent_loop_lib.tools.builtin.sandbox.input_staging`` (the
   existing model-proof parent->child file handoff mechanism, reused here
   unchanged rather than inventing a second staging path).
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
from typing import TYPE_CHECKING, Any
from uuid import uuid4

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
from app.agents.agent_loop.protocol.formatter import ArtifactSSEPayload
from app.config.constants.arangodb import Connectors
from app.models.entities import ArtifactType, ArtifactVisibility
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

# run_code's ONE user-facing deliverable directory, backed by $OUTPUT_DIR
# (see `sanitized_subprocess_env()` / DockerCodingSandbox's container env) on
# both backends. Only files under this path are fetched from the sandbox,
# registered, and delivered to the user — everything else is scratch (see
# `_partition_sandbox_outputs`) and never leaves the sandbox filesystem.
_OUTPUT_DIR_NAME = "output"

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
_CODE_NAME_TOKEN_LEN = 12

# Per-`RunScope` (NOT the flat, tree-wide `AgentContext.artifacts_registered_
# this_run`) record of artifacts registered during exactly this run — see
# `coding_sandbox_result_propagation`'s docstring for why this must be a
# `StateSlot` rather than the shared context list: concurrent sibling
# `coding_agent` spawns must never see each other's artifacts here.
_REGISTERED_ARTIFACTS_SLOT: StateSlot[list[dict[str, Any]]] = StateSlot(
    key="pipeshub.sandbox_bridge.artifacts_registered", default_factory=list,
)

# Per-`RunScope` record of each call's $OUTPUT_DIR-missing scratch files —
# read by `coding_sandbox_result_propagation`'s POST_AGENT rescue ONLY when
# this run ends having registered zero real deliverables (see that
# function's docstring for why the fallback must be deferred to POST_AGENT
# rather than firing per call).
_SCRATCH_FILES_SLOT: StateSlot[list[dict[str, Any]]] = StateSlot(
    key="pipeshub.sandbox_bridge.scratch_files_by_call", default_factory=list,
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
                    "artifact that already exists. Compacted tool-result artifacts "
                    "(artifact_1, artifact_2, …) are staged at "
                    "input/artifacts/<id>.json. Works whether this call creates a fresh "
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
            "regenerate an artifact that already exists. Only files written to $OUTPUT_DIR "
            "are attached to the response automatically as downloadable artifacts — never "
            "re-run code just to attach, verify, or 'provide' a file already written there. "
            "Anything else your code writes stays inside the sandbox and is never shown to "
            "the user, even if the run succeeds."
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
    artifact_store: Any = None,
) -> None:
    """Wire the coding-sandbox PRE/POST hooks onto `hooks`. Explicit here
    (rather than relying on `ControlPlane.start()`'s auto-add) because the
    agent-loop adapter path builds its own `HookRegistry` directly — see
    `PipesHubAgentFactory._build_hooks`.

    `allow_network` should be the SAME resolved value passed to
    `build_coding_sandbox_manager()`/`register_coding_sandbox_tools()` — it
    only changes the package-policy deny message's wording.

    `artifact_store` is the same ``InMemoryArtifactStore`` instance used by
    ``shape_artifact_registration``.  When provided,
    ``coding_sandbox_artifact_staging`` checks it FIRST for
    ``input_artifacts`` refs (e.g. ``artifact_4`` from context-compacted tool
    results) before falling back to ``ArtifactRegistryService``."""
    network_enabled = sandbox_network_enabled() if allow_network is None else allow_network
    hooks.on(HookEvent.PRE_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_safety(max_code_size=max_code_size),
    )
    hooks.on(HookEvent.PRE_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_package_policy(allow_network=network_enabled),
    )
    hooks.on(HookEvent.PRE_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_artifact_staging(context, inmemory_store=artifact_store),
    )
    hooks.on(HookEvent.POST_TOOL_USE).use(
        CODING_SANDBOX_PATH_PATTERN, coding_sandbox_artifact_bridge(context, manager),
    )
    hooks.on(HookEvent.POST_AGENT).use(coding_sandbox_result_propagation(context, manager))


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


def coding_sandbox_result_propagation(context: "AgentContext", manager: SandboxManager):
    """POST_AGENT middleware with two responsibilities:

    1. Copies every artifact registered during exactly THIS run (tracked in
       `_REGISTERED_ARTIFACTS_SLOT`, written by `coding_sandbox_artifact_bridge`
       above) onto `AgentResult.artifacts` as a proper
       `agent_loop_lib.core.types.Artifact` — so a parent/orchestrator agent
       that spawned a `coding_agent` child (via `spawn_agent`/`AgentTool`)
       sees exactly the artifacts THAT CHILD produced in its own
       `AgentResult`, without re-querying the registry itself and with zero
       risk of double-counting a concurrently-running sibling's artifacts
       (the reason this is a per-`RunScope` slot, not the flat, tree-wide
       `AgentContext.artifacts_registered_this_run` list — see that field's
       docstring and `StateSlot`'s concurrency contract).

       `content` carries the full compact metadata dict (`artifact_id`, name,
       version, mime_type, ...) rather than a URL — a parent agent that wants
       to reuse the artifact passes its name/id into ITS OWN `run_code`
       call's `input_artifacts`, it never needs a signed URL for this.

    2. BEFORE that copy, rescues this run's scratch files as a last resort
       if — and only if — the run registered ZERO real ($OUTPUT_DIR)
       deliverables across every `run_code` call it made (see
       `_rescue_scratch_files`). This is deliberately a POST_AGENT check,
       not a per-call one: a per-call fallback would deliver call 1's
       intermediate the moment it saw nothing in $OUTPUT_DIR yet, even
       though call 2 (later in the SAME run) goes on to write the real
       deliverable there — reintroducing exactly the "extra cards" bug this
       module exists to fix. Waiting until the whole run is over is the
       only point at which "the model never used $OUTPUT_DIR at all" can be
       distinguished from "the model hadn't gotten to $OUTPUT_DIR yet"."""

    async def _middleware(ctx: AgentLifecycleContext, next_fn) -> None:
        if ctx.scope is not None:
            registered_slot = ctx.scope.get(_REGISTERED_ARTIFACTS_SLOT)
            if not registered_slot:
                scratch_calls = ctx.scope.get(_SCRATCH_FILES_SLOT)
                if scratch_calls:
                    await _rescue_scratch_files(context, manager, scratch_calls, registered_slot)
            if ctx.result is not None:
                for entry in registered_slot:
                    ctx.result.artifacts.append(LibArtifact(
                        name=entry.get("name") or "artifact",
                        type=LibArtifactType.FILE,
                        content=entry,
                        description=entry.get("description") or None,
                    ))
        await next_fn()

    return _middleware


def coding_sandbox_artifact_staging(context: "AgentContext", *, inmemory_store: Any = None):
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

    `inmemory_store` is the ``InMemoryArtifactStore`` from context
    engineering's artifact registration.  Refs like ``artifact_4`` are
    tried here FIRST — a fast in-process lookup — before falling back to
    ``ArtifactRegistryService`` (blob-backed, MongoDB/ArangoDB).
    """

    async def _middleware(ctx: ToolCallContext, next_fn) -> None:
        registry = context.artifact_registry
        code = ctx.tool_input.get("code")
        if registry is not None and context.conversation_id and isinstance(code, str) and code.strip():
            await _capture_code_artifact(context, registry, ctx)

        refs = ctx.tool_input.get("input_artifacts")
        has_registry = registry is not None and context.conversation_id
        has_inmemory = inmemory_store is not None
        if not refs or (not has_registry and not has_inmemory):
            logger.info(
                "coding_sandbox_artifact_staging: no input_artifacts to stage "
                "(refs=%s registry=%s inmemory=%s conversation_id=%s)",
                bool(refs), registry is not None, has_inmemory, context.conversation_id,
            )
            set_staged_input_files_for_task(None)
            await next_fn()
            return

        logger.info(
            "coding_sandbox_artifact_staging: resolving %d input_artifact ref(s): %s",
            len(refs), refs,
        )
        files, resolved, missing = await _resolve_input_artifacts(
            context, registry, refs, inmemory_store=inmemory_store,
        )
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


def _code_artifact_token(tool_use_id: object) -> str:
    """Short, non-UUID-shaped token that keeps each captured program its own
    artifact rather than a version bump of the last one (`register_output`
    matches on logical name).

    Deliberately never the `sandbox_id`. A sandbox only lives for the turn
    that created it, so a name carrying one reads as a live handle — it even
    works if the model tries it within that turn — and then resolves to
    nothing in every later turn. That is how a model ends up calling
    `read_sandbox_file` with an id it read off a filename.
    """
    raw = getattr(tool_use_id, "hex", None) or str(tool_use_id).replace("-", "")
    token = "".join(ch for ch in raw.lower() if ch.isalnum())[:_CODE_NAME_TOKEN_LEN]
    return token or uuid4().hex[:_CODE_NAME_TOKEN_LEN]


async def _capture_code_artifact(context: "AgentContext", registry: Any, ctx: ToolCallContext) -> None:
    language = ctx.tool_input.get("language") or "typescript"
    ext = _CODE_EXT_BY_LANGUAGE.get(language, "ts")
    logical_name = f"code_{_code_artifact_token(ctx.tool_use_id)}.{ext}"
    actor = Actor(org_id=context.org_id, user_id=context.user_id)
    try:
        # STAGING: the captured program source never gets a download card
        # (it isn't SSE'd or marker-delivered anywhere in this module) — it
        # exists purely for DERIVED_FROM lineage and input_artifacts reuse.
        # Labeling it STAGING keeps it out of list_artifacts/the context
        # reminder's user-visible view while `registry.resolve()` still
        # finds it by name for both of those purposes.
        metadata, _version = await registry.register_output(
            actor=actor,
            name=logical_name,
            artifact_type=ArtifactType.CODE,
            mime_type=_CODE_MIME_BY_LANGUAGE.get(language, "text/plain"),
            content=ctx.tool_input["code"].encode("utf-8"),
            conversation_id=context.conversation_id,
            source_tool=ctx.tool_path,
            visibility=ArtifactVisibility.STAGING,
        )
    except Exception:
        logger.warning("Failed to capture code artifact for %s", ctx.tool_path, exc_info=True)
        return
    ctx.metadata["code_artifact_id"] = metadata.artifact_id
    ctx.metadata["code_artifact_version"] = metadata.version


async def _resolve_input_artifacts(
    context: "AgentContext", registry: Any, refs: list[str],
    *, inmemory_store: Any = None,
) -> tuple[dict[str, bytes], list[dict[str, Any]], list[str]]:
    """Resolve+fetch every ref in `refs`, permission-checked per-ref through
    the registry. Returns `(sandbox_files, resolved_info, missing_refs)` —
    `sandbox_files` is ready for `stage_input_files()`; `resolved_info` and
    `missing_refs` are model-visible reporting, never raw bytes/URLs.

    When `inmemory_store` is provided, refs are tried there FIRST (fast,
    in-process lookup for context-compacted tool results like ``artifact_4``)
    before falling back to ``ArtifactRegistryService`` (blob-backed)."""
    actor = Actor(org_id=context.org_id, user_id=context.user_id)
    files: dict[str, bytes] = {}
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for ref in refs:
        if not isinstance(ref, str) or not ref.strip():
            logger.debug("_resolve_input_artifacts: skipping empty/non-str ref: %r", ref)
            continue

        # --- Try InMemoryArtifactStore first (context-compacted tool results) ---
        if inmemory_store is not None:
            try:
                content_str = await inmemory_store.get(ref)
            except Exception:
                logger.debug("_resolve_input_artifacts: inmemory_store.get(%r) raised", ref, exc_info=True)
                content_str = None
            if content_str is not None:
                staged_path = f"input/artifacts/{ref}.json"
                files[staged_path] = content_str.encode("utf-8")
                resolved_entry: dict[str, Any] = {
                    "ref": ref, "artifact_id": ref, "name": ref,
                    "version": 0, "path": staged_path,
                }
                schema = (
                    inmemory_store.get_schema(ref)
                    if hasattr(inmemory_store, "get_schema") else None
                )
                tool_name = (
                    inmemory_store.get_tool_name(ref)
                    if hasattr(inmemory_store, "get_tool_name") else None
                )
                if schema:
                    resolved_entry["result_schema"] = schema
                    schema_json = json.dumps({
                        "artifact_id": ref,
                        "name": ref,
                        "tool_name": tool_name or "",
                        "data_file": staged_path,
                        "schema": schema,
                    }, indent=2)
                    schema_path = f"input/artifacts/{ref}.schema.json"
                    files[schema_path] = schema_json.encode("utf-8")
                    resolved_entry["schema_path"] = schema_path
                resolved.append(resolved_entry)
                logger.info(
                    "_resolve_input_artifacts: ref %r resolved from inmemory_store "
                    "(%d bytes, schema=%s) -> %s",
                    ref, len(content_str), bool(schema), staged_path,
                )
                continue

        # --- Fall back to ArtifactRegistryService (blob-backed) ---
        if registry is None:
            missing.append(ref)
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
        resolved_entry: dict[str, Any] = {
            "ref": ref, "artifact_id": metadata.artifact_id, "name": metadata.name,
            "version": metadata.version, "path": staged_path,
        }
        if metadata.result_schema:
            resolved_entry["result_schema"] = metadata.result_schema
            schema_json = json.dumps({
                "artifact_id": metadata.artifact_id,
                "name": metadata.name,
                "data_file": staged_path,
                "schema": metadata.result_schema,
            }, indent=2)
            schema_path = f"input/artifacts/{metadata.name}.schema.json"
            files[schema_path] = schema_json.encode("utf-8")
            resolved_entry["schema_path"] = schema_path
        resolved.append(resolved_entry)
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
                _before = len(context.artifacts_registered_this_run)
                await _register_run_code_artifacts(
                    context, manager, sandbox_id, artifacts, data,
                    source_tool=ctx.tool_path,
                    code_artifact_id=ctx.metadata.get("code_artifact_id"),
                    code_artifact_version=ctx.metadata.get("code_artifact_version"),
                    scope=ctx.scope,
                )
                newly_registered = context.artifacts_registered_this_run[_before:]
                if newly_registered:
                    # Read by `hooks/completion_gate.py` — a file-generation
                    # request is only "done" once this flips true. Set from
                    # actually-REGISTERED $OUTPUT_DIR deliverables, not the
                    # raw artifact-path list — a call that only wrote
                    # scratch files must NOT satisfy the gate, or the model
                    # could finish a file-generation turn having produced
                    # nothing the user can see.
                    context.artifacts_produced_this_run = True
                    if ctx.scope is not None:
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


def _is_excluded_scratch_path(normalized_path: str) -> bool:
    """True for paths that are sandbox plumbing rather than something the
    program itself produced as output: staged INPUT files (`input/...` —
    the model's own uploaded/reused artifacts, echoing them back as a "new"
    artifact would be nonsensical) and dotfile directories (`.matplotlib/`,
    `.cache/`, `.config/`, `.npm/`, ... — `HOME`/`TMPDIR` point at the
    working dir, see `sanitized_subprocess_env()`, so tool caches land
    inside it and would otherwise be reported as scratch "files the program
    wrote"). Applied to both the scratch listing shown to the model and the
    POST_AGENT rescue's candidate set — a cache file must never become a
    download card even as a last resort."""
    parts = normalized_path.split("/")
    if parts and parts[0] == "input":
        return True
    return any(part.startswith(".") for part in parts if part)


def _partition_sandbox_outputs(paths: list[str]) -> tuple[list[str], list[str]]:
    """Split `run_code`'s raw output paths into `(deliverables, scratch)`.

    Deliverables are paths under the sandbox's `output/` directory — backed
    by `$OUTPUT_DIR` on both backends (`sanitized_subprocess_env()` for
    local, `DockerCodingSandbox`'s container env for Docker) — the ONLY
    files this bridge downloads, registers, and delivers to the user.

    Scratch is everything else the program wrote to its working directory:
    intermediate renders, per-page temp files, and so on. These are
    reported to the model by NAME ONLY — never fetched, never uploaded,
    never registered — unless the POST_AGENT rescue (`_rescue_scratch_files`)
    decides the run needs them as a last resort. `input/...` paths and
    dotfile-directory paths (tool caches under `$HOME`) are dropped
    entirely from both lists; they were never the program's own output.

    Deduplicates — Docker can list the same relative `output/...` path
    twice when a program writes it via a relative path from its cwd (`/src`)
    that also exists in the extracted `/output` tree."""
    deliverables: list[str] = []
    scratch: list[str] = []
    seen: set[str] = set()
    output_prefix = f"{_OUTPUT_DIR_NAME}/"
    for raw_path in paths:
        normalized = raw_path.replace("\\", "/").lstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        if normalized.startswith(output_prefix):
            deliverables.append(raw_path)
        elif not _is_excluded_scratch_path(normalized):
            scratch.append(raw_path)
    return deliverables, scratch


def _artifact_names_for(fetched: list[tuple[str, bytes]]) -> dict[str, str]:
    """Map each fetched `(sandbox_relative_path, content)` pair to the
    `name` it registers under — the basename, UNLESS two paths in this same
    batch share a basename (e.g. `output/charts/rev.png` and
    `output/tables/rev.png`), in which case both are disambiguated with
    their directory relative to `output/` so the second registration
    doesn't silently collapse onto the first as a new version of it.
    `register_output` identifies artifacts by `(orgId, conversationId,
    logicalName)` — see `ArtifactRegistryService.register_output` — so a
    bare-basename collision is a same-name collision from its point of
    view, not a coincidence to shrug off."""
    basename_counts: dict[str, int] = {}
    for rel_path, _content in fetched:
        base = os.path.basename(rel_path)
        basename_counts[base] = basename_counts.get(base, 0) + 1

    names: dict[str, str] = {}
    for rel_path, _content in fetched:
        base = os.path.basename(rel_path)
        if basename_counts[base] == 1:
            names[rel_path] = base
            continue
        normalized = rel_path.replace("\\", "/").lstrip("/")
        output_prefix = f"{_OUTPUT_DIR_NAME}/"
        if normalized.startswith(output_prefix):
            normalized = normalized[len(output_prefix):]
        stem, ext = os.path.splitext(base)
        dir_part = os.path.dirname(normalized).replace("/", "_")
        names[rel_path] = f"{dir_part}_{stem}{ext}" if dir_part else base
    return names


def _record_scratch_for_fallback(
    scope: Any, *, sandbox_id: str, paths: list[str], source_tool: str,
    code_artifact_id: str | None, code_artifact_version: int | None,
) -> None:
    """Stash this call's scratch paths on the owning `RunScope` so
    `coding_sandbox_result_propagation`'s POST_AGENT rescue can deliver them
    IF — and only if — the whole run ends without ever registering a single
    real ($OUTPUT_DIR) deliverable. A no-op without a scope (e.g. a
    directly-unit-tested call) or an empty `paths` list."""
    if scope is None or not paths:
        return
    scope.turn.run.get(_SCRATCH_FILES_SLOT).append({
        "sandbox_id": sandbox_id, "paths": list(paths), "source_tool": source_tool,
        "code_artifact_id": code_artifact_id, "code_artifact_version": code_artifact_version,
    })


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
    scope: Any = None,
) -> None:
    """Partition `artifact_paths` into `$OUTPUT_DIR` deliverables and
    scratch, then fetch + register ONLY the deliverables from the still-live
    sandbox, synchronously, before this POST hook returns. Mutates `data`
    in place: `data["artifacts"]` becomes the model-visible compact block
    for each registered deliverable (`ArtifactMetadata.to_tool_response`);
    `data["scratch_files"]` lists any non-deliverable paths BY NAME ONLY —
    their bytes are never fetched here, so no blob upload and no ArangoDB
    write happens for them. The original raw sandbox-relative path list is
    never returned to the model as-is, since a path meaningless outside
    this sandbox is a worse handle than a durable `artifact_id`."""
    registry = context.artifact_registry
    conversation_id = context.conversation_id
    org_id = context.org_id
    if not (registry is not None and conversation_id and org_id):
        logger.warning(
            "coding sandbox artifact registration skipped: registry=%s conversation_id=%s org_id=%s",
            registry is not None, conversation_id, org_id,
        )
        data["artifacts"] = []
        return

    try:
        backend = manager.get(SandboxType.CODING, sandbox_id)
    except UnknownSandboxError:
        logger.warning("coding sandbox artifact registration skipped: unknown sandbox_id=%s", sandbox_id)
        data["artifacts"] = []
        return

    deliverable_paths, scratch_paths = _partition_sandbox_outputs(artifact_paths)

    if scratch_paths:
        data["scratch_files"] = [os.path.basename(p) for p in scratch_paths]
        data["scratch_files_note"] = (
            "These files were written outside $OUTPUT_DIR and stayed inside the sandbox "
            "-- they were NOT uploaded or shown to the user. If one of them is actually "
            "the deliverable, move/copy it into $OUTPUT_DIR and re-run with the same "
            "sandbox_id, or call read_sandbox_file on it and then artifacts__save_artifact."
        )
        _record_scratch_for_fallback(
            scope, sandbox_id=sandbox_id, paths=scratch_paths, source_tool=source_tool,
            code_artifact_id=code_artifact_id, code_artifact_version=code_artifact_version,
        )

    if not deliverable_paths:
        data["artifacts"] = []
        logger.info(
            "coding sandbox run produced no $OUTPUT_DIR deliverables (tool=%s sandbox=%s "
            "scratch=%d)", source_tool, sandbox_id, len(scratch_paths),
        )
        return

    fetched: list[tuple[str, bytes]] = []
    for rel_path in deliverable_paths:
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
            deliverable_paths, sandbox_id,
        )
        data["artifacts"] = []
        return

    names = _artifact_names_for(fetched)
    await _register_and_deliver(
        context, registry, fetched, names, data,
        source_tool=source_tool, code_artifact_id=code_artifact_id,
        code_artifact_version=code_artifact_version,
    )


async def _register_and_deliver(
    context: "AgentContext",
    registry: Any,
    fetched: list[tuple[str, bytes]],
    names: dict[str, str],
    data: dict[str, Any] | None,
    *,
    source_tool: str,
    code_artifact_id: str | None,
    code_artifact_version: int | None,
) -> list[ArtifactMetadata]:
    """Register each `(sandbox_relative_path, content)` pair as a VISIBLE
    artifact, record `DERIVED_FROM` lineage against `code_artifact_id`,
    and emit the live SSE event + `::artifact` marker for every
    not-already-delivered version. When `data` is given (the normal
    per-call path), also populates `data["artifacts"]`/`data["artifacts_note"]`
    with the model-visible compact blocks; the POST_AGENT rescue
    (`_rescue_scratch_files`) passes `data=None` since there is no
    in-flight tool response left to mutate by the time a run has ended.

    Shared by both callers so they dedupe delivery through the SAME
    `context.delivered_artifact_versions` set and append to the SAME
    `context.artifacts_registered_this_run` list — a rescued scratch file
    is indistinguishable, from every downstream consumer's point of view,
    from a normal `$OUTPUT_DIR` deliverable."""
    actor = Actor(org_id=context.org_id, user_id=context.user_id)
    registered: list[ArtifactMetadata] = []
    model_blocks: list[dict[str, Any]] = []
    legacy_entries: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []

    for rel_path, content in fetched:
        file_name = names[rel_path]
        mime_type = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        try:
            metadata, _version = await registry.register_output(
                actor=actor,
                name=file_name,
                artifact_type=MIME_TO_ARTIFACT_TYPE.get(mime_type, ArtifactType.OTHER),
                mime_type=mime_type,
                content=content,
                conversation_id=context.conversation_id,
                source_tool=source_tool,
                connector_name=Connectors.CODING_SANDBOX,
            )
        except Exception as exc:
            logger.exception("coding sandbox artifact registration failed for %s", rel_path)
            failures.append({"file": file_name, "error": f"{type(exc).__name__}: {exc}"})
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

    if data is not None and failures:
        # Without this the model sees an empty `artifacts` list, cannot tell
        # "no file" from "the save failed", and reports the file as ready —
        # the user is then told about a download that does not exist.
        data["artifact_errors"] = failures
        data["artifact_errors_note"] = (
            "These files were produced but could NOT be saved to the artifact store, so "
            "they are NOT downloadable and will not exist in later turns. Do NOT tell the "
            "user the file is ready. Retry ONCE via read_sandbox_file + "
            "artifacts__save_artifact; if that fails too, tell the user the file could not "
            "be saved and report the error."
        )

    if not registered:
        if data is not None:
            data["artifacts"] = []
        return []

    logger.info(
        "registered %d artifact(s) for conversation %s: %s (%d newly delivered)",
        len(registered), context.conversation_id, [m.name for m in registered], len(legacy_entries),
    )
    if data is not None:
        # Model-visible block — IDs the very next turn (or this same turn's
        # later tool calls) can pass to `run_code`'s `input_artifacts` or to
        # `save_artifact`/`get_artifact_download_url` (see plan section 5).
        data["artifacts"] = model_blocks
        data["artifacts_note"] = (
            "Every file listed in `artifacts` was written to $OUTPUT_DIR and is already "
            "attached to your response as a downloadable artifact — the user can download "
            "each one. Do NOT re-run code to \"provide\", \"attach\", or \"verify\" these "
            "files, and do NOT put download links or file contents in your reply; just "
            "reference them by name."
        )

    if legacy_entries and context.conversation_id:
        # `::artifact` marker delivery (`streaming.py::_append_task_markers`)
        # still runs off `conversation_tasks.await_and_collect_results` — wrap
        # the ALREADY-COMPUTED result in a trivially-resolved task so that
        # pipeline keeps working unchanged even though registration itself is
        # no longer a background operation.
        async def _immediate() -> dict[str, Any]:
            return {"type": "artifacts", "artifacts": legacy_entries}

        task = asyncio.create_task(_immediate())
        register_task(context.conversation_id, task)

    return registered


async def _rescue_scratch_files(
    context: "AgentContext",
    manager: SandboxManager,
    scratch_calls: list[dict[str, Any]],
    registered_slot: list[dict[str, Any]],
) -> None:
    """POST_AGENT last resort: this run ended having registered ZERO real
    `$OUTPUT_DIR` deliverables, but at least one `run_code` call wrote
    scratch files. Fetch and register those scratch files exactly like a
    normal deliverable — better a card the model should have put in
    `$OUTPUT_DIR` than a user left with nothing after asking for a file.
    Mutates `registered_slot` in place (the same `_REGISTERED_ARTIFACTS_SLOT`
    list `coding_sandbox_result_propagation` is about to copy onto
    `AgentResult.artifacts`) so the rescue is indistinguishable, to every
    downstream consumer, from a normal registration."""
    registry = context.artifact_registry
    if registry is None or not context.conversation_id or not context.org_id:
        return

    for call in scratch_calls:
        sandbox_id = call["sandbox_id"]
        try:
            backend = manager.get(SandboxType.CODING, sandbox_id)
        except UnknownSandboxError:
            logger.warning(
                "coding sandbox scratch rescue skipped: unknown sandbox_id=%s", sandbox_id,
            )
            continue

        fetched: list[tuple[str, bytes]] = []
        for rel_path in call["paths"]:
            try:
                content = await backend.download_file(rel_path)
            except Exception:
                logger.warning(
                    "scratch rescue: download failed for %r in sandbox %s", rel_path, sandbox_id, exc_info=True,
                )
                continue
            fetched.append((rel_path, content))
        if not fetched:
            continue

        names = _artifact_names_for(fetched)
        _before = len(context.artifacts_registered_this_run)
        registered = await _register_and_deliver(
            context, registry, fetched, names, None,
            source_tool=call["source_tool"],
            code_artifact_id=call.get("code_artifact_id"),
            code_artifact_version=call.get("code_artifact_version"),
        )
        if registered:
            context.artifacts_produced_this_run = True
            registered_slot.extend(context.artifacts_registered_this_run[_before:])
            logger.info(
                "coding sandbox scratch rescue: delivered %d file(s) as last-resort "
                "deliverable(s) for sandbox=%s (no $OUTPUT_DIR files this run)",
                len(registered), sandbox_id,
            )


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
        artifact_data = ArtifactSSEPayload(
            artifactId=metadata.artifact_id,
            fileName=metadata.name,
            mimeType=metadata.mime_type,
            sizeBytes=metadata.size_bytes,
            downloadUrl=download_url,
            artifactType=metadata.artifact_type.value,
            isTemporary=metadata.is_temporary,
            recordId=metadata.artifact_id,
            version=metadata.version,
            derivedFromCodeArtifactId=metadata.derived_from_code_artifact_id,
            visibility=metadata.visibility.value,
        )
        for evt in context.formatter.artifact(context, artifact_data=artifact_data):
            await context.event_sink.write(evt)
    except Exception:
        logger.warning("failed to emit live artifact SSE event for %s", metadata.name, exc_info=True)
