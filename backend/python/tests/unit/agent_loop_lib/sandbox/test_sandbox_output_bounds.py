"""What a run hands back to the app is bounded, whatever the run wrote.

- Files collected from ``/output`` and ``/src`` are read as the archive
  streams in, with a per-file, a total and an entry-count limit. A file the
  tar header declares as huge (a sparse ``truncate -s 1G``) is never
  materialised, and the stream is not read past the limits.
- stdout and stderr keep only their first and last bytes, both from Docker
  logs and from a local subprocess, so a run printing 50 MB neither fills
  memory nor holds the event loop.
- ``read_sandbox_file`` reads only the bytes it returns.
"""

from __future__ import annotations

import asyncio
import gc
import io
import os
import sys
import tarfile
import threading
import time
import types
from collections.abc import Iterator  # noqa: TC003
from unittest.mock import MagicMock, patch

import pytest

from app.agent_loop_lib.sandbox.coding import docker as docker_mod
from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.docker_client import reset_default_provider
from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
from app.agent_loop_lib.sandbox.coding.executor import CodeExecutor
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agent_loop_lib.tools.builtin.sandbox.coding_sandbox import ReadSandboxFileTool

MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def _fake_docker_module() -> Iterator[None]:
    created = False
    if "docker" not in sys.modules:
        fake = types.ModuleType("docker")
        errors_mod = types.ModuleType("docker.errors")
        errors_mod.ImageNotFound = type("ImageNotFound", (Exception,), {})
        fake.errors = errors_mod
        fake.from_env = MagicMock()
        sys.modules["docker"] = fake
        sys.modules["docker.errors"] = errors_mod
        created = True
    reset_default_provider()
    try:
        yield
    finally:
        reset_default_provider()
        if created:
            sys.modules.pop("docker", None)
            sys.modules.pop("docker.errors", None)


@pytest.fixture
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX_FILE_BYTES", str(1 * MiB))
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX_TOTAL_BYTES", str(2 * MiB))
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX_FILES", "50")


class _LazyTar:
    """A tar stream whose file data is generated only as it is read, and
    which counts how much was read. A declared size of 1 GiB costs nothing
    unless the reader really reads it."""

    def __init__(self, members: list[tuple[str, int | bytes, bytes]]) -> None:
        # (name, size-or-content, type)
        self.members = members
        self.data_read = 0

    def __iter__(self) -> Iterator[bytes]:
        for name, body, kind in self.members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.SYMTYPE or kind == tarfile.LNKTYPE:
                info.linkname = body if isinstance(body, str) else "/etc/passwd"
                yield info.tobuf(format=tarfile.GNU_FORMAT)
                continue
            size = len(body) if isinstance(body, bytes) else int(body)
            info.size = size if kind in (tarfile.REGTYPE, tarfile.AREGTYPE) else 0
            yield info.tobuf(format=tarfile.GNU_FORMAT)
            if info.size == 0:
                continue
            if isinstance(body, bytes):
                self.data_read += len(body)
                yield body
            else:
                left = size
                while left:
                    n = min(left, MiB)
                    self.data_read += n
                    yield b"\0" * n
                    left -= n
            pad = (-info.size) % tarfile.BLOCKSIZE
            if pad:
                yield b"\0" * pad
        yield b"\0" * (tarfile.BLOCKSIZE * 2)


def _container_with(archives: dict[str, _LazyTar]) -> MagicMock:
    container = MagicMock()
    container.get_archive.side_effect = lambda path: (iter(archives[path]), {})
    return container


class TestCollectedFilesAreBounded:
    def test_a_huge_sparse_file_is_skipped_without_reading_it(self, tmp_path, small_limits) -> None:
        archive = _LazyTar([
            ("output/sparse.bin", 1024 * MiB, tarfile.REGTYPE),
            ("output/ok.txt", b"fine", tarfile.REGTYPE),
        ])
        dest = tmp_path / "out"
        dest.mkdir()
        budget = docker_mod._extract_container_dir(_container_with({"/output": archive}), "/output", str(dest))

        assert not (dest / "sparse.bin").exists()
        # Not read past: a 1 GiB member is far over the 4 MiB read budget.
        assert archive.data_read <= 4 * MiB
        assert "sparse.bin" in budget.note()
        assert sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) <= 2 * MiB

    def test_an_oversized_file_within_the_read_budget_is_skipped_and_the_rest_extracted(
        self, tmp_path, small_limits,
    ) -> None:
        archive = _LazyTar([
            ("output/big.bin", 2 * MiB, tarfile.REGTYPE),
            ("output/after.txt", b"after", tarfile.REGTYPE),
        ])
        dest = tmp_path / "out"
        dest.mkdir()
        budget = docker_mod._extract_container_dir(_container_with({"/output": archive}), "/output", str(dest))

        assert not (dest / "big.bin").exists()
        assert (dest / "after.txt").read_bytes() == b"after"
        assert "big.bin" in budget.note() and "too large" in budget.note()

    def test_collection_stops_at_the_total(self, tmp_path, small_limits) -> None:
        archive = _LazyTar([(f"output/f{i}.bin", 900 * 1024, tarfile.REGTYPE) for i in range(10)])
        dest = tmp_path / "out"
        dest.mkdir()
        budget = docker_mod._extract_container_dir(_container_with({"/output": archive}), "/output", str(dest))

        written = sum(f.stat().st_size for f in dest.iterdir())
        assert written <= 2 * MiB
        assert len(list(dest.iterdir())) == 2
        assert "total limit" in budget.note()
        assert archive.data_read <= 4 * MiB

    def test_the_entry_count_is_bounded(self, tmp_path, small_limits) -> None:
        archive = _LazyTar([(f"output/n{i}", b"x", tarfile.REGTYPE) for i in range(500)])
        dest = tmp_path / "out"
        dest.mkdir()
        budget = docker_mod._extract_container_dir(_container_with({"/output": archive}), "/output", str(dest))

        assert len(list(dest.iterdir())) == 50
        assert "50 files" in budget.note()

    def test_links_devices_and_fifos_are_never_extracted(self, tmp_path, small_limits) -> None:
        archive = _LazyTar([
            ("output/sym", "/etc/passwd", tarfile.SYMTYPE),
            ("output/hard", "/etc/passwd", tarfile.LNKTYPE),
            ("output/fifo", 0, tarfile.FIFOTYPE),
            ("output/dev", 0, tarfile.CHRTYPE),
            ("output/../../escape.txt", b"no", tarfile.REGTYPE),
            ("output/real.txt", b"yes", tarfile.REGTYPE),
        ])
        dest = tmp_path / "out"
        dest.mkdir()
        docker_mod._extract_container_dir(_container_with({"/output": archive}), "/output", str(dest))

        assert sorted(p.name for p in dest.iterdir()) == ["real.txt"]
        assert not (tmp_path / "escape.txt").exists()

    def test_normal_output_is_unchanged(self, tmp_path) -> None:
        archive = _LazyTar([
            ("output/a/b.txt", b"nested", tarfile.REGTYPE),
            ("output/c.csv", b"1,2\n", tarfile.REGTYPE),
        ])
        dest = tmp_path / "out"
        dest.mkdir()
        budget = docker_mod._extract_container_dir(_container_with({"/output": archive}), "/output", str(dest))

        assert (dest / "a" / "b.txt").read_bytes() == b"nested"
        assert (dest / "c.csv").read_bytes() == b"1,2\n"
        assert budget is None or budget.note() == ""

    async def test_the_run_result_tells_the_model_what_was_left_out(self, tmp_path, small_limits) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        container.logs.side_effect = lambda **kw: b"done\n" if kw.get("stdout") else b""
        archives = {
            "/output": _LazyTar([
                ("output/small.txt", b"ok", tarfile.REGTYPE),
                ("output/huge.bin", 1024 * MiB, tarfile.REGTYPE),
            ]),
            "/src": _LazyTar([]),
        }
        container.get_archive.side_effect = lambda path: (iter(archives[path]), {})
        client = MagicMock()
        client.containers.create.return_value = container

        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.artifacts == ["output/small.txt"]
        assert "huge.bin" in result.stderr and "not collected" in result.stderr.lower()
        assert not os.path.exists(os.path.join(sandbox.working_dir, "output", "huge.bin"))


class TestStdioIsBounded:
    def _big_stream(self, total: int) -> Iterator[bytes]:
        line = b"y" * 1023 + b"\n"
        chunk = line * 1024  # 1 MiB
        for _ in range(total // len(chunk)):
            yield chunk

    def test_docker_logs_keep_head_and_tail(self) -> None:
        container = MagicMock()
        container.logs.side_effect = lambda **kw: (
            iter([b"HEAD\n", *self._big_stream(50 * MiB), b"TAIL\n"]) if kw.get("stream") else b"x" * (50 * MiB)
        )
        text = docker_mod._capped_logs(container, stdout=True)

        assert len(text) < 300 * 1024
        assert text.startswith("HEAD\n")
        assert text.endswith("TAIL\n")
        assert "bytes truncated" in text

    async def test_a_50_mb_docker_stdout_is_bounded_and_does_not_hold_the_loop(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        container = MagicMock()
        container.wait.return_value = {"StatusCode": 0}
        big = b"y" * (50 * MiB)
        loop_thread = threading.get_ident()
        on_loop: list[int] = []

        def _chunks() -> Iterator[bytes]:
            for i in range(0, len(big), MiB):
                if threading.get_ident() == loop_thread:
                    on_loop.append(i)
                yield big[i:i + MiB]

        def _logs(**kw: object) -> object:
            if threading.get_ident() == loop_thread:
                on_loop.append(-1)
            if not kw.get("stdout"):
                return iter([]) if kw.get("stream") else b""
            if kw.get("stream"):
                return _chunks()
            return big

        container.logs.side_effect = _logs
        container.get_archive.side_effect = lambda path: (iter(_LazyTar([])), {})
        client = MagicMock()
        client.containers.create.return_value = container

        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert len(result.stdout) < 300 * 1024
        assert "bytes truncated" in result.stdout
        # The 50 MB is read where the logs are read: on a worker thread, never on the loop's. A loop-lag bound would
        # also hold every host stall a busy gate adds (WALLCLOCK-1).
        assert on_loop == []

    async def test_a_50_mb_local_stdout_is_bounded_and_does_not_hold_the_loop(self, tmp_path) -> None:
        work = tmp_path / "w"
        work.mkdir()
        executor = CodeExecutor(str(work), EnvironmentManager(str(work)))
        gaps: list[float] = []

        async def _ticker() -> None:
            last = time.monotonic()
            while True:
                await asyncio.sleep(0.01)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        # Passes of the collector during the window scan only what this run makes, not the xdist worker's heap: a
        # full pass over ten million objects stops the world for seconds (WALLCLOCK-1).
        gc.collect()
        gc.freeze()
        ticker = asyncio.create_task(_ticker())
        try:
            stdout, stderr, code = await executor._run(
                [sys.executable, "-c",
                 "import sys\nfor _ in range(50 * 1024): sys.stdout.write('y' * 1023 + '\\n')\n"
                 "sys.stderr.write('E' * (3 * 1024 * 1024))"],
                60, allow_network=False,
            )
        finally:
            ticker.cancel()
            gc.unfreeze()

        assert code == 0
        assert len(stdout) < 300 * 1024 and "bytes truncated" in stdout
        assert len(stderr) < 300 * 1024 and "bytes truncated" in stderr
        # wall-clock-ok: the pipes are read in chunks of _READ_CHUNK, a step of microseconds; 0.5 s is fifty 10 ms ticks
        assert max(gaps, default=0) < 0.5

    async def test_small_output_is_unchanged(self, tmp_path) -> None:
        work = tmp_path / "w"
        work.mkdir()
        executor = CodeExecutor(str(work), EnvironmentManager(str(work)))
        stdout, stderr, code = await executor._run(
            [sys.executable, "-c", "print('hello'); import sys; sys.stderr.write('warn\\n')"],
            30, allow_network=False,
        )
        assert (stdout, stderr, code) == ("hello\n", "warn\n", 0)


class TestReadSandboxFileReadsOnlyWhatItReturns:
    async def _tool_and_backend(self, tmp_path: os.PathLike) -> tuple[ReadSandboxFileTool, LocalCodingSandbox, str]:
        backend = LocalCodingSandbox(working_dir=str(tmp_path / "sbx"))
        await backend.provision()
        manager = MagicMock()
        manager.get.return_value = backend
        return ReadSandboxFileTool(manager), backend, backend.sandbox_id

    async def test_a_small_max_bytes_never_reads_the_whole_file(self, tmp_path) -> None:
        tool, backend, sandbox_id = await self._tool_and_backend(tmp_path)
        with open(os.path.join(backend.working_dir, "output", "big.bin"), "wb") as f:
            f.truncate(200 * MiB)
        read_sizes: list[int] = []
        real_open = open

        class _Spy(io.FileIO):
            def read(self, size: int = -1) -> bytes:
                read_sizes.append(size)
                return super().read(size)

        def _spy_open(path: str, mode: str = "r", *args: object, **kwargs: object) -> object:
            if str(path).endswith("big.bin") and "b" in mode:
                return _Spy(path, "rb")
            return real_open(path, mode, *args, **kwargs)

        with patch("builtins.open", _spy_open):
            out = await tool.execute(sandbox_id=sandbox_id, path="output/big.bin", max_bytes=16)

        assert out.success
        assert out.data["truncated"] is True
        assert out.data["content"] == "\0" * 16
        assert read_sizes and all(0 <= s <= 17 for s in read_sizes)

    async def test_max_bytes_is_clamped_to_the_server_maximum(self, tmp_path) -> None:
        tool, backend, sandbox_id = await self._tool_and_backend(tmp_path)
        with open(os.path.join(backend.working_dir, "output", "mid.txt"), "wb") as f:
            f.write(b"a" * (3 * MiB))

        out = await tool.execute(sandbox_id=sandbox_id, path="output/mid.txt", max_bytes=10**12)

        assert out.data["truncated"] is True
        assert len(out.data["content"]) == 1_000_000

    async def test_a_whole_small_file_is_returned_as_before(self, tmp_path) -> None:
        tool, backend, sandbox_id = await self._tool_and_backend(tmp_path)
        with open(os.path.join(backend.working_dir, "output", "s.txt"), "w") as f:
            f.write("short")

        out = await tool.execute(sandbox_id=sandbox_id, path="output/s.txt")

        assert out.data == {"content": "short", "encoding": "utf-8", "truncated": False}
