"""Files a run rewrites in place are collected, and replacing a kept file
does not count it against the sandbox's retention a second time."""

from __future__ import annotations

import os
import time
from unittest.mock import patch

from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

from .test_docker_coding_sandbox import (  # noqa: F401  (fixtures)
    _fake_docker_module,
    _fresh_docker_provider,
)
from .test_sandbox_output_retention import _client, _env, _run, _tar  # noqa: F401  (fixture)

KiB = 1024
MiB = 1024 * 1024


class TestSameSizeAndMtimeRewrites:
    async def test_an_output_file_rewritten_with_its_old_size_and_mtime_is_collected(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("output/report.txt", b"AAA")
        mtime = os.path.getmtime(tmp_path / "wd" / "output" / "report.txt")
        archive = _tar([("output", None, mtime), ("output/report.txt", b"BBB", mtime)])
        result, _ = await _run(sandbox, {"/output": archive})

        assert (tmp_path / "wd" / "output" / "report.txt").read_bytes() == b"BBB"
        assert result.artifacts == ["output/report.txt"]
        assert not [p for p in (tmp_path / "wd" / "output").iterdir() if p.name.startswith(".sandbox-")]

    async def test_a_staged_input_rewritten_with_its_old_size_and_mtime_is_collected(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("data.csv", b"a,b\n1,2\n")
        mtime = int(os.path.getmtime(tmp_path / "wd" / "data.csv"))
        archive = _tar([("src", None, mtime), ("src/data.csv", b"a,b\n3,4\n", mtime)])
        result, _ = await _run(sandbox, {"/src": archive})

        assert (tmp_path / "wd" / "data.csv").read_bytes() == b"a,b\n3,4\n"
        assert "data.csv" in result.artifacts

    async def test_an_identical_restored_file_is_still_skipped(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_TOTAL_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("output/old.bin", b"o" * (900 * KiB))
        mtime = os.path.getmtime(tmp_path / "wd" / "output" / "old.bin")
        now = time.time()
        archive = _tar([
            ("output", None, now),
            ("output/old.bin", b"o" * (900 * KiB), mtime),
            ("output/new.bin", b"n" * (300 * KiB), now),
        ])
        result, _ = await _run(sandbox, {"/output": archive})

        assert result.artifacts == ["output/new.bin"]
        assert "Not all output" not in result.stderr


class TestReplacingAKeptFileCountsOnlyItsGrowth:
    async def test_rewriting_one_output_many_times_stays_within_the_sandbox_limit(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        client, _c = _client({})
        for run in range(5):
            stamp = time.time() + 10 * (run + 1)
            body = bytes([65 + run]) * (400 * KiB)
            archive = _tar([("output", None, stamp), ("output/out.bin", body, stamp)])
            result, _ = await _run(sandbox, {"/output": archive}, client)
            assert "Not all output" not in result.stderr, run
            assert (tmp_path / "wd" / "output" / "out.bin").read_bytes() == body

    async def test_rewriting_one_working_dir_file_many_times_stays_within_the_sandbox_limit(
        self, tmp_path, monkeypatch,
    ) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        client, _c = _client({})
        for run in range(5):
            stamp = time.time() + 10 * (run + 1)
            body = bytes([65 + run]) * (400 * KiB)
            archive = _tar([("src", None, stamp), ("src/table.bin", body, stamp)])
            result, _ = await _run(sandbox, {"/src": archive}, client)
            assert "Not all output" not in result.stderr, run
            assert (tmp_path / "wd" / "table.bin").read_bytes() == body

    async def test_new_names_are_still_bounded(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        client, _c = _client({})
        now = time.time()
        stderr = ""
        for run in range(3):
            archive = _tar([("output", None, now), (f"output/p{run}.bin", b"x" * (400 * KiB), now)])
            result, _ = await _run(sandbox, {"/output": archive}, client)
            stderr = result.stderr
        assert "this sandbox already keeps" in stderr


class TestShrinkingAnUploadEarnsNoCredit:
    async def test_truncating_a_staged_input_does_not_make_room_for_new_output(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("big_input.bin", b"i" * (900 * KiB))  # uploaded, never charged
        client, _c = _client({})
        stderr = ""
        for run in range(3):
            stamp = time.time() + 10 * (run + 1)
            archive_src = _tar([("src", None, stamp), ("src/big_input.bin", b"", stamp)])
            archive_out = _tar([("output", None, stamp), (f"output/new{run}.bin", b"n" * (400 * KiB), stamp)])
            result, _ = await _run(sandbox, {"/src": archive_src, "/output": archive_out}, client)
            stderr = result.stderr
        # 3 x 400 KiB of new output is past the 1 MiB limit whatever happened to the upload.
        assert "this sandbox already keeps" in stderr


class TestAnUploadDuringTheRunWins:
    async def test_an_input_replaced_on_the_host_during_the_run_keeps_the_new_content(self, tmp_path) -> None:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("data.csv", b"old,1\n")
        host = tmp_path / "wd" / "data.csv"
        mtime = int(os.path.getmtime(host))
        archives = {"/src": _tar([("src", None, mtime), ("src/data.csv", b"old,1\n", mtime)])}
        client, container = _client({})

        def get_archive(path: str) -> tuple:
            if path == "/src":
                host.write_bytes(b"new upload,22\n")  # another call uploads while this run is going
                os.utime(host, (mtime + 30, mtime + 30))
            return iter([archives.get(path, _tar([]))]), {}

        container.get_archive.side_effect = get_archive
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))

        assert host.read_bytes() == b"new upload,22\n"
        assert "data.csv" not in result.artifacts


class TestShrinkingACollectedFileGivesBackItsCharge:
    async def test_a_charged_working_dir_file_shrunk_then_regrown_leaves_room(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        client, _c = _client({})
        sizes = [800 * KiB, 100 * KiB, 800 * KiB]
        for run, size in enumerate(sizes):
            stamp = time.time() + 10 * (run + 1)
            archive = _tar([("src", None, stamp), ("src/table.bin", bytes([65 + run]) * size, stamp)])
            result, _ = await _run(sandbox, {"/src": archive}, client)
            assert "Not all output" not in result.stderr, run
        stamp = time.time() + 100
        archive = _tar([("output", None, stamp), ("output/extra.bin", b"e" * (100 * KiB), stamp)])
        result, _ = await _run(sandbox, {"/output": archive}, client)
        # 800 KiB + 100 KiB kept is within the 1 MiB limit.
        assert "Not all output" not in result.stderr
        assert (tmp_path / "wd" / "output" / "extra.bin").exists()

    async def test_a_charged_working_dir_file_shrunk_makes_room_for_new_output(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        client, _c = _client({})
        for run, size in enumerate([800 * KiB, 100 * KiB]):
            stamp = time.time() + 10 * (run + 1)
            archive = _tar([("src", None, stamp), ("src/table.bin", bytes([65 + run]) * size, stamp)])
            result, _ = await _run(sandbox, {"/src": archive}, client)
            assert "Not all output" not in result.stderr, run
        stamp = time.time() + 100
        archive = _tar([("output", None, stamp), ("output/extra.bin", b"e" * (700 * KiB), stamp)])
        result, _ = await _run(sandbox, {"/output": archive}, client)
        # 100 KiB + 700 KiB kept is within the 1 MiB limit.
        assert "Not all output" not in result.stderr

    async def test_rewriting_an_upload_charges_it_in_full_once(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", str(1 * MiB))
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("big_input.bin", b"i" * (900 * KiB))  # uploaded, never charged
        client, _c = _client({})
        stamp = time.time() + 10
        archive_src = _tar([("src", None, stamp), ("src/big_input.bin", b"j" * (100 * KiB), stamp)])
        result, _ = await _run(sandbox, {"/src": archive_src}, client)
        assert "Not all output" not in result.stderr
        stamp = time.time() + 20
        archive_out = _tar([("output", None, stamp), ("output/new.bin", b"n" * (950 * KiB), stamp)])
        result, _ = await _run(sandbox, {"/output": archive_out}, client)
        # The rewritten upload now keeps 100 KiB that was charged; 950 KiB more is past 1 MiB.
        assert "this sandbox already keeps" in result.stderr


class TestASameSizeUploadInTheSameSecondWins:
    """The upload keeps its size and whole-second mtime, so only the
    nanosecond mtime or the inode tells it apart from the staged copy."""

    async def _run_with_upload_during_collection(self, tmp_path, upload) -> tuple:
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        await sandbox.upload_file("data.csv", b"old,1\n")
        host = tmp_path / "wd" / "data.csv"
        second = int(time.time()) - 60
        os.utime(host, ns=(second * 10**9 + 100, second * 10**9 + 100))
        # The run rewrote the file in the container with the same size and second.
        archives = {"/src": _tar([("src", None, second), ("src/data.csv", b"ctr,1\n", second)])}
        client, container = _client({})

        def get_archive(path: str) -> tuple:
            if path == "/src":
                upload(host, second)  # another call uploads while this run is going
            return iter([archives.get(path, _tar([]))]), {}

        container.get_archive.side_effect = get_archive
        with patch("docker.from_env", return_value=client):
            result = await sandbox.execute(CodeRequest(code="print(1)", language="python"))
        return host, result

    async def test_an_in_place_upload_with_a_later_nanosecond_mtime_is_kept(self, tmp_path) -> None:
        def upload(host, second: int) -> None:
            host.write_bytes(b"new,1\n")
            os.utime(host, ns=(second * 10**9 + 500, second * 10**9 + 500))

        host, result = await self._run_with_upload_during_collection(tmp_path, upload)

        assert host.read_bytes() == b"new,1\n"
        assert "data.csv" not in result.artifacts

    async def test_an_upload_renamed_into_place_with_the_same_mtime_is_kept(self, tmp_path) -> None:
        def upload(host, second: int) -> None:
            staged = os.stat(host)
            tmp = host.with_name("data.csv.tmp")
            tmp.write_bytes(b"new,1\n")
            os.utime(tmp, ns=(staged.st_atime_ns, staged.st_mtime_ns))
            os.replace(tmp, host)
            assert os.stat(host).st_mtime_ns == staged.st_mtime_ns

        host, result = await self._run_with_upload_during_collection(tmp_path, upload)

        assert host.read_bytes() == b"new,1\n"
        assert "data.csv" not in result.artifacts
