"""An in-memory ``SandboxBroker``: no processes, no containers, deterministic.

Programs are Python coroutines looked up by ``argv[0]``: a sandbox's own
(``FakeSandboxHandle.install``), then the broker's (``FAKE_PROGRAMS`` plus any a
test adds). Each sandbox has an in-memory filesystem in which only mount
targets and ``/tmp`` are writable, mirroring a read-only root, a loopback
"network" that ``connect`` reaches, and request-level egress (``FakeEgress``)
for programs that stand in for engines.

With ``persistent_mounts=True`` a mount's ``source`` names a directory of the
broker's own store that outlives the sandbox (as Docker's data volume does):
it is copied in at acquire and written back, deletions included, whenever the
sandbox is destroyed (release, reap or a warm sweep), so a killed sandbox's
writes persist as they would in a live mount.
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import json
import posixpath
import signal as signals
import stat
import tarfile
import time
from collections.abc import MutableMapping
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, cast, override

from harness_core.engines.background import LIST_SCRIPT, STOP_SCRIPT
from harness_core.engines.binary import BinaryObservation, vouched_binaries
from harness_core.engines.snapshots import CAPTURE_SCRIPT, RESTORE_ARGV
from harness_core.failpoints import afailpoint
from harness_core.sandbox.egress.admission import admit_egress
from harness_core.sandbox.errors import (
    ImageNotAllowed,
    InvalidSandboxRequest,
    InvalidSandboxSpec,
    IsolationUnavailable,
    SandboxConnectError,
    SandboxError,
    SandboxNotFound,
    SandboxPathNotFound,
)
from harness_core.sandbox.ledger import AcquireLedger, key_digest
from harness_core.sandbox.metering import NOOP_BROKER_METRICS, SandboxMeter
from harness_core.sandbox.pool import (
    CapacityProbe,
    WarmPool,
    WarmPoolConfig,
    WarmSandbox,
    WarmShape,
)
from harness_core.sandbox.streams import (
    BytePipe,
    ChunkChannel,
    MemoryByteStream,
    collect,
    memory_stream_pair,
)
from harness_core.sandbox.types import (
    ENGINE_IMAGES,
    PERSISTENT_MOUNTS,
    BrokerHealth,
    MountSpec,
    OrgSandboxErasure,
    RuntimeMode,
    SandboxSpec,
    engine_images_detail,
    is_safe_segment,
)
from harness_core.sandbox.workspace_fs import MOVE_SCRIPT, READ_SCRIPT, WRITE_SCRIPT
from harness_core.testing.ids import SeededIds
from harness_core.testing.sandbox.contract import (
    WORK,
    EgressContractEnv,
    SandboxContractEnv,
)
from harness_core.testing.sandbox.egress import (
    EgressDenied,
    FakeEgress,
    FakeEgressProxy,
)

if TYPE_CHECKING:
    from collections.abc import (
        AsyncIterator,
        Callable,
        Coroutine,
        Iterator,
        Mapping,
        Sequence,
    )

    from harness_core.clock import Clock
    from harness_core.engines.types import JsonValue
    from harness_core.ids import IdSource
    from harness_core.sandbox.metering import BrokerMetrics
    from harness_core.sandbox.ports import ByteStream, ProcessHandle
    from harness_core.sandbox.types import LeaseRef
    from harness_core.testing.clock import ManualClock
    from harness_core.testing.sandbox.egress import EgressRequest

    FakeProgram = Callable[["FakeProcessContext"], Coroutine[Any, Any, int]]
    PortHandler = Callable[[ByteStream], Coroutine[Any, Any, None]]


_Pipe = BytePipe
FakeStream = MemoryByteStream
_connection = memory_stream_pair

# Stops every process of the sandbox but the caller, as SIGSTOP does (CF-09: an engine that answers nothing).
STALL_SCRIPT = "kill -STOP -1 2>/dev/null; exit 0"
# The program whose coroutine is running: what a port it listens on belongs to.
_RUNNING: contextvars.ContextVar[FakeProcessContext | None] = contextvars.ContextVar("fake_running", default=None)


@dataclass
class _File:
    """A regular file (``mode`` is its permission bits) or a symbolic link (``data`` is the target)."""

    data: bytes
    mode: int

    @property
    def is_symlink(self) -> bool:
        return stat.S_ISLNK(self.mode)


SYMLINK = stat.S_IFLNK | 0o777


@dataclass(frozen=True, slots=True)
class ExecRecord:
    argv: tuple[str, ...]
    env: Mapping[str, str]


@dataclass
class FakeSandboxState:
    sandbox_id: str
    spec: SandboxSpec
    created_at: float
    egress: FakeEgress = field(default_factory=FakeEgress)
    files: dict[str, _File] = field(default_factory=dict[str, _File])
    dirs: set[str] = field(default_factory=set[str])
    listeners: dict[int, asyncio.Queue[FakeStream]] = field(default_factory=dict[int, "asyncio.Queue[FakeStream]"])
    processes: list[FakeProcess] = field(default_factory=list["FakeProcess"])
    programs: dict[str, FakeProgram] = field(default_factory=dict[str, "FakeProgram"])
    execs: list[ExecRecord] = field(default_factory=list[ExecRecord])
    servers: dict[int, asyncio.Task[None]] = field(default_factory=dict[int, "asyncio.Task[None]"])
    port_owners: dict[int, FakeProcessContext] = field(default_factory=dict[int, "FakeProcessContext"])
    # A warm sandbox (P5.9a): created from ``shape`` with no run's labels, bound once at acquire.
    shape: WarmShape | None = None
    claimed: bool = False
    # A test's stand-in for the container's OOMKilled: the memory limit ended a process in this sandbox.
    out_of_memory: bool = False

    @property
    def pooled(self) -> bool:
        return self.shape is not None and not self.claimed

    def writable(self, path: str) -> bool:
        """Where a program may write: writable mounts and /tmp."""
        return path == "/tmp" or path.startswith("/tmp/") or self.archivable(path)

    def archivable(self, path: str) -> bool:
        """Where ``put_tar`` may write: writable mounts only (Docker refuses /tmp on a read-only root)."""
        mount = self.spec.mount_for(path)
        return mount is not None and not mount.read_only


class FakeProcessContext:
    """What a fake program sees: argv, env, stdio, the sandbox's files and loopback.

    ``sandbox`` is the sandbox's state; ``handle`` is the sandbox as a caller
    holds it (files, egress, ``listen``), for programs that act like servers.
    """

    def __init__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str],
        handle: FakeSandboxHandle,
        stdin: _Pipe,
        stdout: ChunkChannel,
        stderr: ChunkChannel,
    ) -> None:
        self.argv = list(argv)
        self.env = dict(env)
        self.handle = handle
        self.sandbox = handle.state
        self._stdin = stdin
        self.stdout = stdout
        self.stderr = stderr
        self._listening: dict[int, asyncio.Queue[FakeStream]] = {}
        # Cleared while the process is stopped (SIGSTOP): it then reads, writes and answers nothing.
        self.running = asyncio.Event()
        self.running.set()

    async def emit(self, data: bytes, *, stderr: bool = False) -> None:
        await self.running.wait()
        await (self.stderr if stderr else self.stdout).put(data)

    async def read_stdin(self, max_bytes: int = 65536) -> bytes:
        await self.running.wait()
        data = await self._stdin.read(max_bytes)
        # A process stopped while it waited sees what arrived only once it continues.
        await self.running.wait()
        return data

    def listen(self, port: int) -> asyncio.Queue[FakeStream]:
        queue: asyncio.Queue[FakeStream] = asyncio.Queue()
        self.sandbox.listeners[port] = queue
        self.sandbox.port_owners[port] = self
        self._listening[port] = queue
        return queue

    def close_listeners(self) -> None:
        """A process that ends stops listening at once: a later ``connect`` is refused, as on a real host."""
        for port, queue in self._listening.items():
            if self.sandbox.listeners.get(port) is queue:
                del self.sandbox.listeners[port]
        for port in [p for p, owner in self.sandbox.port_owners.items() if owner is self]:
            del self.sandbox.port_owners[port]
        self._listening.clear()

    def write_file(self, path: str, data: bytes) -> None:
        if not self.sandbox.writable(path):
            raise PermissionError(path)
        self.sandbox.files[path] = _File(data, 0o644)

    def remove_file(self, path: str) -> None:
        if not self.sandbox.writable(path):
            raise PermissionError(path)
        del self.sandbox.files[path]


class _ServingEnd:
    """The server's end of a connection to a port a process listens on: it moves no bytes while that process is
    stopped."""

    def __init__(self, inner: FakeStream, owner: FakeProcessContext) -> None:
        self._inner = inner
        self._owner = owner

    async def read(self, max_bytes: int = 65536) -> bytes:
        await self._owner.running.wait()
        data = await self._inner.read(max_bytes)
        await self._owner.running.wait()
        return data

    async def write(self, data: bytes) -> None:
        await self._owner.running.wait()
        await self._inner.write(data)

    async def write_eof(self) -> None:
        await self._owner.running.wait()
        await self._inner.write_eof()

    async def aclose(self) -> None:
        await self._inner.aclose()


class FakeProcess:
    def __init__(self, context: FakeProcessContext, stdin: _Pipe, *, stdin_open: bool) -> None:
        self._context = context
        self._stdin = stdin
        self._stdin_open = stdin_open
        self._exit: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._task: asyncio.Task[int] | None = None
        self._killed_by: int | None = None
        # A signal sent while stopped: delivered when the process continues; SIGKILL alone acts at once.
        self._pending: int | None = None
        # As ``/proc`` numbers it (``sh -c LIST_SCRIPT``): set when the sandbox starts it.
        self.pid = 0

    def start(self, program: FakeProgram) -> None:
        async def run() -> int:
            _ = _RUNNING.set(self._context)
            return await program(self._context)

        self._task = asyncio.get_running_loop().create_task(run())
        self._task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[int]) -> None:
        if task.cancelled():
            code = 128 + (self._killed_by or signals.SIGKILL)
        elif task.exception() is not None:
            code = 1
        else:
            code = task.result()
        self._context.close_listeners()
        # Nothing waits on a dead process to continue.
        self._context.running.set()
        self._context.stdout.close()
        self._context.stderr.close()
        self._stdin.channel.close()
        if not self._exit.done():
            self._exit.set_result(code)

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return aiter(self._context.stdout)

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return aiter(self._context.stderr)

    async def write(self, data: bytes) -> None:
        if not self._stdin_open or self._stdin.channel.closed:
            raise InvalidSandboxRequest("stdin is not open")
        await self._stdin.channel.put(data)

    async def close_stdin(self) -> None:
        self._stdin.channel.close()

    async def signal(self, sig: int) -> None:
        if self._task is None or self._task.done():
            return
        running = self._context.running
        if sig == signals.SIGSTOP:
            running.clear()
            return
        if sig == signals.SIGCONT:
            running.set()
            if self._pending is None:
                return
            sig, self._pending = self._pending, None
        elif not running.is_set() and sig != signals.SIGKILL:
            self._pending = sig
            return
        self._killed_by = sig
        _ = self._task.cancel()

    @property
    def stopped(self) -> bool:
        return not self._context.running.is_set()

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await asyncio.wait_for(asyncio.shield(self._exit), timeout_s)

    @property
    def running(self) -> bool:
        return not self._exit.done()

    @property
    def context(self) -> FakeProcessContext:
        return self._context

    def kill(self) -> None:
        if self._task is not None and not self._task.done():
            self._killed_by = signals.SIGKILL
            self._context.close_listeners()
            _ = self._task.cancel()


class _FileView(MutableMapping[str, bytes]):
    """The sandbox's files as bytes by path, for tests and fake programs (no permission checks)."""

    def __init__(self, files: dict[str, _File]) -> None:
        self._files = files

    @override
    def __getitem__(self, path: str) -> bytes:
        return self._files[path].data

    @override
    def __setitem__(self, path: str, data: bytes) -> None:
        current = self._files.get(path)
        self._files[path] = _File(data, current.mode if current is not None else 0o644)

    @override
    def __delitem__(self, path: str) -> None:
        del self._files[path]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._files)

    @override
    def __len__(self) -> int:
        return len(self._files)


class FakeSandboxHandle:
    def __init__(self, broker: FakeSandboxBroker, state: FakeSandboxState) -> None:
        self._broker = broker
        self._state = state

    @property
    def state(self) -> FakeSandboxState:
        return self._state

    @property
    def files(self) -> MutableMapping[str, bytes]:
        return _FileView(self._state.files)

    @property
    def egress(self) -> FakeEgress:
        return self._state.egress

    @property
    def processes(self) -> list[FakeProcess]:
        return self._state.processes

    @property
    def execs(self) -> list[ExecRecord]:
        return self._state.execs

    def put_file(self, path: str, data: bytes, mode: int = 0o644) -> None:
        """Write a file as a process in the sandbox would (no permission check: test set-up)."""
        self._state.files[path] = _File(bytes(data), mode & 0o7777)

    def put_symlink(self, path: str, target: str) -> None:
        self._state.files[path] = _File(target.encode("utf-8", "surrogateescape"), SYMLINK)

    def make_dir(self, path: str) -> None:
        self._state.dirs.add(path)

    def install(self, name: str, program: FakeProgram) -> None:
        """A program only this sandbox runs (it shadows the broker's of the same name), by name or by path."""
        self._state.programs[posixpath.basename(name)] = program

    def listen(self, port: int, handler: PortHandler) -> None:
        """Serve each connection to ``port`` with ``handler``, as a program's server would."""
        state = self._live()
        if port in state.listeners:
            raise SandboxError(f"port {port} is in use")
        queue: asyncio.Queue[FakeStream] = asyncio.Queue()
        state.listeners[port] = queue
        owner = _RUNNING.get()
        if owner is not None:
            state.port_owners[port] = owner

        async def serve() -> None:
            connections: set[asyncio.Task[None]] = set()
            try:
                while True:
                    conn = await queue.get()
                    task = asyncio.get_running_loop().create_task(handler(conn))
                    connections.add(task)
                    task.add_done_callback(connections.discard)
            finally:
                for task in connections:
                    _ = task.cancel()

        state.servers[port] = asyncio.get_running_loop().create_task(serve())

    def unlisten(self, port: int) -> None:
        _ = self._state.listeners.pop(port, None)
        _ = self._state.port_owners.pop(port, None)
        server = self._state.servers.pop(port, None)
        if server is not None:
            _ = server.cancel()

    @property
    def id(self) -> str:
        return self._state.sandbox_id

    @property
    def mode(self) -> RuntimeMode:
        return self._state.spec.mode

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        return self._broker.vouched(self._state.spec)

    async def memory_exhausted(self) -> bool:
        return self._state.out_of_memory

    def _live(self) -> FakeSandboxState:
        _ = self._broker.ledger.held(self._state.sandbox_id)
        return self._state

    async def exec(
        self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False
    ) -> ProcessHandle:
        state = self._live()
        if not argv:
            raise InvalidSandboxRequest("argv is empty")
        stdin_pipe = _Pipe()
        context = FakeProcessContext(argv, env, self, stdin_pipe, ChunkChannel(), ChunkChannel())
        process = FakeProcess(context, stdin_pipe, stdin_open=stdin)
        process.pid = 100 + len(state.processes)
        if not stdin:
            stdin_pipe.channel.close()
        name = posixpath.basename(argv[0])
        program = state.programs.get(name) or self._broker.programs.get(argv[0], _not_found)
        process.start(program)
        state.processes.append(process)
        state.execs.append(ExecRecord(tuple(argv), dict(env)))
        self._broker.calls.append(("exec", state.sandbox_id, tuple(argv)))
        return process

    async def connect(self, port: int) -> ByteStream:
        state = self._live()
        listener = state.listeners.get(port)
        if listener is None:
            raise SandboxConnectError(f"nothing listens on port {port}")
        client, server = _connection()
        owner = state.port_owners.get(port)
        await listener.put(server if owner is None else cast("FakeStream", _ServingEnd(server, owner)))
        return client

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        state = self._live()
        if not state.archivable(dest):
            raise SandboxError(f"{dest} is not a writable mount")
        _extract(state, dest, await collect(stream))
        self._broker.calls.append(("put_tar", state.sandbox_id, (dest,)))

    async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        state = self._live()
        if not state.spec.seedable(dest):
            raise InvalidSandboxRequest(f"{dest} is not in a read-only scratch mount")
        _extract(state, dest, await collect(stream))
        self._broker.calls.append(("seed_tar", state.sandbox_id, (dest,)))

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        state = self._live()
        src = posixpath.normpath(src)
        prefix = src.rstrip("/") + "/"
        members = {path: f for path, f in state.files.items() if path == src or path.startswith(prefix)}
        if not members and src not in state.dirs and not any(d.startswith(prefix) for d in state.dirs):
            raise SandboxPathNotFound(f"{src} does not exist")
        base = posixpath.basename(src)
        dirs = sorted(d for d in state.dirs if d == src or d.startswith(prefix))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in dirs:
                info = tarfile.TarInfo(base if path == src else posixpath.join(base, path[len(prefix) :]))
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                archive.addfile(info)
            for path in sorted(members):
                rel = base if path == src else posixpath.join(base, path[len(prefix) :])
                info = tarfile.TarInfo(rel)
                entry = members[path]
                if entry.is_symlink:
                    info.type = tarfile.SYMTYPE
                    info.linkname = entry.data.decode("utf-8", "surrogateescape")
                    info.mode = 0o777
                    archive.addfile(info)
                    continue
                info.size = len(entry.data)
                info.mode = entry.mode
                archive.addfile(info, io.BytesIO(entry.data))
        payload = buffer.getvalue()

        async def chunks() -> AsyncIterator[bytes]:
            yield payload

        return chunks()


def _extract(state: FakeSandboxState, dest: str, data: bytes) -> None:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive:
            name = posixpath.normpath(posixpath.join(dest, member.name))
            if not (name == dest or name.startswith(dest.rstrip("/") + "/")):
                raise InvalidSandboxRequest(f"tar member {member.name!r} escapes {dest}")
            if member.isdir():
                _ = state.files.pop(name, None)
                state.dirs.add(name)
            elif member.issym():
                state.files[name] = _File(member.linkname.encode("utf-8", "surrogateescape"), SYMLINK)
            elif member.isfile():
                extracted = archive.extractfile(member)
                content = extracted.read() if extracted is not None else b""
                state.files[name] = _File(content, member.mode & 0o7777)


async def _not_found(context: FakeProcessContext) -> int:
    await context.stderr.put(f"{context.argv[0]}: not found\n".encode())
    return 127


class FakeSandboxBroker:
    """``SandboxBroker`` over in-memory sandboxes; ``calls`` records what happened."""

    def __init__(
        self,
        *,
        clock: Clock,
        ids: IdSource | None = None,
        modes: frozenset[RuntimeMode] = frozenset({RuntimeMode.DOCKER_HARDENED}),
        programs: Mapping[str, FakeProgram] | None = None,
        reserved_label_prefix: str = "fake.broker.",
        egress: FakeEgress | None = None,
        egress_proxy: FakeEgressProxy | None = None,
        persistent_mounts: bool = False,
        warm_pool: WarmPoolConfig | None = None,
        memory_mb: int = 16 * 1024,
        verified_binaries: Mapping[str, Mapping[str, str]] | None = None,
        image_files: Mapping[str, Mapping[str, bytes]] | None = None,
        image_digests: Mapping[str, str] | None = None,
        engine_images: Mapping[str, str] | None = None,
        metrics: BrokerMetrics = NOOP_BROKER_METRICS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self.meter = SandboxMeter(metrics, monotonic=monotonic)
        # image -> the digest it answers to now; a test "rebuilds" an image by changing it (C2.20).
        self.image_digests = dict(image_digests or {})
        # kind@version -> digest, reported in health (``ENGINE_IMAGES``) as the Docker broker does; empty: not said.
        self.engine_images = dict(engine_images or {})
        # image -> absolute path -> content: files the image itself carries, in every sandbox made from it.
        self._image_files = {image: dict(files) for image, files in (image_files or {}).items()}
        # image -> path -> digest: what a real broker would have read from that image (EH2).
        self._verified = {image: dict(files) for image, files in (verified_binaries or {}).items()}
        self.memory_mb = memory_mb
        self._persistent = persistent_mounts
        # source key -> path relative to the mount -> file; only with persistent mounts.
        self.volumes: dict[str, dict[str, _File]] = {}
        self._egress = egress
        self.egress_proxy = egress_proxy
        self._ids = ids or SeededIds()
        self._modes = modes
        self.programs: dict[str, FakeProgram] = dict(FAKE_PROGRAMS if programs is None else programs)
        self._reserved = reserved_label_prefix
        self.ledger = AcquireLedger(clock)
        self.sandboxes: dict[str, FakeSandboxState] = {}
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.pool = WarmPool(self, warm_pool or WarmPoolConfig(ceiling=0), clock=clock, metrics=metrics)

    def modes(self) -> frozenset[RuntimeMode]:
        return self._modes

    def vouched(self, spec: SandboxSpec) -> dict[str, str]:
        """As the Docker broker vouches: never for a file a mount of ``spec`` or ``/tmp`` could replace."""
        files = self._verified.get(spec.image_digest, {})
        return vouched_binaries(
            (BinaryObservation(path, path, digest) for path, digest in files.items()), spec, writable=("/tmp",)
        )

    async def acquire(self, spec: SandboxSpec, *, lease: LeaseRef, idempotency_key: str) -> FakeSandboxHandle:
        proxied = self._validate(spec)
        key = key_digest(idempotency_key)
        try:
            async with self.pool.acquiring(), self.ledger.lock(key):
                entry = self.ledger.by_key(key)
                if entry is not None:
                    self.ledger.admit(entry, lease)
                    state = self.sandboxes[entry.sandbox_id]
                    await self._rebind(state, spec, proxied=proxied)
                    self.meter.bound(state.sandbox_id, state.spec)
                    return FakeSandboxHandle(self, state)
                await asyncio.sleep(0)
                state = await self._from_pool(spec, proxied=proxied)
                if state is None:
                    self.pool.learn(spec)
                    _ = await self.pool.make_room(spec)
                    state = await self._new_state(spec, proxied=proxied)
                    self.calls.append(("acquire", state.sandbox_id, ()))
                else:
                    self.calls.append(("acquire_warm", state.sandbox_id, ()))
                _ = self.ledger.record(key, state.sandbox_id, lease)
                self.meter.bound(state.sandbox_id, spec)
                return FakeSandboxHandle(self, state)
        finally:
            self.pool.kick()

    async def _rebind(self, state: FakeSandboxState, spec: SandboxSpec, *, proxied: bool) -> None:
        """As the Docker broker: a later turn's re-acquire brings its egress binding; nothing else changes."""
        if spec.egress == state.spec.egress and spec.labels == state.spec.labels:
            return
        if {d.host for d in spec.egress.destinations} != {d.host for d in state.spec.egress.destinations}:
            raise InvalidSandboxSpec("a re-acquire cannot change where the sandbox may reach", reason="invalid_spec")
        if proxied and self.egress_proxy is not None:
            # Bound again in place, never unbound first: the Docker broker's re-acquire only re-registers, so the
            # proxy's tunnels and token cache see what they see live (SIGNFIX).
            state.egress = await self.egress_proxy.bind(state.sandbox_id, spec)
        state.spec = replace(state.spec, egress=spec.egress, labels=spec.labels)
        self.calls.append(("rebind", state.sandbox_id, ()))

    async def _new_state(self, spec: SandboxSpec, *, proxied: bool, shape: WarmShape | None = None) -> FakeSandboxState:
        sandbox_id = self._ids.new_id("sbx")
        state = FakeSandboxState(sandbox_id, spec, self._clock.now().timestamp(), shape=shape)
        for path, content in self._image_files.get(spec.image_digest, {}).items():
            state.files[path] = _File(content, 0o444)
        if self._egress is not None and shape is None:
            state.egress = self._egress
        if proxied and self.egress_proxy is not None:
            state.egress = await self.egress_proxy.bind(sandbox_id, spec)
        for mount in spec.mounts:
            state.dirs.add(mount.target)
            if self._persistent and mount.source is not None:
                volume = self.volumes.setdefault(mount.source, {})
                for rel, entry in volume.items():
                    state.files[posixpath.join(mount.target, rel)] = _File(entry.data, entry.mode)
        self.sandboxes[sandbox_id] = state
        return state

    async def _from_pool(self, spec: SandboxSpec, *, proxied: bool) -> FakeSandboxState | None:
        warm = await self.pool.take(spec)
        if warm is None:
            return None
        state = self.sandboxes[warm.sandbox_id]
        try:
            await afailpoint("broker.warm.after_claim")
            state.spec = spec
            state.created_at = self._clock.now().timestamp()
            if self._egress is not None:
                state.egress = self._egress
            if proxied and self.egress_proxy is not None:
                state.egress = await self.egress_proxy.bind(state.sandbox_id, spec)
        except Exception:
            # Never handed out half-bound: destroyed, and this acquire goes cold.
            await self.discard_warm(warm, reason="bind_failed")
            return None
        return state

    async def create_warm(self, shape: WarmShape, labels: Mapping[str, str]) -> WarmSandbox:
        state = await self._new_state(shape.template(labels), proxied=False, shape=shape)
        self.calls.append(("create_warm", state.sandbox_id, ()))
        return WarmSandbox(state.sandbox_id, shape, state.created_at)

    async def claim_warm(self, sandbox: WarmSandbox) -> bool:
        state = self.sandboxes.get(sandbox.sandbox_id)
        if state is None or not state.pooled:
            return False
        state.claimed = True
        return True

    async def discard_warm(self, sandbox: WarmSandbox, *, reason: str) -> None:
        self._destroy(sandbox.sandbox_id)

    async def list_warm(self) -> list[WarmSandbox]:
        return [
            WarmSandbox(state.sandbox_id, state.shape, state.created_at)
            for state in self.sandboxes.values()
            if state.pooled and state.shape is not None
        ]

    async def probe_capacity(self) -> CapacityProbe:
        warm = [state for state in self.sandboxes.values() if state.pooled]
        held = [state for state in self.sandboxes.values() if not state.pooled]
        return CapacityProbe(
            memory_mb=self.memory_mb,
            held=len(held),
            warm=len(warm),
            held_mb=sum(state.spec.resources.memory_mb for state in held),
            warm_mb=sum(state.spec.resources.memory_mb for state in warm),
        )

    def holds(self, sandbox_id: str) -> bool:
        state = self.sandboxes.get(sandbox_id)
        return state is not None and not state.pooled

    async def fill_warm(self) -> int:
        return await self.pool.refill()

    async def trim_warm(self) -> int:
        return await self.pool.trim()

    def warm_ids(self) -> frozenset[str]:
        return self.pool.warm_ids()

    def _validate(self, spec: SandboxSpec) -> bool:
        if spec.mode not in self._modes:
            raise IsolationUnavailable(f"mode {spec.mode} is not available", reason="mode_unavailable")
        errors = spec.validation_errors()
        errors += [f"label {k!r} is reserved" for k in spec.labels if k.startswith(self._reserved)]
        if errors:
            raise InvalidSandboxSpec("; ".join(errors), reason="invalid_spec")
        current = self.image_digests.get(spec.image_digest)
        if spec.image_pin is not None and current is not None and current != spec.image_pin:
            raise ImageNotAllowed(
                f"image {spec.image_digest!r} is not the pinned {spec.image_pin}", reason="image_digest_mismatch"
            )
        return admit_egress(spec, proxy_configured=self.egress_proxy is not None)

    async def release(self, handle: object, *, keep_warm_s: int = 0) -> None:
        sandbox_id = _handle_id(handle)
        entry = self.ledger.by_id(sandbox_id)
        if entry is None:
            return
        if keep_warm_s > 0:
            self.ledger.keep_warm(sandbox_id, keep_warm_s)
            self.meter.kept(sandbox_id)
            return
        self._destroy(sandbox_id)
        self.pool.kick()

    def sweep_warm(self) -> int:
        expired = self.ledger.expired_warm()
        for entry in expired:
            self._destroy(entry.sandbox_id)
        return len(expired)

    def _destroy(self, sandbox_id: str) -> None:
        _ = self.meter.ended(sandbox_id)
        _ = self.ledger.forget(sandbox_id)
        state = self.sandboxes.pop(sandbox_id, None)
        if state is not None:
            self._write_back(state)
            for process in state.processes:
                process.kill()
            for server in state.servers.values():
                _ = server.cancel()
            if self.egress_proxy is not None:
                self.egress_proxy.unbind(sandbox_id)
            self.calls.append(("destroy", sandbox_id, ()))

    def _write_back(self, state: FakeSandboxState) -> None:
        if not self._persistent:
            return
        for mount in state.spec.mounts:
            if mount.source is None or mount.read_only:
                continue
            prefix = mount.target + "/"
            self.volumes[mount.source] = {
                path[len(prefix) :]: _File(entry.data, entry.mode)
                for path, entry in state.files.items()
                if path.startswith(prefix)
            }

    async def erase_session(self, *, org_id: str, session_id: str) -> int:
        if not (is_safe_segment(org_id) and is_safe_segment(session_id)):
            raise InvalidSandboxRequest("org and session ids must be single safe path segments")
        prefix = f"{org_id}/{session_id}/"
        doomed = [key for key in self.volumes if key.startswith(prefix) or key == prefix[:-1]]
        for key in doomed:
            del self.volumes[key]
        return 1 if doomed else 0

    async def erase_org(self, *, org_id: str) -> OrgSandboxErasure:
        if not is_safe_segment(org_id):
            raise InvalidSandboxRequest("the org id must be a single safe path segment")
        sandboxes = await self.reap(labels={"orgId": org_id}, older_than_s=0)
        prefix = f"{org_id}/"
        doomed = [key for key in self.volumes if key.startswith(prefix) or key == org_id]
        for key in doomed:
            del self.volumes[key]
        return OrgSandboxErasure(sandboxes, 1 if doomed else 0)

    async def reap(self, *, labels: Mapping[str, str], older_than_s: int) -> int:
        now = self._clock.now().timestamp()
        doomed = [
            sid
            for sid, state in self.sandboxes.items()
            if all(state.spec.labels.get(k) == v for k, v in labels.items())
            and now - state.created_at >= older_than_s
        ]
        for sid in doomed:
            self.pool.forget(sid)
            self._destroy(sid)
        return len(doomed)

    async def health(self) -> BrokerHealth:
        detail = {PERSISTENT_MOUNTS: "true"} if self._persistent else {}
        if self.engine_images:
            detail[ENGINE_IMAGES] = engine_images_detail(self.engine_images)
        if self.pool.enabled:
            detail.update(self.pool.detail(await self.pool.capacity()))
        return BrokerHealth(ok=True, modes=self._modes, active=self.ledger.active(), detail=detail)


def _handle_id(handle: object) -> str:
    sandbox_id = getattr(handle, "id", None)
    if not isinstance(sandbox_id, str):
        raise SandboxNotFound("not a sandbox handle")
    return sandbox_id


async def _print(context: FakeProcessContext) -> int:
    """``print <stdout> <stderr> <exit code>``."""
    _, out, err, code = context.argv
    await context.stdout.put(out.encode())
    await context.stderr.put(err.encode())
    return int(code)


async def _cat(context: FakeProcessContext) -> int:
    while chunk := await context.read_stdin():
        await context.stdout.put(chunk)
    return 0


async def _printenv(context: FakeProcessContext) -> int:
    value = context.env.get(context.argv[1])
    if value is None:
        return 1
    await context.stdout.put(value.encode())
    return 0


async def _read_file(context: FakeProcessContext) -> int:
    """``read-file <path>``: the file's bytes on stdout; 1 if it does not exist."""
    found = context.sandbox.files.get(context.argv[1])
    if found is None:
        await context.stderr.put(b"no such file\n")
        return 1
    await context.stdout.put(found.data)
    return 0


async def _head(context: FakeProcessContext) -> int:
    """``head -c <n> -- <path>``: the file's first ``n`` bytes; 1 if it does not exist or is a link."""
    argv = context.argv
    found = context.sandbox.files.get(argv[-1])
    if found is None or found.mode == SYMLINK:
        await context.stderr.put(b"head: cannot open\n")
        return 1
    await context.stdout.put(found.data[: int(argv[argv.index("-c") + 1])])
    return 0


async def _write_file(context: FakeProcessContext) -> int:
    """``write-file <path> <text>``; a read-only path fails as ``PermissionError`` would."""
    context.write_file(context.argv[1], context.argv[2].encode())
    return 0


async def _remove_file(context: FakeProcessContext) -> int:
    context.remove_file(context.argv[1])
    return 0


async def _rm(context: FakeProcessContext) -> int:
    """``rm -rf -- <paths>``: each path, and everything under it, if the sandbox may write there."""
    paths = context.argv[context.argv.index("--") + 1 :] if "--" in context.argv else context.argv[2:]
    sandbox = context.sandbox
    for path in paths:
        if not sandbox.writable(path):
            await context.stderr.put(f"rm: cannot remove {path!r}: Read-only file system\n".encode())
            return 1
        prefix = path.rstrip("/") + "/"
        for name in [n for n in sandbox.files if n == path or n.startswith(prefix)]:
            del sandbox.files[name]
        sandbox.dirs.difference_update({d for d in sandbox.dirs if d == path or d.startswith(prefix)})
    return 0


async def _sleep(context: FakeProcessContext) -> int:
    _ = await asyncio.Event().wait()
    return 0


async def _echo_server(context: FakeProcessContext) -> int:
    """``echo-server <port>``: echoes each connection until its peer half-closes."""
    listener = context.listen(int(context.argv[1]))
    while True:
        conn = await listener.get()
        while data := await conn.read():
            await conn.write(data)
        await conn.aclose()


async def _http(context: FakeProcessContext) -> int:
    """``http <url> [<Header: value>]...``: prints ``<status> <refusal or ->``, a newline, then the body."""
    headers = dict(h.split(": ", 1) for h in context.argv[2:])
    try:
        body = await context.handle.egress.request(context.argv[1], method="GET", headers=headers, body=None)
    except EgressDenied as denied:
        await context.stdout.put(f"{denied.status or 403} {denied.reason or '-'}\n".encode())
        return 0
    await context.stdout.put(b"200 -\n" + json.dumps(body).encode())
    return 0


async def _sh(context: FakeProcessContext) -> int:
    """``sh -c <script> <argv0> <args>...`` for the scripts the harness runs in a sandbox, and no other:
    ``exit 0`` (a liveness probe) and the snapshot capture (P5.9b)."""
    argv = context.argv
    if len(argv) >= 3 and argv[1] == "-c":
        if argv[2] == "exit 0":
            return 0
        if argv[2] == CAPTURE_SCRIPT:
            return await _tar_create(context, argv[4:])
        if argv[2] == LIST_SCRIPT:
            return await _proc_stat(context)
        if argv[2] == READ_SCRIPT and len(argv) == 7:
            return await _fs_read(context, argv[4], argv[5], int(argv[6]))
        if argv[2] == WRITE_SCRIPT and len(argv) == 6:
            return await _fs_write(context, argv[4], argv[5])
        if argv[2] == MOVE_SCRIPT and len(argv) == 7:
            return _fs_move(context, argv[4], argv[5], argv[6])
        if argv[2] == STALL_SCRIPT:
            for process in context.sandbox.processes:
                if process.running and process.context is not context:
                    await process.signal(signals.SIGSTOP)
            return 0
        if argv[2] == STOP_SCRIPT:
            wanted = set(argv[4:])
            for process in context.sandbox.processes:
                if str(process.pid) in wanted:
                    process.kill()
            return 0
    await context.stderr.put(b"sh: script not supported by the fake\n")
    return 2


def _resolve(sandbox: FakeSandboxState, path: str) -> str:
    """``readlink -f``: every symbolic link along ``path`` followed (at most 40, as the kernel does)."""
    parts = [p for p in path.split("/") if p]
    resolved = "/"
    hops = 0
    while parts:
        name = parts.pop(0)
        if name == "..":
            resolved = posixpath.dirname(resolved)
            continue
        if name == ".":
            continue
        candidate = posixpath.join(resolved, name)
        entry = sandbox.files.get(candidate)
        if entry is not None and entry.is_symlink:
            hops += 1
            if hops > 40:
                return candidate
            target = entry.data.decode("utf-8", "surrogateescape")
            parts = [p for p in target.split("/") if p] + parts
            resolved = "/" if target.startswith("/") else resolved
            continue
        resolved = candidate
    return resolved


def _is_dir(sandbox: FakeSandboxState, path: str) -> bool:
    prefix = path.rstrip("/") + "/"
    return path == "/" or path in sandbox.dirs or any(p.startswith(prefix) for p in [*sandbox.files, *sandbox.dirs])


async def _fs_read(context: FakeProcessContext, root: str, path: str, limit: int) -> int:
    """``READ_SCRIPT`` (``harness_core.sandbox.workspace_fs``) over the fake's files."""
    sandbox = context.sandbox
    target = _resolve(sandbox, path)
    if not target.startswith(root + "/"):
        return 4
    entry = sandbox.files.get(target)
    if entry is None or entry.is_symlink:
        return 5 if _is_dir(sandbox, target) else 3
    await context.emit(entry.data[:limit])
    return 0


async def _fs_write(context: FakeProcessContext, root: str, path: str) -> int:
    """``WRITE_SCRIPT`` over the fake's files: stdin to the file, never through a link or out of ``root``."""
    sandbox = context.sandbox
    ancestor = posixpath.dirname(path)
    while not _is_dir(sandbox, ancestor):
        ancestor = posixpath.dirname(ancestor)
    if not (_resolve(sandbox, ancestor) + "/").startswith(root + "/"):
        return 4
    folder = _resolve(sandbox, posixpath.dirname(path))
    if not (folder + "/").startswith(root + "/"):
        return 4
    target = posixpath.join(folder, posixpath.basename(path))
    entry = sandbox.files.get(target)
    if entry is not None and entry.is_symlink:
        return 4
    if _is_dir(sandbox, target):
        return 5
    data = b""
    while chunk := await context.read_stdin():
        data += chunk
    try:
        context.write_file(target, data)
    except PermissionError:
        return 6
    return 0


def _fs_move(context: FakeProcessContext, root: str, staged: str, path: str) -> int:
    """``MOVE_SCRIPT`` over the fake's files: the staged file to ``path``, never through a link or out of ``root``;
    the staged file goes either way."""
    sandbox = context.sandbox

    def drop_folder() -> None:
        _ = sandbox.files.pop(staged, None)

    resolved = _resolve(sandbox, posixpath.dirname(path))
    if not (resolved + "/").startswith(root + "/"):
        drop_folder()
        return 4
    target = posixpath.join(resolved, posixpath.basename(path))
    entry = sandbox.files.get(target)
    if (entry is not None and entry.is_symlink) or _is_dir(sandbox, target):
        drop_folder()
        return 4
    moved = sandbox.files.pop(staged, None)
    if moved is None or not sandbox.writable(target):
        drop_folder()
        return 6
    sandbox.dirs.add(resolved)
    sandbox.files[target] = moved
    drop_folder()
    return 0


async def _proc_stat(context: FakeProcessContext) -> int:
    """The sandbox's processes as ``/proc/<pid>/stat`` lines (pid, comm, state, parent 1, start time = pid), after
    the probe's own pid, as ``LIST_SCRIPT`` prints them."""
    processes = context.sandbox.processes
    me = next((p.pid for p in processes if p.running and p.context is context), 0)
    lines = [str(me)]
    for process in processes:
        comm = posixpath.basename(process.context.argv[0])[:15]
        fields = ["R" if process.running else "Z", "1", *["0"] * 17, str(process.pid), "0"]
        lines.append(f"{process.pid} ({comm}) {' '.join(fields)}")
    await context.emit(("\n".join(lines) + "\n").encode())
    return 0


async def _tar_create(context: FakeProcessContext, members: Sequence[str]) -> int:
    """``tar -c -f - <members>`` run at ``/``: the members that exist, folders first, as busybox writes them."""
    sandbox = context.sandbox
    found = [
        m for m in members
        if any(_at_or_under(p, "/" + m) for p in sandbox.files) or any(_at_or_under(d, "/" + m) for d in sandbox.dirs)
    ]
    if not found:
        return 0
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for member in found:
            root = "/" + member
            files = sorted(p for p in sandbox.files if _at_or_under(p, root))
            folders = {d for d in sandbox.dirs if _at_or_under(d, root)} | {root}
            for path in files:
                parent = posixpath.dirname(path)
                while _at_or_under(parent, root):
                    folders.add(parent)
                    parent = posixpath.dirname(parent)
            for folder in sorted(folders):
                info = tarfile.TarInfo(folder.lstrip("/"))
                info.type, info.mode = tarfile.DIRTYPE, 0o755
                archive.addfile(info)
            for path in files:
                entry = sandbox.files[path]
                info = tarfile.TarInfo(path.lstrip("/"))
                if entry.is_symlink:
                    info.type, info.linkname = tarfile.SYMTYPE, entry.data.decode("utf-8", "surrogateescape")
                    archive.addfile(info)
                    continue
                info.size, info.mode = len(entry.data), entry.mode
                archive.addfile(info, io.BytesIO(entry.data))
    payload = buffer.getvalue()
    for start in range(0, len(payload), 65536):
        await context.stdout.put(payload[start : start + 65536])
    return 0


def _at_or_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


async def _tar(context: FakeProcessContext) -> int:
    """``tar -x -f - -C /`` from stdin (the snapshot restore), writing only where the sandbox may write."""
    if tuple(context.argv) != RESTORE_ARGV:
        await context.stderr.put(b"tar: only the snapshot restore is supported by the fake\n")
        return 2
    data = b""
    while chunk := await context.read_stdin():
        data += chunk
    sandbox = context.sandbox
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
        for member in archive:
            path = posixpath.normpath("/" + member.name)
            if not sandbox.writable(path):
                await context.stderr.put(b"tar: Read-only file system\n")
                return 2
            if member.isdir():
                sandbox.dirs.add(path)
            elif member.isreg():
                extracted = archive.extractfile(member)
                sandbox.files[path] = _File(extracted.read() if extracted is not None else b"", member.mode & 0o7777)
            else:
                await context.stderr.put(b"tar: unsupported member\n")
                return 2
    return 0


FAKE_PROGRAMS: Mapping[str, FakeProgram] = {
    "print": _print,
    "cat": _cat,
    "printenv": _printenv,
    "read-file": _read_file,
    "head": _head,
    "write-file": _write_file,
    "remove-file": _remove_file,
    "rm": _rm,
    "sleep": _sleep,
    "echo-server": _echo_server,
    "http": _http,
    "sh": _sh,
    "tar": _tar,
}

class FakeContractEnv(SandboxContractEnv):
    """``SandboxContractEnv`` for :class:`FakeSandboxBroker` on a ``ManualClock``."""

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock
        self.reserved_label = "fake.broker."

    @override
    def spec(self, *, labels: Mapping[str, str] | None = None, mode: RuntimeMode | None = None) -> SandboxSpec:
        return SandboxSpec(
            image_digest="fake@sha256:" + "0" * 64,
            mode=mode or RuntimeMode.DOCKER_HARDENED,
            mounts=(MountSpec(WORK),),
            labels=labels or {},
        )

    @override
    def argv_print(self, stdout: str, stderr: str, code: int) -> list[str]:
        return ["print", stdout, stderr, str(code)]

    @override
    def argv_cat(self) -> list[str]:
        return ["cat"]

    @override
    def argv_printenv(self, name: str) -> list[str]:
        return ["printenv", name]

    @override
    def argv_sleep(self) -> list[str]:
        return ["sleep"]

    @override
    def argv_echo_server(self, port: int) -> list[str]:
        return ["echo-server", str(port)]

    @override
    def argv_read_file(self, path: str) -> list[str]:
        return ["read-file", path]

    @override
    def argv_write_file(self, path: str, text: str) -> list[str]:
        return ["write-file", path, text]

    @override
    def argv_remove_file(self, path: str) -> list[str]:
        return ["remove-file", path]

    @override
    def advance(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


async def echo_gateway(request: EgressRequest) -> JsonValue:
    """A gateway that answers with the credential it received (the proxy must scrub it on the way back)."""
    return {"auth": request.headers.get("authorization", "")}


class FakeEgressContractEnv(EgressContractEnv):
    """``EgressContractEnv`` for a :class:`FakeSandboxBroker` bound to a :class:`FakeEgressProxy`."""

    def __init__(self, clock: Clock) -> None:
        self.proxy = FakeEgressProxy(clock=clock, gateways={self.gateway_host: echo_gateway})

    @override
    def argv_http(self, url: str, headers: Mapping[str, str]) -> list[str]:
        return ["http", url, *(f"{k}: {v}" for k, v in headers.items())]

    @override
    async def gateway_received(self, authorization: str) -> bool:
        return any(r.headers.get("authorization") == authorization for r in self.proxy.seen)

    @override
    def expected_token(self, sandbox_id: str) -> str:
        return self.proxy.expected_token(sandbox_id, self.credential_ref)

    @override
    async def bound(self) -> frozenset[str]:
        return await self.proxy.bindings.bound()


__all__ = [
    "FAKE_PROGRAMS",
    "ExecRecord",
    "FakeContractEnv",
    "FakeEgressContractEnv",
    "FakeProcess",
    "FakeProcessContext",
    "FakeSandboxBroker",
    "FakeSandboxHandle",
    "FakeSandboxState",
    "STALL_SCRIPT",
    "FakeStream",
    "echo_gateway",
]
