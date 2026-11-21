"""Contract suite every ``ISandboxProvider`` must pass (C2.14, SP-1..SP-7), on top of the broker suite (SB-*).

Subclass :class:`SandboxProviderContract` and provide ``provider`` (the provider under test) and ``sandbox_env`` (a
:class:`SandboxContractEnv` for its broker). The broker suite runs against the same provider, so a provider is a
broker in every case the broker suite checks (put/get files and exec among them), and the provider's own verbs
are checked here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.snapshots import SnapshotPolicy
from harness_core.sandbox.errors import InvalidSandboxRequest, SandboxNotFound
from harness_core.sandbox.provider import PROVIDER_VERBS
from harness_core.sandbox.streams import iter_bytes
from harness_core.testing.sandbox.contract import (
    LEASE,
    WORK,
    SandboxBrokerContract,
    run,
    tar_of,
)

if TYPE_CHECKING:
    from harness_core.sandbox.provider import ISandboxProvider
    from harness_core.testing.sandbox.contract import SandboxContractEnv

# A folder of the writable scratch mount, as an engine's caches are a folder outside its workspace.
CACHE = f"{WORK}/cache"
POLICY = SnapshotPolicy(paths=(CACHE,))


class SandboxProviderContract(SandboxBrokerContract):
    @pytest.fixture
    def broker(self, provider: ISandboxProvider) -> ISandboxProvider:
        return provider

    async def test_sp1_capabilities_name_the_kind_every_verb_and_the_modes_health_reports(
        self, provider: ISandboxProvider
    ) -> None:
        caps = provider.capabilities()
        assert caps.kind is provider.kind
        assert caps.verbs == PROVIDER_VERBS
        assert caps.modes == provider.modes() == (await provider.health()).modes

    async def test_sp2_attach_reaches_the_sandbox_create_made_and_another_key_gets_another(
        self, provider: ISandboxProvider, sandbox_env: SandboxContractEnv
    ) -> None:
        made = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-a")
        other = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-b")
        try:
            attached = await provider.attach(sandbox_env.spec(), lease=LEASE, key="sp-a")
            assert attached.id == made.id
            assert other.id != made.id
        finally:
            await provider.destroy(made)
            await provider.destroy(other)

    async def test_sp3_resume_takes_back_a_suspended_sandbox_with_its_files(
        self, provider: ISandboxProvider, sandbox_env: SandboxContractEnv
    ) -> None:
        made = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-suspend")
        _, _, code = await run(await made.exec(sandbox_env.argv_write_file(f"{WORK}/kept.txt", "kept"), env={}))
        assert code == 0
        await provider.suspend(made, keep_s=600)
        resumed = await provider.resume(sandbox_env.spec(), lease=LEASE, key="sp-suspend")
        try:
            assert resumed.id == made.id
            out, _, code = await run(await resumed.exec(sandbox_env.argv_read_file(f"{WORK}/kept.txt"), env={}))
            assert (out, code) == (b"kept", 0)
        finally:
            await provider.destroy(resumed)

    async def test_sp4_suspend_for_no_time_is_refused(
        self, provider: ISandboxProvider, sandbox_env: SandboxContractEnv
    ) -> None:
        made = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-zero")
        try:
            with pytest.raises(InvalidSandboxRequest):
                await provider.suspend(made, keep_s=0)
        finally:
            await provider.destroy(made)

    async def test_sp5_destroy_ends_the_sandbox_is_idempotent_and_resume_then_makes_a_new_one(
        self, provider: ISandboxProvider, sandbox_env: SandboxContractEnv
    ) -> None:
        made = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-destroy")
        await provider.destroy(made)
        await provider.destroy(made)
        with pytest.raises(SandboxNotFound):
            _ = await made.exec(sandbox_env.argv_print("x", "", 0), env={})
        again = await provider.resume(sandbox_env.spec(), lease=LEASE, key="sp-destroy")
        try:
            assert again.id != made.id
        finally:
            await provider.destroy(again)

    async def test_sp6_a_snapshot_restores_into_another_sandbox(
        self, provider: ISandboxProvider, sandbox_env: SandboxContractEnv
    ) -> None:
        source = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-snap-from")
        target = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-snap-to")
        try:
            await source.put_tar(WORK, iter_bytes(tar_of({"cache/state.txt": b"cached"})))
            archive = await provider.snapshot(source, POLICY)
            assert archive is not None
            await provider.restore(target, archive, POLICY)
            out, _, code = await run(await target.exec(sandbox_env.argv_read_file(f"{CACHE}/state.txt"), env={}))
            assert (out, code) == (b"cached", 0)
        finally:
            await provider.destroy(source)
            await provider.destroy(target)

    async def test_sp7_a_snapshot_of_paths_that_do_not_exist_is_none(
        self, provider: ISandboxProvider, sandbox_env: SandboxContractEnv
    ) -> None:
        made = await provider.create(sandbox_env.spec(), lease=LEASE, key="sp-snap-none")
        try:
            assert await provider.snapshot(made, POLICY) is None
        finally:
            await provider.destroy(made)


__all__ = ["CACHE", "POLICY", "SandboxProviderContract"]
