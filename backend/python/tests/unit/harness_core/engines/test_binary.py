"""EH-1a..EH-1f (EH1, ENG-31): the one check every engine adapter runs on its binary before anything runs it."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.binary import check_reported_version, verify_binary
from harness_core.engines.errors import EngineBinaryMismatch, EngineCrashed
from harness_core.engines.types import EngineVersion
from harness_core.logs import HarnessLog
from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker

if TYPE_CHECKING:
    from harness_core.testing.sandbox.fake import (
        FakeProcessContext,
        FakeProgram,
        FakeSandboxHandle,
    )

PINNED = EngineVersion("codex", "0.160.0", "sha256:" + "a" * 64)
OTHER = "sha256:" + "b" * 64
PATH = "/usr/local/bin/codex-app-server"
_LOG = HarnessLog(logging.getLogger("tests.eh1"))


async def _sandbox(sha256sum: FakeProgram) -> FakeSandboxHandle:
    broker = FakeSandboxBroker(clock=ManualClock(), modes=frozenset(RuntimeMode), programs=dict(FAKE_PROGRAMS))
    spec = SandboxSpec(image_digest="engine@sha256:" + "0" * 64, mode=RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/w"),))
    handle = await broker.acquire(spec, lease=LeaseRef(holder="eh1", fence=1), idempotency_key="eh1")
    handle.install("sha256sum", sha256sum)
    return handle


def _printing(stdout: str, code: int = 0) -> FakeProgram:
    async def program(context: FakeProcessContext) -> int:
        await context.emit(stdout.encode())
        return code

    return program


def _mismatches(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        r.harness  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) == "engine.binary.mismatch"
    ]


async def test_eh1a_a_binary_that_hashes_to_its_pin_passes_after_one_hash() -> None:
    sandbox = await _sandbox(sha256sum_program({PATH: PINNED.binary_digest}.get))
    await verify_binary(sandbox, PATH, PINNED, _LOG)
    assert [list(e.argv) for e in sandbox.execs] == [["sha256sum", PATH]]


async def test_eh1b_another_digest_is_refused_and_names_the_engine_for_the_admin(caplog: pytest.LogCaptureFixture) -> None:
    sandbox = await _sandbox(sha256sum_program({PATH: OTHER}.get))
    with caplog.at_level(logging.WARNING), pytest.raises(EngineBinaryMismatch) as refused:
        await verify_binary(sandbox, PATH, PINNED, _LOG)
    assert refused.value.reason_code == "binary_mismatch"
    message = str(refused.value)
    for named in ("codex", "0.160.0", PATH, OTHER, PINNED.binary_digest):
        assert named in message
    [fields] = _mismatches(caplog)
    assert fields["reasonCode"] == "binary_mismatch"
    assert (fields["engineKind"], fields["pinnedVersion"]) == ("codex", "0.160.0")
    assert (fields["foundDigest"], fields["pinnedDigest"]) == (OTHER, PINNED.binary_digest)


async def test_eh1c_a_missing_binary_is_a_mismatch_with_nothing_found(caplog: pytest.LogCaptureFixture) -> None:
    sandbox = await _sandbox(sha256sum_program(lambda _path: None))
    with caplog.at_level(logging.WARNING), pytest.raises(EngineBinaryMismatch) as refused:
        await verify_binary(sandbox, PATH, PINNED, _LOG)
    assert refused.value.reason_code == "binary_mismatch"
    [fields] = _mismatches(caplog)
    assert fields["foundDigest"] == "none"


@pytest.mark.parametrize(
    ("stdout", "code"),
    [
        (f"{'A' * 64}  {PATH}\n", 0),
        (f"{'a' * 63}  {PATH}\n", 0),
        (f"SHA256 ({PATH}) = {'a' * 64}\n", 0),
        ("", 0),
        (f"{'a' * 64}  {PATH}\n", 1),
    ],
    ids=["upper-case", "short", "bsd-format", "empty", "non-zero-exit"],
)
async def test_eh1d_only_an_exact_successful_match_passes(stdout: str, code: int) -> None:
    sandbox = await _sandbox(_printing(stdout, code))
    with pytest.raises(EngineBinaryMismatch):
        await verify_binary(sandbox, PATH, PINNED, _LOG)


@pytest.mark.parametrize("code", [137, -9])
async def test_eh1e_a_hash_killed_by_a_signal_is_a_lost_turn_not_a_mismatch(code: int) -> None:
    sandbox = await _sandbox(_printing("", code))
    with pytest.raises(EngineCrashed) as gone:
        await verify_binary(sandbox, PATH, PINNED, _LOG)
    assert gone.value.reason_code == "engine_crashed"


def test_eh1f_the_pinned_version_passes() -> None:
    check_reported_version(PINNED, "0.160.0", _LOG)


@pytest.mark.parametrize("reported", ["0.161.0", ""])
def test_eh1f_another_version_is_refused_and_named(reported: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING), pytest.raises(EngineBinaryMismatch) as refused:
        check_reported_version(PINNED, reported, _LOG)
    assert refused.value.reason_code == "binary_mismatch"
    assert "codex" in str(refused.value) and "0.160.0" in str(refused.value)
    [fields] = _mismatches(caplog)
    assert (fields["engineKind"], fields["pinnedVersion"], fields["reportedVersion"]) == ("codex", "0.160.0", reported or "none")
