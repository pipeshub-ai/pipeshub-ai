"""The controller mounts the shared caches the ``SharedCacheMounts`` extension point answers, and nothing without it.

- CACHE-PORT-1: without the port a run whose environment names caches mounts none and runs.
- CACHE-PORT-2: with it, each mount it answers is in the sandbox's spec, read-only; what is asked is the run's pinned
  environment's members this build does not read, for the run's organisation; the variables ``verify`` answers reach the engine, the harness's own still win.
- CACHE-PORT-3: a ``verify`` that refuses stops the turn before the engine starts.
- CACHE-PORT-4: a mount is read-only by construction; git configuration is joined in mount order.
- WP100-FU-8: a mount's ``insteadOf`` rule rewrites its own repository only (git matches the longest prefix), and the
  anchored rules reach the session's git through one included file the controller writes, never the environment.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING, cast

import pytest

from harness_core.engines.cache_mounts import (
    MIRRORS_GITCONFIG,
    CacheMount,
    GitConfig,
    mirrors_gitconfig,
    mounts_git_config,
    with_mounts,
)
from harness_core.engines.errors import EngineRefusal
from harness_core.engines.git_checkout import git_config_env
from harness_core.sandbox.types import MountSpec, RuntimeMode, SandboxSpec
from tests.unit.harness_core.engines.test_controller import ORG, World, task

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from pydantic import JsonValue

    from harness_core.logs import HarnessLog
    from harness_core.sandbox.ports import SandboxHandle

# The member an edition's overlay adds to the pinned environment, naming the caches its port mounts.
PART = "editionPart"
PYPI = CacheMount("pypi", MountSpec("/opt/caches/pypi", source=f"{ORG}/pypi", read_only=True))


class _Mounts:
    def __init__(self, *, refuse: bool = False, answer: tuple[CacheMount, ...] = (PYPI,)) -> None:
        self.refuse = refuse
        self.answer = answer
        self.asked: list[tuple[str, tuple[str, ...]]] = []
        self.verified: list[tuple[str, ...]] = []

    async def mounts(self, tenant: str, environment: Mapping[str, JsonValue]) -> Sequence[CacheMount]:
        names = tuple(cast("list[str]", environment.get(PART) or []))
        self.asked.append((tenant, names))
        return list(self.answer) if "pypi" in names else []

    async def verify(self, sandbox: SandboxHandle, mounts: Sequence[CacheMount], log: HarnessLog) -> Mapping[str, str]:
        self.verified.append(tuple(m.name for m in mounts))
        if self.refuse:
            raise EngineRefusal("the pypi cache does not hold what was recorded", reason="cache_unverified")
        return {"PIP_FIND_LINKS": "/opt/caches/pypi", "FAKE_ENGINE_TOKEN": "from-the-cache"}


def _cache_targets(world: World) -> list[tuple[str, bool]]:
    [(spec, _)] = world.broker.acquired
    return [(m.target, m.read_only) for m in spec.mounts if m.target.startswith("/opt/caches")]


async def test_cache_port_1_without_the_port_no_cache_is_mounted() -> None:
    world = World()
    run = world.run(environment_extension={PART: ["pypi"]})
    assert (await world.controller.run_turn(run, task(0, "say ok"))).reason == "completed"
    assert _cache_targets(world) == []


async def test_cache_port_2_answered_mounts_are_read_only_and_their_variables_reach_the_engine() -> None:
    mounts = _Mounts()
    world = World(config_overrides={"shared_caches": mounts})
    envs: list[dict[str, str]] = []
    opener = world.adapter.open

    async def open_(sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        envs.append(dict(launch.env))
        return await opener(sandbox, launch, restore, correlation=correlation)

    world.adapter.open = open_  # type: ignore[method-assign]
    run = world.run(environment_extension={PART: ["pypi", "npm"]})
    assert (await world.controller.run_turn(run, task(0, "say ok"))).reason == "completed"
    assert mounts.asked == [(ORG, ("pypi", "npm"))] and mounts.verified == [("pypi",)]
    assert _cache_targets(world) == [("/opt/caches/pypi", True)]
    [env] = envs
    assert env["PIP_FIND_LINKS"] == "/opt/caches/pypi" and env["FAKE_ENGINE_TOKEN"] != "from-the-cache"


async def test_cache_port_2_a_run_naming_no_cache_never_asks() -> None:
    mounts = _Mounts()
    world = World(config_overrides={"shared_caches": mounts})
    assert (await world.controller.run_turn(world.run(), task(0, "say ok"))).reason == "completed"
    assert mounts.asked == [] and mounts.verified == []


async def test_cache_port_3_a_refused_verify_stops_the_turn_before_the_engine() -> None:
    world = World(config_overrides={"shared_caches": _Mounts(refuse=True)})
    with pytest.raises(EngineRefusal) as refused:
        _ = await world.controller.run_turn(world.run(environment_extension={PART: ["pypi"]}), task(0, "say ok"))
    assert refused.value.reason_code == "cache_unverified"
    assert world.adapter.files_at_open == []  # type: ignore[attr-defined]


def test_cache_port_4_a_mount_is_read_only_and_git_config_keeps_mount_order() -> None:
    with pytest.raises(ValueError, match="read-only"):
        _ = CacheMount("x", MountSpec("/opt/caches/x", source="s", read_only=False))
    first = CacheMount("a", MountSpec("/opt/caches/a", source="a", read_only=True), (("k1", "v1"),))
    second = CacheMount("b", MountSpec("/opt/caches/b", source="b", read_only=True), (("k2", "v2"), ("k3", "v3")))
    assert mounts_git_config([first, second]) == (("k1", "v1"), ("k2", "v2"), ("k3", "v3"))
    assert mounts_git_config([]) == ()
    base = SandboxSpec(image_digest="img", mode=RuntimeMode.DOCKER_HARDENED, mounts=(MountSpec("/workspace"),))
    assert [m.target for m in with_mounts(base, [first, second]).mounts] == ["/workspace", "/opt/caches/a", "/opt/caches/b"]


# --- WP100-FU-8: a mount's rule rewrites its own repository only ----------------------------------------------------

_LOCAL = "file:///opt/caches/mirror/github.com/acme"


def _mirror_rules(*repositories: str) -> GitConfig:
    """What a git mirror mount supplies for each repository: both spellings fetch from it, pushes stay put."""
    pairs: list[tuple[str, str]] = []
    for repository in repositories:
        canonical = f"https://github.com/acme/{repository}"
        mirror = f"{_LOCAL}/{repository}.git"
        pairs += [
            (f"url.{mirror}.insteadOf", f"{canonical}.git"),
            (f"url.{mirror}.insteadOf", canonical),
            (f"url.{canonical}.pushInsteadOf", canonical),
        ]
    return tuple(pairs)


def _rewritten(tmp_path: Path, config: bytes, url: str, *, push: bool = False) -> str:
    """What the engine's git makes of ``url`` (fetch or push), reading the rules as the controller hands them over: a
    file the session's environment includes."""
    rules = tmp_path / "mirrors.gitconfig"
    rules.write_bytes(config)
    repo = tmp_path / "repo"
    if not repo.exists():
        _ = subprocess.run(["git", "init", "-q", str(repo)], check=True)
    env = {
        **os.environ,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        **git_config_env((("include.path", str(rules)),)),
    }
    _ = subprocess.run(["git", "-C", str(repo), "remote", "remove", "x"], env=env, capture_output=True, check=False)
    _ = subprocess.run(["git", "-C", str(repo), "remote", "add", "x", url], env=env, check=True)
    args = ["git", "-C", str(repo), "remote", "get-url", *(["--push"] if push else []), "x"]
    return subprocess.run(args, env=env, capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_wp100_fu8_a_rule_for_o_r_rewrites_o_r_and_never_o_r2(tmp_path: Path) -> None:
    """git's ``insteadOf`` is a longest-prefix match: a rule for ``acme/widgets`` rewrote ``acme/widgets2`` to
    ``…/widgets.git2``, a folder that does not exist, so that repository's clone failed. Each rule is anchored now."""
    config = mirrors_gitconfig(_mirror_rules("widgets"))
    local = f"{_LOCAL}/widgets.git"
    for own in ("https://github.com/acme/widgets", "https://github.com/acme/widgets.git"):
        assert _rewritten(tmp_path, config, own) == local
        assert _rewritten(tmp_path, config, own, push=True) == own, "pushes stay on the repository"
    for other in (
        "https://github.com/acme/widgets2",
        "https://github.com/acme/widgets-cli.git",
        "https://github.com/acme/widgets.js",
        "https://github.com/acme/widgets.github.io",
        "https://github.com/acme/widgets_",
    ):
        assert _rewritten(tmp_path, config, other) == other
        assert _rewritten(tmp_path, config, other, push=True) == other


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_wp100_fu8_two_rules_whose_names_share_a_prefix_each_keep_their_own(tmp_path: Path) -> None:
    config = mirrors_gitconfig(_mirror_rules("w", "w2", "w.js"))
    assert _rewritten(tmp_path, config, "https://github.com/acme/w") == f"{_LOCAL}/w.git"
    assert _rewritten(tmp_path, config, "https://github.com/acme/w2.git") == f"{_LOCAL}/w2.git"
    assert _rewritten(tmp_path, config, "https://github.com/acme/w.js") == f"{_LOCAL}/w.js.git"
    assert _rewritten(tmp_path, config, "https://github.com/acme/w3") == "https://github.com/acme/w3"


async def test_wp100_fu8_the_sessions_git_reads_the_anchored_rules_from_one_included_file() -> None:
    """The anchored rules are too many for the environment (each gains a guard per character a name can continue
    with): the controller writes them to the sandbox's state folder and the session's git includes that one file; a
    session whose mounts carry no rule gets neither."""
    rules = _mirror_rules("widgets")
    mirror = CacheMount("pypi", PYPI.mount, rules)
    world = World(config_overrides={"shared_caches": _Mounts(answer=(mirror,))})
    path = f"/state/{MIRRORS_GITCONFIG}"
    envs: list[dict[str, str]] = []
    files: list[bytes | None] = []
    opener = world.adapter.open

    async def open_(sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN202
        envs.append(dict(launch.env))
        held = world.broker.sandboxes[sandbox.id].files.get(path)
        files.append(held.data if held is not None else None)
        return await opener(sandbox, launch, restore, correlation=correlation)

    world.adapter.open = open_  # type: ignore[method-assign]
    assert (await world.controller.run_turn(world.run(environment_extension={PART: ["pypi"]}), task(0, "say ok"))).reason == "completed"
    [env] = envs
    pairs = {(env[f"GIT_CONFIG_KEY_{i}"], env[f"GIT_CONFIG_VALUE_{i}"]) for i in range(int(env.get("GIT_CONFIG_COUNT", "0")))}
    assert ("include.path", path) in pairs, "the rules ride one included file"
    assert not any(key.endswith("insteadOf") for key, _ in pairs), "no rule rides the environment"
    assert files == [mirrors_gitconfig(rules)]

    plain = World(config_overrides={"shared_caches": _Mounts()})
    assert (await plain.controller.run_turn(plain.run(environment_extension={PART: ["pypi"]}), task(0, "say ok"))).reason == "completed"
    assert all(path not in s.files for s in plain.broker.sandboxes.values())
