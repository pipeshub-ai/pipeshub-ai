"""Recommendations 1, 3 and 4 (recs audit) on a real engine: opencode on its pinned binary in the release image,
under the Docker broker, behind the real egress proxy, against the real HTTP gateways (E0's real lane).

* R1: while a turn is held mid-flight, the engine container was created with no environment (``docker inspect``),
  and nothing a sandbox process can read (its environment, command line or any writable file) holds the
  provider's key, the worker's credential, the proxy's control token or a gateway token the proxy was issued.
* R3: every engine run image ships ``ph`` and a versioned ``/pipeshub`` folder (a call through it per engine:
  ``ph_e2e`` in each engine's controller lane).
* R4: every model request of a session carries the same ``tools`` array, byte for byte (names, descriptions,
  schemas and order), across tool calls, the next turn restored from the engine's checkpoint, and a turn
  re-driven on a new sandbox after the first one was killed.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import tarfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.engines.events import turn_id_for
from harness_core.engines.types import FollowUp
from harness_core.security.token_patterns import find_forbidden
from tests.support.sandbox_confinement import exec_in
from tests.unit.harness.engines.matrix.confinement import (
    _DUMP,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness.engines.real_stack import (
    PROVIDER_KEY,
    STATIC_MODEL_KEY,
    WORKER_CREDENTIAL,
)
from tests.unit.harness.engines.test_real_controller import RUN, Lane, task

if TYPE_CHECKING:
    from typing import Any

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]

# Recommendation 3's shell entry point and versioned tool folder: in every run image.
PH_PATHS = (
    "/usr/local/bin/ph", "/pipeshub/VERSION", "/pipeshub/AGENTS.md", "/pipeshub/README.md", "/pipeshub/tools.txt",
    "/pipeshub/tools", "/pipeshub/skills", "/pipeshub/bin/ph", "/pipeshub/inputs", "/pipeshub/outputs",
    "/pipeshub/workspace",
)


def _member(archive: bytes) -> tarfile.TarInfo:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        return tar.getmembers()[0]


def _read(archive: bytes) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        member = tar.getmembers()[0]
        extracted = tar.extractfile(member)
        assert extracted is not None, member.name
        return extracted.read()


@pytest.mark.parametrize("kind", ["codex", "claude_code", "opencode"])
def test_r3_each_engine_image_ships_ph_and_a_versioned_pipeshub_folder(kind: str) -> None:
    """``ph`` is executable, ``/pipeshub/VERSION`` is the contract the worker writes, AGENTS.md is the package's, the
    index, the tools folder and skills are links to what each turn writes (never a list baked into the image), and the
    contract v1 paths are there (WS-01). ``ph_e2e`` runs a call through it per engine."""
    import docker
    import docker.errors

    from harness_engines import pipeshub
    from harness_engines.pipeshub import VERSION
    from harness_engines.release import release_allowlist

    image = next(r.image for r in release_allowlist() if r.version.kind == kind)
    client = docker.from_env()
    try:
        client.images.get(image)
    except docker.errors.ImageNotFound:
        client.close()
        pytest.skip(f"{image} is not built (deployment/sandbox-images/pipeshub/build.sh)")
    container = client.containers.create(image, name=f"pxh-recs-r3-{uuid.uuid4().hex[:8]}", network_disabled=True)
    try:
        found: dict[str, bytes] = {}
        missing = []
        for path in PH_PATHS:
            try:
                stream, _ = container.get_archive(path)
                found[path] = b"".join(stream)
            except docker.errors.NotFound:
                missing.append(path)
        assert missing == [], f"{image} lacks {missing}"
        assert _member(found["/usr/local/bin/ph"]).mode & 0o111
        assert _read(found["/pipeshub/VERSION"]).decode().strip() == VERSION
        # The sandbox user (uid 1000) can enter the folder and read what is in it.
        listing = container.get_archive("/pipeshub")[1]
        assert int(listing["mode"]) & 0o755 == 0o755, oct(int(listing["mode"]))
        assert _member(found["/pipeshub/tools.txt"]).linkname == "session/tools.txt"
        assert _member(found["/pipeshub/skills"]).linkname == "/skills"
        assert _member(found["/pipeshub/tools"]).linkname == "session/tools"
        assert _member(found["/pipeshub/bin/ph"]).linkname == "/usr/local/bin/ph"
        assert _member(found["/pipeshub/README.md"]).linkname == "AGENTS.md"
        assert (_member(found["/pipeshub/inputs"]).linkname, _member(found["/pipeshub/outputs"]).linkname) == (
            "/workspace/in",
            "/workspace/out",
        )
        assert _read(found["/pipeshub/AGENTS.md"]) == (Path(pipeshub.__file__).parent / "AGENTS.md").read_bytes()
    finally:
        container.remove(force=True)
        client.close()


@pytest.fixture
def lane(stack: RealStack, broker: DockerSandboxBroker) -> Lane:
    return Lane(stack, broker)


def _sandbox(lane: Lane, attempt: int) -> dict[str, Any]:
    api = lane.stack.client.api
    (found,) = api.containers(filters={"label": [f"attemptId={RUN}.a{attempt}", f"pxh.lane={lane.stack.suffix}"]})
    return api.inspect_container(found["Id"])


async def test_r1_a_real_engine_mid_turn_holds_no_secret_in_its_container_processes_or_files(lane: Lane) -> None:
    held = asyncio.ensure_future(
        lane.controller.run_turn(lane.run(1), task(0, 'R1\ntool search {"q": "q3"}\nhold\nsay done'))
    )
    model = lane.stack.models.gateways[STATIC_MODEL_KEY]
    try:
        await asyncio.wait_for(model.holding.wait(), 300)
        sandbox = _sandbox(lane, 1)
        dump = await asyncio.to_thread(exec_in, lane.stack.client, str(sandbox["Id"]), _DUMP)
    finally:
        model.release()
    outcome = await asyncio.wait_for(held, 300)
    assert (outcome.reason, outcome.final_text.strip()) == ("completed", "done")

    issued = list(lane.stack.models.issued)
    assert issued, "the proxy fetched no gateway token: the tool call did not go through it"
    secrets = [PROVIDER_KEY, WORKER_CREDENTIAL, lane.stack.control_token, *issued]
    # The container is created bare: placeholders reach the engine per exec, never through the container's config.
    env = sandbox["Config"]["Env"] or []
    assert not [e for e in env if e.startswith(("HARNESS_", "OPENCODE_", "ANTHROPIC_", "OPENAI_"))], env
    config = repr(sandbox["Config"]) + repr(sandbox["HostConfig"])
    assert [s[:6] for s in secrets if s in config] == []
    assert [s[:6] for s in secrets if s in dump] == []
    assert not find_forbidden(dump)
    # Positive control: the dump did read the live engine's environment, which holds its placeholders only.
    assert "HARNESS_GATEWAY_TOKEN=pxh-model-" in dump and "HARNESS_TOOLS_TOKEN=pxh-tools-" in dump


async def test_r4_a_sessions_tool_list_is_byte_identical_across_calls_resume_and_a_new_sandbox(lane: Lane) -> None:
    lists = lane.stack.models.tool_lists.setdefault(STATIC_MODEL_KEY, [])
    lists.clear()
    first = await lane.controller.run_turn(
        lane.run(1), task(0, 'R4\ntool search {"q": "q3"}\nshell echo kept > kept.txt\nsay one')
    )
    assert (first.reason, first.checkpoint is not None) == ("completed", True)
    after_first = len(lists)
    assert after_first >= 3, lists  # the tool call, the shell call and the answer

    second = await lane.controller.run_turn(
        lane.run(2, restore=first.checkpoint), FollowUp(turn_id_for(RUN, 1), 'tool search {"q": "q4"}\nsay two')
    )
    assert (second.reason, second.restored) == ("completed", "state")
    after_second = len(lists)
    assert second.checkpoint is not None

    crashed = asyncio.ensure_future(
        lane.controller.run_turn(lane.run(3, restore=second.checkpoint), task(2, "shell echo x\nhold\nsay never"))
    )
    model = lane.stack.models.gateways[STATIC_MODEL_KEY]
    await asyncio.wait_for(model.holding.wait(), 300)
    assert lane.kill(3) == 1
    with contextlib.suppress(Exception):
        _ = await asyncio.wait_for(crashed, 300)
    model.release()
    redriven = await lane.controller.run_turn(lane.run(4, restore=second.checkpoint), task(2, "say three"))
    assert (redriven.reason, redriven.restored) == ("completed", "state")

    assert after_first < after_second < len(lists)
    assert len(set(lists)) == 1, {i: digest[:12] for i, digest in enumerate(lists)}
    # The list the model saw names the run's granted tool through the gateway, every time.
    calls = lane.gateways[1].model_calls
    assert all(any(name.endswith("search") for name in call.tools) for call in calls), [c.tools for c in calls]
