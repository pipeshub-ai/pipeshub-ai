"""What a sandbox keeps across runs is bounded, and reading run archives is too.

- A Docker sandbox's files persist on the host for its later runs. Each run
  may only add up to what the sandbox may still keep across all its runs.
- Files a run got back from the host unchanged (restored output, staged
  inputs) are not extracted again and do not use up the run's budget.
- Staged files are streamed into the container from disk, not read into
  one in-memory archive per run.
- Every tar entry counts toward the entry cap, directories too; headers are
  not kept as the archive is read; a run's archives share one read limit.
"""

from __future__ import annotations

import io
import os
import tarfile
import time
from unittest.mock import MagicMock, patch

import pytest

import app.agent_loop_lib.sandbox.coding.docker as docker_mod
import app.agent_loop_lib.sandbox.coding.egress_firewall as ef
from app.agent_loop_lib.sandbox.coding.base import CodeRequest, CodeResult
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox
from app.agent_loop_lib.sandbox.coding.output_limits import (
    ExtractBudget,
    ExtractLimits,
    SandboxRetention,
    extract_tar_stream,
)

from .test_docker_coding_sandbox import (  # noqa: F401  (fixtures)
    _fake_docker_module,
    _fresh_docker_provider,
)

KiB = 1024
MiB = 1024 * 1024


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SANDBOX_DISK_QUOTA", "off")
    monkeypatch.setattr(docker_mod, "_disk_watch_limit", None)
    monkeypatch.setattr(docker_mod, "_log_config_level", 0)
    monkeypatch.setattr(ef, "_storage_opt_usable", None)
    monkeypatch.setattr(ef, "_oom_score_adj_usable", True)
    for name in ("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", "SANDBOX_OUTPUT_MAX_SANDBOX_FILES",
                 "SANDBOX_OUTPUT_MAX_TOTAL_BYTES", "SANDBOX_OUTPUT_MAX_FILE_BYTES",
                 "SANDBOX_OUTPUT_MAX_FILES"):
        monkeypatch.delenv(name, raising=False)


def _tar(entries: list[tuple[str, bytes | None, float]]) -> bytes:
    """``(name, data or None for a directory, mtime)`` entries."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data, mtime in entries:
            info = tarfile.TarInfo(name=name)
            info.mtime = mtime
            if data is None:
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
            else:
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _client(archives: dict[str, bytes]) -> tuple[MagicMock, MagicMock]:
    container = MagicMock()
    container.id = "ab" * 32
    container.wait.return_value = {"StatusCode": 0}
    container.logs.side_effect = lambda **kw: b""
    container.get_archive.side_effect = lambda path: (iter([archives.get(path, _tar([]))]), {})
    client = MagicMock()
    client.containers.create.return_value = container
    return client, container


async def _run(
    sandbox: DockerCodingSandbox, archives: dict[str, bytes], client: MagicMock | None = None,
) -> tuple[CodeResult, MagicMock]:
    """One run whose container hands back ``archives``. Pass the same
    ``client`` for every run of one sandbox: the process caches the first."""
    client = client or _client({})[0]
    container = client.containers.create.return_value
    container.get_archive.side_effect = lambda path: (iter([archives.get(path, _tar([]))]), {})
    with patch("docker.from_env", return_value=client):
        result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))
    return result, container


class TestASandboxKeepsABoundedAmountAcrossRuns:
    async def test_new_files_past_the_sandbox_limit_are_not_kept(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        now = time.time()
        results = []
        client, _c = _client({})
        for i in range(3):
            # Each run deletes what it got back and writes a new name.
            archive = _tar([("output", None, now), (f"output/part{i}.bin", b"x" * (400 * KiB), now)])
            result, _ = await _run(sandbox, {"/output": archive}, client)
            results.append(result)

        out = tmp_path / "wd" / "output"
        kept = sorted(p.name for p in out.iterdir())
        assert kept == ["part0.bin", "part1.bin"]
        assert results[2].artifacts == []
        assert "this sandbox already keeps" in results[2].stderr
        assert "across all runs" in results[2].stderr

    async def test_the_file_count_is_bounded_across_runs(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_FILES", "5")
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        now = time.time()
        client, _c = _client({})
        for run in range(3):
            files = [(f"output/r{run}-{i}.txt", b"hi", now) for i in range(3)]
            await _run(sandbox, {"/output": _tar([("output", None, now), *files])}, client)

        assert len(list((tmp_path / "wd" / "output").iterdir())) == 5

    async def test_files_restored_unchanged_do_not_use_the_run_budget(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_TOTAL_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("output/old.bin", b"o" * (900 * KiB))
        old_mtime = os.path.getmtime(tmp_path / "wd" / "output" / "old.bin")
        now = time.time()

        archive = _tar([
            ("output", None, now),
            ("output/old.bin", b"o" * (900 * KiB), old_mtime),
            ("output/new.bin", b"n" * (300 * KiB), now),
        ])
        result, _ = await _run(sandbox, {"/output": archive})

        assert result.artifacts == ["output/new.bin"]
        assert "Not all output" not in result.stderr
        assert (tmp_path / "wd" / "output" / "new.bin").stat().st_size == 300 * KiB

    async def test_a_changed_file_with_the_old_name_is_collected(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("output/report.txt", b"draft")
        now = time.time() + 5
        archive = _tar([("output", None, now), ("output/report.txt", b"final", now)])
        result, _ = await _run(sandbox, {"/output": archive})

        assert result.artifacts == ["output/report.txt"]
        assert (tmp_path / "wd" / "output" / "report.txt").read_bytes() == b"final"


class TestStagedFilesAreStreamed:
    async def test_large_staged_files_are_sent_from_disk(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        payload = os.urandom(3 * MiB)
        await sandbox.upload_file("input/big.bin", payload)
        seen: list[tuple[str, bool, bytes]] = []
        client, container = _client({})

        def _put(path: str, data: object) -> bool:
            is_bytes = isinstance(data, (bytes, bytearray))
            seen.append((path, is_bytes, bytes(data) if is_bytes else data.read()))
            return True

        container.put_archive.side_effect = _put
        with patch.object(docker_mod, "_collect_working_dir_inputs", side_effect=AssertionError), \
                patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert result.success
        staged = [s for s in seen if s[0] == "/src" and b"input/big.bin" in s[2][:4096]]
        assert len(staged) == 1
        _, is_bytes, body = staged[0]
        assert is_bytes is False
        with tarfile.open(fileobj=io.BytesIO(body)) as tar:
            assert tar.extractfile("input/big.bin").read() == payload

    async def test_an_unchanged_staged_file_is_not_extracted_or_counted(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_TOTAL_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("input/data.bin", b"d" * (900 * KiB))
        mtime = int(os.path.getmtime(tmp_path / "wd" / "input" / "data.bin"))
        now = time.time()
        archive = _tar([
            ("src", None, now),
            ("src/input/data.bin", b"d" * (900 * KiB), mtime),
            ("src/chart.png", b"p" * (300 * KiB), now),
        ])
        result, _ = await _run(sandbox, {"/src": archive})

        assert result.artifacts == ["chart.png"]
        assert "Not all output" not in result.stderr


class TestEveryEntryCounts:
    def test_directories_count_toward_the_entry_cap(self, tmp_path) -> None:
        entries: list[tuple[str, bytes | None, float]] = [("output", None, 0)]
        entries += [(f"output/d{i}", None, 0) for i in range(1000)]
        entries.append(("output/late.txt", b"x", 0))
        budget = ExtractBudget(ExtractLimits(max_file_bytes=MiB, max_total_bytes=MiB, max_files=50))

        extract_tar_stream([_tar(entries)], "/output", str(tmp_path), budget)

        assert budget.stopped
        assert budget.entries == 51
        assert not (tmp_path / "late.txt").exists()

    def test_headers_are_not_kept_while_reading(self, tmp_path) -> None:
        entries: list[tuple[str, bytes | None, float]] = [("output", None, 0)]
        entries += [(f"output/d{i}", None, 0) for i in range(500)]
        budget = ExtractBudget(ExtractLimits(max_file_bytes=MiB, max_total_bytes=MiB, max_files=10_000))
        most = 0
        real_next = tarfile.TarFile.next

        def _next(self: tarfile.TarFile) -> tarfile.TarInfo | None:
            nonlocal most
            most = max(most, len(self.members))
            return real_next(self)

        with patch.object(tarfile.TarFile, "next", _next):
            extract_tar_stream([_tar(entries)], "/output", str(tmp_path), budget)

        assert budget.entries == 501
        assert most <= 2

    def test_a_runs_archives_share_one_read_limit(self, tmp_path) -> None:
        budget = ExtractBudget(ExtractLimits(max_file_bytes=MiB, max_total_bytes=MiB, max_files=10))
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        extract_tar_stream([_tar([("output/a.txt", b"a", 0)])], "/output", str(tmp_path / "a"), budget)
        assert budget.read_bytes > 0
        budget.read_bytes = budget.read_limit

        extract_tar_stream([_tar([("src/b.txt", b"b", 0)])], "/src", str(tmp_path / "b"), budget)

        assert budget.stopped
        assert "read limit" in budget.note()
        assert not (tmp_path / "b" / "b.txt").exists()

    def test_retention_room_bounds_one_extraction(self, tmp_path) -> None:
        budget = ExtractBudget(
            ExtractLimits(max_file_bytes=MiB, max_total_bytes=MiB, max_files=100),
            retention=SandboxRetention(max_bytes=10, max_files=100, kept_bytes=6, kept_files=1),
        )
        extract_tar_stream(
            [_tar([("output/a", b"1234", 0), ("output/b", b"x", 0)])], "/output", str(tmp_path), budget,
        )
        assert (tmp_path / "a").exists()
        assert not (tmp_path / "b").exists()
        assert "this sandbox already keeps" in budget.note()
