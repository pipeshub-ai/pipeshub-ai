"""SEC-FS-03 (C6.40): every place the harness unpacks an archive bounds what it may expand to.

The shared meter (``harness_core.archive_limits``) counts the bytes actually produced, never a header's claim,
and holds three limits at once: the call site's byte cap, a ratio of produced to consumed bytes past a floor,
and a member count. Each call site is shown refusing a bomb that it used to inflate or read in full.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import struct
import tarfile
import zlib
from typing import TYPE_CHECKING

import pytest

from harness_core.archive_limits import (
    ENV_MAX_MEMBERS,
    ENV_MAX_RATIO,
    ENV_RATIO_FLOOR_BYTES,
    EXPANDED_TOO_LARGE,
    RATIO_TOO_HIGH,
    TOO_MANY_MEMBERS,
    ExpansionLimits,
    ExpansionMeter,
    ExpansionRefused,
    InvalidArchiveLimit,
    inflate,
)
from harness_core.engines.errors import SnapshotRefused, SnapshotUnavailable
from harness_core.engines.snapshots import (
    SNAPSHOT_CORRUPT,
    SNAPSHOT_TOO_LARGE,
    SnapshotPolicy,
    pack,
    read_archive,
    unpack,
)
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.git import decoded_body, pack_commits
from harness_core.sandbox.egress.types import ProxyRequest
from harness_core.sandbox.streams import iter_bytes
from harness_core.sandbox.tar import read_tar_stream
from harness_core.testing.contracts.sandbox_snapshots import tar_of
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.sync import (
    EXPANSION_LIMIT,
    SyncLimits,
    SyncState,
    TarWorkspaceSync,
)
from harness_core.workspace.types import ObjectScope, empty_manifest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

MiB = 1024**2
SCOPE = ObjectScope("org-1", "ws:session-1")
# A small floor, so a bomb of a few MiB shows what a large one does in production.
SMALL_FLOOR = {ENV_RATIO_FLOOR_BYTES: str(MiB)}


@pytest.fixture
def small_floor(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in SMALL_FLOOR.items():
        monkeypatch.setenv(name, value)


# The meter and the inflater


def test_sec_fs_03_c6_40_the_inflater_round_trips_and_says_what_followed() -> None:
    for data in (b"", b"a" * 10, bytes(range(256)) * 2000, b"z" * (3 * MiB)):
        out, inflater = inflate(zlib.compress(data) + b"tail", ExpansionLimits(max_bytes=10 * MiB))
        assert out == data and inflater.eof and inflater.unused_data == b"tail"


def test_sec_fs_03_c6_40_a_zlib_bomb_is_refused_on_its_ratio_without_inflating_it_whole() -> None:
    bomb = zlib.compress(b"\0" * (64 * MiB))
    limits = ExpansionLimits(max_bytes=1024 * MiB, ratio_floor_bytes=MiB)
    with pytest.raises(ExpansionRefused) as refused:
        _ = inflate(bomb, limits)
    assert refused.value.reason_code == RATIO_TOO_HIGH


def test_sec_fs_03_c6_40_a_stream_past_the_byte_cap_is_refused() -> None:
    with pytest.raises(ExpansionRefused) as refused:
        _ = inflate(zlib.compress(b"x" * (2 * MiB)), ExpansionLimits(max_bytes=MiB))
    assert refused.value.reason_code == EXPANDED_TOO_LARGE


def test_sec_fs_03_c6_40_the_meter_counts_members_and_produced_bytes() -> None:
    meter = ExpansionMeter(ExpansionLimits(max_bytes=100, max_ratio=2, ratio_floor_bytes=10, max_members=2), consumed=20)
    meter.member()
    meter.member()
    with pytest.raises(ExpansionRefused) as members:
        meter.member()
    assert members.value.reason_code == TOO_MANY_MEMBERS
    assert meter.allowance() == 40 and meter.fits(40) and not meter.fits(41)
    with pytest.raises(ExpansionRefused) as ratio:
        meter.produce(41)
    assert ratio.value.reason_code == RATIO_TOO_HIGH


def test_sec_fs_03_c6_40_the_limits_read_their_settings_strictly() -> None:
    assert ExpansionLimits.from_env(10, {}) == ExpansionLimits(max_bytes=10)
    configured = ExpansionLimits.from_env(10, {ENV_MAX_RATIO: "20", ENV_RATIO_FLOOR_BYTES: "4096", ENV_MAX_MEMBERS: "7"})
    assert configured == ExpansionLimits(max_bytes=10, max_ratio=20, ratio_floor_bytes=4096, max_members=7)
    for env in ({ENV_MAX_RATIO: "lots"}, {ENV_MAX_RATIO: "-1"}, {ENV_MAX_MEMBERS: "2.5"}, {ENV_RATIO_FLOOR_BYTES: "nan"}):
        with pytest.raises(InvalidArchiveLimit):
            _ = ExpansionLimits.from_env(10, env)


# Engine snapshots (P5.9b): the sealed envelope and the sandbox's own tar


def test_sec_fs_03_c6_40_a_snapshot_envelope_that_inflates_like_a_bomb_is_refused(small_floor: None) -> None:
    payload = pack(SCOPE, b"\0" * (32 * MiB))
    with pytest.raises(SnapshotUnavailable) as refused:
        _ = unpack(SCOPE, payload)
    assert refused.value.reason_code == SNAPSHOT_CORRUPT
    assert "times its size" in str(refused.value)


def test_sec_fs_03_c6_40_a_snapshot_that_compresses_well_inside_the_floor_still_restores() -> None:
    data = tar_of({"tmp/.cache/zeros": b"\0" * (8 * MiB)})
    assert unpack(SCOPE, pack(SCOPE, data)) == data


def test_sec_fs_03_c6_40_a_snapshot_with_too_many_members_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_MAX_MEMBERS, "3")
    data = tar_of({f"tmp/.cache/{n}": b"x" for n in range(4)})
    with pytest.raises(SnapshotRefused) as refused:
        _ = read_archive(data, SnapshotPolicy(("/tmp/.cache",), max_bytes=MiB))
    assert refused.value.reason_code == SNAPSHOT_TOO_LARGE


# A sandbox folder read whole (engine transcripts, tool folder version)


async def _chunks(data: bytes) -> AsyncIterator[bytes]:
    async for piece in iter_bytes(data):
        yield piece


async def test_sec_fs_03_c6_40_a_compressed_sandbox_archive_is_refused_not_inflated() -> None:
    bomb = gzip.compress(tar_of({"transcript.jsonl": b"\0" * (4 * MiB)}))
    with pytest.raises(tarfile.ReadError):
        _ = await read_tar_stream(_chunks(bomb))


async def test_sec_fs_03_c6_40_a_sandbox_archive_over_its_byte_or_member_cap_is_refused() -> None:
    data = tar_of({f"t/{n}": b"y" * 1024 for n in range(8)})
    assert len(await read_tar_stream(_chunks(data))) == 8
    with pytest.raises(ExpansionRefused) as large:
        _ = await read_tar_stream(_chunks(data), limits=ExpansionLimits(max_bytes=4096))
    assert large.value.reason_code == EXPANDED_TOO_LARGE
    with pytest.raises(ExpansionRefused) as many:
        _ = await read_tar_stream(_chunks(data), limits=ExpansionLimits(max_bytes=MiB, max_members=4))
    assert many.value.reason_code == TOO_MANY_MEMBERS


# Workspace and outputs collection: hard links make a small archive read a large file many times


class _Sandbox:
    def __init__(self, archive: bytes) -> None:
        self._archive = archive

    @property
    def id(self) -> str:
        return "sbx-bomb"

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        return _chunks(self._archive)


def _hard_link_bomb(links: int, size: int) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        big = tarfile.TarInfo("ws/out/big.bin")
        big.size = size
        archive.addfile(big, io.BytesIO(b"b" * size))
        for n in range(links):
            link = tarfile.TarInfo(f"ws/out/copy-{n}.bin")
            link.type, link.linkname = tarfile.LNKTYPE, "ws/out/big.bin"
            archive.addfile(link)
    return buffer.getvalue()


async def test_sec_fs_03_c6_40_hard_links_past_the_expansion_cap_are_skipped_and_the_rest_collected() -> None:
    sync = TarWorkspaceSync(
        objects=InMemoryBinaryObjectStore(), scope=SCOPE, limits=SyncLimits(max_expanded_bytes=4 * MiB)
    )
    collected = await sync.collect(
        _Sandbox(_hard_link_bomb(links=20, size=MiB)),  # type: ignore[arg-type]
        SyncState("sbx-bomb", "/ws", empty_manifest("ws:session-1")),
    )
    kept = set(collected.changes.upserts)
    assert len(kept) == 4 and "out/big.bin" in kept
    assert len(collected.skipped) == 17
    assert set(collected.skipped.values()) == {EXPANSION_LIMIT}


async def test_sec_fs_03_c6_40_hard_links_are_bounded_by_ratio_as_well(small_floor: None) -> None:
    sync = TarWorkspaceSync(objects=InMemoryBinaryObjectStore(), scope=SCOPE)
    archive = _hard_link_bomb(links=400, size=256 * 1024)
    collected = await sync.collect(
        _Sandbox(archive),  # type: ignore[arg-type]
        SyncState("sbx-bomb", "/ws", empty_manifest("ws:session-1")),
    )
    # 100 times the archive's size, not 401 copies of the file.
    collected_bytes = 256 * 1024 * len(collected.changes.upserts)
    assert collected_bytes <= 100 * len(archive)
    assert EXPANSION_LIMIT in set(collected.skipped.values())


# The egress proxy's git inspection: a gzip body and a pushed pack


def _single_object_pack(kind: int, content: bytes, *, declared: int | None = None) -> bytes:
    size = len(content) if declared is None else declared
    header = bytearray([(kind << 4) | (size & 0x0F) | (0x80 if size > 15 else 0)])
    size >>= 4
    while size:
        header.append((size & 0x7F) | (0x80 if size > 0x7F else 0))
        size >>= 7
    body = b"PACK" + struct.pack(">II", 2, 1) + bytes(header) + zlib.compress(content)
    return body + hashlib.sha1(body, usedforsecurity=False).digest()


def test_sec_fs_03_c6_40_a_pushed_blob_that_inflates_like_a_bomb_is_refused(small_floor: None) -> None:
    with pytest.raises(EgressRefused) as refused:
        _ = pack_commits(_single_object_pack(3, b"\0" * (16 * MiB)))
    assert refused.value.reason == R.BODY_TOO_LARGE


def test_sec_fs_03_c6_40_a_pushed_object_that_inflates_past_its_declared_size_is_refused() -> None:
    with pytest.raises(EgressRefused):
        _ = pack_commits(_single_object_pack(3, b"\0" * MiB, declared=10))
    assert pack_commits(_single_object_pack(3, b"blob")) == {}


def test_sec_fs_03_c6_40_a_gzip_git_body_that_inflates_like_a_bomb_is_refused(small_floor: None) -> None:
    request = ProxyRequest(
        method="POST",
        target="https://git.example/o/r.git/git-upload-pack",
        headers=(("content-encoding", "gzip"),),
        body=gzip.compress(b"0" * (32 * MiB)),
    )
    with pytest.raises(EgressRefused) as refused:
        _ = decoded_body(request)
    assert refused.value.reason == R.BODY_TOO_LARGE
