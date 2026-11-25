"""``EngineAdapter`` for Codex (the standalone ``codex-app-server`` driven over stdio)."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, ClassVar

from harness_core.access.types import ModelApi
from harness_core.engines.binary import check_reported_version, verify_binary
from harness_core.engines.errors import EngineUnhealthy
from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.engines.stopping import stop_after_failed_open, stop_process
from harness_core.engines.types import EngineCapabilities
from harness_core.gateway.responses_wire import MAX_TOOL_NAME as RESPONSES_MAX_TOOL_NAME
from harness_core.logs import HarnessLog
from harness_core.sandbox.tar import put_config, put_files
from harness_core.workspace.sync.archive import Owner
from harness_engines.codex.launch import (
    BINARY_PATH,
    CLIENT_NAME,
    CODEX_HOME,
    POLICY_HOOK_KEY,
    SESSIONS_DIR,
    SKILLS_ENV,
    WORKDIR,
    exposed_tool_name,
    render_launch,
    renders_policy_hook,
)
from harness_engines.codex.rpc import CodexRpc
from harness_engines.codex.session import CodexSession
from harness_engines.codex.state import decode_state

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.engines.events import EngineState
    from harness_core.engines.types import (
        EngineKind,
        EngineLaunch,
        EngineProfile,
        GatewayEndpoints,
        JsonValue,
    )
    from harness_core.sandbox import ProcessHandle, SandboxHandle

_LOG = logging.getLogger(__name__)
# Codex reads at most this many tools from one MCP server; one more and the whole server fails to start,
# so every gateway tool would vanish from the model's list (codex-mcp/src/pagination.rs, E8 RX-C4).
MAX_MCP_TOOLS = 2048
# Codex sends a gateway tool as the namespace ``mcp__tools`` member; the model gateway flattens it to
# ``mcp__tools__<tool>`` and refuses a flat name over its limit, which would fail every call of the run (FB-14).
MAX_TOOL_NAME = RESPONSES_MAX_TOOL_NAME - len(exposed_tool_name(""))
INITIALIZE_TIMEOUT_S = 60.0
# ``hooks/list`` trust states under which Codex runs a hook (a managed source needs no hash).
TRUSTED_STATUSES = frozenset({"trusted", "managed"})
_USER_AGENT_VERSION = re.compile(r"^[^/\s]+/(?P<version>[0-9][^\s(]*)")


def reported_version(user_agent: str) -> str:
    """The app-server's version from ``initialize``'s ``userAgent`` (``<client>/<version> (…)``)."""
    found = _USER_AGENT_VERSION.match(user_agent)
    return found.group("version") if found is not None else ""


class CodexAdapter:
    kind: ClassVar[EngineKind] = "codex"

    def __init__(self, *, log: HarnessLog | None = None) -> None:
        self._log = log or HarnessLog(_LOG)

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            can_steer=True,
            native_skills=True,
            sub_agents=True,
            model_apis=frozenset({ModelApi.RESPONSES}),
            max_tools=MAX_MCP_TOOLS,
            drive="stdio",
            max_tool_name=MAX_TOOL_NAME,
            can_compact=True,
            # ``bash -lc`` execs a lone command in its place, so it is the app-server's child, never a shell's.
            command_parents=frozenset({"codex-app-serve"}),
        )

    def render_launch(self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> EngineLaunch:
        return render_launch(profile, endpoints, skills_dir)

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> CodexSession:
        log = self._log.bind(**dict(correlation or {}), sandboxId=sandbox.id, engineVersion=launch.engine.version)
        restored = decode_state(restore, launch.engine) if restore is not None else None
        await verify_binary(sandbox, BINARY_PATH, launch.engine, log)
        if restored is not None:
            # Codex appends to its rollout: the sandbox user's (P5.5 Owner).
            await put_files(sandbox, {f"{SESSIONS_DIR}/{restored.path}": restored.rollout}, owner=Owner())
        # What Codex obeys is root's and read-only, in a folder it shares with root (SEC1), written after its state.
        await put_config(sandbox, launch.files, homes=(CODEX_HOME,))
        process = await sandbox.exec(list(launch.argv), env=dict(launch.env), stdin=True)
        rpc = CodexRpc(process)
        rpc.start()
        try:
            try:
                init = as_object(
                    await rpc.request(
                        "initialize",
                        {"clientInfo": {"name": CLIENT_NAME, "version": "1"}, "capabilities": {"experimentalApi": False}},
                        timeout_s=INITIALIZE_TIMEOUT_S,
                    )
                )
            except TimeoutError as exc:
                raise EngineUnhealthy("codex-app-server did not answer initialize") from exc
            version = reported_version(as_str(init.get("userAgent")))
            check_reported_version(launch.engine, version, log)
            await rpc.notify("initialized")
            skills_dir = launch.env.get(SKILLS_ENV)
            if skills_dir:
                await rpc.request("skills/extraRoots/set", {"extraRoots": [skills_dir]})
            if renders_policy_hook(launch):
                await _require_policy_hook(rpc, log)
        except BaseException:
            await stop_after_failed_open(self._stop(process, rpc), log)
            raise
        session = CodexSession(
            engine=launch.engine,
            sandbox=sandbox,
            process=process,
            rpc=rpc,
            reported_version=version,
            resume_thread=restored.thread_id if restored is not None else None,
            secrets=frozenset(),
            log=log,
        )
        log.info("engine.session.opened", restored=restored is not None)
        return session

    async def _stop(self, process: ProcessHandle, rpc: CodexRpc) -> None:
        await rpc.close()
        await stop_process(process)


def loaded_policy_hook(listed: JsonValue) -> bool:
    """Whether ``hooks/list`` shows the managed policy hook enabled and trusted for the workspace."""
    for entry in as_list(as_object(listed).get("data")):
        for hook in as_list(as_object(entry).get("hooks")):
            found = as_object(hook)
            if (
                as_str(found.get("key")) == POLICY_HOOK_KEY
                and found.get("enabled") is True
                and as_str(found.get("trustStatus")) in TRUSTED_STATUSES
            ):
                return True
    return False


def listed_policy_hook_hash(listed: JsonValue) -> str | None:
    """The hash ``hooks/list`` gives the managed policy hook (``currentHash``), trusted or not; ``None`` when it does
    not list the hook. A Codex release that hashes hook definitions differently gives another value than
    ``TRUSTED_HOOKS`` pins, and then skips the hook: the engine-candidate gate compares the two (CC1)."""
    for entry in as_list(as_object(listed).get("data")):
        for hook in as_list(as_object(entry).get("hooks")):
            found = as_object(hook)
            if as_str(found.get("key")) == POLICY_HOOK_KEY:
                current = as_str(found.get("currentHash"))
                return current or None
    return None


async def _require_policy_hook(rpc: CodexRpc, log: HarnessLog) -> None:
    """Fail closed (G-25): a Codex that would skip the managed hook, because a release changed how it trusts hooks, is
    not started at all."""
    listed = await rpc.request("hooks/list", {"cwds": [WORKDIR]})
    if not loaded_policy_hook(listed):
        # The hash the binary gives the hook names the cause when its release changed how it hashes them.
        log.warning("engine.policy_hook.missing", key=POLICY_HOOK_KEY, listedHash=listed_policy_hook_hash(listed))
        raise EngineUnhealthy("Codex did not load PipesHub's managed policy hook as trusted")


__all__ = [
    "MAX_MCP_TOOLS",
    "MAX_TOOL_NAME",
    "CodexAdapter",
    "listed_policy_hook_hash",
    "loaded_policy_hook",
    "reported_version",
]
