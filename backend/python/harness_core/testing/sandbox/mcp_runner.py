"""Test kit for MCP runners (P5.10): a line JSON-RPC client, the in-process echo server, the fake bridge,
and ``MCPRunnerContract``, which every ``MCPRunner`` adapter passes.

A subclass provides two fixtures: ``mcp_runner`` (the adapter under test, built
with ``mcp_env.config``) and ``mcp_env`` (``MCPRunnerContractEnv``: specs for the
echo server, and ``advance`` for the injected clock). Skips live in fixtures.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.sandbox.mcp_runner.errors import (
    RunnerEnvUndeclared,
    RunnerOwnerMismatch,
    RunnerSpecInvalid,
    RunnerUnpinned,
)
from harness_core.sandbox.mcp_runner.sandboxed import bridge_source
from harness_core.sandbox.mcp_runner.types import (
    BRIDGE_PATH,
    BRIDGE_READY,
    MCPRunnerSpec,
    RunnerConfig,
    RunnerEgressHost,
    RunnerKey,
    RunnerRequest,
    RunnerSecrets,
)
from harness_core.testing.sandbox.egress import EgressDenied
from harness_core.testing.sandbox.mcp_echo_server import FetchNeeded, fetched, respond

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.sandbox.mcp_runner.ports import MCPRunner
    from harness_core.sandbox.ports import ByteStream
    from harness_core.testing.sandbox.fake import FakeProcessContext

    Fetch = Callable[[str, Mapping[str, str]], Awaitable[tuple[int, str]]]

ECHO_COMMAND = "pxh-echo-mcp"
TENANT = "org-mr"
SECRET_NAME = "ECHO_API_TOKEN"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LineRpc:
    """A JSON-RPC 2.0 client over one runner session (newline-delimited)."""

    def __init__(self, stream: ByteStream, *, timeout_s: float = 60.0) -> None:
        self._stream = stream
        self._buffer = b""
        self._next = 1
        self._timeout_s = timeout_s

    async def _line(self) -> bytes:
        while b"\n" not in self._buffer:
            chunk = await asyncio.wait_for(self._stream.read(), self._timeout_s)
            if not chunk:
                raise ConnectionError("the session ended")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line

    async def request(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request_id = self._next
        self._next += 1
        body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params or {})}
        await self._stream.write(json.dumps(body).encode() + b"\n")
        while True:
            message: dict[str, Any] = json.loads(await self._line())
            if message.get("id") == request_id:
                return message

    async def initialize(self) -> dict[str, Any]:
        reply = await self.request(
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "contract", "version": "1"}},
        )
        note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        await self._stream.write(json.dumps(note).encode() + b"\n")
        return reply

    async def tool_names(self) -> list[str]:
        reply = await self.request("tools/list")
        return [tool["name"] for tool in reply["result"]["tools"]]

    async def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> str:
        reply = await self.request("tools/call", {"name": name, "arguments": dict(arguments or {})})
        return str(reply["result"]["content"][0]["text"])

    async def aclose(self) -> None:
        await self._stream.aclose()


async def serve_echo(stream: ByteStream, env: Mapping[str, str], _argv: Sequence[str], *, fetch: Fetch | None = None) -> None:
    """The echo server in process: the same ``respond`` the script runs, on a ``ByteStream``."""
    pending = b""
    while True:
        chunk: bytes = await stream.read()
        if not chunk:
            return
        *lines, pending = (pending + chunk).split(b"\n")
        for line in lines:
            if not line.strip():
                continue
            reply = respond(json.loads(line), env)
            if isinstance(reply, FetchNeeded):
                status, body = await fetch(reply.url, reply.headers) if fetch is not None else (599, "no network")
                reply = fetched(reply.request_id, status, body)
            if reply is not None:
                await stream.write(json.dumps(reply).encode() + b"\n")


def fake_bridge(servers: Mapping[str, Callable[..., Awaitable[None]]] | None = None) -> Callable[[FakeProcessContext], Awaitable[int]]:
    """``python3 <bridge> <port> -- <command> ...`` for ``FakeSandboxBroker``, behaving as the real bridge does.

    It runs only if the runner seeded the real bridge's bytes at the bridge path; it
    reports ready once listening; each connection runs the command's in-process
    server with the bridge's environment and the sandbox's own egress.
    """
    table = dict(servers or {ECHO_COMMAND: serve_echo})

    async def program(context: FakeProcessContext) -> int:
        argv = context.argv
        if len(argv) < 5 or argv[1] != BRIDGE_PATH or argv[3] != "--":
            await context.emit(b"usage\n", stderr=True)
            return 2
        if context.handle.files.get(BRIDGE_PATH) != bridge_source():
            await context.emit(b"python3: can't open file\n", stderr=True)
            return 2
        server = table.get(argv[4])
        listener = context.listen(int(argv[2]))
        await context.emit(BRIDGE_READY)

        async def fetch(url: str, headers: Mapping[str, str]) -> tuple[int, str]:
            try:
                body = await context.handle.egress.request(url, method="GET", headers=dict(headers), body=None)
            except EgressDenied as denied:
                return denied.status or 403, json.dumps({"error": {"reason": denied.reason}})
            return 200, json.dumps(body)

        async def session(conn: ByteStream) -> None:
            try:
                if server is not None:
                    await server(conn, context.env, argv[4:], fetch=fetch)
            finally:
                await conn.aclose()

        tasks: set[asyncio.Task[None]] = set()
        try:
            while True:
                conn = await listener.get()
                task = asyncio.ensure_future(session(conn))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            # In process, nothing else would ever end these; a real runner's go with its sandbox.
            for task in list(tasks):
                _ = task.cancel()

    return program


class MCPRunnerContractEnv:
    """What the contract needs from a host besides the runner."""

    config = RunnerConfig(idle_ttl_s=60, max_age_s=300, reap_grace_s=60, ready_timeout_s=60)
    image = "unused"
    # Hosts the echo server may reach; a sandboxed runner's secrets reach them, never the server (OWD-1).
    egress: tuple[RunnerEgressHost, ...] = ()

    async def received_secret(self, rpc: LineRpc) -> str:
        """Digest of the declared secret as what the server uses it for receives it. A runner with no sandbox (in
        process) hands the server the secret itself; a sandboxed one answers through a listed host."""
        return await rpc.call("env_digest", {"name": SECRET_NAME})

    def spec(
        self,
        server_id: str = "srv-echo",
        *,
        env_names: frozenset[str] = frozenset({SECRET_NAME}),
        command: str = ECHO_COMMAND,
        args: tuple[str, ...] = (),
    ) -> MCPRunnerSpec:
        return MCPRunnerSpec(
            server_id=server_id, image=self.image, command=command, args=args, env_names=env_names, egress=self.egress
        )

    def advance(self, seconds: float) -> None:
        raise NotImplementedError


def request(
    env: MCPRunnerContractEnv,
    owner: str = "alice",
    *,
    tenant: str = TENANT,
    server_id: str = "srv-echo",
    secret: str | None = "s3cret-alice-0123456789",
    principal: str | None = None,
    spec: MCPRunnerSpec | None = None,
) -> RunnerRequest:
    return RunnerRequest(
        key=RunnerKey(tenant, server_id, owner),
        spec=spec or env.spec(server_id),
        principal=principal or owner,
        secrets=RunnerSecrets({SECRET_NAME: secret} if secret is not None else {}),
        labels={"pipeshub.contract": "mr"},
    )


async def session(runner: MCPRunner, req: RunnerRequest) -> LineRpc:
    rpc = LineRpc(await runner.open(req))
    _ = await rpc.initialize()
    return rpc


def _ids(runner: MCPRunner) -> set[str]:
    return {info.runner_id for info in runner.runners()}


class MCPRunnerContract:
    async def test_mr1_list_and_call_over_a_session(self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv) -> None:
        rpc = LineRpc(await mcp_runner.open(request(mcp_env)))
        init = await rpc.initialize()
        assert init["result"]["serverInfo"]["name"] == "pxh-echo"
        assert {"echo", "env_digest", "fetch", "where"} <= set(await rpc.tool_names())
        assert await rpc.call("echo", {"text": "hello runner"}) == "hello runner"
        await rpc.aclose()

    async def test_mr2_one_key_one_runner_each_open_its_own_session(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        first = await session(mcp_runner, request(mcp_env))
        await first.aclose()
        second = await session(mcp_runner, request(mcp_env))
        both = await asyncio.gather(session(mcp_runner, request(mcp_env)), session(mcp_runner, request(mcp_env)))
        assert len(mcp_runner.runners()) == 1
        assert mcp_runner.runners()[0].sessions == 3
        assert [await rpc.call("echo", {"text": str(i)}) for i, rpc in enumerate([second, *both])] == ["0", "1", "2"]
        for rpc in (second, *both):
            await rpc.aclose()
        assert mcp_runner.runners()[0].sessions == 0

    async def test_mr3_owners_and_servers_get_their_own_runners(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        alice = await session(mcp_runner, request(mcp_env, "alice", secret="token-alice-0123456789"))
        bob = await session(mcp_runner, request(mcp_env, "bob", secret="token-bob-0123456789"))
        other = await session(mcp_runner, request(mcp_env, "alice", server_id="srv-other", secret="token-alice-0123456789"))
        runners = {(info.key.owner, info.key.server_id): info.runner_id for info in mcp_runner.runners()}
        assert len(set(runners.values())) == 3
        assert await mcp_env.received_secret(alice) == digest("token-alice-0123456789")
        assert await mcp_env.received_secret(bob) == digest("token-bob-0123456789")
        for rpc in (alice, bob, other):
            await rpc.aclose()

    async def test_mr4_a_declared_secret_reaches_what_the_server_uses_it_for_and_nothing_shows_it(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        canary = "canary-mr4-7f1e2d3c4b5a69788796"
        req = request(mcp_env, secret=canary)
        rpc = await session(mcp_runner, req)
        assert await mcp_env.received_secret(rpc) == digest(canary)
        for shown in (repr(req), repr(req.spec), str(req.secrets), repr(mcp_runner.runners())):
            assert canary not in shown
        await rpc.aclose()

    async def test_mr5_an_undeclared_secret_is_refused_before_anything_starts(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        req = RunnerRequest(
            key=RunnerKey(TENANT, "srv-echo", "alice"),
            spec=mcp_env.spec(env_names=frozenset()),
            principal="alice",
            secrets=RunnerSecrets({SECRET_NAME: "s3cret-0123456789"}),
        )
        with pytest.raises(RunnerEnvUndeclared) as refused:
            _ = await mcp_runner.open(req)
        assert refused.value.reason_code == "runner_env_undeclared"
        with pytest.raises(RunnerSpecInvalid):
            _ = await mcp_runner.open(request(mcp_env, spec=mcp_env.spec(env_names=frozenset({"LD_PRELOAD"})), secret=None))
        assert mcp_runner.runners() == ()

    async def test_mr6_another_owners_secret_is_refused(self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv) -> None:
        with pytest.raises(RunnerOwnerMismatch) as refused:
            _ = await mcp_runner.open(request(mcp_env, "alice", principal="bob", secret="token-bob-0123456789"))
        assert refused.value.reason_code == "runner_owner_mismatch"
        assert mcp_runner.runners() == ()

    async def test_mr7_an_idle_runner_is_swept_and_the_next_open_starts_a_new_one(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        held = await session(mcp_runner, request(mcp_env))
        (first,) = _ids(mcp_runner)
        mcp_env.advance(mcp_env.config.idle_ttl_s + 1)
        assert await mcp_runner.sweep() == 0
        assert _ids(mcp_runner) == {first}
        await held.aclose()
        mcp_env.advance(mcp_env.config.idle_ttl_s - 1)
        assert await mcp_runner.sweep() == 0
        mcp_env.advance(2)
        assert await mcp_runner.sweep() >= 1
        assert mcp_runner.runners() == ()
        again = await session(mcp_runner, request(mcp_env))
        assert await again.call("echo", {"text": "back"}) == "back"
        assert _ids(mcp_runner) - {first} and first not in _ids(mcp_runner)
        await again.aclose()

    async def test_mr8_a_runner_past_its_max_age_is_recycled_when_free(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        step = mcp_env.config.idle_ttl_s / 2
        elapsed = 0.0
        while elapsed < mcp_env.config.max_age_s:
            rpc = await session(mcp_runner, request(mcp_env))
            await rpc.aclose()
            assert await mcp_runner.sweep() == 0
            mcp_env.advance(step)
            elapsed += step
        assert await mcp_runner.sweep() >= 1
        assert mcp_runner.runners() == ()

    @pytest.mark.parametrize(
        ("command", "args"),
        [("npx", ("-y", "some-server")), ("uvx", ("some-server",)), ("pipx", ("run", "some-server")), ("/usr/bin/npm", ("exec", "x"))],
    )
    async def test_mr9_a_fetching_launcher_is_refused(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv, command: str, args: tuple[str, ...]
    ) -> None:
        with pytest.raises(RunnerUnpinned) as refused:
            _ = await mcp_runner.open(request(mcp_env, spec=mcp_env.spec(command=command, args=args)))
        assert refused.value.reason_code == "runner_unpinned"
        assert mcp_runner.runners() == ()

    async def test_mr10_a_changed_secret_gets_a_new_runner(self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv) -> None:
        old = await session(mcp_runner, request(mcp_env, secret="token-v1-0123456789"))
        (first,) = _ids(mcp_runner)
        new = await session(mcp_runner, request(mcp_env, secret="token-v2-0123456789"))
        assert await mcp_env.received_secret(new) == digest("token-v2-0123456789")
        assert await mcp_env.received_secret(old) == digest("token-v1-0123456789")
        assert len(_ids(mcp_runner)) == 2
        await old.aclose()
        assert await mcp_runner.sweep() == 1
        assert first not in _ids(mcp_runner)
        await new.aclose()

    async def test_mr11_stop_all_then_a_new_runner(self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv) -> None:
        for owner in ("alice", "bob"):
            rpc = await session(mcp_runner, request(mcp_env, owner, secret=f"token-{owner}-0123456789"))
            await rpc.aclose()
        before = _ids(mcp_runner)
        assert await mcp_runner.stop_all() == 2
        assert mcp_runner.runners() == ()
        rpc = await session(mcp_runner, request(mcp_env))
        assert await rpc.call("echo", {"text": "again"}) == "again"
        assert not (_ids(mcp_runner) & before)
        await rpc.aclose()

    async def test_mr12_qa26_no_runner_crosses_owner_or_org_across_idle_ttl_and_recycle(
        self, mcp_runner: MCPRunner, mcp_env: MCPRunnerContractEnv
    ) -> None:
        keys = [(TENANT, "alice"), (TENANT, "bob"), (OTHER_TENANT, "alice")]
        claimed: dict[str, tuple[str, str]] = {}

        async def use_each() -> None:
            for tenant, owner in keys:
                secret = f"token-{tenant}-{owner}-0123456789"
                rpc = await session(mcp_runner, request(mcp_env, owner, tenant=tenant, secret=secret))
                assert await mcp_env.received_secret(rpc) == digest(secret), (tenant, owner)
                for info in mcp_runner.runners():
                    for handle in (info.runner_id, info.sandbox_id):
                        if handle is not None:
                            assert claimed.setdefault(handle, (info.key.tenant, info.key.owner)) == (
                                info.key.tenant,
                                info.key.owner,
                            ), f"{handle} served two owners"
                await rpc.aclose()

        step = mcp_env.config.idle_ttl_s / 2
        for _ in range(2):
            await use_each()
            mcp_env.advance(mcp_env.config.idle_ttl_s + 1)
            assert await mcp_runner.sweep() >= len(keys)
            assert mcp_runner.runners() == ()
        elapsed = 0.0
        while elapsed <= mcp_env.config.max_age_s:
            await use_each()
            mcp_env.advance(step)
            elapsed += step
            _ = await mcp_runner.sweep()
        await use_each()
        runner_ids = {h for h in claimed if h.startswith("mcpr")}
        assert len(runner_ids) >= 4 * len(keys), "every idle sweep and every recycle started new runners"


OTHER_TENANT = "org-mr-other"

__all__ = [
    "ECHO_COMMAND",
    "SECRET_NAME",
    "TENANT",
    "LineRpc",
    "MCPRunnerContract",
    "MCPRunnerContractEnv",
    "digest",
    "fake_bridge",
    "request",
    "serve_echo",
    "session",
]
