"""SEC-FS-03 (C6.40): the Docker broker's persistent-folder copy bounds the archive a sandbox's folder makes.

``_strip_top`` re-roots a ``get_archive`` stream for a ``put_archive`` elsewhere. Its input is whatever the
sandbox left in the folder, so the stream is capped like every archive the broker takes, and read as plain tar:
the daemon never compresses one, so a compressed stream (a bomb's shape) is refused rather than inflated.
"""

from __future__ import annotations

import gzip
import io
import tarfile

import pytest

from app.harness.sandbox.docker_broker import _strip_top  # pyright: ignore[reportPrivateUsage]
from harness_core.sandbox.errors import InvalidSandboxRequest
from harness_core.testing.contracts.sandbox_snapshots import tar_of


def _copy(data: bytes, **kwargs: int) -> dict[str, bytes]:
    out = io.BytesIO()
    _strip_top(iter([data[i : i + 4096] for i in range(0, len(data), 4096)]), out, **kwargs)
    with tarfile.open(fileobj=out, mode="r:") as archive:
        return {m.name: archive.extractfile(m).read() for m in archive if m.isfile()}  # type: ignore[union-attr]


def test_sec_fs_03_c6_40_a_folder_is_re_rooted_at_its_top() -> None:
    assert _copy(tar_of({"cache/a": b"a", "cache/d/b": b"b"})) == {"a": b"a", "d/b": b"b"}


def test_sec_fs_03_c6_40_a_folder_archive_over_the_cap_is_refused_while_spooling() -> None:
    with pytest.raises(InvalidSandboxRequest):
        _ = _copy(tar_of({"cache/big": b"x" * 20_000}), max_bytes=16_384)


def test_sec_fs_03_c6_40_a_compressed_folder_archive_is_refused_not_inflated() -> None:
    with pytest.raises(tarfile.ReadError):
        _ = _copy(gzip.compress(tar_of({"cache/zeros": b"\0" * 1_000_000})))
