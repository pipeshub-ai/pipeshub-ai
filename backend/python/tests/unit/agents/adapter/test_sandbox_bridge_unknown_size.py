"""A backend that cannot tell a file's size never has it read whole: at most
one byte past the artifact cap is read, and a larger file is reported."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox
from app.agents.agent_loop.sandbox_bridge import _fetch_within_cap

if TYPE_CHECKING:
    from pathlib import Path

CAP = 1024


class _NoSize(LocalCodingSandbox):
    def __init__(self, working_dir: str, *, raises: bool = False) -> None:
        super().__init__(working_dir=working_dir)
        self.raises = raises
        self.whole_reads: list[str] = []

    async def file_size(self, path: str) -> int | None:
        if self.raises:
            raise OSError("metadata lookup failed")
        return None

    async def download_file(self, path: str) -> bytes:
        self.whole_reads.append(path)
        return await super().download_file(path)


async def _backend(tmp_path: Path, *, raises: bool = False) -> _NoSize:
    backend = _NoSize(working_dir=str(tmp_path / "sbx"), raises=raises)
    await backend.provision()
    (tmp_path / "sbx" / "output" / "big.bin").write_bytes(b"x" * (CAP + 1))
    (tmp_path / "sbx" / "output" / "ok.txt").write_bytes(b"x" * CAP)
    return backend


def _registry() -> MagicMock:
    registry = MagicMock()
    registry._max_bytes = CAP
    return registry


async def test_an_unknown_size_is_read_up_to_the_cap_only(tmp_path: Path) -> None:
    for raises in (False, True):
        backend = await _backend(tmp_path / str(raises), raises=raises)
        fetched, oversized = await _fetch_within_cap(
            backend, ["output/big.bin", "output/ok.txt"], _registry(), "sbx-1",
        )
        assert backend.whole_reads == []
        assert fetched == [("output/ok.txt", b"x" * CAP)]
        assert [e["file"] for e in oversized] == ["big.bin"]
        assert "FileTooLarge" in oversized[0]["error"]


class _Stream:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.read = 0
        self.closed = False

    def __aiter__(self) -> _Stream:
        return self

    async def __anext__(self) -> bytes:
        if self.read >= len(self.chunks):
            raise StopAsyncIteration
        self.read += 1
        return self.chunks[self.read - 1]

    async def aclose(self) -> None:
        self.closed = True


async def test_the_e2b_head_read_streams_and_stops_past_the_limit() -> None:
    stream = _Stream([b"a" * 600, b"b" * 600, b"c" * 600, b"d" * 600])
    calls: list[tuple[str, str]] = []

    async def read(path: str, format: str = "text") -> _Stream:  # noqa: A002  (the SDK's keyword)
        calls.append((path, format))
        return stream

    sandbox = E2BCodingSandbox()
    sandbox._sbx = SimpleNamespace(files=SimpleNamespace(read=read))
    data, more = await sandbox.read_file_head("output/big.bin", CAP)
    assert calls[0][1] == "stream"
    assert more is True
    assert data == b"a" * 600 + b"b" * 424
    assert stream.read == 2
    assert stream.closed

    small = _Stream([b"hi"])
    async def read_small(path: str, format: str = "text") -> _Stream:  # noqa: A002
        return small

    sandbox._sbx = SimpleNamespace(files=SimpleNamespace(read=read_small))
    assert await sandbox.read_file_head("output/ok.txt", CAP) == (b"hi", False)


class _Grows(LocalCodingSandbox):
    """Reports the size before another command makes the file larger."""

    def __init__(self, working_dir: str) -> None:
        super().__init__(working_dir=working_dir)
        self.whole_reads: list[str] = []

    async def file_size(self, path: str) -> int | None:
        size = await super().file_size(path)
        with open(os.path.join(self.working_dir, path), "ab") as f:
            f.write(b"y" * (4 * CAP))
        return size

    async def download_file(self, path: str) -> bytes:
        self.whole_reads.append(path)
        return await super().download_file(path)


async def test_a_file_that_grows_after_its_size_check_is_still_read_up_to_the_cap(tmp_path: Path) -> None:
    backend = _Grows(working_dir=str(tmp_path / "sbx"))
    await backend.provision()
    (tmp_path / "sbx" / "output" / "late.bin").write_bytes(b"x" * 10)
    fetched, oversized = await _fetch_within_cap(backend, ["output/late.bin"], _registry(), "sbx-1")
    assert backend.whole_reads == []
    assert fetched == []
    assert [e["file"] for e in oversized] == ["late.bin"]


async def test_the_files_held_together_are_bounded_by_the_run_total(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANDBOX_OUTPUT_MAX_TOTAL_BYTES", str(2 * CAP))
    for known_size in (True, False):
        backend = await _backend(tmp_path / str(known_size))
        if not known_size:
            backend.file_size = lambda path: _none()  # type: ignore[method-assign]
        out = tmp_path / str(known_size) / "sbx" / "output"
        for name in ("a.txt", "b.txt", "c.txt"):
            (out / name).write_bytes(b"y" * CAP)

        fetched, skipped = await _fetch_within_cap(
            backend, ["output/a.txt", "output/b.txt", "output/c.txt"], _registry(), "sbx-1",
        )

        assert [p for p, _ in fetched] == ["output/a.txt", "output/b.txt"]
        assert [e["file"] for e in skipped] == ["c.txt"]
        assert "total" in skipped[0]["error"]


async def _none() -> None:
    return None
