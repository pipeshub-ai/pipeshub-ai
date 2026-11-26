"""EH-2..EH-5 (EH1, ENG-32, ENG-33): every engine adapter hashes its binary before anything runs it, and runs
the file it hashed. EH2-15..EH2-17 (EH2, ENG-37): a binary the broker vouches for is not hashed again."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.errors import EngineBinaryMismatch
from harness_core.engines.events import turn_id_for
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineTask,
    GatewayEndpoints,
)
from harness_core.sandbox import LeaseRef, MountSpec, RuntimeMode, SandboxSpec
from harness_core.testing.clock import ManualClock
from harness_core.testing.engines.binary import sha256sum_program
from harness_core.testing.engines.contract import collect, open_rig
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS, FakeSandboxBroker
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.codex.adapter import CodexAdapter
from harness_engines.opencode.adapter import OpencodeAdapter
from harness_engines.opencode.launch import BINARY_PATH as OPENCODE_BINARY
from harness_engines.release import release_allowlist
from tests.unit.harness_engines.opencode.conftest import OpencodeHarness

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.engines.ports import EngineAdapter
    from harness_core.engines.types import EngineLaunch, EngineVersion
    from harness_core.testing.sandbox.fake import (
        FakeProcessContext,
        FakeProgram,
        FakeSandboxHandle,
    )

REPO = Path(__file__).resolve().parents[5]
RETAGGED = "sha256:" + "e" * 64


@dataclass(frozen=True)
class Engine:
    kind: str
    adapter: Callable[[], EngineAdapter]
    api: ModelApi
    image_dir: str

    @property
    def pin(self) -> EngineVersion:
        return next(r.version for r in release_allowlist() if r.version.kind == self.kind)

    def launch(self) -> EngineLaunch:
        endpoints = GatewayEndpoints("http://model-gateway:8097/v1", self.api, "http://tool-gateway:8097/mcp", "p" * 16)
        profile = EngineProfile(engine=self.pin, mode=EngineMode.BUILD, access=AccessRouteRef("route-1", "d1"))
        return self.adapter().render_launch(profile, endpoints, "/skills")


ENGINES = [
    Engine("opencode", OpencodeAdapter, ModelApi.CHAT_COMPLETIONS, "opencode"),
    Engine("codex", CodexAdapter, ModelApi.RESPONSES, "codex"),
    Engine("claude_code", ClaudeCodeAdapter, ModelApi.ANTHROPIC_MESSAGES, "claude-code"),
]
BY_KIND = {e.kind: e for e in ENGINES}


IMAGE = "engine@sha256:" + "0" * 64


async def _sandbox(vouched: dict[str, str] | None = None) -> FakeSandboxHandle:
    broker = FakeSandboxBroker(
        clock=ManualClock(),
        modes=frozenset(RuntimeMode),
        programs=dict(FAKE_PROGRAMS),
        verified_binaries={IMAGE: vouched or {}},
    )
    spec = SandboxSpec(
        image_digest=IMAGE,
        mode=RuntimeMode.DOCKER_HARDENED,
        mounts=(MountSpec("/workspace"), MountSpec("/state"), MountSpec("/skills"), MountSpec("/engine")),
    )
    return await broker.acquire(spec, lease=LeaseRef(holder="eh1", fence=1), idempotency_key="eh1")


async def _refused_open(engine: Engine) -> FakeSandboxHandle:
    """Opens a session on a sandbox whose engine binary hashes to another digest than the pin (a retagged image)."""
    sandbox = await _sandbox()
    sandbox.install("sha256sum", sha256sum_program(lambda _path: RETAGGED))
    with pytest.raises(EngineBinaryMismatch) as refused:
        await engine.adapter().open(sandbox, engine.launch(), None)
    assert refused.value.reason_code == "binary_mismatch"
    return sandbox


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.kind)
async def test_eh2_a_retagged_binary_is_refused_before_anything_runs(engine: Engine) -> None:
    sandbox = await _refused_open(engine)
    assert [e.argv[0] for e in sandbox.execs] == ["sha256sum"], "something ran besides the hash"


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.kind)
async def test_eh3_the_session_runs_the_file_that_was_hashed(engine: Engine) -> None:
    [hashed] = [e.argv[1] for e in (await _refused_open(engine)).execs]
    assert engine.launch().argv[0] == hashed


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.kind)
async def test_eh4_the_image_puts_the_binary_where_the_adapter_hashes_it(engine: Engine) -> None:
    [hashed] = [e.argv[1] for e in (await _refused_open(engine)).execs]
    dockerfile = (REPO / "deployment" / "sandbox-images" / engine.image_dir / "Dockerfile").read_text()
    assert re.findall(r"^COPY \S+ engine-binary (\S+)$", dockerfile, re.MULTILINE) == [hashed]


async def test_eh3_opencode_restores_and_exports_with_the_hashed_binary() -> None:
    harness = OpencodeHarness()
    first = await open_rig(harness)
    try:
        await collect(first.session, EngineTask(turn_id_for("run-eh1", 0), "say alpha"))
        state = await first.session.capture_state()
        [export] = [e.argv for e in first.sandbox.execs if e.argv[0] == "sh"]
    finally:
        await first.session.close()
    hashed = first.launch.argv[0]
    assert re.search(rf"\bexec {re.escape(hashed)} export\b", export[2])
    second = await open_rig(harness, restore=state)
    await second.session.close()
    runs = [e.argv for e in second.sandbox.execs]
    assert runs[0] == ("sha256sum", hashed)
    assert [argv[0] for argv in runs if "import" in argv] == [hashed]


async def test_eh5_codex_with_the_pinned_bytes_but_another_version_is_refused() -> None:
    engine = BY_KIND["codex"]
    launch = engine.launch()

    async def app_server(context: FakeProcessContext) -> int:
        request = json.loads((await context.read_stdin()).split(b"\n")[0])
        answer = {"id": request["id"], "result": {"userAgent": "pipeshub/0.161.0 (Alpine)"}}
        await context.emit(json.dumps(answer).encode() + b"\n")
        await context.read_stdin()
        return 0

    sandbox = await _sandbox()
    sandbox.install("sha256sum", sha256sum_program(lambda _path: engine.pin.binary_digest))
    sandbox.install(launch.argv[0], app_server)
    with pytest.raises(EngineBinaryMismatch) as refused:
        await engine.adapter().open(sandbox, launch, None)
    assert "0.161.0" in str(refused.value)


def _reporting(engine: Engine, version: str) -> FakeProgram:
    """The engine binary as far as its version check reaches: it reports ``version``."""

    async def codex(context: FakeProcessContext) -> int:
        request = json.loads((await context.read_stdin()).split(b"\n")[0])
        answer = {"id": request["id"], "result": {"userAgent": f"pipeshub/{version} (Alpine)"}}
        await context.emit(json.dumps(answer).encode() + b"\n")
        await context.read_stdin()
        return 0

    async def claude(context: FakeProcessContext) -> int:
        await context.emit(f"{version} (Claude Code)\n".encode())
        return 0

    return {"codex": codex, "claude_code": claude}[engine.kind]


def _hashes(sandbox: FakeSandboxHandle) -> list[tuple[str, ...]]:
    return [e.argv for e in sandbox.execs if e.argv[0] == "sha256sum"]


@pytest.mark.parametrize("kind", ["codex", "claude_code"])
async def test_eh2_15_a_vouched_binary_is_not_hashed_and_its_version_is_still_checked(kind: str) -> None:
    engine = BY_KIND[kind]
    launch = engine.launch()
    sandbox = await _sandbox({launch.argv[0]: engine.pin.binary_digest})
    sandbox.install("sha256sum", sha256sum_program(lambda _path: RETAGGED))
    sandbox.install(launch.argv[0], _reporting(engine, "9.9.9"))
    with pytest.raises(EngineBinaryMismatch) as refused:
        await engine.adapter().open(sandbox, launch, None)
    assert "9.9.9" in str(refused.value), "the version check did not run"
    assert _hashes(sandbox) == []


async def test_eh2_15_opencode_opens_on_a_vouched_binary_without_hashing() -> None:
    harness = OpencodeHarness()
    harness.vouched = {OPENCODE_BINARY: harness.version.binary_digest}
    rig = await open_rig(harness)
    try:
        assert _hashes(rig.sandbox) == []
    finally:
        await rig.session.close()


async def test_eh2_15_opencode_on_a_vouched_binary_still_checks_its_version() -> None:
    harness = OpencodeHarness(binary_version="9.9.9")
    harness.vouched = {OPENCODE_BINARY: harness.version.binary_digest}
    with pytest.raises(EngineBinaryMismatch) as refused:
        await open_rig(harness)
    assert "9.9.9" in str(refused.value)


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.kind)
async def test_eh2_16_a_vouched_other_digest_is_refused_without_running_anything(
    engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    launch = engine.launch()
    sandbox = await _sandbox({launch.argv[0]: RETAGGED})
    with pytest.raises(EngineBinaryMismatch) as refused:
        await engine.adapter().open(sandbox, launch, None)
    assert refused.value.reason_code == "binary_mismatch"
    assert sandbox.execs == []
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("engine.binary.mismatch")]
    assert f"foundDigest={RETAGGED}" in line
    assert f"engineKind={engine.kind}" in line


@pytest.mark.parametrize("engine", ENGINES, ids=lambda e: e.kind)
async def test_eh2_17_a_binary_not_vouched_for_is_hashed_in_the_sandbox(engine: Engine) -> None:
    launch = engine.launch()
    sandbox = await _sandbox({"/usr/local/bin/something-else": engine.pin.binary_digest})
    sandbox.install("sha256sum", sha256sum_program(lambda _path: RETAGGED))
    with pytest.raises(EngineBinaryMismatch):
        await engine.adapter().open(sandbox, launch, None)
    assert _hashes(sandbox) == [("sha256sum", launch.argv[0])]
