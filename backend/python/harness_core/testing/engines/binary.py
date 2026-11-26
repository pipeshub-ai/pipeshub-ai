"""Test doubles for binary checks: a fake ``sha256sum`` for the fake broker's sandboxes (engines' pins name real
digests, which no fake bytes hash to, so the fake prints the digest a test assigns to a path, as coreutils formats
it), and ``InMemoryImageBinaries``, the in-memory ``ImageBinaryReader`` (EH2)."""

from __future__ import annotations

import hashlib
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.binary import BinaryObservation
from harness_core.sandbox.errors import BrokerUnavailable

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from harness_core.testing.sandbox.fake import FakeProcessContext, FakeProgram

_MAX_LINKS = 40


@dataclass(frozen=True, slots=True)
class Link:
    target: str


@dataclass(frozen=True, slots=True)
class Directory:
    pass


Entry = bytes | Link | Directory


def sha256_digest(data: bytes) -> str:
    return f"sha256:{hashlib.sha256(data).hexdigest()}"


class InMemoryImageBinaries:
    """Images as maps of path -> bytes, ``Link`` or ``Directory``. ``reads`` records every observation asked for;
    ``fail_next`` makes that many reads fail as a daemon error would."""

    def __init__(self, images: Mapping[str, Mapping[str, Entry]] | None = None) -> None:
        self.images: dict[str, dict[str, Entry]] = {k: dict(v) for k, v in (images or {}).items()}
        self.reads: list[tuple[str, str]] = []
        self.fail_next = 0

    async def observe(self, image_id: str, path: str) -> BinaryObservation:
        self.reads.append((image_id, path))
        if self.fail_next > 0:
            self.fail_next -= 1
            raise BrokerUnavailable(f"could not read {path} from image {image_id}")
        files = self.images.get(image_id)
        if files is None:
            raise BrokerUnavailable(f"no image {image_id}")
        resolved = posixpath.normpath(path)
        for _ in range(_MAX_LINKS):
            entry = files.get(resolved)
            if not isinstance(entry, Link):
                break
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(resolved), entry.target))
        entry = files.get(resolved)
        return BinaryObservation(path, resolved, sha256_digest(entry) if isinstance(entry, bytes) else None)


def sha256sum_program(digest_of: Callable[[str], str | None]) -> FakeProgram:
    """``digest_of(path)``: the ``sha256:<hex>`` digest of the file there, or ``None`` when there is none."""

    async def sha256sum(context: FakeProcessContext) -> int:
        path = context.argv[1] if len(context.argv) > 1 else "-"
        digest = digest_of(path)
        if digest is None:
            await context.emit(f"sha256sum: {path}: No such file or directory\n".encode(), stderr=True)
            return 1
        await context.emit(f"{digest.removeprefix('sha256:')}  {path}\n".encode())
        return 0

    return sha256sum


__all__ = ["Directory", "Entry", "InMemoryImageBinaries", "Link", "sha256_digest", "sha256sum_program"]
