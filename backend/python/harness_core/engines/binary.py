"""The engine binary a session runs is the allow-listed one, byte for byte (ARCHITECTURE §4.6.4).

An image is pinned by tag, so a tag can be pointed at other bytes by hand. Every adapter therefore hashes the
binary inside the sandbox before anything runs it, and launches that same absolute path: a bare name would let
an image put another program earlier on ``PATH``. Only an exact, successful ``sha256sum`` line equal to the pin
passes; a missing or unreadable binary is a mismatch, and a hash killed by a signal is a dead sandbox (a lost
turn), not a verdict on the bytes.

That hash costs a session start 0.5-0.9 s (EH2). An image id names its bytes, so a broker may instead read the
binary from the image once per (image id, path) (``ImageBinaryVerifier``) and vouch for it in every sandbox whose
spec leaves nothing able to change that file (``vouched_binaries``); an adapter then trusts the voucher
(``SandboxHandle.verified_binaries``) and runs nothing.
"""

from __future__ import annotations

import asyncio
import posixpath
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from harness_core.engines.errors import (
    EngineBinaryMismatch,
    EngineCrashed,
    killed_by_signal,
)
from harness_core.sandbox.errors import ImageNotAllowed
from harness_core.sandbox.streams import collect

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from harness_core.engines.types import EngineVersion
    from harness_core.logs import Field, HarnessLog
    from harness_core.sandbox import SandboxHandle
    from harness_core.sandbox.types import SandboxSpec

CHECK_TIMEOUT_S = 60.0
_OUTPUT_LIMIT = 1 << 16
_SHA256SUM_LINE = re.compile(r"^(?P<hex>[0-9a-f]{64})\s")
_NONE = "none"


async def run_check(
    sandbox: SandboxHandle, argv: Sequence[str], env: Mapping[str, str] | None = None
) -> tuple[int, str]:
    """A short command the adapter runs before the engine starts: its exit code and its stdout."""
    process = await sandbox.exec(list(argv), env=dict(env or {}))
    out, _ = await asyncio.gather(
        collect(process.stdout, limit=_OUTPUT_LIMIT), collect(process.stderr, limit=_OUTPUT_LIMIT)
    )
    return await process.wait(timeout_s=CHECK_TIMEOUT_S), out.decode("utf-8", "replace")


async def verify_binary(sandbox: SandboxHandle, path: str, engine: EngineVersion, log: HarnessLog) -> None:
    """Refuses (``binary_mismatch``) unless ``path`` hashes to the engine's pinned digest.

    A digest the broker vouches for is the hash of these very bytes (EH2), so nothing runs for it."""
    vouched = sandbox.verified_binaries.get(path)
    if vouched is not None:
        _judge(vouched, path, engine, log)
        return
    code, printed = await run_check(sandbox, ["sha256sum", path])
    if killed_by_signal(code):
        raise EngineCrashed(f"sha256sum {path} exited with {code}")
    _judge((parse_sha256sum(printed) if code == 0 else None) or "", path, engine, log)


def parse_sha256sum(printed: str) -> str | None:
    """``sha256:<hex>`` from the first line of coreutils' or busybox's ``sha256sum`` output; ``None`` otherwise."""
    found = _SHA256SUM_LINE.match(printed)
    return f"sha256:{found.group('hex')}" if found is not None else None


def _judge(digest: str, path: str, engine: EngineVersion, log: HarnessLog, **fields: Field) -> None:
    if digest == engine.binary_digest:
        return
    log.warning(
        "engine.binary.mismatch",
        **{
            **fields,
            "reasonCode": EngineBinaryMismatch.reason_code,
            "engineKind": engine.kind,
            "pinnedVersion": engine.version,
            "foundDigest": digest or _NONE,
            "pinnedDigest": engine.binary_digest,
        },
    )
    raise EngineBinaryMismatch(
        f"{engine.kind} {engine.version}: {path} hashes to {digest or _NONE}; the allowlist pins {engine.binary_digest}"
    )


def check_reported_version(
    engine: EngineVersion, reported: str, log: HarnessLog, *, expected: str | None = None
) -> None:
    """Refuses (``binary_mismatch``) a binary that reports another version than its pin; ``expected`` where the
    binary's pinned release reports a fixed version of its own (an ACP profile's ``agent.reportedVersion``)."""
    if reported == (expected or engine.version):
        return
    log.warning(
        "engine.binary.mismatch",
        reasonCode=EngineBinaryMismatch.reason_code,
        engineKind=engine.kind,
        pinnedVersion=expected or engine.version,
        reportedVersion=reported or _NONE,
    )
    raise EngineBinaryMismatch(f"{engine.kind} reports version {reported or _NONE}; the allowlist pins {engine.version}")


@dataclass(frozen=True, slots=True)
class BinaryObservation:
    """What an image holds at ``path``: the file it resolves to, and that file's ``sha256:<hex>``, or ``None`` when
    there is no regular file there that the sandbox user can read."""

    path: str
    resolved: str
    digest: str | None


class ImageBinaryReader(Protocol):
    async def observe(self, image_id: str, path: str) -> BinaryObservation:
        """Reads the image's own bytes, never a running sandbox's.

        Raises ``BrokerUnavailable`` when it could not look: that says nothing about the image."""
        ...


@dataclass(frozen=True, slots=True)
class PinnedBinary:
    """Where an image of ``engine`` holds its binary; the allowlist pins the digest."""

    engine: EngineVersion
    path: str


class ImageBinaryVerifier:
    """Checks the pinned binaries of an image once per image id and remembers what it read.

    The cache holds observations of content-addressed bytes, keyed by image id and never by tag: a tag can be
    moved to other bytes, an id cannot. Every sight compares again, so a mismatch is refused every time.
    Failed reads are not kept. ``pins`` is keyed by image reference as ``normalize`` spells it.
    """

    def __init__(
        self,
        pins: Mapping[str, Iterable[PinnedBinary]],
        reader: ImageBinaryReader,
        *,
        log: HarnessLog,
        normalize: Callable[[str], str] = str,
    ) -> None:
        self._normalize = normalize
        self._pins = {normalize(image): tuple(pinned) for image, pinned in pins.items()}
        self._reader = reader
        self._log = log
        self._observed: dict[tuple[str, str], asyncio.Future[BinaryObservation]] = {}

    def pinned(self, image: str) -> tuple[PinnedBinary, ...]:
        return self._pins.get(self._normalize(image), ())

    async def verify(self, image: str, image_id: str, **fields: Field) -> tuple[BinaryObservation, ...]:
        """The pinned binaries of ``image`` as image ``image_id`` holds them; ``ImageNotAllowed``
        (``binary_mismatch``) when one does not hash to its pin."""
        observations: list[BinaryObservation] = []
        for pinned in self.pinned(image):
            seen = await self._observe(image_id, pinned.path)
            try:
                _judge(
                    seen.digest or "", pinned.path, pinned.engine, self._log,
                    **{**fields, "imageId": image_id, "binaryPath": pinned.path},
                )
            except EngineBinaryMismatch as mismatch:
                raise ImageNotAllowed(f"image {image}: {mismatch}", reason=EngineBinaryMismatch.reason_code) from None
            observations.append(seen)
        return tuple(observations)

    async def _observe(self, image_id: str, path: str) -> BinaryObservation:
        key = (image_id, path)
        observed = self._observed.get(key)
        if observed is None:
            observed = asyncio.ensure_future(self._read(image_id, path))
            self._observed[key] = observed
            observed.add_done_callback(lambda done: self._forget_failed(key, done))
        # One read per key however many sandboxes wait for it; a waiter that is cancelled leaves it running.
        return await asyncio.shield(observed)

    async def _read(self, image_id: str, path: str) -> BinaryObservation:
        seen = await self._reader.observe(image_id, path)
        self._log.info("broker.binary.observed", imageId=image_id, binaryPath=path, foundDigest=seen.digest or _NONE)
        return seen

    def _forget_failed(self, key: tuple[str, str], done: asyncio.Future[BinaryObservation]) -> None:
        if (done.cancelled() or done.exception() is not None) and self._observed.get(key) is done:
            del self._observed[key]


def _covers(root: str, path: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def vouched_binaries(
    observations: Iterable[BinaryObservation], spec: SandboxSpec, *, writable: Iterable[str] = ()
) -> dict[str, str]:
    """The binaries a sandbox of ``spec`` cannot change, by path: the root is read-only, so a file the image holds
    is the image's unless a mount of the spec, or a directory the broker itself makes writable (``writable``),
    covers the path or the file it resolves to."""
    roots = tuple(writable)

    def fixed(path: str) -> bool:
        path = posixpath.normpath(path)
        return spec.mount_for(path) is None and not any(_covers(root, path) for root in roots)

    return {
        seen.path: seen.digest
        for seen in observations
        if seen.digest is not None and fixed(seen.path) and fixed(seen.resolved)
    }


__all__ = [
    "CHECK_TIMEOUT_S",
    "BinaryObservation",
    "ImageBinaryReader",
    "ImageBinaryVerifier",
    "PinnedBinary",
    "check_reported_version",
    "parse_sha256sum",
    "run_check",
    "verify_binary",
    "vouched_binaries",
]
