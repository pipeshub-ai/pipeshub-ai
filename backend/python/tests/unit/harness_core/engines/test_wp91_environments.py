"""C20.18 / G-17 (WP-91): environment definitions and their setup, in each session's fresh sandbox.

- WP91-ENV-1: a definition's variables are non-secret and never the harness's own.
- WP91-ENV-2: a repository's ``devcontainer.json`` subset: its env and its three create commands, in order; every
  other key reported, never applied; a substitution from the worker's environment refused.
- WP91-ENV-3: without an ``EnvironmentSnapshotter`` every session runs the setup; the engine starts with the
  environment's variables and the harness keeps its own.
- WP91-ENV-4: a changed definition or a new engine image is another digest.
- WP91-ENV-5: with an ``EnvironmentSnapshotter`` (an extension point), state it restores replaces the setup, and a setup
  that ran is offered to it with the run's secrets and the definition's digest; one with nothing to restore sets up.
- WP91-ENV-6: a failed setup stops the turn before the engine starts.
- WP91-ENV-7: the setup runs before the person's workspace reaches the sandbox (it reads nothing a person wrote).
- WP91-ENV-8: a cache folder that is, or holds, the workspace is refused.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.environments import (
    EnvironmentDefinition,
    EnvironmentInvalid,
    EnvironmentSetupFailed,
    parse_devcontainer,
)
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS
from harness_core.workspace.types import ObjectRef
from tests.unit.harness_core.engines.test_controller import ORG, World, task

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.engines.environments import EnvironmentSnapshotter
    from harness_core.engines.snapshots import SnapshotPolicy
    from harness_core.logs import HarnessLog
    from harness_core.sandbox.ports import SandboxHandle
    from harness_core.testing.sandbox.fake import FakeProcessContext

SETUP = "set -eu\nmkdir -p /tmp/.toolchain && echo built > /tmp/.toolchain/ready\n"
CACHED = "/tmp/.toolchain/ready"


def _definition(**changes: object) -> EnvironmentDefinition:
    fields: dict[str, object] = {
        "environment_id": "python-data",
        "level": "agent",
        "setup": SETUP,
        "env": {"PIP_INDEX_URL": "https://pypi.corp.example/simple", "LANG": "C.UTF-8"},
        "cache_paths": ("/tmp/.toolchain",),
    }
    fields.update(changes)
    return EnvironmentDefinition(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("env", "reason"),
    [
        ({"PATH": "/x"}, "environment_variable_reserved"),
        ({"HTTPS_PROXY": "http://elsewhere"}, "environment_variable_reserved"),
        ({"PXH_ANYTHING": "1"}, "environment_variable_reserved"),
        ({"NODE_EXTRA_CA_CERTS": "/tmp/ca.pem"}, "environment_variable_reserved"),
        ({"GITHUB_TOKEN": "x"}, "environment_variable_secret"),
        ({"DB_PASSWORD": "x"}, "environment_variable_secret"),
        ({"PLAIN": "ghp_" + "a1b2c3d4e5" * 4}, "environment_variable_secret"),
        ({"1BAD": "x"}, "environment_variable_invalid"),
    ],
)
def test_wp91_env_1_variables_are_non_secret_and_never_the_harnesss(env: dict[str, str], reason: str) -> None:
    with pytest.raises(EnvironmentInvalid) as refused:
        _ = _definition(env=env)
    assert refused.value.reason == reason


def test_wp91_env_1_plain_variables_are_kept() -> None:
    assert dict(_definition().env) == {"PIP_INDEX_URL": "https://pypi.corp.example/simple", "LANG": "C.UTF-8"}


def test_wp91_env_2_a_devcontainer_subset_is_read_and_the_rest_reported() -> None:
    document = b"""{
      "name": "widgets",
      "image": "mcr.microsoft.com/devcontainers/python:3.12",
      "features": {"ghcr.io/devcontainers/features/node:1": {}},
      "containerEnv": {"LANG": "C.UTF-8"},
      "remoteEnv": {"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
      "postCreateCommand": "pip download -d /tmp/.toolchain/wheels requests",
      "onCreateCommand": ["python3", "-m", "venv", "/tmp/.toolchain/venv"],
      "updateContentCommand": {"b": "echo two", "a": "echo one"},
      "runArgs": ["--privileged"]
    }"""
    parsed = parse_devcontainer(document, environment_id="widgets", cache_paths=("/tmp/.toolchain",))
    definition = parsed.definition
    assert definition.level == "repository"
    assert dict(definition.env) == {"LANG": "C.UTF-8", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    assert definition.setup is not None
    lines = definition.setup.splitlines()
    assert lines[:2] == ["set -eu", "cd '/workspace'"]
    assert lines[2:] == [
        "'python3' '-m' 'venv' '/tmp/.toolchain/venv'",
        "echo one",
        "echo two",
        "pip download -d /tmp/.toolchain/wheels requests",
    ]
    assert parsed.ignored == ("features", "image", "runArgs")


@pytest.mark.parametrize(
    "document",
    [b'{"containerEnv": {"HOME_DIR": "${localEnv:HOME}"}}', b"[1]", b"not json", b'{"postCreateCommand": 3}'],
)
def test_wp91_env_2_what_the_subset_cannot_read_is_refused(document: bytes) -> None:
    with pytest.raises(EnvironmentInvalid):
        _ = parse_devcontainer(document, environment_id="x")


class EnvWorld:
    """A controller world whose sandboxes run the environment's setup (the fake ``sh`` writes its cache file)."""

    def __init__(self, snapshots: EnvironmentSnapshotter | None = None, *, fail: bool = False) -> None:
        overrides: dict[str, object] = {} if snapshots is None else {"environment_snapshots": snapshots}
        self.world = World(config_overrides=overrides)
        self.setups: list[dict[str, str]] = []
        self.workspace_at_setup: list[list[str]] = []
        shell = FAKE_PROGRAMS["sh"]

        async def sh(context: FakeProcessContext) -> int:
            argv = context.argv
            if len(argv) > 3 and argv[1] == "-c" and argv[3] == "pxh-environment-setup":
                self.setups.append(dict(context.env))
                self.workspace_at_setup.append(sorted(p for p in context.sandbox.files if p.startswith("/workspace/")))
                if fail:
                    await context.emit(b"pip: no matching distribution\n", stderr=True)
                    return 1
                context.write_file(CACHED, b"built\n")
                return 0
            return await shell(context)

        self.world.broker.programs["sh"] = sh

    async def turn(self, definition: EnvironmentDefinition, *, session: str = "session-1", tenant: str = ORG) -> str:
        run = self.world.run(environment=definition, session_id=session, tenant=tenant)
        return (await self.world.controller.run_turn(run, task(0, "say ok"))).reason


KEPT = ObjectRef("kept-1", "sha256:" + "b" * 64, 10)


class _Snapshotter:
    """An ``EnvironmentSnapshotter`` that restores ``CACHED`` once it has kept a setup, and records what it was asked."""

    def __init__(self, *, restores: bool = True) -> None:
        self.restores = restores
        self.kept: list[tuple[str, str, tuple[str, ...], tuple[str, ...]]] = []
        self.restored: list[str] = []

    async def restore(
        self,
        sandbox: SandboxHandle,
        definition: EnvironmentDefinition,
        *,
        tenant: str,
        digest: str,
        policy: SnapshotPolicy,
        log: HarnessLog,
    ) -> ObjectRef | None:
        if not self.restores or not any(entry[:2] == (tenant, digest) for entry in self.kept):
            return None
        self.restored.append(digest)
        return KEPT

    async def keep(
        self,
        sandbox: SandboxHandle,
        definition: EnvironmentDefinition,
        *,
        tenant: str,
        digest: str,
        policy: SnapshotPolicy,
        secrets: Callable[[], tuple[str, ...]],
        log: HarnessLog,
    ) -> ObjectRef | None:
        self.kept.append((tenant, digest, policy.paths, secrets()))
        return KEPT


async def test_wp91_env_3_without_a_snapshotter_every_session_runs_the_setup() -> None:
    first = EnvWorld()
    assert await first.turn(_definition()) == "completed"
    second = EnvWorld()
    assert await second.turn(_definition(), session="session-2") == "completed"
    assert len(first.setups) == len(second.setups) == 1
    assert first.setups[0]["PIP_INDEX_URL"] == "https://pypi.corp.example/simple"
    assert second.world.adapter.files_at_open[-1][CACHED] == b"built\n"  # type: ignore[attr-defined]


async def test_wp91_env_3_the_engine_gets_the_environments_variables_and_the_harness_keeps_its_own() -> None:
    env_world = EnvWorld()
    envs: list[dict[str, str]] = []
    adapter = env_world.world.adapter
    opener = adapter.open

    async def open_(sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        envs.append(dict(launch.env))
        return await opener(sandbox, launch, restore, correlation=correlation)

    adapter.open = open_  # type: ignore[method-assign]
    assert await env_world.turn(_definition()) == "completed"
    [env] = envs
    assert env["PIP_INDEX_URL"] == "https://pypi.corp.example/simple" and env["LANG"] == "C.UTF-8"


def test_wp91_env_4_a_new_engine_image_or_definition_is_another_digest() -> None:
    definition = _definition()
    assert definition.digest("sha256:" + "a" * 64) != definition.digest("sha256:" + "b" * 64)
    assert definition.digest("i") != _definition(setup=SETUP + "echo more\n").digest("i")
    assert definition.digest("i") != _definition(env={"LANG": "en_US.UTF-8"}).digest("i")


async def test_wp91_env_5_a_snapshotter_replaces_the_setup_with_what_it_kept() -> None:
    snapshots = _Snapshotter()
    first = EnvWorld(snapshots)
    assert await first.turn(_definition()) == "completed"
    assert len(first.setups) == 1
    [(tenant, digest, paths, secrets)] = snapshots.kept
    assert tenant == ORG and digest.startswith("sha256:") and paths == ("/tmp/.toolchain",)
    assert secrets, "the run's own placeholders reach the keeper, which never keeps them"
    second = EnvWorld(snapshots)
    assert await second.turn(_definition(), session="session-2") == "completed"
    assert second.setups == [] and snapshots.restored == [digest]


async def test_wp91_env_5_nothing_to_restore_sets_up_and_no_cache_folder_keeps_nothing() -> None:
    snapshots = _Snapshotter(restores=False)
    env_world = EnvWorld(snapshots)
    assert await env_world.turn(_definition()) == "completed"
    assert len(env_world.setups) == 1 and len(snapshots.kept) == 1
    bare = EnvWorld(snapshots)
    assert await bare.turn(_definition(cache_paths=())) == "completed"
    assert len(bare.setups) == 1 and len(snapshots.kept) == 1


async def test_wp91_env_6_a_failed_setup_stops_the_turn_before_the_engine() -> None:
    snapshots = _Snapshotter()
    env_world = EnvWorld(snapshots, fail=True)
    with pytest.raises(EnvironmentSetupFailed) as failed:
        _ = await env_world.turn(_definition())
    assert failed.value.reason_code == "environment_setup_failed"
    assert env_world.world.adapter.files_at_open == []  # type: ignore[attr-defined]
    assert len(env_world.world.broker.released) == 1
    assert snapshots.kept == []


async def test_wp91_env_7_the_setup_runs_before_the_workspace_reaches_the_sandbox() -> None:
    env_world = EnvWorld()
    await env_world.world.seed({"notes.md": b"the person's file"})
    assert await env_world.turn(_definition()) == "completed"
    assert env_world.workspace_at_setup == [[]]
    assert env_world.world.adapter.files_at_open[-1]["/workspace/notes.md"] == b"the person's file"  # type: ignore[attr-defined]


@pytest.mark.parametrize("path", ["/workspace", "/workspace/cache"])
async def test_wp91_env_8_a_cache_folder_over_the_workspace_is_refused(path: str) -> None:
    env_world = EnvWorld()
    with pytest.raises(EnvironmentInvalid) as refused:
        _ = await env_world.turn(_definition(cache_paths=(path,)))
    assert refused.value.reason == "environment_cache_invalid"
