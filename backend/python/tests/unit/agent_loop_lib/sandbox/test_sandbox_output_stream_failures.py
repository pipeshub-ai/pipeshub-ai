"""A run archive that breaks part-way is not taken for a complete one, and
already-buffered output is capped without a full-size encoded copy."""

from __future__ import annotations

import io
import os
import tarfile
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

import app.agent_loop_lib.sandbox.coding.docker as docker_mod
from app.agent_loop_lib.sandbox.coding.output_limits import (
    ExtractBudget,
    cap_text,
    extract_tar_stream,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

KiB = 1024


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SANDBOX_OUTPUT_MAX_SANDBOX_BYTES", "SANDBOX_OUTPUT_MAX_SANDBOX_FILES",
                 "SANDBOX_OUTPUT_MAX_TOTAL_BYTES", "SANDBOX_OUTPUT_MAX_FILE_BYTES",
                 "SANDBOX_OUTPUT_MAX_FILES", "SANDBOX_DEPS_MAX_TOTAL_BYTES",
                 "SANDBOX_STDIO_HEAD_BYTES", "SANDBOX_STDIO_TAIL_BYTES"):
        monkeypatch.delenv(name, raising=False)


def _tar(entries: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="output")
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        for name, data in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _broken_after(data: bytes, cut: int) -> Iterator[bytes]:
    """The archive's first ``cut`` bytes, then the connection fails."""
    yield data[:cut]
    raise ConnectionError("connection reset by peer")


def _cut_point(archive: bytes, marker: bytes) -> int:
    """An offset in the middle of the member whose data starts with ``marker``."""
    return archive.index(marker) + 16 * KiB


class TestABrokenArchiveStream:
    def test_a_kept_file_is_not_overwritten_with_part_of_its_replacement(self, tmp_path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        (out / "report.txt").write_bytes(b"old report")
        archive = _tar([("output/report.txt", b"N" + b"n" * (64 * KiB))])
        budget = ExtractBudget.for_output()

        extract_tar_stream(_broken_after(archive, _cut_point(archive, b"Nn")), "/output", str(out), budget)

        assert (out / "report.txt").read_bytes() == b"old report"
        assert sorted(os.listdir(out)) == ["report.txt"]
        assert budget.stopped and "/output" in budget.note()

    def test_a_new_file_cut_short_is_not_left_behind(self, tmp_path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        archive = _tar([("output/done.txt", b"done"), ("output/big.bin", b"B" + b"b" * (64 * KiB))])
        budget = ExtractBudget.for_output()

        extract_tar_stream(_broken_after(archive, _cut_point(archive, b"Bb")), "/output", str(out), budget)

        assert sorted(os.listdir(out)) == ["done.txt"]
        assert (out / "done.txt").read_bytes() == b"done"
        assert budget.stopped and budget.kept_files == 1

    def test_an_archive_that_ends_early_is_reported(self, tmp_path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        archive = _tar([("output/big.bin", b"B" + b"b" * (64 * KiB))])
        budget = ExtractBudget.for_output()

        extract_tar_stream(iter([archive[:_cut_point(archive, b"Bb")]]), "/output", str(out), budget)

        assert os.listdir(out) == []
        assert budget.stopped

    def test_a_complete_archive_still_extracts_with_its_mtime(self, tmp_path) -> None:
        out = tmp_path / "out"
        out.mkdir()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="output/a.txt")
            info.size, info.mtime = 3, 1_700_000_000
            tar.addfile(info, io.BytesIO(b"abc"))
        budget = ExtractBudget.for_output()

        extract_tar_stream(iter([buf.getvalue()]), "/output", str(out), budget)

        assert os.listdir(out) == ["a.txt"]
        assert (out / "a.txt").read_bytes() == b"abc"
        assert int(os.path.getmtime(out / "a.txt")) == 1_700_000_000
        assert budget.note() == ""

    def test_a_dependency_archive_cut_short_fails_the_install_and_is_not_merged(self, tmp_path) -> None:
        archive = _tar([("output/pkg/__init__.py", b"P" + b"p" * (64 * KiB))])
        container = MagicMock()
        container.get_archive.return_value = (_broken_after(archive, _cut_point(archive, b"Pp")), {})
        staging = tmp_path / "staging"
        staging.mkdir()
        budget = ExtractBudget.for_dependencies()

        docker_mod._extract_container_dir(container, "/output", str(staging), allow_symlinks=True, budget=budget)

        # The installer refuses a non-empty note instead of merging staging.
        assert budget.note()


class _NoWholeEncode(str):
    """A string that refuses to be encoded in one piece."""

    def encode(self, *args: object, **kwargs: object) -> bytes:  # type: ignore[override]
        raise AssertionError("the whole output was encoded at once")


class TestCapText:
    def test_large_output_is_not_encoded_in_one_piece(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_STDIO_HEAD_BYTES", "100")
        monkeypatch.setenv("SANDBOX_STDIO_TAIL_BYTES", "50")
        text = "h" * 100 + "m" * 300_000 + "é" * 25

        capped = cap_text(_NoWholeEncode(text))

        assert capped == "h" * 100 + "\n[... 300000 bytes truncated ...]\n" + "é" * 25

    def test_output_with_characters_across_slice_boundaries_keeps_its_byte_count(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_STDIO_HEAD_BYTES", "10")
        monkeypatch.setenv("SANDBOX_STDIO_TAIL_BYTES", "10")
        text = "€" * 100_000

        capped = cap_text(text)

        dropped = len(text.encode()) - 20
        assert f"[... {dropped} bytes truncated ...]" in capped

    def test_small_output_is_returned_as_is(self) -> None:
        assert cap_text("hello") == "hello"
