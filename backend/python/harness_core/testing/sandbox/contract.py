"""Contract suite every ``SandboxBroker`` must pass (P5.1 SB-1..SB-22, P7.1 SB-23..SB-25, P5.4 SB-26..SB-30).

Subclass :class:`SandboxBrokerContract` and provide two pytest fixtures:
``broker`` (the adapter under test) and ``sandbox_env`` (a
:class:`SandboxContractEnv` for it). The env speaks a small program
vocabulary so the same test bodies run on the in-memory fake, on a container
runtime, and through the HTTP client. Skips belong in fixtures, never in
these bodies.

The egress cases also need ``egress_broker`` (the adapter bound to an egress
proxy) and ``egress_env`` (an :class:`EgressContractEnv`); the defaults skip,
so a subclass whose adapter has a proxy overrides both in the class.
"""

from __future__ import annotations

import asyncio
import io
import signal
import tarfile
import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.egress.types import EGRESS_PROXY_PORT
from harness_core.sandbox.errors import (
    InvalidSandboxRequest,
    InvalidSandboxSpec,
    IsolationUnavailable,
    LeaseFenced,
    PolicyUnenforceable,
    SandboxConnectError,
    SandboxError,
    SandboxNotFound,
    SandboxPathNotFound,
)
from harness_core.sandbox.streams import collect, iter_bytes
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    LeaseRef,
    MountSpec,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.sandbox.ports import (
        ByteStream,
        ProcessHandle,
        SandboxBroker,
        SandboxHandle,
    )

LEASE = LeaseRef("worker-1", 1)
WORK = "/work"
SEEDED = "/seeded"


class SandboxContractEnv(ABC):
    """What the suite needs to know about one adapter."""

    #: A mode the adapter under test does not offer.
    unsupported_mode: RuntimeMode = RuntimeMode.MICROVM
    #: A label prefix the adapter reserves for itself.
    reserved_label: str = ""
    #: Seconds a "sleeping" process is given before a timeout is asserted.
    short_wait_s: float = 0.3

    @abstractmethod
    def spec(self, *, labels: Mapping[str, str] | None = None, mode: RuntimeMode | None = None) -> SandboxSpec:
        """A spec with a writable scratch mount at ``WORK``."""

    @abstractmethod
    def argv_print(self, stdout: str, stderr: str, code: int) -> list[str]: ...

    @abstractmethod
    def argv_cat(self) -> list[str]: ...

    @abstractmethod
    def argv_printenv(self, name: str) -> list[str]: ...

    @abstractmethod
    def argv_sleep(self) -> list[str]: ...

    @abstractmethod
    def argv_echo_server(self, port: int) -> list[str]: ...

    @abstractmethod
    def argv_read_file(self, path: str) -> list[str]:
        """Prints the file's bytes, exit 0; non-zero if it is missing."""

    @abstractmethod
    def argv_write_file(self, path: str, text: str) -> list[str]:
        """Writes ``text`` to ``path``, exit 0; non-zero where the sandbox may not write."""

    @abstractmethod
    def argv_remove_file(self, path: str) -> list[str]:
        """Removes ``path``, exit 0; non-zero where the sandbox may not write."""

    @abstractmethod
    def advance(self, seconds: float) -> None:
        """Move the broker's clock forward."""


class EgressContractEnv(ABC):
    """What the egress cases need: a gateway behind the adapter's proxy and a way to call it."""

    gateway_host: str = "model-gateway"
    credential_ref: str = "gateway:model"

    def gateway_url(self, path: str) -> str:
        return f"http://{self.gateway_host}:{EGRESS_PROXY_PORT}{path}"

    def policy(self, placeholder: str) -> EgressPolicy:
        return EgressPolicy(
            destinations=(EgressDestination(self.gateway_host, (EGRESS_PROXY_PORT,)),),
            token_routes=(TokenRoute(placeholder, self.gateway_host, self.credential_ref),),
        )

    @abstractmethod
    def argv_http(self, url: str, headers: Mapping[str, str]) -> list[str]:
        """A GET through the proxy that prints ``<status> <X-Egress-Refusal or ->``, a newline, then the body."""

    @abstractmethod
    async def gateway_received(self, authorization: str) -> bool:
        """Whether the gateway has received this ``Authorization`` value (waiting briefly where it records asynchronously)."""

    @abstractmethod
    def expected_token(self, sandbox_id: str) -> str:
        """The first token the proxy's token source issues for this sandbox's route."""

    @abstractmethod
    async def bound(self) -> frozenset[str]:
        """Sandbox ids the proxy holds a binding for."""


def placeholder() -> str:
    # Unique per call: suites on one shared proxy run in parallel.
    return f"ph-contract-{uuid.uuid4().hex}"


def with_egress(spec: SandboxSpec, policy: EgressPolicy) -> SandboxSpec:
    return SandboxSpec(spec.image_digest, spec.mode, spec.resources, policy, spec.mounts, spec.labels)


def parse_http(out: bytes) -> tuple[int, str, str]:
    head, _, body = out.decode("utf-8", "replace").partition("\n")
    status, _, reason = head.strip().partition(" ")
    return int(status or 0), (reason.strip() or "-"), body


def tar_of(files: Mapping[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def files_of(tar_bytes: bytes) -> dict[str, bytes]:
    """Regular files in a tar, with the top-level directory name stripped."""
    found: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as archive:
        for member in archive:
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            name = member.name.split("/", 1)[1] if "/" in member.name else member.name
            found[name] = extracted.read() if extracted is not None else b""
    return found


async def run(process: ProcessHandle, *, timeout_s: float = 30.0) -> tuple[bytes, bytes, int]:
    out, err = await asyncio.wait_for(
        asyncio.gather(collect(process.stdout), collect(process.stderr)), timeout_s
    )
    return out, err, await process.wait(timeout_s=timeout_s)


async def connect_when_listening(handle: SandboxHandle, port: int, *, timeout_s: float = 20.0) -> ByteStream:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while True:
        try:
            return await handle.connect(port)
        except SandboxConnectError:
            if asyncio.get_running_loop().time() > deadline:
                raise
            await asyncio.sleep(0.1)


async def read_exactly(stream: ByteStream, size: int, *, timeout_s: float = 10.0) -> bytes:
    data = b""
    while len(data) < size:
        chunk = await asyncio.wait_for(stream.read(), timeout_s)
        if not chunk:
            break
        data += chunk
    return data


class SandboxBrokerContract:
    @pytest.fixture
    def egress_broker(self) -> SandboxBroker:
        pytest.skip("this suite runs its adapter without an egress proxy")

    @pytest.fixture
    def egress_env(self) -> EgressContractEnv:
        pytest.skip("this suite runs its adapter without an egress proxy")

    async def test_same_key_returns_the_same_sandbox(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        first = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-same")
        second = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-same")
        try:
            assert first.id == second.id
            assert (await broker.health()).active == 1
        finally:
            await broker.release(first)

    async def test_concurrent_acquire_with_one_key_makes_one_sandbox(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handles = await asyncio.gather(
            *(broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-race") for _ in range(4))
        )
        try:
            assert len({h.id for h in handles}) == 1
            assert (await broker.health()).active == 1
        finally:
            await broker.release(handles[0])

    async def test_different_keys_make_different_sandboxes(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        a = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-a")
        b = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-b")
        try:
            assert a.id != b.id
        finally:
            await broker.release(a)
            await broker.release(b)

    async def test_a_released_handle_is_gone_and_release_is_idempotent(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-rel")
        await broker.release(handle)
        with pytest.raises(SandboxNotFound):
            _ = await handle.exec(sandbox_env.argv_print("x", "", 0), env={})
        await broker.release(handle)
        assert (await broker.health()).active == 0

    async def test_acquire_after_release_makes_a_new_sandbox(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        first = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-again")
        await broker.release(first)
        second = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-again")
        try:
            assert second.id != first.id
        finally:
            await broker.release(second)

    async def test_keep_warm_lets_the_same_key_take_it_back(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        first = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-warm")
        await broker.release(first, keep_warm_s=600)
        second = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-warm")
        try:
            assert second.id == first.id
            out, _, code = await run(await second.exec(sandbox_env.argv_print("warm", "", 0), env={}))
            assert (out, code) == (b"warm", 0)
        finally:
            await broker.release(second)

    async def test_reap_removes_matching_sandboxes_old_enough(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        old_a = await broker.acquire(sandbox_env.spec(labels={"run": "r1"}), lease=LEASE, idempotency_key="k-r1")
        old_b = await broker.acquire(sandbox_env.spec(labels={"run": "r1", "x": "y"}), lease=LEASE, idempotency_key="k-r2")
        other = await broker.acquire(sandbox_env.spec(labels={"run": "r2"}), lease=LEASE, idempotency_key="k-r3")
        sandbox_env.advance(120)
        young = await broker.acquire(sandbox_env.spec(labels={"run": "r1"}), lease=LEASE, idempotency_key="k-r4")
        try:
            assert await broker.reap(labels={"run": "r1"}, older_than_s=60) == 2
            for gone in (old_a, old_b):
                with pytest.raises(SandboxNotFound):
                    _ = await gone.exec(sandbox_env.argv_print("x", "", 0), env={})
            for kept in (other, young):
                _, _, code = await run(await kept.exec(sandbox_env.argv_print("x", "", 0), env={}))
                assert code == 0
            assert await broker.reap(labels={"run": "r1"}, older_than_s=60) == 0
        finally:
            await broker.release(other)
            await broker.release(young)

    async def test_exec_returns_both_streams_and_the_exit_code(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-exec")
        try:
            out, err, code = await run(await handle.exec(sandbox_env.argv_print("to-out", "to-err", 3), env={}))
            assert (out, err, code) == (b"to-out", b"to-err", 3)
        finally:
            await broker.release(handle)

    async def test_stdin_is_written_and_closed(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-stdin")
        try:
            process = await handle.exec(sandbox_env.argv_cat(), env={}, stdin=True)
            await process.write(b"hello ")
            await process.write(b"sandbox")
            await process.close_stdin()
            out, _, code = await run(process)
            assert (out, code) == (b"hello sandbox", 0)
        finally:
            await broker.release(handle)

    async def test_exec_env_reaches_the_process(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-env")
        try:
            out, _, code = await run(
                await handle.exec(sandbox_env.argv_printenv("SBX_VALUE"), env={"SBX_VALUE": "v-42"})
            )
            assert (out.strip(), code) == (b"v-42", 0)
        finally:
            await broker.release(handle)

    async def test_signal_terminates_the_process(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-sig")
        try:
            process = await handle.exec(sandbox_env.argv_sleep(), env={})
            await process.signal(signal.SIGTERM)
            _, _, code = await run(process)
            assert code == 128 + signal.SIGTERM
        finally:
            await broker.release(handle)

    async def test_wait_times_out_and_kill_ends_the_process(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-kill")
        try:
            process = await handle.exec(sandbox_env.argv_sleep(), env={})
            with pytest.raises(TimeoutError):
                _ = await process.wait(timeout_s=sandbox_env.short_wait_s)
            await process.signal(signal.SIGKILL)
            _, _, code = await run(process)
            assert code == 128 + signal.SIGKILL
        finally:
            await broker.release(handle)

    async def test_connect_reaches_a_loopback_server(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-conn")
        try:
            server = await handle.exec(sandbox_env.argv_echo_server(7071), env={})
            stream = await connect_when_listening(handle, 7071)
            await stream.write(b"ping-1")
            assert await read_exactly(stream, 6) == b"ping-1"
            await stream.write(b"ping-2")
            await stream.write_eof()
            assert await read_exactly(stream, 6) == b"ping-2"
            assert await asyncio.wait_for(stream.read(), 10) == b""
            await stream.aclose()
            await server.signal(signal.SIGKILL)
            _ = await run(server)
        finally:
            await broker.release(handle)

    async def test_connect_to_a_closed_port_fails(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-closed")
        try:
            with pytest.raises(SandboxConnectError):
                _ = await handle.connect(7999)
        finally:
            await broker.release(handle)

    async def test_tar_round_trips(self, broker: SandboxBroker, sandbox_env: SandboxContractEnv) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-tar")
        files = {"a.txt": b"alpha", "d/b.bin": bytes(range(256)) * 64}
        try:
            await handle.put_tar(WORK, iter_bytes(tar_of(files), chunk_size=1000))
            assert files_of(await collect(await handle.get_tar(WORK))) == files
        finally:
            await broker.release(handle)

    async def test_put_tar_outside_writable_mounts_fails(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-ro")
        try:
            with pytest.raises(SandboxError):
                await handle.put_tar("/etc", iter_bytes(tar_of({"x": b"y"})))
        finally:
            await broker.release(handle)

    async def test_sb22_put_tar_into_tmp_fails(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        # /tmp is writable by programs but is not a mount: files reach a sandbox only through mounts (X1).
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-tmp")
        try:
            with pytest.raises(SandboxError):
                await handle.put_tar("/tmp", iter_bytes(tar_of({"x": b"y"})))
        finally:
            await broker.release(handle)

    async def test_get_tar_of_a_missing_path_fails(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-missing")
        try:
            with pytest.raises(SandboxPathNotFound):
                _ = await handle.get_tar(f"{WORK}/no-such-file")
        finally:
            await broker.release(handle)

    async def test_eh2_13_a_sandbox_of_an_image_without_pins_vouches_for_no_binary(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(sandbox_env.spec(), lease=LEASE, idempotency_key="k-vouch")
        try:
            assert dict(handle.verified_binaries) == {}
        finally:
            await broker.release(handle)

    async def test_health_reports_the_modes(self, broker: SandboxBroker) -> None:
        health = await broker.health()
        assert health.ok
        assert health.modes == broker.modes()
        assert broker.modes()

    async def test_an_unsupported_mode_is_refused(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        assert sandbox_env.unsupported_mode not in broker.modes()
        with pytest.raises(IsolationUnavailable) as refused:
            _ = await broker.acquire(
                sandbox_env.spec(mode=sandbox_env.unsupported_mode), lease=LEASE, idempotency_key="k-mode"
            )
        assert refused.value.reason == "mode_unavailable"
        assert (await broker.health()).active == 0

    async def test_a_stale_fence_is_refused_and_a_higher_one_adopts(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        first = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("w-1", 5), idempotency_key="k-fence")
        try:
            with pytest.raises(LeaseFenced):
                _ = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("w-0", 4), idempotency_key="k-fence")
            adopted = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("w-2", 6), idempotency_key="k-fence")
            assert adopted.id == first.id
            with pytest.raises(LeaseFenced):
                _ = await broker.acquire(sandbox_env.spec(), lease=LeaseRef("w-1", 5), idempotency_key="k-fence")
        finally:
            await broker.release(first)

    async def test_an_invalid_spec_is_refused(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        base = sandbox_env.spec()
        bad = [
            SandboxSpec(base.image_digest, base.mode, base.resources, base.egress, (MountSpec("relative"),), {}),
            SandboxSpec(base.image_digest, base.mode, base.resources, base.egress, (MountSpec("/a"), MountSpec("/a")), {}),
            SandboxSpec(base.image_digest, base.mode, base.resources, base.egress, (MountSpec("/a", source="../x"),), {}),
        ]
        if sandbox_env.reserved_label:
            bad.append(sandbox_env.spec(labels={sandbox_env.reserved_label + "key": "x"}))
        for spec in bad:
            with pytest.raises(InvalidSandboxSpec):
                _ = await broker.acquire(spec, lease=LEASE, idempotency_key="k-invalid")
        assert (await broker.health()).active == 0

    async def test_sb23_seed_tar_fills_a_read_only_mount_processes_cannot_change(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        handle = await broker.acquire(
            with_mounts(sandbox_env.spec(), MountSpec(SEEDED, read_only=True)), lease=LEASE, idempotency_key="k-seed"
        )
        files = {"skill/SKILL.md": b"# seeded", "skill/assets/logo.bin": bytes(range(256))}
        try:
            await handle.seed_tar(SEEDED, iter_bytes(tar_of(files), chunk_size=100))
            for name, data in files.items():
                out, _, code = await run(await handle.exec(sandbox_env.argv_read_file(f"{SEEDED}/{name}"), env={}))
                assert (out, code) == (data, 0)
            for argv in (
                sandbox_env.argv_write_file(f"{SEEDED}/skill/SKILL.md", "changed"),
                sandbox_env.argv_write_file(f"{SEEDED}/skill/new.txt", "new"),
                sandbox_env.argv_remove_file(f"{SEEDED}/skill/SKILL.md"),
            ):
                _, _, code = await run(await handle.exec(argv, env={}))
                assert code != 0, argv
            with pytest.raises(SandboxError):
                await handle.put_tar(SEEDED, iter_bytes(tar_of({"skill/SKILL.md": b"changed"})))
            assert files_of(await collect(await handle.get_tar(f"{SEEDED}/skill"))) == {
                name.split("/", 1)[1]: data for name, data in files.items()
            }
        finally:
            await broker.release(handle)

    async def test_sb24_seed_tar_outside_a_read_only_scratch_mount_is_refused(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        base = sandbox_env.spec()
        handle = await broker.acquire(base, lease=LEASE, idempotency_key="k-seed-refused")
        try:
            for dest in (WORK, "/tmp", "/", "/etc"):
                with pytest.raises(InvalidSandboxRequest):
                    await handle.seed_tar(dest, iter_bytes(tar_of({"x": b"y"})))
            _, _, code = await run(await handle.exec(sandbox_env.argv_read_file(f"{WORK}/x"), env={}))
            assert code != 0
        finally:
            await broker.release(handle)

    async def test_sb25_a_read_only_mount_inside_a_writable_one_stays_read_only(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        nested = f"{WORK}/skills"
        handle = await broker.acquire(
            with_mounts(sandbox_env.spec(), MountSpec(nested, read_only=True)), lease=LEASE, idempotency_key="k-nested"
        )
        try:
            await handle.seed_tar(nested, iter_bytes(tar_of({"s/SKILL.md": b"# nested"})))
            _, _, code = await run(
                await handle.exec(sandbox_env.argv_write_file(f"{nested}/s/SKILL.md", "changed"), env={})
            )
            assert code != 0
            with pytest.raises(SandboxError):
                await handle.put_tar(nested, iter_bytes(tar_of({"s/SKILL.md": b"changed"})))
            out, _, code = await run(await handle.exec(sandbox_env.argv_read_file(f"{nested}/s/SKILL.md"), env={}))
            assert (out, code) == (b"# nested", 0)
            _, _, code = await run(await handle.exec(sandbox_env.argv_write_file(f"{WORK}/ok.txt", "ok"), env={}))
            assert code == 0
        finally:
            await broker.release(handle)


    async def test_sb26_an_allowlist_without_a_proxy_is_unenforceable(
        self, broker: SandboxBroker, sandbox_env: SandboxContractEnv
    ) -> None:
        policy = EgressPolicy(destinations=(EgressDestination("model-gateway", (EGRESS_PROXY_PORT,)),))
        with pytest.raises(PolicyUnenforceable) as refused:
            _ = await broker.acquire(with_egress(sandbox_env.spec(), policy), lease=LEASE, idempotency_key="k-egress")
        assert refused.value.reason == "egress_proxy_missing"
        assert (await broker.health()).active == 0

    async def test_sb27_the_gateway_gets_the_run_bound_token_and_the_sandbox_only_the_placeholder(
        self, egress_broker: SandboxBroker, sandbox_env: SandboxContractEnv, egress_env: EgressContractEnv
    ) -> None:
        ph = placeholder()
        spec = with_egress(sandbox_env.spec(labels={"runId": "run-sb27"}), egress_env.policy(ph))
        handle = await egress_broker.acquire(spec, lease=LEASE, idempotency_key=f"k-sb27-{ph}")
        try:
            argv = egress_env.argv_http(egress_env.gateway_url("/v1/echo"), {"Authorization": f"Bearer {ph}"})
            out, _, code = await run(await handle.exec(argv, env={}))
            status, reason, body = parse_http(out)
            assert (code, status, reason) == (0, 200, "-"), out
            token = egress_env.expected_token(handle.id)
            assert await egress_env.gateway_received(f"Bearer {token}")
            assert token not in out.decode()
            assert ph in body
        finally:
            await egress_broker.release(handle)

    async def test_sb28_a_destination_outside_the_allowlist_is_refused(
        self, egress_broker: SandboxBroker, sandbox_env: SandboxContractEnv, egress_env: EgressContractEnv
    ) -> None:
        ph = placeholder()
        spec = with_egress(sandbox_env.spec(), egress_env.policy(ph))
        handle = await egress_broker.acquire(spec, lease=LEASE, idempotency_key=f"k-sb28-{ph}")
        try:
            out, _, _ = await run(await handle.exec(egress_env.argv_http("http://example.com/", {}), env={}))
            assert parse_http(out)[:2] == (403, "destination_not_allowed"), out
        finally:
            await egress_broker.release(handle)

    async def test_sb29_a_placeholder_is_held_by_one_live_sandbox(
        self, egress_broker: SandboxBroker, sandbox_env: SandboxContractEnv, egress_env: EgressContractEnv
    ) -> None:
        ph = placeholder()
        spec = with_egress(sandbox_env.spec(), egress_env.policy(ph))
        first = await egress_broker.acquire(spec, lease=LEASE, idempotency_key=f"k-sb29a-{ph}")
        with pytest.raises(InvalidSandboxSpec) as refused:
            _ = await egress_broker.acquire(spec, lease=LEASE, idempotency_key=f"k-sb29b-{ph}")
        assert refused.value.reason == "placeholder_in_use"
        await egress_broker.release(first)
        assert first.id not in await egress_env.bound()
        second = await egress_broker.acquire(spec, lease=LEASE, idempotency_key=f"k-sb29b-{ph}")
        try:
            assert second.id in await egress_env.bound()
        finally:
            await egress_broker.release(second)
        assert second.id not in await egress_env.bound()

    async def test_sb31_a_kept_sandbox_taken_back_gets_the_new_turns_egress_binding(
        self, egress_broker: SandboxBroker, sandbox_env: SandboxContractEnv, egress_env: EgressContractEnv
    ) -> None:
        """SIGNIN-5: a later turn re-acquires a kept sandbox with its own placeholder; the old one stops working."""
        old, new = placeholder(), placeholder()
        key = f"k-sb31-{old}"
        first = await egress_broker.acquire(
            with_egress(sandbox_env.spec(), egress_env.policy(old)), lease=LEASE, idempotency_key=key
        )
        await egress_broker.release(first, keep_warm_s=600)
        handle = await egress_broker.acquire(
            with_egress(sandbox_env.spec(), egress_env.policy(new)), lease=LEASE, idempotency_key=key
        )
        try:
            assert handle.id == first.id
            url = egress_env.gateway_url("/v1/echo")
            out, _, _ = await run(await handle.exec(egress_env.argv_http(url, {"Authorization": f"Bearer {new}"}), env={}))
            assert parse_http(out)[0] == 200, out
            assert await egress_env.gateway_received(f"Bearer {egress_env.expected_token(handle.id)}")
            out, _, _ = await run(await handle.exec(egress_env.argv_http(url, {"Authorization": f"Bearer {old}"}), env={}))
            status, _, body = parse_http(out)
            assert status != 200 or old in body, out
        finally:
            await egress_broker.release(handle)

    async def test_sb30_an_invalid_egress_policy_is_refused(
        self, egress_broker: SandboxBroker, sandbox_env: SandboxContractEnv, egress_env: EgressContractEnv
    ) -> None:
        policy = EgressPolicy(
            destinations=(EgressDestination(egress_env.gateway_host, (EGRESS_PROXY_PORT,)),),
            token_routes=(TokenRoute(placeholder(), "not-allowlisted", egress_env.credential_ref),),
        )
        before = (await egress_broker.health()).active
        with pytest.raises(InvalidSandboxSpec) as refused:
            _ = await egress_broker.acquire(with_egress(sandbox_env.spec(), policy), lease=LEASE, idempotency_key="k-sb30")
        assert refused.value.reason == "egress_policy_invalid"
        assert (await egress_broker.health()).active == before


def with_mounts(spec: SandboxSpec, *mounts: MountSpec) -> SandboxSpec:
    return SandboxSpec(
        spec.image_digest, spec.mode, spec.resources, spec.egress, (*spec.mounts, *mounts), spec.labels
    )


__all__ = [
    "LEASE",
    "EgressContractEnv",
    "parse_http",
    "placeholder",
    "with_egress",
    "SEEDED",
    "WORK",
    "SandboxBrokerContract",
    "SandboxContractEnv",
    "connect_when_listening",
    "files_of",
    "read_exactly",
    "run",
    "tar_of",
    "with_mounts",
]
