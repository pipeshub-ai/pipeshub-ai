"""Files a run rewrites in place are collected, and replacing a kept file
does not count it against the sandbox's retention a second time."""

from __future__ import annotations

import os
import time

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
