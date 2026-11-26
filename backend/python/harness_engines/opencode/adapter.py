"""``EngineAdapter`` for opencode (``opencode serve`` driven over HTTP)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import signal
from typing import TYPE_CHECKING, ClassVar, NoReturn

import httpcore

from harness_core.access.types import ModelApi
from harness_core.engines.binary import check_reported_version, verify_binary
from harness_core.engines.errors import (
    EngineCrashed,
    EngineStateIncompatible,
    EngineUnhealthy,
)
from harness_core.engines.jsonview import as_object, as_str
from harness_core.engines.stopping import stop_after_failed_open
from harness_core.engines.types import EngineCapabilities
from harness_core.logs import HarnessLog
from harness_core.sandbox.tar import put_files
from harness_engines.opencode.launch import (
    AGENT,
    BINARY_PATH,
    DATA_HOME,
    MCP_SERVER,
    exposed_tool_name,
    render_launch,
)
from harness_engines.opencode.session import OpencodeSession, finish
from harness_engines.opencode.state import decode_state
from harness_engines.opencode_api.http import OpencodeHttp, OpencodeHttpError
from harness_engines.opencode_api.readiness import GATEWAY_TIMEOUT_S, await_tool_gateway

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Mapping

    from harness_core.engines.events import EngineState
    from harness_core.engines.types import (
        EngineKind,
        EngineLaunch,
        EngineProfile,
        GatewayEndpoints,
    )
    from harness_core.sandbox import ProcessHandle, SandboxHandle

_LOG = logging.getLogger(__name__)
# Under the data-home mount: the root is read-only and archives are written only into mounts.
RESTORE_PATH = f"{DATA_HOME}/harness-restore/session.json"
PASSWORD_ENV = "OPENCODE_SERVER_PASSWORD"
# Chat Completions providers take function names of at most 64 characters, and opencode names a gateway tool
# ``tools_<tool>`` without shortening it (P2.8 FB-1: a 64-character MCP export reached the model as 70).
PROVIDER_MAX_TOOL_NAME = 64
MAX_TOOL_NAME = PROVIDER_MAX_TOOL_NAME - len(exposed_tool_name(""))
# Refused probes poll at STARTUP_POLL_S: 300 x 0.1 s keeps the 30 s a slow first start has always had.
STARTUP_ATTEMPTS = 300
STARTUP_POLL_S = 0.1
# opencode accepts connections before it serves, and a probe sent then is never answered: the first probes
# give up quickly (P5.9a found 2 s of each start lost to one such probe), doubling up to the ceiling so a
# slow host still gets its answer.
STARTUP_FIRST_PROBE_S = 0.25
STARTUP_PROBE_TIMEOUT_S = 2.0
IMPORT_TIMEOUT_S = 60.0
STOP_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 10.0
# What ``opencode serve`` prints on stdout once it serves (1.18: "opencode server listening on http://…").
LISTENING = b"server listening on"
LISTENING_TAIL = 4096


def _random_password() -> str:
    return secrets.token_urlsafe(32)


class _Serving(Exception):
    """The server said it serves while a probe or a poll was in flight."""


async def _until[T](work: Awaitable[T], ready: asyncio.Event | None) -> T:
    """``work``, unless ``ready`` is set first: then it is cancelled and ``_Serving`` raised."""
    if ready is None:
        return await work
    task = asyncio.ensure_future(work)
    said = asyncio.ensure_future(ready.wait())
    try:
        _ = await asyncio.wait((task, said), return_when=asyncio.FIRST_COMPLETED)
    finally:
        _ = said.cancel()
    if task.done():
        return task.result()
    _ = task.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await task
    raise _Serving


async def _watch_listening(stdout: AsyncIterator[bytes], ready: asyncio.Event) -> None:
    """Sets ``ready`` at opencode's listening line, then drains its output to the end: the exec port bounds what it
    buffers, and an unread stream would stall the server (E5)."""
    seen = b""
    async for chunk in stdout:
        if not ready.is_set():
            seen = (seen + chunk)[-LISTENING_TAIL:]
            if LISTENING in seen:
                ready.set()


class OpencodeAdapter:
    kind: ClassVar[EngineKind] = "opencode"

    def __init__(
        self,
        *,
        password: Callable[[], str] = _random_password,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        log: HarnessLog | None = None,
        gateway_timeout_s: float = GATEWAY_TIMEOUT_S,
    ) -> None:
        self._password = password
        self._sleep = sleep
        self._log = log or HarnessLog(_LOG)
        self._gateway_timeout_s = gateway_timeout_s

    def capabilities(self) -> EngineCapabilities:
        return EngineCapabilities(
            can_steer=False,
            native_skills=True,
            sub_agents=True,
            model_apis=frozenset({ModelApi.CHAT_COMPLETIONS}),
            max_tools=None,
            drive="port",
            max_tool_name=MAX_TOOL_NAME,
            can_compact=True,
        )

    def render_launch(
        self, profile: EngineProfile, endpoints: GatewayEndpoints, skills_dir: str
    ) -> EngineLaunch:
        return render_launch(profile, endpoints, skills_dir)

    async def open(
        self,
        sandbox: SandboxHandle,
        launch: EngineLaunch,
        restore: EngineState | None,
        *,
        correlation: Mapping[str, str] | None = None,
    ) -> OpencodeSession:
        log = self._log.bind(**dict(correlation or {}), sandboxId=sandbox.id, engineVersion=launch.engine.version)
        restored = decode_state(restore, launch.engine) if restore is not None else None
        if launch.port is None:
            raise ValueError("opencode is driven over a port")
        await verify_binary(sandbox, BINARY_PATH, launch.engine, log)
        password = self._password()
        env = {**launch.env, PASSWORD_ENV: password}
        if restored is not None:
            await put_files(sandbox, {RESTORE_PATH: restored.export})
            importer = await sandbox.exec([BINARY_PATH, "import", RESTORE_PATH], env=env)
            _, code = await finish(importer, timeout_s=IMPORT_TIMEOUT_S)
            if code != 0:
                log.warning("engine.state.restore_failed", reasonCode="import_failed")
                raise EngineStateIncompatible("opencode could not import the state", reason="import_failed")
        process = await sandbox.exec(list(launch.argv), env=env)
        http = OpencodeHttp(sandbox, launch.port, password)
        ready = asyncio.Event()
        output = asyncio.ensure_future(_watch_listening(process.stdout, ready))
        try:
            check_reported_version(launch.engine, await self._wait_healthy(http, ready, process, output), log)
            session_id = restored.session_id if restored is not None else await self._create(http)
            session = OpencodeSession(
                engine=launch.engine,
                sandbox=sandbox,
                process=process,
                http=http,
                session_id=session_id,
                env=env,
                secrets=frozenset({password}),
                log=log,
                output=output,
            )
            await session.start(connect_timeout_s=CONNECT_TIMEOUT_S)
            # No turn starts without the tool gateway's tools (E2E3-F6, ``readiness``).
            await await_tool_gateway(http, log, server=MCP_SERVER, timeout_s=self._gateway_timeout_s)
        except BaseException:
            await stop_after_failed_open(self._stop(process, http), log)
            _ = output.cancel()
            raise
        log.info("engine.session.opened", engineSessionId=session_id, restored=restored is not None)
        return session

    async def _wait_healthy(
        self,
        http: OpencodeHttp,
        ready: asyncio.Event | None = None,
        process: ProcessHandle | None = None,
        output: asyncio.Future[None] | None = None,
    ) -> str:
        """Probes until the server answers. ``ready`` is set when it prints that it listens, which it does once it
        serves: the probe or poll then in flight is dropped and the next probe goes out at once (WP-17).

        ``output`` (the drain of ``process``'s stdout) ends when the process does, or when the stream to it is lost
        (P9 FB-9): probing on would take minutes and end ``EngineUnhealthy``, so the process's own end is raised."""
        last: Exception | None = None
        probe_s = STARTUP_FIRST_PROBE_S
        for _ in range(STARTUP_ATTEMPTS):
            if process is not None and output is not None and output.done():
                await self._ended_before_serving(process)
            waiting = ready is not None and not ready.is_set()
            try:
                body = as_object(
                    await _until(http.request("GET", "/global/health", timeout_s=probe_s), ready if waiting else None)
                )
            except _Serving:
                continue
            except httpcore.TimeoutException as exc:
                last = exc
                probe_s = min(probe_s * 2, STARTUP_PROBE_TIMEOUT_S)
                continue
            # Until the server listens, a tunnel may refuse, or accept and close.
            except (httpcore.NetworkError, httpcore.RemoteProtocolError, OpencodeHttpError) as exc:
                last = exc
                with contextlib.suppress(_Serving):
                    await _until(self._sleep(STARTUP_POLL_S), ready if waiting else None)
                continue
            return as_str(body.get("version"))
        raise EngineUnhealthy("opencode did not become healthy") from last

    @staticmethod
    async def _ended_before_serving(process: ProcessHandle) -> NoReturn:
        """The process's exit as an error; the broker's own error when the stream to it was lost instead."""
        code = await process.wait(timeout_s=STOP_TIMEOUT_S)
        raise EngineCrashed(f"opencode exited with {code} before it served")

    async def _create(self, http: OpencodeHttp) -> str:
        created = as_object(await http.request("POST", "/session", {"title": "task", "agent": AGENT}))
        session_id = as_str(created.get("id"))
        if not session_id:
            raise RuntimeError("opencode returned no session id")
        return session_id

    async def _stop(self, process: ProcessHandle, http: OpencodeHttp) -> None:
        await http.aclose()
        await process.signal(signal.SIGTERM)
        await process.wait(timeout_s=STOP_TIMEOUT_S)


__all__ = ["OpencodeAdapter"]
