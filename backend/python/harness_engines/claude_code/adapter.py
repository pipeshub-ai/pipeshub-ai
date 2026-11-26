"""``EngineAdapter`` for Claude Code (the admin-supplied ``claude`` binary driven with stream-json over stdio).

The binary is proprietary and PipesHub never ships it (D29): an admin supplies
it and builds the engine image. ``open`` hashes it and checks the version it
reports against the allow-listed pin before the engine starts, as every
adapter does (``harness_core.engines.binary``).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, ClassVar

from harness_core.access.types import ModelApi
from harness_core.engines.binary import check_reported_version, run_check, verify_binary
from harness_core.engines.errors import EngineCrashed, killed_by_signal
from harness_core.engines.stopping import stop_after_failed_open
from harness_core.engines.types import EngineCapabilities
from harness_core.logs import HarnessLog
from harness_core.sandbox.tar import put_config, put_files
from harness_core.workspace.sync.archive import Owner
from harness_engines.claude_code.launch import (
    BINARY_PATH,
    CONFIG_DIR,
    SKILLS_ENV,
    SKILLS_LINK,
    TRANSCRIPT_DIR,
    exposed_tool_name,
    render_launch,
)
from harness_engines.claude_code.readiness import GATEWAY_TIMEOUT_S, await_tool_gateway
from harness_engines.claude_code.session import ClaudeSession
from harness_engines.claude_code.signin import (
    STATUS,
    is_own_login,
    own_login_launch,
    park,
    processes,
    sign_in,
    sign_out,
    signed_in,
)
from harness_engines.claude_code.state import decode_state
from harness_engines.claude_code.stream import ClaudeStream

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from contextlib import AbstractAsyncContextManager

    from harness_core.engines.events import EngineState
    from harness_core.engines.signin import SignedIn, SignInAttempt, SignInHosts
    from harness_core.engines.types import (
        EngineKind,
        EngineLaunch,
        EngineProfile,
        GatewayEndpoints,
    )
    from harness_core.sandbox import SandboxHandle

_LOG = logging.getLogger(__name__)
# Providers behind the model gateway take tool names of at most 64 characters (Anthropic's and OpenAI's
# ``^[a-zA-Z0-9_-]{1,64}$``); Claude Code names a gateway tool ``mcp__tools__<tool>`` and does not shorten it.
PROVIDER_MAX_TOOL_NAME = 64
MAX_TOOL_NAME = PROVIDER_MAX_TOOL_NAME - len(exposed_tool_name(""))
_VERSION = re.compile(r"^(?P<version>[0-9][^\s(]*)\s*\(Claude Code\)")


def reported_version(output: str) -> str:
    """The version from ``claude --version`` (``2.1.289 (Claude Code)``)."""
    found = _VERSION.match(output.strip())
    return found.group("version") if found is not None else ""


class ClaudeCodeAdapter:
    kind: ClassVar[EngineKind] = "claude_code"

    def __init__(self, *, log: HarnessLog | None = None, gateway_timeout_s: float = GATEWAY_TIMEOUT_S) -> None:
        self._log = log or HarnessLog(_LOG)
        self._gateway_timeout_s = gateway_timeout_s

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            can_steer=True,
            native_skills=True,
            sub_agents=True,
            model_apis=frozenset({ModelApi.ANTHROPIC_MESSAGES}),
            max_tools=None,
            drive="stdio",
            max_tool_name=MAX_TOOL_NAME,
            can_compact=True,
        )

    def render_launch(self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str) -> EngineLaunch:
        return render_launch(profile, endpoints, skills_dir)

    def own_login(self, launch: EngineLaunch, model: str | None = None) -> EngineLaunch:
        return own_login_launch(launch, model)

    async def sign_in(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        hosts: SignInHosts,
        prompt: Callable[[], AbstractAsyncContextManager[SignInAttempt]],
        progress: Callable[[str, str | None, str | None], Awaitable[None]],
    ) -> SignedIn:
        log = self._log.bind(sandboxId=sandbox.id, engineVersion=launch.engine.version)
        await verify_binary(sandbox, BINARY_PATH, launch.engine, log)
        # The config folder as the engine finds it (root's, sticky): the login writes its own files beside root's.
        await put_config(sandbox, launch.files, homes=(CONFIG_DIR,))
        return await sign_in(sandbox, launch, hosts, prompt, progress, log)

    async def sign_out(self, sandbox: SandboxHandle, launch: EngineLaunch) -> None:
        await sign_out(sandbox, launch, self._log.bind(sandboxId=sandbox.id))

    async def signed_in(self, sandbox: SandboxHandle, launch: EngineLaunch) -> SignedIn | None:
        _, printed = await run_check(sandbox, STATUS, dict(launch.env))
        return signed_in(printed)

    async def processes(self, sandbox: SandboxHandle) -> frozenset[int]:
        return await processes(sandbox)

    async def park(self, sandbox: SandboxHandle, spare: frozenset[int]) -> bool:
        return await park(sandbox, spare, self._log.bind(sandboxId=sandbox.id))

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> ClaudeSession:
        log = self._log.bind(**dict(correlation or {}), sandboxId=sandbox.id, engineVersion=launch.engine.version)
        restored = decode_state(restore, launch.engine) if restore is not None else None
        version = await self._verify_binary(sandbox, launch, log)
        if restored is not None:
            # Claude Code appends to its transcript: the sandbox user's.
            transcript = {f"{TRANSCRIPT_DIR}/{restored.session_id}.jsonl": restored.transcript}
            await put_files(sandbox, transcript, owner=Owner())
        skills_dir = launch.env.get(SKILLS_ENV)
        # Claude Code reads user-scope skills from its config folder only; the run's skills are mounted elsewhere.
        # What it obeys is root's and read-only, in a folder it shares with root (SEC1), written after its state.
        await put_config(
            sandbox, launch.files, homes=(CONFIG_DIR,), links={SKILLS_LINK: skills_dir} if skills_dir else None
        )
        own_login = is_own_login(launch)
        # The account Claude Code names in its transcript, kept out of the saved state (SIGNIN).
        redact = (await self._signed_in_email(sandbox, launch),) if own_login else ()
        argv = [*launch.argv, *(("--resume", restored.session_id) if restored is not None else ())]
        process = await sandbox.exec(argv, env=dict(launch.env), stdin=True)
        stream = ClaudeStream(process)
        stream.start()
        session = ClaudeSession(
            engine=launch.engine,
            sandbox=sandbox,
            process=process,
            stream=stream,
            reported_version=version,
            resume_session=restored.session_id if restored is not None else None,
            secrets=frozenset(),
            log=log,
            own_login=own_login,
            redact=redact,
        )
        try:
            # No turn starts without the tool gateway's tools (E2E3-F6, ``readiness``).
            await await_tool_gateway(stream, log, timeout_s=self._gateway_timeout_s)
        except BaseException:
            await stop_after_failed_open(session.close(), log)
            raise
        log.info("engine.session.opened", restored=restored is not None)
        return session

    async def _signed_in_email(self, sandbox: SandboxHandle, launch: EngineLaunch) -> str:
        _, printed = await run_check(sandbox, STATUS, dict(launch.env))
        account = signed_in(printed)
        return account.email if account is not None else ""

    async def _verify_binary(self, sandbox: SandboxHandle, launch: EngineLaunch, log: HarnessLog) -> str:
        await verify_binary(sandbox, BINARY_PATH, launch.engine, log)
        code, printed = await run_check(sandbox, [BINARY_PATH, "--version"], {"HOME": "/tmp", "DISABLE_AUTOUPDATER": "1"})
        if killed_by_signal(code):
            # A binary killed as it starts is a dead engine, not a different version.
            raise EngineCrashed(f"claude --version exited with {code}")
        version = reported_version(printed)
        check_reported_version(launch.engine, version, log)
        return version


__all__ = ["MAX_TOOL_NAME", "PROVIDER_MAX_TOOL_NAME", "ClaudeCodeAdapter", "reported_version"]
