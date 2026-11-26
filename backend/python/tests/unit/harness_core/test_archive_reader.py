"""The shared metered archive reader (SEC-C-05, SEC-C-07): a bomb corpus, unsafe members, and a normal round trip."""

from __future__ import annotations

import bz2
import dataclasses
import io
import lzma
import stat
import tarfile
import zipfile
import zlib
from typing import TYPE_CHECKING

import pytest

from harness_core.archive_limits import (
    EXPANDED_TOO_LARGE,
    RATIO_TOO_HIGH,
    TOO_MANY_MEMBERS,
)
from harness_core.archive_reader import (
    CORRUPT,
    ENCRYPTED,
    ENTRY_TOO_LARGE,
    LINK_ENTRY,
    NOT_AN_ARCHIVE,
    SPECIAL_ENTRY,
    UNSAFE_PATH,
    ArchiveLimits,
    ArchiveRefused,
    archive_kind,
    check_zip,
    extract_archive,
    iter_archive,
    read_archive,
    safe_entry_path,
    spool_chunks,
    zip_declared_entries,
)

if TYPE_CHECKING:
    from pathlib import Path

_MIB = 1024 * 1024
LIMITS = ArchiveLimits(max_total_bytes=8 * _MIB, max_entry_bytes=4 * _MIB, max_entries=50, ratio_floor_bytes=_MIB)


class _Counting(io.BytesIO):
    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.consumed = 0

    def read(self, size: int | None = -1) -> bytes:
        chunk = super().read(size)
        self.consumed += len(chunk)
        return chunk


def _tar_bomb(size: int, compress: str) -> bytes:
    """A tar whose one member declares ``size`` bytes of zeros, compressed as ``compress``."""
    header = tarfile.TarInfo("SKILL.md")
    header.size = size
    raw = io.BytesIO()
    raw.write(header.tobuf(format=tarfile.GNU_FORMAT))
    compressor = {
        "gz": lambda: zlib.compressobj(1, zlib.DEFLATED, 31),
        "bz2": lambda: bz2.BZ2Compressor(1),
        "xz": lambda: lzma.LZMACompressor(preset=0),
    }[compress]()
    out = io.BytesIO()
    out.write(compressor.compress(raw.getvalue()))
    zeros = bytes(_MIB)
    for _ in range(size // _MIB):
        out.write(compressor.compress(zeros))
    out.write(compressor.flush())
    return out.getvalue()


def _tar(members: list[tarfile.TarInfo], payloads: dict[str, bytes] | None = None, mode: str = "w") -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode=mode, format=tarfile.PAX_FORMAT) as archive:
        for member in members:
            data = (payloads or {}).get(member.name)
            archive.addfile(member, io.BytesIO(data) if data is not None else None)
    return out.getvalue()


def _file(name: str, data: bytes) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    return info, data


def _zip(entries: dict[str, bytes], *, compression: int = zipfile.ZIP_DEFLATED) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out.getvalue()


@pytest.mark.parametrize("compress", ["gz", "bz2", "xz"])
def test_a_compressed_tar_bomb_is_refused_from_its_header(compress: str) -> None:
    data = _tar_bomb(64 * _MIB, compress)
    source = _Counting(data)
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(source, LIMITS)
    assert refused.value.reason_code == ENTRY_TOO_LARGE
    # The first header is in the first compressed block; the rest of the stream is never inflated.
    assert source.consumed < max(256 * 1024, len(data) // 4)


def test_many_members_each_under_the_cap_stop_at_the_total() -> None:
    members = [_file(f"f{i}", bytes(3 * _MIB)) for i in range(5)]
    data = _tar([m for m, _ in members], {m.name: d for m, d in members})
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(data, LIMITS)
    assert refused.value.reason_code == EXPANDED_TOO_LARGE


def test_too_many_tar_members_are_refused() -> None:
    members = [_file(f"f{i}", b"x") for i in range(LIMITS.max_entries + 1)]
    data = _tar([m for m, _ in members], {m.name: d for m, d in members})
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(data, LIMITS)
    assert refused.value.reason_code == TOO_MANY_MEMBERS


def test_a_pax_header_bomb_is_refused_before_it_is_read() -> None:
    header = tarfile.TarInfo("././@PaxHeader")
    header.type = tarfile.XHDTYPE
    header.size = 512 * _MIB
    data = header.tobuf(format=tarfile.USTAR_FORMAT) + bytes(8 * _MIB)
    source = _Counting(data)
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(source, LIMITS)
    assert refused.value.reason_code == ENTRY_TOO_LARGE
    # tarfile would read the declared pax data whole into memory; it is refused from the header block.
    assert source.consumed < 64 * 1024


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        (tarfile.SYMTYPE, LINK_ENTRY),
        (tarfile.LNKTYPE, LINK_ENTRY),
        (tarfile.CHRTYPE, SPECIAL_ENTRY),
        (tarfile.BLKTYPE, SPECIAL_ENTRY),
        (tarfile.FIFOTYPE, SPECIAL_ENTRY),
    ],
)
def test_tar_links_and_devices_are_refused(kind: bytes, reason: str) -> None:
    member = tarfile.TarInfo("scripts/run.py")
    member.type = kind
    member.linkname = "/etc/passwd"
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(_tar([member]), LIMITS)
    assert refused.value.reason_code == reason


@pytest.mark.parametrize("name", ["../x", "a/../../x", "/abs", "a\\b", "./x", "a//b", "C:/x", "a/./b"])
def test_unsafe_names_are_refused_in_tar_and_zip(name: str) -> None:
    member, data = _file(name, b"x")
    with pytest.raises(ArchiveRefused) as tar_refused:
        read_archive(_tar([member], {name: data}), LIMITS)
    assert tar_refused.value.reason_code == UNSAFE_PATH
    with pytest.raises(ArchiveRefused) as zip_refused:
        read_archive(_zip({name: b"x"}), LIMITS)
    assert zip_refused.value.reason_code == UNSAFE_PATH


def test_safe_entry_path_keeps_a_directory_without_its_slash() -> None:
    assert safe_entry_path("pkg/scripts/") == "pkg/scripts"


def test_a_nul_in_a_name_is_refused() -> None:
    # tarfile and zipfile both cut a name at NUL when they write one, so the check is on the path itself.
    with pytest.raises(ArchiveRefused) as refused:
        safe_entry_path("a\x00b")
    assert refused.value.reason_code == UNSAFE_PATH


def test_a_zip_symlink_is_refused() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        info = zipfile.ZipInfo("link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "/etc/passwd")
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(out.getvalue(), LIMITS)
    assert refused.value.reason_code == LINK_ENTRY


def test_a_high_ratio_zip_is_refused_before_inflating(monkeypatch: pytest.MonkeyPatch) -> None:
    data = _zip({"zeros.bin": bytes(3 * _MIB)})
    inflated = 0
    real_read = zipfile.ZipExtFile.read

    def counting_read(self: zipfile.ZipExtFile, n: int = -1) -> bytes:
        nonlocal inflated
        chunk = real_read(self, n)
        inflated += len(chunk)
        return chunk

    monkeypatch.setattr(zipfile.ZipExtFile, "read", counting_read)
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(data, dataclasses.replace(LIMITS, max_ratio=10.0))
    assert refused.value.reason_code == RATIO_TOO_HIGH
    assert inflated == 0


def test_a_zip_member_that_lies_about_its_size_is_refused() -> None:
    data = bytearray(_zip({"bomb.bin": bytes(2 * _MIB)}))
    central = data.rfind(b"PK\x01\x02")
    data[central + 24 : central + 28] = (16).to_bytes(4, "little")
    with pytest.raises(ArchiveRefused) as refused:
        check_zip(bytes(data), LIMITS)
    assert refused.value.reason_code == CORRUPT


def test_a_zip_declaring_more_entries_than_the_limit_is_refused_before_parsing() -> None:
    data = _zip({f"f{i}": b"" for i in range(LIMITS.max_entries + 1)}, compression=zipfile.ZIP_STORED)
    assert zip_declared_entries(data) == LIMITS.max_entries + 1
    with pytest.raises(ArchiveRefused) as refused:
        check_zip(data, LIMITS)
    assert refused.value.reason_code == TOO_MANY_MEMBERS


def test_an_encrypted_zip_member_is_refused() -> None:
    data = bytearray(_zip({"a.txt": b"hello"}))
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = data.find(signature)
        data[at + offset] |= 0x1
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(bytes(data), LIMITS)
    assert refused.value.reason_code == ENCRYPTED


def test_a_damaged_zip_member_fails_its_crc() -> None:
    data = bytearray(_zip({"a.txt": b"hello world" * 10}, compression=zipfile.ZIP_STORED))
    at = data.find(b"hello world")
    data[at] ^= 0xFF
    with pytest.raises(ArchiveRefused) as refused:
        check_zip(bytes(data), LIMITS)
    assert refused.value.reason_code == CORRUPT


def test_a_zip_inside_a_zip_counts_only_as_its_bytes() -> None:
    inner = _zip({"zeros.bin": bytes(64 * _MIB)})
    outer = _zip({"inner.zip": inner}, compression=zipfile.ZIP_STORED)
    files = read_archive(outer, LIMITS)
    assert files == {"inner.zip": inner}


def test_overlapping_zip_members_are_refused() -> None:
    data = bytearray(_zip({"a": b"x" * 100, "b": b"y" * 100}, compression=zipfile.ZIP_STORED))
    first = data.find(b"PK\x01\x02")
    second = data.find(b"PK\x01\x02", first + 1)
    data[second + 42 : second + 46] = (0).to_bytes(4, "little")
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(bytes(data), LIMITS)
    assert refused.value.reason_code == CORRUPT


def test_not_an_archive_is_refused() -> None:
    assert archive_kind(b"plain text, not an archive" * 40) is None
    with pytest.raises(ArchiveRefused) as refused:
        read_archive(b"plain text" * 100, LIMITS)
    assert refused.value.reason_code == NOT_AN_ARCHIVE


@pytest.mark.parametrize("mode", ["w", "w:gz", "w:bz2", "w:xz"])
def test_a_normal_tar_round_trips(mode: str, tmp_path: Path) -> None:
    members = [_file("pkg/SKILL.md", b"# skill"), _file("pkg/scripts/run.py", b"print(1)\n" * 100)]
    folder = tarfile.TarInfo("pkg/scripts")
    folder.type = tarfile.DIRTYPE
    data = _tar([folder, *(m for m, _ in members)], {m.name: d for m, d in members}, mode=mode)
    assert archive_kind(data) == "tar"
    assert read_archive(data, LIMITS) == {"pkg/SKILL.md": b"# skill", "pkg/scripts/run.py": b"print(1)\n" * 100}
    assert sorted(extract_archive(data, LIMITS, tmp_path)) == ["pkg/SKILL.md", "pkg/scripts/run.py"]
    assert (tmp_path / "pkg" / "scripts" / "run.py").read_bytes() == b"print(1)\n" * 100


def test_a_normal_zip_round_trips() -> None:
    entries = {"word/document.xml": b"<w:document/>", "docProps/app.xml": b"<x/>" * 1000}
    data = _zip(entries)
    assert archive_kind(data) == "zip"
    assert read_archive(data, LIMITS) == entries
    assert set(check_zip(data, LIMITS)) == set(entries)


def test_an_unread_zip_member_is_still_checked() -> None:
    data = bytearray(_zip({"a.txt": b"hello world" * 10, "b.txt": b"ok"}, compression=zipfile.ZIP_STORED))
    at = data.find(b"hello world")
    data[at] ^= 0xFF
    with pytest.raises(ArchiveRefused) as refused:
        for _entry, _stream in iter_archive(bytes(data), LIMITS):
            pass
    assert refused.value.reason_code == CORRUPT


def test_spooled_chunks_stop_at_the_cap() -> None:
    out = io.BytesIO()
    with pytest.raises(ArchiveRefused) as refused:
        spool_chunks(iter([b"x" * 600, b"y" * 600]), out, max_bytes=1000)
    assert refused.value.reason_code == EXPANDED_TOO_LARGE
    assert spool_chunks(iter([b"ab", b"cd"]), io.BytesIO(), max_bytes=4) == 4
