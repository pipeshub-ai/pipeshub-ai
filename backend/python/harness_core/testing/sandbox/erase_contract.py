"""Contract of ``SessionFolderEraser`` (E1): a session's persistent folders go with the session, nothing else.

A subclass provides ``eraser`` (a broker with persistent folders that implements ``erase_session``) and
``sandbox_env``. Files are written and read through sandboxes, so the suite holds for any store behind the
broker (an in-memory map, a Docker volume subpath, a copy).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.errors import InvalidSandboxRequest, SandboxNotFound
from harness_core.sandbox.types import MountSpec
from harness_core.testing.sandbox.contract import LEASE, run, with_mounts

if TYPE_CHECKING:
    from harness_core.sandbox.ports import SandboxBroker
    from harness_core.testing.sandbox.contract import SandboxContractEnv

TARGET = "/ws"


async def _write(broker: SandboxBroker, env: SandboxContractEnv, source: str, text: str) -> None:
    handle = await broker.acquire(
        with_mounts(env.spec(), MountSpec(TARGET, source=source)), lease=LEASE, idempotency_key=f"w-{uuid.uuid4().hex}"
    )
    try:
        _, err, code = await run(await handle.exec(env.argv_write_file(f"{TARGET}/note.txt", text), env={}))
        assert code == 0, err
    finally:
        await broker.release(handle)


async def _read(broker: SandboxBroker, env: SandboxContractEnv, source: str) -> bytes | None:
    handle = await broker.acquire(
        with_mounts(env.spec(), MountSpec(TARGET, source=source)), lease=LEASE, idempotency_key=f"r-{uuid.uuid4().hex}"
    )
    try:
        out, _, code = await run(await handle.exec(env.argv_read_file(f"{TARGET}/note.txt"), env={}))
        return out if code == 0 else None
    finally:
        await broker.release(handle)


class SessionFolderEraserContract:
    @pytest.fixture
    def eraser(self) -> SandboxBroker:
        raise NotImplementedError

    async def test_erasing_a_session_removes_its_folders_and_no_other(
        self, eraser: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        org, other_org = f"org-{uuid.uuid4().hex[:8]}", f"org-{uuid.uuid4().hex[:8]}"
        doomed = (f"{org}/ses-a/workspace", f"{org}/ses-a/fork-1")
        kept = (f"{org}/ses-b/workspace", f"{org}/ses-ab/workspace", f"{other_org}/ses-a/workspace")
        for source in (*doomed, *kept):
            await _write(eraser, sandbox_env, source, source)
        assert await eraser.erase_session(org_id=org, session_id="ses-a") == 1  # type: ignore[attr-defined]
        for source in doomed:
            assert await _read(eraser, sandbox_env, source) is None, source
        for source in kept:
            assert await _read(eraser, sandbox_env, source) == source.encode(), source

    async def test_a_session_without_folders_erases_nothing(self, eraser: SandboxBroker) -> None:
        assert await eraser.erase_session(org_id="org-none", session_id=f"ses-{uuid.uuid4().hex}") == 0  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("org_id", "session_id"),
        [("..", "s"), ("org", ".."), ("org", "a/b"), ("", "s"), ("org", ""), ("org", "../x"), ("o/rg", "s")],
    )
    async def test_ids_that_are_not_one_safe_segment_are_refused(
        self, eraser: SandboxBroker, org_id: str, session_id: str
    ) -> None:
        with pytest.raises(InvalidSandboxRequest):
            _ = await eraser.erase_session(org_id=org_id, session_id=session_id)  # type: ignore[attr-defined]


    async def test_cleanup_g1_erasing_an_org_ends_its_sandboxes_and_removes_its_folders_and_no_other(
        self, eraser: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        org, other_org = f"org-{uuid.uuid4().hex[:8]}", f"org-{uuid.uuid4().hex[:8]}"
        doomed = (f"{org}/ses-a/workspace", f"{org}/ses-b/fork-1")
        kept = (f"{other_org}/ses-a/workspace", f"{org}x/ses-a/workspace")
        for source in (*doomed, *kept):
            await _write(eraser, sandbox_env, source, source)
        mine = await eraser.acquire(sandbox_env.spec(labels={"orgId": org}), lease=LEASE, idempotency_key=f"o-{org}")
        theirs = await eraser.acquire(sandbox_env.spec(labels={"orgId": other_org}), lease=LEASE, idempotency_key=f"o-{other_org}")
        try:
            erased = await eraser.erase_org(org_id=org)  # type: ignore[attr-defined]
            assert (erased.sandboxes, erased.folders) == (1, 1)
            again = await eraser.erase_org(org_id=org)  # type: ignore[attr-defined]
            assert (again.sandboxes, again.folders) == (0, 0)
            with pytest.raises(SandboxNotFound):
                _ = await mine.exec(sandbox_env.argv_print("x", "", 0), env={})
            _, _, code = await run(await theirs.exec(sandbox_env.argv_print("x", "", 0), env={}))
            assert code == 0
            for source in doomed:
                assert await _read(eraser, sandbox_env, source) is None, source
            for source in kept:
                assert await _read(eraser, sandbox_env, source) == source.encode(), source
        finally:
            await eraser.release(theirs)

    @pytest.mark.parametrize("org_id", ["..", "", "a/b", "../x", "."])
    async def test_cleanup_g1_an_org_id_that_is_not_one_safe_segment_is_refused(self, eraser: SandboxBroker, org_id: str) -> None:
        with pytest.raises(InvalidSandboxRequest):
            _ = await eraser.erase_org(org_id=org_id)  # type: ignore[attr-defined]


__all__ = ["SessionFolderEraserContract"]
