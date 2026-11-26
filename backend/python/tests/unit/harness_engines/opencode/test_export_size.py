"""EV2-23 (found by the wave 2 crash slice): a long session's engine state is captured whole.

The engine image's runtime (Bun) cuts what a process writes to a pipe at 64 KiB when it exits, and
``opencode export`` exits right after writing. A session whose export passed 64 KiB (a few turns with large
tool output) was captured truncated, the checkpoint failed to parse, every later attempt failed the same way
and the run ended ``failed`` (``crash_loop``). The stub below cuts a piped ``opencode export`` exactly so.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.logs import HarnessLog
from harness_engines.opencode.session import OpencodeSession
from harness_engines.release import release_allowlist

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence

PIPE_CUT = 64 * 1024
SESSION = "ses_big"


class _Process:
    def __init__(self, out: bytes, code: int = 0) -> None:
        self._out = out
        self._code = code

    @property
    def stdout(self) -> AsyncIterator[bytes]:
        return self._chunks(self._out)

    @property
    def stderr(self) -> AsyncIterator[bytes]:
        return self._chunks(b"")

    @staticmethod
    async def _chunks(data: bytes) -> AsyncIterator[bytes]:
        if data:
            yield data

    async def wait(self, timeout_s: float | None = None) -> int:
        return self._code

    async def signal(self, sig: int) -> None:
        return None


class _Sandbox:
    """Runs ``opencode export`` as the image does: to a pipe, cut at 64 KiB; to a file, whole."""

    id = "sbx_stub"

    def __init__(self, export: bytes) -> None:
        self._export = export
        self.files: dict[str, bytes] = {}
        self.argv: list[Sequence[str]] = []

    async def exec(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None, **_: object) -> _Process:
        self.argv.append(list(argv))
        if list(argv[:2]) == ["opencode", "export"]:
            return _Process(self._export[:PIPE_CUT])
        if argv[0] in ("sh", "/bin/sh") and "opencode export" in argv[2]:
            self.files[str(argv[-1])] = self._export
            return _Process(b"")
        if argv[0] == "cat":
            return _Process(self.files.get(str(argv[1]), b""), 0 if str(argv[1]) in self.files else 1)
        return _Process(b"", 127)


def _export(size: int) -> bytes:
    filler = "x" * size
    return json.dumps({"info": {"id": SESSION}, "messages": [{"info": {}, "parts": [{"type": "text", "text": filler}]}]}).encode()


def _session(sandbox: _Sandbox) -> OpencodeSession:
    engine = next(r.version for r in release_allowlist() if r.version.kind == "opencode")
    return OpencodeSession(
        engine=engine,
        sandbox=sandbox,  # type: ignore[arg-type]
        process=_Process(b""),  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        session_id=SESSION,
        env={},
        secrets=frozenset(),
        log=HarnessLog.named("tests.ev2"),
    )


async def test_ev2_23_an_export_larger_than_a_pipe_is_captured_whole() -> None:
    sandbox = _Sandbox(_export(200_000))
    state = await _session(sandbox).capture_state()
    envelope = json.loads(state.payload)
    assert len(envelope["export"]["messages"][0]["parts"][0]["text"]) == 200_000
    assert not any(list(a[:2]) == ["opencode", "export"] for a in sandbox.argv), "never read the export from a pipe"


async def test_ev2_23_a_small_export_still_round_trips() -> None:
    sandbox = _Sandbox(_export(10))
    state = await _session(sandbox).capture_state()
    assert json.loads(state.payload)["sessionId"] == SESSION


@pytest.mark.docker
def test_ev2_23_in_the_engine_image_a_file_carries_what_a_pipe_cuts() -> None:
    """The runtime behaviour the fix works around, on the release image (the opencode binary is Bun)."""
    from tests.unit.harness.engines.real_stack import IMAGE, OWNER, lane_available

    available, reason = lane_available()
    if not available:
        pytest.skip(reason)
    import docker

    write = "BUN_BE_BUN=1 opencode -e 'process.stdout.write(\"x\".repeat(200000)); process.exit(0)'"
    client = docker.from_env()
    try:
        out = client.containers.run(
            IMAGE,
            ["-c", f"{write} | wc -c; {write} > /tmp/o; wc -c < /tmp/o"],
            entrypoint="/bin/sh",
            name=f"pxh-ev2-export-{uuid.uuid4().hex[:8]}",
            labels={**OWNER, "pipeshub.agent": "EV2"},
            network_mode="none",
            remove=True,
        )
    finally:
        client.close()
    piped, filed = (int(n) for n in out.decode().split())
    assert filed == 200_000
    assert piped <= filed
