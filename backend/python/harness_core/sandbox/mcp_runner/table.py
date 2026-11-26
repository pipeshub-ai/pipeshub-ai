"""The bookkeeping every ``MCPRunner`` adapter shares: one runner per key, sessions, idle and age.

An adapter supplies how a runner starts (``_start``) and what a started runner
can do (``RunnerBackend``); this class decides when to start, reuse, retire and
stop, refuses before anything starts, and logs each refusal once.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets as token_bytes
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harness_core.logs import HarnessLog
from harness_core.sandbox.mcp_runner.errors import (
    MCPRunnerRefused,
    RunnerOwnerMismatch,
    RunnerStartFailed,
    RunnerUnavailable,
)
from harness_core.sandbox.mcp_runner.policy import check_request, secrets_fingerprint
from harness_core.sandbox.mcp_runner.types import (
    RunnerConfig,
    RunnerInfo,
    RunnerSecrets,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.clock import Clock
    from harness_core.ids import IdSource
    from harness_core.sandbox.mcp_runner.types import RunnerKey, RunnerRequest
    from harness_core.sandbox.ports import ByteStream

    # No: the server no longer runs for the key. Secrets: it does, and would start with these now.
    Retain = Callable[[RunnerKey], Awaitable[bool | RunnerSecrets]]

_LOG_NAME = "harness_core.sandbox.mcp_runner"


class RunnerBackend(Protocol):
    """A started runner as its adapter holds it."""

    @property
    def sandbox_id(self) -> str | None: ...

    def alive(self) -> bool: ...

    async def connect(self) -> ByteStream: ...

    async def stop(self) -> None: ...


@dataclass
class _Runner:
    runner_id: str
    key: RunnerKey
    spec_digest: str
    fingerprint: str
    backend: RunnerBackend
    started_at: float
    last_used: float
    sessions: int = 0
    retired: bool = False

    def info(self) -> RunnerInfo:
        return RunnerInfo(
            runner_id=self.runner_id,
            key=self.key,
            spec_digest=self.spec_digest,
            sandbox_id=self.backend.sandbox_id,
            sessions=self.sessions,
            started_at=self.started_at,
            last_used=self.last_used,
        )


class _Session:
    """A session's stream; closing it frees the runner for the idle clock."""

    def __init__(self, stream: ByteStream, table: RunnerTable, runner: _Runner) -> None:
        self._stream = stream
        self._table = table
        self._runner = runner
        self._closed = False

    async def read(self, max_bytes: int = 65536) -> bytes:
        return await self._stream.read(max_bytes)

    async def write(self, data: bytes) -> None:
        await self._stream.write(data)

    async def write_eof(self) -> None:
        await self._stream.write_eof()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._stream.aclose()
        finally:
            self._table.session_closed(self._runner)


class RunnerTable:
    """``MCPRunner`` minus how a runner starts: subclasses implement ``_start`` (and may extend ``sweep``)."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdSource,
        config: RunnerConfig | None = None,
        fingerprint_key: bytes | None = None,
        retain: Retain | None = None,
    ) -> None:
        """``retain`` answers whether a runner's server still runs for its key (its instance not deleted or
        disabled, its owner still signed in); the sweep stops one it answers no for, sessions or not
        (R36COVER-FU-4, R43MCP-FU-2). Answered with the secrets it would start with now, a runner holding others is
        retired: stopped once its sessions end, as the next open would replace it."""
        self._retain = retain
        self._clock = clock
        self._ids = ids
        self.config = config or RunnerConfig()
        # Process-local and random: a fingerprint says "same secret or not" and nothing else.
        self._fingerprint_key = fingerprint_key or token_bytes.token_bytes(32)
        self._by_key: dict[str, _Runner] = {}
        self._retired: list[_Runner] = []
        self._locks: dict[str, asyncio.Lock] = {}
        self._log = HarnessLog.named(_LOG_NAME)

    async def _start(self, runner_id: str, request: RunnerRequest) -> RunnerBackend:
        raise NotImplementedError

    async def open(self, request: RunnerRequest) -> ByteStream:
        log = self._log.bind(orgId=request.key.tenant, mcpServer=request.key.server_id, keyDigest=request.key.digest())
        try:
            check_request(request)
            return await self._open(request, log)
        except MCPRunnerRefused as refused:
            log.warning("mcp_runner.refused", reasonCode=refused.reason_code, cause=getattr(refused, "cause", None))
            raise

    async def _open(self, request: RunnerRequest, log: HarnessLog) -> ByteStream:
        slot = request.key.digest()
        fingerprint = secrets_fingerprint(request.secrets, self._fingerprint_key)
        async with self._locks.setdefault(slot, asyncio.Lock()):
            runner = self._by_key.get(slot)
            if runner is not None and runner.key.owner != request.principal:
                raise RunnerOwnerMismatch("a runner is handed only to its own owner")
            if runner is not None and not self._reusable(runner, request.spec.digest(), fingerprint):
                await self._retire(runner, "replaced" if runner.backend.alive() else "dead")
                runner = None
            if runner is None:
                runner = await self._new_runner(slot, request, fingerprint, log)
            try:
                stream = await self._connect(runner)
            except MCPRunnerRefused:
                raise
            except Exception as exc:
                # The bridge went away between our look and the connect: one fresh runner, then give up.
                await self._retire(runner, "connect_failed")
                runner = await self._new_runner(slot, request, fingerprint, log)
                try:
                    stream = await self._connect(runner)
                except Exception as again:
                    await self._retire(runner, "connect_failed")
                    raise RunnerStartFailed(f"runner unreachable: {type(again).__name__}") from exc
            runner.last_used = self._clock.monotonic()
            return _Session(stream, self, runner)

    async def _connect(self, runner: _Runner) -> ByteStream:
        # Counted before the connect yields: the sweep takes no slot lock, and an uncounted runner looks idle to it.
        runner.sessions += 1
        try:
            return await runner.backend.connect()
        except BaseException:
            runner.sessions -= 1
            raise

    def _reusable(self, runner: _Runner, spec_digest: str, fingerprint: str) -> bool:
        return runner.backend.alive() and runner.spec_digest == spec_digest and runner.fingerprint == fingerprint

    async def _new_runner(self, slot: str, request: RunnerRequest, fingerprint: str, log: HarnessLog) -> _Runner:
        await self._make_room(request.key.tenant)
        runner_id = self._ids.new_id("mcpr")
        began = self._clock.monotonic()
        backend = await self._start(runner_id, request)
        now = self._clock.monotonic()
        runner = _Runner(
            runner_id=runner_id,
            key=request.key,
            spec_digest=request.spec.digest(),
            fingerprint=fingerprint,
            backend=backend,
            started_at=now,
            last_used=now,
        )
        self._by_key[slot] = runner
        log.info("mcp_runner.started", runnerId=runner_id, sandboxId=backend.sandbox_id, ms=int((now - began) * 1000))
        return runner

    async def _make_room(self, tenant: str) -> None:
        """Past the org's cap, its least recently used idle runner makes way; with none idle the start is refused
        (``runner_capacity``) rather than taking another org's share of the host."""
        mine = [r for r in self._by_key.values() if r.key.tenant == tenant]
        if len(mine) < self.config.max_per_tenant:
            return
        idle = sorted((r for r in mine if r.sessions == 0), key=lambda r: r.last_used)
        if not idle:
            raise RunnerUnavailable(
                f"this organization already runs {len(mine)} MCP servers here, all in use", cause="runner_capacity"
            )
        await self._stop(idle[0], "org_cap")

    async def _retire(self, runner: _Runner, reason: str) -> None:
        slot = runner.key.digest()
        if self._by_key.get(slot) is runner:
            del self._by_key[slot]
        if runner.sessions == 0:
            await self._stop(runner, reason)
        else:
            runner.retired = True
            self._retired.append(runner)

    async def _stop(self, runner: _Runner, reason: str) -> None:
        with contextlib.suppress(ValueError):
            self._retired.remove(runner)
        slot = runner.key.digest()
        if self._by_key.get(slot) is runner:
            del self._by_key[slot]
        try:
            await runner.backend.stop()
        except Exception as exc:
            self._log.warning(
                "mcp_runner.stop_failed", runnerId=runner.runner_id, reason=reason, errorType=type(exc).__name__
            )
            return
        self._log.info(
            "mcp_runner.stopped",
            orgId=runner.key.tenant,
            mcpServer=runner.key.server_id,
            runnerId=runner.runner_id,
            sandboxId=runner.backend.sandbox_id,
            reason=reason,
        )

    def session_closed(self, runner: _Runner) -> None:
        runner.sessions = max(0, runner.sessions - 1)
        runner.last_used = self._clock.monotonic()

    def _due(self, runner: _Runner, now: float) -> str | None:
        if runner.sessions > 0:
            return None
        if runner.retired:
            return "replaced"
        if not runner.backend.alive():
            return "dead"
        if now - runner.started_at >= self.config.max_age_s:
            return "max_age"
        if now - runner.last_used >= self.config.idle_ttl_s:
            return "idle"
        return None

    async def sweep(self) -> int:
        stopped = await self._stop_removed()
        now = self._clock.monotonic()
        for runner in [*self._retired, *self._by_key.values()]:
            reason = self._due(runner, now)
            if reason is not None:
                await self._stop(runner, reason)
                stopped += 1
        return stopped

    async def _stop_removed(self) -> int:
        """The runners whose server no longer runs for their key: stopped now, not at their idle time. A check that
        fails keeps the runner (its idle and age limits still stop it) and is logged."""
        if self._retain is None:
            return 0
        stopped = 0
        for runner in list(self._by_key.values()):
            try:
                kept = await self._retain(runner.key)
            except Exception as exc:
                self._log.warning(
                    "mcp_runner.retain_check_failed", mcpServer=runner.key.server_id, errorType=type(exc).__name__
                )
                continue
            if self._by_key.get(runner.key.digest()) is not runner:
                continue
            # Not ``not kept``: an empty ``RunnerSecrets`` (a server with no secret) is falsy and still runs.
            if kept is False:
                await self._stop(runner, "instance_removed")
                stopped += 1
            elif isinstance(kept, RunnerSecrets) and secrets_fingerprint(kept, self._fingerprint_key) != runner.fingerprint:
                idle = runner.sessions == 0
                await self._retire(runner, "secret_changed")
                stopped += int(idle)
        return stopped

    async def stop_all(self) -> int:
        doomed = [*self._retired, *self._by_key.values()]
        for runner in doomed:
            await self._stop(runner, "stopped")
        return len(doomed)

    def runners(self) -> tuple[RunnerInfo, ...]:
        return tuple(runner.info() for runner in [*self._by_key.values(), *self._retired])


__all__ = ["RunnerBackend", "RunnerTable"]
