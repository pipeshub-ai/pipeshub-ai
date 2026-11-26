"""SL-1, LK-S1 (SIGNIN-2) live: ``ClaudeCodeAdapter`` signs the real binary in, runs one turn on the person's plan,
captures its state and signs out, against the fake Anthropic (no real account, no network beyond the fake).

The sandbox is the lab's container through ``docker exec`` (``signin_lab/sandbox.py``); the launch is the adapter's
own-login launch with two lab-only changes: no egress proxy (the fake is reached directly) and the lab's CA.
Nothing PipesHub logs, journals or captures holds the token, the code or the account's email.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import run_farewell
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.events import MessageCompleted, TurnCompleted, turn_id_for
from harness_core.engines.signin import CodeRequest, SignInReply
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineTask,
    GatewayEndpoints,
)
from harness_engines.claude_code.adapter import ClaudeCodeAdapter
from harness_engines.claude_code.signin import CREDENTIALS, FAREWELL, STATUS
from harness_engines.vendor_hosts import release_sign_in_hosts
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness_engines.claude_code.signin_lab.lab import GOOD_CODE, lab
from tests.unit.harness_engines.claude_code.signin_lab.sandbox import DockerExecSandbox

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

RELEASE = lane_release("claude_code")
# The signed release list itself, so the lanes prove the shipped hosts are enough for the real binary to sign in.
HOSTS = release_sign_in_hosts()
assert HOSTS is not None, "vendor_hosts.json is unsigned"
ENDPOINTS = GatewayEndpoints(
    model_base_url="http://model-gateway:8097/v1",
    model_api=ModelApi.ANTHROPIC_MESSAGES,
    tools_mcp_url="http://tool-gateway:8097/mcp",
    token_placeholder="pxh-model-placeholder-0123456789",
    tools_token_placeholder="pxh-tools-placeholder-0123456789",
)


@dataclass
class Attempt:
    attempt_id: str = "sin_live"
    links: list[str] = field(default_factory=list[str])
    replies: list[SignInReply] = field(default_factory=list[SignInReply])

    def show(self, link: str) -> None:
        self.links.append(link)

    async def code(self, timeout_s: float) -> CodeRequest:
        state = urllib.parse.parse_qs(urllib.parse.urlsplit(self.links[-1]).query)["state"][0]
        # The fake reports a Max plan: the org admits consumer plans here.
        return CodeRequest(f"{GOOD_CODE}#{state}", work_plans_only=False)

    async def reply(self, reply: SignInReply) -> None:
        self.replies.append(reply)


def _image_present() -> None:
    if shutil.which("docker") is None:
        pytest.skip("no docker CLI")
    if subprocess.run(["docker", "image", "inspect", RELEASE.image], capture_output=True, check=False).returncode:
        pytest.skip(f"{RELEASE.image} is not built here (deployment/sandbox-images/claude-code/build.sh --binary PATH)")


async def test_sl1_the_adapter_signs_in_runs_a_turn_and_signs_out(caplog: pytest.LogCaptureFixture) -> None:
    _image_present()
    caplog.set_level(logging.DEBUG)
    adapter = ClaudeCodeAdapter()
    profile = EngineProfile(engine=RELEASE.version, mode=EngineMode.BUILD, access=AccessRouteRef("plan", "d1"))
    own = adapter.own_login(adapter.render_launch(profile, ENDPOINTS, ""))
    # Lab only: the fake is reached directly (no egress proxy here) and trusted through the lab's CA.
    env = {k: v for k, v in own.env.items() if k.lower() != "https_proxy"}
    launch = dataclasses.replace(own, env={**env, "NODE_EXTRA_CA_CERTS": "/certs/ca.pem"})
    attempt = Attempt()
    shown: list[tuple[str, str | None, str | None]] = []

    @contextlib.asynccontextmanager
    async def prompt() -> AsyncIterator[Attempt]:
        yield attempt

    async def progress(state: str, reason: str | None, plan: str | None) -> None:
        shown.append((state, reason, plan))

    with lab(RELEASE.image) as running:
        sandbox = DockerExecSandbox(running.client)
        account = await adapter.sign_in(sandbox, launch, HOSTS, prompt, progress)  # type: ignore[arg-type]
        assert (account.email, account.plan) == ("ada@example.com", "max")
        assert [r.state for r in attempt.replies] == ["signed_in"]
        assert shown == [("requested", None, None), ("signed_in", None, "max")]
        assert attempt.links[0].startswith("https://claude.com/cai/oauth/authorize?")

        session = await adapter.open(sandbox, launch, None)  # type: ignore[arg-type]
        events = [e async for e in session.run_turn(EngineTask(turn_id=turn_id_for("run-1", 0), prompt="say hi"))]
        assert [e.reason for e in events if isinstance(e, TurnCompleted)] == ["completed"]
        assert [e.text for e in events if isinstance(e, MessageCompleted)][-1] == "hi"
        state = await session.capture_state()
        await session.close()
        await adapter.sign_out(sandbox, launch)  # type: ignore[arg-type]

        left = subprocess.run(["docker", "exec", running.client, "ls", "-A", "/state/claude"],
                              capture_output=True, text=True, check=False).stdout.split()
        # Before any other `claude` command: each one writes a fresh .claude.json.
        assert CREDENTIALS.rsplit("/", 1)[1] not in left and ".claude.json" not in left
        status = subprocess.run(
            ["docker", "exec", "-e", "CLAUDE_CONFIG_DIR=/state/claude", running.client, *STATUS],
            capture_output=True, text=True, check=False,
        )
        assert json.loads(status.stdout)["loggedIn"] is False
        revokes = [r for r in running.requests() if r["path"] == "/v1/oauth/token/revoke"]
        assert len(revokes) == 1

    # LK-S1: no token, code or email in anything PipesHub logged or would journal or save.
    kept = caplog.text + json.dumps([str(e) for e in events]) + state.payload.decode("utf-8", "replace")
    for secret in ("sk-ant-", GOOD_CODE, "ada@example.com", "FAKEREFRESH", "FAKEACCESS"):
        assert secret not in kept, secret


def _lab_launch(adapter: ClaudeCodeAdapter) -> object:
    profile = EngineProfile(engine=RELEASE.version, mode=EngineMode.BUILD, access=AccessRouteRef("plan", "d1"))
    own = adapter.own_login(adapter.render_launch(profile, ENDPOINTS, ""))
    env = {k: v for k, v in own.env.items() if k.lower() != "https_proxy"}
    return dataclasses.replace(own, env={**env, "NODE_EXTRA_CA_CERTS": "/certs/ca.pem"})


async def test_sl2_two_turns_of_one_conversation_sign_in_once(caplog: pytest.LogCaptureFixture) -> None:
    """SIGNIN-5 on the real binary: turn 1 signs in and runs; the sandbox is parked (every process the turn left
    ended, its own PID 1 spared); turn 2 finds the login by `claude auth status` alone and runs on it, nobody asked
    again; the idle end logs out and wipes `.credentials.json` and `.claude.json` with no `claude` command after."""
    _image_present()
    caplog.set_level(logging.DEBUG)
    adapter = ClaudeCodeAdapter()
    launch = _lab_launch(adapter)
    attempt = Attempt()

    @contextlib.asynccontextmanager
    async def prompt() -> AsyncIterator[Attempt]:
        yield attempt

    async def progress(state: str, reason: str | None, plan: str | None) -> None:
        return

    events: list[object] = []
    with lab(RELEASE.image) as running:
        sandbox = DockerExecSandbox(running.client)
        spare = await adapter.processes(sandbox)  # type: ignore[arg-type]
        assert 1 in spare
        _ = await adapter.sign_in(sandbox, launch, HOSTS, prompt, progress)  # type: ignore[arg-type]
        session = await adapter.open(sandbox, launch, None)  # type: ignore[arg-type]
        events += [e async for e in session.run_turn(EngineTask(turn_id=turn_id_for("run-1", 0), prompt="say hi"))]
        state = await session.capture_state()
        await session.close()
        # Something a turn left running in the background is ended when the sandbox is kept.
        _ = subprocess.run(["docker", "exec", "-d", running.client, "sleep", "600"], check=True)
        assert await adapter.park(sandbox, spare) is True  # type: ignore[arg-type]
        ps = subprocess.run(["docker", "exec", running.client, "sh", "-c", "ls /proc | grep -c '^[0-9]'"],
                            capture_output=True, text=True, check=True)
        assert int(ps.stdout.strip()) <= len(spare) + 2, ps.stdout  # PID 1, and this listing's own shell and ls

        # The conversation's next message: the same sandbox, still signed in, nobody asked.
        account = await adapter.signed_in(sandbox, launch)  # type: ignore[arg-type]
        assert account is not None and account.plan == "max"
        session = await adapter.open(sandbox, launch, state)  # type: ignore[arg-type]
        second = [e async for e in session.run_turn(EngineTask(turn_id=turn_id_for("run-2", 0), prompt="say hi"))]
        events += second
        assert [e.reason for e in second if isinstance(e, TurnCompleted)] == ["completed"]
        await session.close()
        assert len(attempt.links) == 1
        assert len([r for r in running.requests() if r["path"] == "/v1/oauth/token"]) == 1

        # Idle end: logout and wipe, as at a turn's end.
        assert await adapter.park(sandbox, spare) is True  # type: ignore[arg-type]
        await adapter.sign_out(sandbox, launch)  # type: ignore[arg-type]
        left = subprocess.run(["docker", "exec", running.client, "ls", "-A", "/state/claude"],
                              capture_output=True, text=True, check=False).stdout.split()
        assert ".credentials.json" not in left and ".claude.json" not in left, left
        assert len([r for r in running.requests() if r["path"] == "/v1/oauth/token/revoke"]) == 1

    kept = caplog.text + json.dumps([str(e) for e in events]) + state.payload.decode("utf-8", "replace")
    for secret in ("sk-ant-", GOOD_CODE, "ada@example.com", "FAKEREFRESH", "FAKEACCESS"):
        assert secret not in kept, secret


async def test_sl3_a_parked_login_whose_worker_is_gone_is_revoked_by_the_brokers_farewell(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """GAPS-2 on the real binary: a turn signs in and parks the sandbox, then its worker is gone (nothing calls
    ``sign_out``). The broker's own farewell, the very ``run_farewell`` its keep-warm expiry, reaps and orphan sweep
    run before removing the sandbox, logs out at the fake Anthropic (one revoke call) and wipes the login; a second
    farewell finds nothing to do. Nothing of the token, code or email reaches the logs."""
    _image_present()
    caplog.set_level(logging.DEBUG)
    adapter = ClaudeCodeAdapter()
    launch = _lab_launch(adapter)
    attempt = Attempt()

    @contextlib.asynccontextmanager
    async def prompt() -> AsyncIterator[Attempt]:
        yield attempt

    async def progress(state: str, reason: str | None, plan: str | None) -> None:
        return

    # Lab only, as for the launch: the fake is reached directly and trusted through the lab's CA.
    env = {k: v for k, v in FAREWELL.env.items() if k.lower() != "https_proxy"}
    farewell = dataclasses.replace(FAREWELL, env={**env, "NODE_EXTRA_CA_CERTS": "/certs/ca.pem"})
    provider = DockerClientProvider()
    try:
        with lab(RELEASE.image) as running:
            sandbox = DockerExecSandbox(running.client)
            spare = await adapter.processes(sandbox)  # type: ignore[arg-type]
            _ = await adapter.sign_in(sandbox, launch, HOSTS, prompt, progress)  # type: ignore[arg-type]
            assert await adapter.park(sandbox, spare) is True  # type: ignore[arg-type]
            assert not [r for r in running.requests() if r["path"] == "/v1/oauth/token/revoke"]

            assert await run_farewell(provider, running.client, farewell) == "done"
            left = subprocess.run(["docker", "exec", running.client, "ls", "-A", "/state/claude"],
                                  capture_output=True, text=True, check=False).stdout.split()
            assert ".credentials.json" not in left and ".claude.json" not in left, left
            assert await run_farewell(provider, running.client, farewell) == "nothing_to_do"
            seen: list[str] = []
            for _ in range(50):  # the fake appends its request log after it answers
                seen += [r["path"] for r in running.requests()]
                if "/v1/oauth/token/revoke" in seen:
                    break
                await asyncio.sleep(0.1)
            assert seen.count("/v1/oauth/token/revoke") == 1, seen
    finally:
        provider.close()
    for secret in ("sk-ant-", GOOD_CODE, "ada@example.com", "FAKEREFRESH", "FAKEACCESS"):
        assert secret not in caplog.text, secret
