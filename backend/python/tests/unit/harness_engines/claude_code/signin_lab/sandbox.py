"""A ``SandboxHandle`` over ``docker exec`` into the lab's Claude Code container (SIGNIN-2 lane).

Just enough of the broker's handle for the adapter's sign-in, one engine turn, state capture and sign-out to run on
the real binary against the fake Anthropic: plain pipes (as the broker's exec), archives in and out with ``tar`` as
root (as the broker writes root-owned config). Not a broker: no egress proxy, no mounts, no isolation of its own.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from harness_core.sandbox.types import RuntimeMode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence


class DockerExecProcess:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process

    async def _read(self, reader: asyncio.StreamReader | None) -> AsyncIterator[bytes]:
        if reader is None:
            return
        while chunk := await reader.read(65536):
            yield chunk

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self._read(self._process.stdout)

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self._read(self._process.stderr)

    async def write(self, data: bytes) -> None:
        assert self._process.stdin is not None
        self._process.stdin.write(data)
        await self._process.stdin.drain()

    async def close_stdin(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()

    async def signal(self, sig: int) -> None:
        if self._process.returncode is None:
            self._process.send_signal(sig)

    async def wait(self, *, timeout_s: float | None = None) -> int:
        return await asyncio.wait_for(self._process.wait(), timeout_s)


class DockerExecSandbox:
    def __init__(self, container: str) -> None:
        self._container = container
        self.processes: list[DockerExecProcess] = []

    @property
    def id(self) -> str:
        return self._container

    @property
    def mode(self) -> RuntimeMode:
        return RuntimeMode.DOCKER_HARDENED

    @property
    def verified_binaries(self) -> Mapping[str, str]:
        return {}

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str], stdin: bool = False) -> DockerExecProcess:
        flags = [arg for key, value in env.items() for arg in ("-e", f"{key}={value}")]
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", *(["-i"] if stdin else []), "-w", "/workspace", *flags, self._container, *argv,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        handle = DockerExecProcess(process)
        self.processes.append(handle)
        return handle

    async def connect(self, port: int) -> object:
        raise NotImplementedError("the lab sandbox tunnels nothing")

    async def put_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", "-u", "0", self._container, "tar", "-x", "-C", dest,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None
        async for chunk in stream:
            process.stdin.write(chunk)
            await process.stdin.drain()
        process.stdin.close()
        _, err = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"tar into {dest} failed: {err.decode(errors='replace')}")

    async def seed_tar(self, dest: str, stream: AsyncIterator[bytes]) -> None:
        await self.put_tar(dest, stream)

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        process = await asyncio.create_subprocess_exec(
            "docker", "exec", self._container, "tar", "-c", "-C", src, ".",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"tar of {src} failed: {err.decode(errors='replace')}")

        async def chunks() -> AsyncIterator[bytes]:
            yield out

        return chunks()


__all__ = ["DockerExecSandbox"]
