"""recs2 item 2: can each engine's own sandbox be a second layer inside PipesHub's engine sandbox? Real binaries.

Each case runs in a sandbox the real Docker broker provisions (runc, hardened as production: read-only root, every
capability dropped, ``no-new-privileges``, uid 1000, Docker's default seccomp profile), from the release images
already on the host. The outer layer lets the engine user write outside the workspace (``/state``, ``/tmp``): that
is what a second layer would have to refuse on its own.

* **Codex 0.160.0: cannot.** Any filesystem or network restriction runs its Linux sandbox helper, which needs
  bubblewrap: with the legacy Landlock path switched on it still panics "filesystem-restricted execution requires
  bubblewrap to isolate app-server sockets". Bubblewrap is not in the image, and it needs an unprivileged user
  namespace, which the hardened sandbox refuses (``unshare -U`` is ``EPERM`` under Docker's default seccomp profile
  with every capability dropped). Granting one means a custom seccomp profile that allows ``CLONE_NEWUSER`` in a
  sandbox that runs model-written code: the kernel attack surface the outer layer exists to close. The only policies
  that start (``externalSandbox``, ``danger-full-access``) restrict nothing, so ``danger-full-access`` stays.
* **Claude Code 2.1.294: cannot.** Its sandbox needs bubblewrap and socat on Linux; with
  ``sandbox.failIfUnavailable`` it refuses to start ("sandbox required but unavailable"), weaker nested mode
  included. Bubblewrap would need the same user namespace.
* **opencode 1.18.34: yes, for its own tools.** It has no OS sandbox; its permission rules are its layer. Its file
  tools refuse a path outside the workspace (``external_directory`` deny) although the sandbox lets the user write
  there, and it offers no network tool (``webfetch`` and ``websearch`` fall under ``"*": "deny"``). Shell commands
  are not confined by opencode: the outer layer (egress ``none`` behind the proxy) confines them.

The skipped cases state the second layer the design asks for; they run the day the pins below stop holding.

**Under gVisor** (R31GVISO-FU-6, R32-SECCOMP) each Codex and Claude Code case runs on runsc too, as an engine does in
production. Without ``--oci-seccomp`` gVisor lets the sandbox user make a user namespace inside its own kernel
(``unshare -U`` succeeds), yet neither engine's sandbox starts: bubblewrap is not in either image, so the refusal is
the same. With bubblewrap added to a throwaway Codex image, Codex's ``workspaceWrite`` sandbox does start there and
refuses a write outside the workspace; on runc, and under gVisor with ``--oci-seccomp`` (the installer's default since
R32-SECCOMP), bubblewrap gets no namespace. The decision, recorded in ADR-OWD-03: engines run without their own
sandbox on both runtimes, and gVisor's in-guest syscall filter is the second layer; the outer sandbox is the boundary.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from harness_core.ids import RandomIds
from harness_core.sandbox.types import (
    LeaseRef,
    MountSpec,
    Resources,
    RuntimeMode,
    SandboxSpec,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.contract import run
from tests.unit.harness.engines.lane_releases import lane_release
from tests.unit.harness.engines.real_stack import STATIC_MODEL_KEY, engine_broker
from tests.unit.harness.engines.test_real_controller import RUN, Lane, task
from tests.unit.harness.sandbox_broker.conftest import cleanup

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxHandle
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

LEASE = LeaseRef("worker-recs2", 1)
OUTSIDE = "/state/outside.txt"
CODEX_BUBBLEWRAP = "requires bubblewrap"
CLAUDE_REFUSAL = "refusing to start without a working sandbox"
# Drives codex-app-server over stdio: initialize, then each argv[2:] command through `command/exec` under the
# sandbox policy in argv[1], printing one JSON result per line.
CODEX_DRIVER = r"""
import json, os, subprocess, sys
home = "/state/codex"
os.makedirs(home, exist_ok=True)
open(home + "/config.toml", "w").write("[features]\nuse_legacy_landlock = true\n")
p = subprocess.Popen(["/usr/local/bin/codex-app-server", "--listen", "stdio://"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=dict(os.environ, CODEX_HOME=home), text=True)
def call(i, method, params):
    p.stdin.write(json.dumps({"id": i, "method": method, "params": params}) + "\n"); p.stdin.flush()
    while True:
        m = json.loads(p.stdout.readline())
        if m.get("id") == i:
            return m
call(1, "initialize", {"clientInfo": {"name": "harness", "version": "0"}})
p.stdin.write(json.dumps({"method": "initialized"}) + "\n"); p.stdin.flush()
for n, cmd in enumerate(sys.argv[2:], start=2):
    r = call(n, "command/exec", {"command": ["sh", "-c", cmd], "cwd": "/workspace", "sandboxPolicy": json.loads(sys.argv[1])})
    print(json.dumps(r.get("result") or r.get("error")))
p.terminate()
"""
WORKSPACE_WRITE = {
    "type": "workspaceWrite", "writableRoots": [], "networkAccess": False,
    "excludeTmpdirEnvVar": True, "excludeSlashTmp": True,
}


@pytest.fixture(scope="module")
def provider() -> Iterator[DockerClientProvider]:
    docker_provider = DockerClientProvider(max_workers=8)
    try:
        docker_provider.client.ping()
    except Exception as exc:
        pytest.skip(f"needs a Docker daemon: {type(exc).__name__}")
    yield docker_provider
    docker_provider.close()


def _image(provider: DockerClientProvider, kind: str) -> str:
    image = lane_release(kind).image
    try:
        provider.client.images.get(image)
    except Exception:
        pytest.skip(f"needs the {image} image")
    return image


@pytest.fixture(params=[RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR], ids=["runc", "gvisor"])
def mode(request: pytest.FixtureRequest) -> RuntimeMode:
    return request.param


@pytest.fixture
async def broker(provider: DockerClientProvider, mode: RuntimeMode) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.recs2sb.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-recs2sb-" + namespace.rsplit(".", 1)[1]
    created = await DockerSandboxBroker.create(
        provider=provider,
        clock=ManualClock(),
        ids=RandomIds(),
        allowed_images=frozenset(lane_release(kind).image for kind in ("codex", "claude_code")),
        label_namespace=namespace,
        volume_prefix=prefix,
    )
    if mode not in created.modes():
        pytest.skip(f"this Docker daemon offers no {mode.value}")
    yield created
    await provider.run_blocking(cleanup, provider, namespace, prefix)


def _filtered(broker: DockerSandboxBroker, mode: RuntimeMode) -> bool:
    """Docker's seccomp profile reaches the engine user: always on runc, under gVisor only with ``--oci-seccomp``."""
    return mode is RuntimeMode.DOCKER_HARDENED or broker.capabilities.runsc_oci_seccomp


async def _sandbox(broker: DockerSandboxBroker, image: str, mode: RuntimeMode = RuntimeMode.DOCKER_HARDENED) -> DockerSandboxHandle:
    spec = SandboxSpec(
        image_digest=image,
        mode=mode,
        resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=64),
        mounts=(MountSpec("/workspace"), MountSpec("/state")),
        labels={"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "recs2"},
    )
    return await broker.acquire(spec, lease=LEASE, idempotency_key=f"recs2-{uuid.uuid4().hex}")


async def _sh(handle: DockerSandboxHandle, *argv: str, env: dict[str, str] | None = None) -> tuple[str, str, int]:
    out, err, code = await run(await handle.exec(list(argv), env=env or {"HOME": "/tmp"}), timeout_s=120)
    return out.decode(), err.decode(), code


async def _outer_layer_allows_writing_outside_the_workspace(handle: DockerSandboxHandle) -> None:
    out, _, code = await _sh(handle, "sh", "-c", f"echo outer > {OUTSIDE} && cat {OUTSIDE}")
    assert (code, out.strip()) == (0, "outer"), "the positive control: only an engine's own layer could refuse it"


async def _user_namespaces(handle: DockerSandboxHandle, *, refused: bool) -> None:
    """The engine user's unprivileged user namespace: refused under Docker's profile, made inside gVisor's own kernel
    without it. Either way bubblewrap, which each engine's sandbox needs, is not in the image."""
    _, err, code = await _sh(handle, "unshare", "-U", "-r", "true")
    if refused:
        assert code != 0 and "Operation not permitted" in err, (code, err)
    else:
        assert code == 0, err
    _, _, found = await _sh(handle, "sh", "-c", "command -v bwrap")
    assert found != 0, "bubblewrap is in the image: rerun the second-layer cases and revisit ADR-OWD-03's decision"


async def _codex(handle: DockerSandboxHandle, policy: dict[str, object], *commands: str) -> list[dict[str, object]]:
    out, err, code = await _sh(handle, "python3", "-c", CODEX_DRIVER, json.dumps(policy), *commands)
    assert code == 0, err
    return [json.loads(line) for line in out.splitlines() if line.strip()]


async def _codex_probe(
    broker: DockerSandboxBroker, image: str, mode: RuntimeMode
) -> tuple[DockerSandboxHandle, list[dict[str, object]]]:
    handle = await _sandbox(broker, image, mode)
    return handle, await _codex(handle, WORKSPACE_WRITE, "echo in > /workspace/in.txt", f"echo out > {OUTSIDE}")


async def test_recs2_codex_own_sandbox_cannot_start_inside_the_engine_sandbox(
    provider: DockerClientProvider, broker: DockerSandboxBroker, mode: RuntimeMode
) -> None:
    """Pin: fails the day Codex's sandbox starts here, so the skipped second-layer case below is revisited."""
    handle, probed = await _codex_probe(broker, _image(provider, "codex"), mode)
    try:
        await _outer_layer_allows_writing_outside_the_workspace(handle)
        await _user_namespaces(handle, refused=_filtered(broker, mode))
        assert [r["exitCode"] for r in probed] == [101, 101], probed
        assert all(CODEX_BUBBLEWRAP in str(r["stderr"]) for r in probed), probed
        # What does start applies nothing: the write outside the workspace goes through.
        (external,) = await _codex(
            handle, {"type": "externalSandbox", "networkAccess": "restricted"}, f"echo ext > {OUTSIDE} && cat {OUTSIDE}"
        )
        assert (external["exitCode"], str(external["stdout"]).strip()) == (0, "ext")
    finally:
        await broker.release(handle)


async def test_recs2_codex_refuses_a_write_outside_the_workspace_and_a_network_call_by_itself(
    provider: DockerClientProvider, broker: DockerSandboxBroker, mode: RuntimeMode
) -> None:
    handle, probed = await _codex_probe(broker, _image(provider, "codex"), mode)
    try:
        if any(CODEX_BUBBLEWRAP in str(r.get("stderr")) for r in probed):
            pytest.skip(
                "Codex 0.160's own sandbox needs bubblewrap and an unprivileged user namespace, which the hardened "
                "engine sandbox refuses (Docker's default seccomp profile, every capability dropped; under gVisor "
                "with --oci-seccomp too, and without it bubblewrap is still not in the image); allowing one would "
                "widen the outer boundary. The outer sandbox is the boundary (see the module docstring)."
            )
        inside, outside = probed
        assert inside["exitCode"] == 0 and outside["exitCode"] != 0, probed
        (network,) = await _codex(
            handle, WORKSPACE_WRITE, "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\""
        )
        assert network["exitCode"] != 0
    finally:
        await broker.release(handle)


CLAUDE_SETTINGS = {"sandbox": {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False}}


async def _claude_with_its_sandbox(handle: DockerSandboxHandle) -> tuple[str, int]:
    settings = json.dumps(CLAUDE_SETTINGS)
    _, err, code = await _sh(
        handle, "claude", "-p", "--settings", settings, "--output-format", "text", "say hi",
        env={"HOME": "/tmp", "CLAUDE_CONFIG_DIR": "/tmp/cc", "ANTHROPIC_API_KEY": "sk-placeholder"},
    )
    return err, code


async def test_recs2_claude_code_refuses_to_start_with_its_own_sandbox_inside_the_engine_sandbox(
    provider: DockerClientProvider, broker: DockerSandboxBroker, mode: RuntimeMode
) -> None:
    """Pin, as for Codex: Claude Code's sandbox needs bubblewrap and socat, and bubblewrap a user namespace."""
    handle = await _sandbox(broker, _image(provider, "claude_code"), mode)
    try:
        await _outer_layer_allows_writing_outside_the_workspace(handle)
        await _user_namespaces(handle, refused=_filtered(broker, mode))
        err, code = await _claude_with_its_sandbox(handle)
        assert code != 0 and CLAUDE_REFUSAL in err, err
        assert "bubblewrap" in err
    finally:
        await broker.release(handle)


async def test_recs2_claude_code_refuses_a_write_outside_the_workspace_and_a_network_call_by_itself(
    provider: DockerClientProvider, broker: DockerSandboxBroker, mode: RuntimeMode
) -> None:
    handle = await _sandbox(broker, _image(provider, "claude_code"), mode)
    try:
        err, code = await _claude_with_its_sandbox(handle)
    finally:
        await broker.release(handle)
    if code != 0 and CLAUDE_REFUSAL in err:
        pytest.skip(
            "Claude Code's own sandbox (2.1.294, 2.1.319) needs bubblewrap and socat (not in the image) and bubblewrap an "
            "unprivileged user namespace, which the hardened engine sandbox refuses (gVisor too with --oci-seccomp); "
            "the outer sandbox is the boundary"
        )
    pytest.fail("Claude Code's sandbox started here: write the second-layer case with a scripted model and drop the pin")


@pytest.fixture
def lane(stack: RealStack, broker_opencode: DockerSandboxBroker) -> Lane:
    return Lane(stack, broker_opencode)


@pytest.fixture
async def broker_opencode(stack: RealStack) -> AsyncIterator[DockerSandboxBroker]:
    namespace = f"io.pipeshub.recs2oc.{uuid.uuid4().hex[:8]}"
    prefix = "pxh-recs2oc-" + namespace.rsplit(".", 1)[1]
    created, provider = await engine_broker(stack, namespace, prefix)
    yield created
    await provider.run_blocking(cleanup, provider, namespace, prefix)
    provider.close()


def _container(lane: Lane, attempt: int) -> str:
    api = lane.stack.client.api
    (found,) = api.containers(filters={"label": [f"attemptId={RUN}.a{attempt}", f"pxh.lane={lane.stack.suffix}"]})
    return str(found["Id"])


async def test_recs2_opencode_refuses_a_write_outside_the_workspace_with_its_own_permission_rules(lane: Lane) -> None:
    from tests.support.sandbox_confinement import exec_in

    escape = "/tmp/recs2-escape.txt"
    held = asyncio.ensure_future(
        lane.controller.run_turn(lane.run(1), task(0, f"SB\nwrite {escape} escaped\nwrite kept.txt kept\nhold\nsay done"))
    )
    model = lane.stack.models.gateways[STATIC_MODEL_KEY]
    try:
        await asyncio.wait_for(model.holding.wait(), 300)
        container = _container(lane, 1)
        listing = await asyncio.to_thread(
            exec_in, lane.stack.client, container,
            f"test -e {escape} && echo LEAKED; test -e /workspace/kept.txt && echo KEPT; "
            "echo outer > /tmp/recs2-outer.txt && cat /tmp/recs2-outer.txt",
        )
    finally:
        model.release()
    outcome = await asyncio.wait_for(held, 300)
    assert outcome.reason == "completed"
    assert "LEAKED" not in listing and "KEPT" in listing, listing
    assert "outer" in listing, "the positive control: the sandbox itself lets the engine user write there"
    results = [str(m.content) for m in lane.gateways[1].model_calls[-1].messages if m.role == "tool"]
    assert any("external_directory" in r or "permission" in r.lower() or "denied" in r.lower() for r in results), results
    offered = {name for call in lane.gateways[1].model_calls for name in call.tools}
    assert not offered & {"webfetch", "websearch"}, "opencode offers no network tool: '*': 'deny' covers them"


def test_recs2_opencode_confines_shell_network_calls_by_itself() -> None:
    pytest.skip(
        "opencode 1.18.34 has no OS sandbox: its permission rules gate its own tools, not what a shell command does. "
        "A shell's network call is confined by the outer layer only (egress none behind the proxy)."
    )


async def test_recs2_a_broker_without_an_egress_proxy_says_engines_have_no_sandbox(broker: DockerSandboxBroker) -> None:
    """recs2 item 5: Helm's broker has no egress proxy (``deployment/helm/tests/sandbox_topology.py`` pins it), so
    its health says engines have no sandbox, and Node disables every engine with ``ENGINE_SANDBOX_MISSING``."""
    from harness_core.sandbox.types import ENGINE_SANDBOX, ENGINE_SANDBOX_MISSING

    assert (await broker.health()).detail[ENGINE_SANDBOX] == ENGINE_SANDBOX_MISSING


async def test_recs2_a_broker_with_an_egress_proxy_says_engines_have_their_sandbox(
    broker_opencode: DockerSandboxBroker,
) -> None:
    from harness_core.sandbox.types import ENGINE_SANDBOX, ENGINE_SANDBOX_READY

    assert (await broker_opencode.health()).detail[ENGINE_SANDBOX] == ENGINE_SANDBOX_READY
