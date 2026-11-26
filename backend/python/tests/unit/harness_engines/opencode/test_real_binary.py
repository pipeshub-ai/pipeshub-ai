"""OC-13 (optional): the adapter against the real opencode v1.18.34 binary in Docker.

Skipped unless Docker and the probe image (``Dockerfile.sandbox``, tag
``oc-probe:ro-config``) are present. The sandbox below is a test-only
``SandboxHandle``: ``exec`` is ``docker exec`` and ``connect(port)`` dials the
port published on the host's loopback. It stands in for the broker's tunnel, so
it binds the server to all container interfaces (a ``docker exec -i`` relay to
loopback hung on Docker Desktop). The gateway hosts do not resolve, so the
model call fails: the turn must end with a mapped error, not hang.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import shutil
import subprocess
import time
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import EngineEvent, TurnCompleted, turn_id_for
from harness_core.engines.types import EngineTask
from harness_core.sandbox import RuntimeMode
from harness_core.testing.engines.contract import ENDPOINTS, make_profile
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.opencode.state import decode_state
from tests.unit.harness_engines.opencode.conftest import PINNED, OpencodeHarness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

IMAGE = "oc-probe:ro-config"
LABEL = "pipeshub.agent=E5"
CONTAINER_PORT = 4096

pytestmark = [pytest.mark.docker]


def _image_present() -> bool:
    if shutil.which("docker") is None:
        return False
    found = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    return found.returncode == 0


class _Stream:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    async def read(self, max_bytes: int = 65536) -> bytes:
        return await self._reader.read(max_bytes)

    async def write(self, data: bytes) -> None:
        self._writer.write(data)
        await self._writer.drain()

    async def aclose(self) -> None:
        self._writer.close()
        with contextlib.suppress(OSError):
            await self._writer.wait_closed()


class _Process:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self.stdout = self._lines(process.stdout)
        self.stderr = self._lines(process.stderr)

    @staticmethod
    async def _lines(stream: asyncio.StreamReader | None) -> AsyncIterator[bytes]:
        while stream is not None:
            chunk = await stream.read(65536)
            if not chunk:
                return
            yield chunk

    async def write(self, data: bytes) -> None:
        raise NotImplementedError

    async def signal(self, sig: int) -> None:
        if self._process.returncode is None:
            self._process.send_signal(sig)

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await asyncio.wait_for(self._process.wait(), timeout_s)


@dataclasses.dataclass
class DockerSandbox:
    id: str
    host_port: int
    mode: RuntimeMode = RuntimeMode.DOCKER_HARDENED
    verified_binaries: Mapping[str, str] = dataclasses.field(default_factory=dict[str, str])

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> _Process:
        flags = [item for key, value in env.items() for item in ("-e", f"{key}={value}")]
        argv = ["0.0.0.0" if a == "127.0.0.1" else a for a in argv]
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", *flags, self.id, *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        return _Process(process)

    async def connect(self, port: int) -> _Stream:
        if port != CONTAINER_PORT:
            raise ConnectionRefusedError(f"only {CONTAINER_PORT} is published")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", self.host_port)
        except OSError as exc:
            raise ConnectionRefusedError(str(exc)) from exc
        return _Stream(reader, writer)

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        data = b"".join([chunk async for chunk in stream])
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", self.id, "tar", "-x", "-C", dest, stdin=asyncio.subprocess.PIPE
        )
        await process.communicate(data)
        assert process.returncode == 0

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        raise NotImplementedError


def _start() -> DockerSandbox:
    container = subprocess.run(
        [
            "docker", "run", "-d", "--rm", "--label", LABEL, "--read-only",
            "--tmpfs", "/tmp", "--tmpfs", "/state", "--tmpfs", "/home/agent",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "-p", f"127.0.0.1::{CONTAINER_PORT}",
            "--entrypoint", "sleep", IMAGE, "infinity",
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    for _ in range(50):
        mapping = subprocess.run(
            ["docker", "port", container, str(CONTAINER_PORT)], capture_output=True, text=True, check=False
        ).stdout.split()
        if mapping:
            return DockerSandbox(container, int(mapping[0].rsplit(":", 1)[1]))
        time.sleep(0.2)
    _stop(DockerSandbox(container, 0))
    raise RuntimeError("Docker did not publish the port")


def _stop(sandbox: DockerSandbox) -> None:
    subprocess.run(["docker", "rm", "-f", sandbox.id], capture_output=True, check=False)


@pytest.mark.skipif(not _image_present(), reason=f"needs Docker and the {IMAGE} probe image")
@pytest.mark.timeout(240)
async def test_oc13_real_binary_open_turn_capture_and_restore() -> None:
    harness = OpencodeHarness()
    adapter = OpencodeAdapter()
    launch = adapter.render_launch(make_profile(harness), ENDPOINTS, "/tmp/skills")
    started: list[DockerSandbox] = []
    try:
        first = _start()
        started.append(first)
        session = await adapter.open(first, launch, None)  # type: ignore[arg-type]
        health = await session.health()
        assert (health.alive, health.engine_version) == (True, PINNED.version)
        events: list[EngineEvent] = []

        async def drive() -> None:
            events.extend([e async for e in session.run_turn(EngineTask(turn_id_for("run-real", 0), "hello"))])

        # The model host does not resolve and opencode retries model calls with backoff,
        # so the turn is ended by an interrupt, which is also the cancel path under test.
        runner = asyncio.get_running_loop().create_task(drive())
        await asyncio.sleep(3)
        await session.interrupt()
        await asyncio.wait_for(runner, 60)
        assert isinstance(events[-1], TurnCompleted)
        assert events[-1].reason == "interrupted"
        assert [e.meta.seq for e in events] == list(range(len(events)))
        state = await session.capture_state()
        restored_id = decode_state(state, PINNED).session_id
        await session.close()
        second = _start()
        started.append(second)
        resumed = await adapter.open(second, launch, state)  # type: ignore[arg-type]
        assert resumed.session_id == restored_id
        assert (await resumed.health()).alive
        await resumed.close()
    finally:
        for sandbox in started:
            _stop(sandbox)
