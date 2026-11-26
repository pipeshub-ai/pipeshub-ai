"""R14-TS-D1 (round 14, Docker): a turn's inputs are moved in as the sandbox user while a process left running in
the sandbox keeps planting links where they go."""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING

import pytest

from harness_core.testing.contracts.workspace_sync import (
    CountingObjectStore,
    acquire,
    commit,
)
from harness_core.workspace.sync import TAR, TarWorkspaceSync
from harness_core.workspace.sync.types import ON_FIRST_READ

from .test_workspace_sync_docker import (  # pyright: ignore[reportPrivateUsage]
    ROOT,
    DockerSyncHarness,
    _ok,
)

if TYPE_CHECKING:
    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from harness_core.workspace import ObjectScope

    from .conftest import DockerContractEnv

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]


class _SlowInputs:
    """The object store, slow to hand out the input's bytes: a large input fetched while root's archive streams."""

    def __init__(self, inner: CountingObjectStore, slow: frozenset[str], delay_s: float) -> None:
        self._inner, self._slow, self._delay_s = inner, slow, delay_s

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def get(self, scope: ObjectScope, ref: object) -> bytes:
        if getattr(ref, "content_hash", None) in self._slow:
            await asyncio.sleep(self._delay_s)
        return await self._inner.get(scope, ref)  # type: ignore[arg-type]


_PLANTER = (
    "mkdir -p /tmp/r14-target; "
    "while :; do rm -rf /workspace/in /workspace/.pipeshub-staging-* 2>/dev/null; "
    "ln -s /tmp/r14-target /workspace/in 2>/dev/null; "
    "for s in /workspace/.pipeshub-staging-*; do [ -d \"$s\" ] && rm -rf \"$s\" && ln -s /tmp/r14-target \"$s\"; done; "
    "done"
)


async def test_r14_ts_d1_a_kept_sandboxs_background_process_never_redirects_a_turns_inputs(
    docker_broker: DockerSandboxBroker, sandbox_env: DockerContractEnv
) -> None:
    """R14-TS (Docker proof): a process left running in the sandbox keeps turning ``in`` (and any staging folder it
    sees) into a link to ``/tmp/r14-target`` while a turn starts with an input whose bytes arrive slowly. Nothing is
    written there as root; the input is in place as the sandbox user's, or waits for its first read."""
    h = DockerSyncHarness(docker_broker, sandbox_env, TAR)
    manifest = await commit(h, {"notes.md": b"n", "in/sales.csv": b"region,amount\nnorth,1\n"})
    slow = frozenset({manifest.files["in/sales.csv"].content.content_hash})
    sync = TarWorkspaceSync(objects=_SlowInputs(h.objects, slow, 1.5), scope=h.object_scope)  # type: ignore[arg-type]
    sandbox = await acquire(h)
    try:
        planter = await sandbox.exec(["sh", "-c", _PLANTER], env={})
        await asyncio.sleep(0.5)
        state = await sync.materialize(sandbox, manifest, root=ROOT)
        await planter.signal(signal.SIGKILL)
        _ = await planter.wait(timeout_s=10)
        owned = await _ok(sandbox, ["sh", "-c", "find /tmp/r14-target -mindepth 1 -printf '%u %p\\n'"])
        assert b"root" not in owned, owned
        assert b"sales.csv" not in owned or b"1000" in owned, owned
        listing = await _ok(sandbox, ["ls", "-A", ROOT])
        assert b".pipeshub-staging-" not in listing, listing
        if state.hidden:
            assert dict(state.hidden) == {"in/sales.csv": ON_FIRST_READ}
        else:
            # Moved in; the planter may have removed it since, as the engine may remove its own files.
            out = await _ok(sandbox, ["sh", "-c", f'f={ROOT}/in/sales.csv; [ -e "$f" ] && stat -c %u "$f" || echo gone'])
            assert out.strip() in (b"1000", b"gone"), out
    finally:
        await h.broker.release(sandbox)
