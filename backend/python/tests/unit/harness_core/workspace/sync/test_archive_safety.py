"""TP-1..TP-6, TB-1..TB-4: what a hostile or broken sandbox archive can and cannot make collect do."""

from __future__ import annotations

import io
import logging
import tarfile
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.streams import iter_bytes
from harness_core.testing.contracts.workspace_sync import CountingObjectStore
from harness_core.workspace import (
    EXECUTABLE_MODE,
    SYMLINK_MODE,
    ObjectScope,
    empty_manifest,
)
from harness_core.workspace.memory import InMemoryBinaryObjectStore
from harness_core.workspace.sync import (
    FILE_TOO_LARGE,
    HARD_LINK,
    SPECIAL_FILE,
    SYMLINK_ESCAPES,
    UNSTORABLE_PATH,
    ArchiveMalformed,
    ArchiveTooLarge,
    SyncLimits,
    SyncState,
    TarWorkspaceSync,
    TooManyEntries,
    UnsafeArchiveMember,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

ROOT = "/ws"
SCOPE = ObjectScope("tenant-a", "ws:s")


class HostileSandbox:
    """A sandbox whose ``get_tar`` returns whatever archive the test crafted."""

    def __init__(self, archive: bytes | Sequence[bytes]) -> None:
        self._chunks = [archive] if isinstance(archive, bytes) else list(archive)

    @property
    def id(self) -> str:
        return "sbx-hostile"

    async def get_tar(self, src: str) -> AsyncIterator[bytes]:
        assert src == ROOT

        async def chunks() -> AsyncIterator[bytes]:
            for chunk in self._chunks:
                for piece in [p async for p in iter_bytes(chunk)]:
                    yield piece

        return chunks()


def member(name: str, data: bytes = b"", *, kind: bytes = tarfile.REGTYPE, link: str = "", mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.type, info.linkname, info.mode = kind, link, mode
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    return info, data


def archive(*members: tuple[tarfile.TarInfo, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as out:
        for info, data in members:
            out.addfile(info, io.BytesIO(data) if info.isreg() else None)
    return buffer.getvalue()


def sync(limits: SyncLimits | None = None) -> tuple[TarWorkspaceSync, CountingObjectStore]:
    objects = CountingObjectStore(InMemoryBinaryObjectStore())
    return TarWorkspaceSync(objects=objects, scope=SCOPE, limits=limits), objects


def state() -> SyncState:
    return SyncState("sbx-hostile", ROOT, empty_manifest("ws:s"))


@pytest.mark.parametrize("name", ["../x", "/etc/passwd", "ws/../x", "ws/./x", "other/x", "wsx/y"])
async def test_tp1_an_unsafe_member_refuses_the_collect(name: str, caplog: pytest.LogCaptureFixture) -> None:
    engine_sync, objects = sync()
    hostile = HostileSandbox(archive(member("ws/ok.txt", b"fine"), member(name, b"evil")))
    with caplog.at_level(logging.INFO, logger="harness_core.workspace.sync"), pytest.raises(UnsafeArchiveMember):
        await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    assert objects.puts == 0
    refused = [r for r in caplog.records if r.getMessage().startswith("workspace_sync.refused")]
    assert len(refused) == 1
    assert refused[0].levelno == logging.WARNING
    assert refused[0].harness["reason"] == "unsafe_member"  # type: ignore[attr-defined]


async def test_tp2_names_the_store_cannot_hold_are_skipped() -> None:
    engine_sync, _ = sync()
    long_name, deep, nfd = "x" * 1025, "/".join(["d"] * 33), "café.txt"
    hostile = HostileSandbox(
        archive(
            member("ws/ok.txt", b"fine"),
            member(f"ws/{long_name}", b"1"),
            member(f"ws/{deep}", b"2"),
            member("ws/back\\slash", b"3"),
            member(f"ws/{nfd}", b"4"),
        )
    )
    collected = await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    assert set(collected.changes.upserts) == {"ok.txt"}
    assert collected.skipped == dict.fromkeys([long_name, deep, "back\\slash", nfd], UNSTORABLE_PATH)


async def test_tp3_links_out_of_the_root_are_skipped_and_links_inside_kept() -> None:
    engine_sync, _ = sync()
    hostile = HostileSandbox(
        archive(
            member("ws/passwd", kind=tarfile.SYMTYPE, link="/etc/passwd"),
            member("ws/a/up", kind=tarfile.SYMTYPE, link="../../x"),
            member("ws/a/sib", kind=tarfile.SYMTYPE, link="../target.txt"),
            member("ws/target.txt", b"t"),
        )
    )
    collected = await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    assert collected.skipped == {"passwd": SYMLINK_ESCAPES, "a/up": SYMLINK_ESCAPES}
    link = collected.changes.upserts["a/sib"]
    assert link.mode == SYMLINK_MODE
    assert link.mime_type == "inode/symlink"
    assert link.content.size_bytes == len(b"../target.txt")


async def test_tp4_a_hard_link_to_a_collected_file_is_that_file() -> None:
    engine_sync, objects = sync()
    hostile = HostileSandbox(
        archive(
            member("ws/original.bin", b"shared bytes", mode=0o755),
            member("ws/again.bin", kind=tarfile.LNKTYPE, link="ws/original.bin"),
            member("ws/missing", kind=tarfile.LNKTYPE, link="ws/nowhere"),
            member("ws/outside", kind=tarfile.LNKTYPE, link="../etc/shadow"),
            member("ws/later", kind=tarfile.LNKTYPE, link="ws/after"),
            member("ws/after", b"after"),
        )
    )
    collected = await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    upserts = collected.changes.upserts
    assert upserts["again.bin"].content == upserts["original.bin"].content
    assert upserts["again.bin"].mode == EXECUTABLE_MODE
    assert collected.skipped == {"missing": HARD_LINK, "outside": HARD_LINK, "later": HARD_LINK}
    assert objects.puts == 2


async def test_tp5_devices_and_fifos_are_skipped() -> None:
    engine_sync, _ = sync()
    hostile = HostileSandbox(
        archive(
            member("ws/tty", kind=tarfile.CHRTYPE),
            member("ws/disk", kind=tarfile.BLKTYPE),
            member("ws/pipe", kind=tarfile.FIFOTYPE),
            member("ws/ok", b"ok"),
        )
    )
    collected = await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    assert collected.skipped == dict.fromkeys(["tty", "disk", "pipe"], SPECIAL_FILE)
    assert set(collected.changes.upserts) == {"ok"}


async def test_tp6_set_id_and_sticky_bits_are_dropped() -> None:
    engine_sync, _ = sync()
    hostile = HostileSandbox(archive(member("ws/suid", b"x", mode=0o4755), member("ws/sticky", b"y", mode=0o1644)))
    upserts = (await engine_sync.collect(hostile, state())).changes.upserts  # type: ignore[arg-type]
    assert (upserts["suid"].mode, upserts["sticky"].mode) == (0o100755, 0o100644)


async def test_tb1_too_many_entries_are_refused_before_any_upload() -> None:
    engine_sync, objects = sync(SyncLimits(max_entries=10))
    hostile = HostileSandbox(archive(*(member(f"ws/f{i}", f"{i}".encode()) for i in range(11))))
    with pytest.raises(TooManyEntries):
        await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    assert objects.puts == 0


async def test_tb2_a_size_past_the_end_of_the_archive_is_malformed_and_never_allocated() -> None:
    header = tarfile.TarInfo("ws/huge")
    header.size = 1 << 40
    crafted = header.tobuf(format=tarfile.GNU_FORMAT) + b"x" * 8192 + bytes(1024)
    engine_sync, objects = sync()
    tracemalloc.start()
    try:
        with pytest.raises(ArchiveMalformed):
            await engine_sync.collect(HostileSandbox(crafted), state())  # type: ignore[arg-type]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024**2
    assert objects.puts == 0


async def test_tb2_a_truncated_member_is_malformed() -> None:
    whole = archive(member("ws/a", b"a" * 4096))
    engine_sync, _ = sync()
    with pytest.raises(ArchiveMalformed):
        await engine_sync.collect(HostileSandbox(whole[:1024]), state())  # type: ignore[arg-type]


async def test_tb3_an_archive_stream_over_the_limit_is_refused_while_spooling() -> None:
    engine_sync, objects = sync(SyncLimits(max_archive_bytes=1024**2))
    endless = [bytes(256 * 1024)] * 8
    with pytest.raises(ArchiveTooLarge):
        await engine_sync.collect(HostileSandbox(endless), state())  # type: ignore[arg-type]
    assert objects.puts == 0


async def test_tb4_a_file_over_the_limit_is_skipped_and_the_rest_collected() -> None:
    engine_sync, _ = sync(SyncLimits(max_file_bytes=1000))
    hostile = HostileSandbox(archive(member("ws/big", b"b" * 1001), member("ws/small", b"s" * 1000)))
    collected = await engine_sync.collect(hostile, state())  # type: ignore[arg-type]
    assert collected.skipped == {"big": FILE_TOO_LARGE}
    assert set(collected.changes.upserts) == {"small"}


async def test_reason_codes_are_distinct() -> None:
    codes = [cls.reason_code for cls in (UnsafeArchiveMember, ArchiveMalformed, TooManyEntries, ArchiveTooLarge)]
    assert len(set(codes)) == len(codes)
