"""EH2-1..EH2-7 (SBX-33..SBX-35): the broker reads a pinned binary once per image id and vouches for it only
where a sandbox cannot change it. IBR-1..IBR-3 against the in-memory reader."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.binary import (
    BinaryObservation,
    ImageBinaryVerifier,
    PinnedBinary,
    vouched_binaries,
)
from harness_core.engines.types import EngineVersion
from harness_core.logs import HarnessLog
from harness_core.sandbox import MountSpec, RuntimeMode, SandboxSpec
from harness_core.sandbox.errors import BrokerUnavailable, ImageNotAllowed
from harness_core.testing.contracts.image_binaries import (
    CONTRACT_FILES,
    ImageBinaryReaderContract,
)
from harness_core.testing.engines.binary import (
    Directory,
    InMemoryImageBinaries,
    Link,
    sha256_digest,
)

if TYPE_CHECKING:
    from harness_core.testing.contracts.image_binaries import Subject

REAL = b"the allow-listed engine binary"
IMPOSTOR = b"another binary that reports the same version"
PATH = "/usr/local/bin/codex-app-server"
ENGINE = EngineVersion("codex", "0.160.0", sha256_digest(REAL))
TAG = "pxh-codex:0.160.0"
GOOD_ID = "sha256:" + "1" * 64
BAD_ID = "sha256:" + "2" * 64
_LOG = HarnessLog(logging.getLogger("tests.eh2"))


class TestInMemoryReader(ImageBinaryReaderContract):
    @pytest.fixture
    def subject(self) -> Subject:
        return InMemoryImageBinaries({GOOD_ID: CONTRACT_FILES}), GOOD_ID


def _reader() -> InMemoryImageBinaries:
    return InMemoryImageBinaries({GOOD_ID: {PATH: REAL}, BAD_ID: {PATH: IMPOSTOR}, "sha256:" + "3" * 64: {}})


def _verifier(reader: InMemoryImageBinaries) -> ImageBinaryVerifier:
    return ImageBinaryVerifier({TAG: (PinnedBinary(ENGINE, PATH),)}, reader, log=_LOG)


def _mismatches(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("engine.binary.mismatch")]


async def test_eh2_1_a_pinned_binary_is_read_once_per_image_id() -> None:
    reader = _reader()
    verifier = _verifier(reader)
    for _ in range(3):
        [seen] = await verifier.verify(TAG, GOOD_ID)
        assert seen == BinaryObservation(PATH, PATH, ENGINE.binary_digest)
    assert reader.reads == [(GOOD_ID, PATH)]


async def test_eh2_1_concurrent_first_sights_read_once() -> None:
    gate = asyncio.Event()
    reader = _reader()
    observe = reader.observe

    async def held(image_id: str, path: str) -> BinaryObservation:
        await gate.wait()
        return await observe(image_id, path)

    reader.observe = held  # type: ignore[method-assign]
    verifier = _verifier(reader)
    waiting = [asyncio.ensure_future(verifier.verify(TAG, GOOD_ID)) for _ in range(8)]
    # One yield runs every waiter up to the held read: all eight are in flight before any read ends.
    await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*waiting)
    assert all(r == results[0] for r in results)
    assert reader.reads == [(GOOD_ID, PATH)]


async def test_eh2_2_a_tag_moved_to_other_bytes_is_read_again_and_refused() -> None:
    reader = _reader()
    verifier = _verifier(reader)
    _ = await verifier.verify(TAG, GOOD_ID)
    with pytest.raises(ImageNotAllowed) as refused:
        _ = await verifier.verify(TAG, BAD_ID)
    assert refused.value.reason == "binary_mismatch"
    assert reader.reads == [(GOOD_ID, PATH), (BAD_ID, PATH)]
    _ = await verifier.verify(TAG, GOOD_ID)


async def test_eh2_3_a_mismatch_refuses_every_sandbox_and_is_never_cached_as_a_pass(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING, logger="tests.eh2")
    reader = _reader()
    verifier = _verifier(reader)
    for _ in range(2):
        with pytest.raises(ImageNotAllowed) as refused:
            _ = await verifier.verify(TAG, BAD_ID, runId="r-1")
        assert refused.value.reason == "binary_mismatch"
        assert ENGINE.binary_digest in str(refused.value)
        assert sha256_digest(IMPOSTOR) in str(refused.value)
    assert reader.reads == [(BAD_ID, PATH)]
    lines = _mismatches(caplog)
    assert len(lines) == 2
    for field in (
        "reasonCode=binary_mismatch",
        "engineKind=codex",
        "pinnedVersion=0.160.0",
        f"foundDigest={sha256_digest(IMPOSTOR)}",
        f"pinnedDigest={ENGINE.binary_digest}",
        f"imageId={BAD_ID}",
        f"binaryPath={PATH}",
        "runId=r-1",
    ):
        assert field in lines[0], field


@pytest.mark.parametrize("entry", [None, Directory(), Link("/nowhere")], ids=["missing", "directory", "dangling-link"])
async def test_eh2_4_no_regular_file_at_the_path_is_a_mismatch(
    entry: Directory | Link | None, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="tests.eh2")
    image = {} if entry is None else {PATH: entry}
    verifier = _verifier(InMemoryImageBinaries({GOOD_ID: image}))
    with pytest.raises(ImageNotAllowed) as refused:
        _ = await verifier.verify(TAG, GOOD_ID)
    assert refused.value.reason == "binary_mismatch"
    [line] = _mismatches(caplog)
    assert "foundDigest=none" in line


async def test_eh2_5_a_failed_read_is_not_cached() -> None:
    reader = _reader()
    reader.fail_next = 1
    verifier = _verifier(reader)
    with pytest.raises(BrokerUnavailable):
        _ = await verifier.verify(TAG, GOOD_ID)
    [seen] = await verifier.verify(TAG, GOOD_ID)
    assert seen.digest == ENGINE.binary_digest
    assert reader.reads == [(GOOD_ID, PATH), (GOOD_ID, PATH)]


async def test_eh2_6_an_image_without_pins_is_not_read() -> None:
    reader = _reader()
    verifier = _verifier(reader)
    assert verifier.pinned("pxh-sandbox:pr1") == ()
    assert await verifier.verify("pxh-sandbox:pr1", GOOD_ID) == ()
    assert reader.reads == []


async def test_eh2_6_pins_are_matched_as_the_broker_spells_images() -> None:
    verifier = ImageBinaryVerifier(
        {"docker.io/library/pxh-codex:0.160.0": (PinnedBinary(ENGINE, PATH),)},
        _reader(),
        log=_LOG,
        normalize=lambda image: image if "/" in image else f"docker.io/library/{image}",
    )
    assert verifier.pinned(TAG) == (PinnedBinary(ENGINE, PATH),)


def _spec(*mounts: MountSpec) -> SandboxSpec:
    return SandboxSpec(image_digest=TAG, mode=RuntimeMode.DOCKER_HARDENED, mounts=mounts)


_DIGEST = ENGINE.binary_digest


@pytest.mark.parametrize(
    ("mounts", "resolved", "vouched"),
    [
        ((MountSpec("/workspace"), MountSpec("/state")), PATH, True),
        ((MountSpec("/usr/local/bin"),), PATH, False),
        ((MountSpec("/usr"),), PATH, False),
        ((MountSpec("/usr/local/bin", read_only=True),), PATH, False),
        ((MountSpec("/usr/local", source="org/session"),), PATH, False),
        ((MountSpec("/usr/local/binaries"),), PATH, True),
        ((MountSpec("/opt"),), "/opt/codex/codex-app-server", False),
        ((), "/tmp/codex-app-server", False),
        ((), "/tmpfile/codex-app-server", True),
    ],
    ids=[
        "engine-spec",
        "mount-at-dir",
        "mount-above",
        "read-only-scratch",
        "persistent",
        "sibling-prefix",
        "link-into-mount",
        "link-into-tmp",
        "tmp-prefix-only",
    ],
)
def test_eh2_7_a_binary_a_sandbox_could_replace_is_not_vouched_for(
    mounts: tuple[MountSpec, ...], resolved: str, vouched: bool
) -> None:
    seen = (BinaryObservation(PATH, resolved, _DIGEST), BinaryObservation("/usr/local/bin/other", "/usr/local/bin/other", None))
    expected = {PATH: _DIGEST} if vouched else {}
    assert vouched_binaries(seen, _spec(*mounts), writable=("/tmp",)) == expected
