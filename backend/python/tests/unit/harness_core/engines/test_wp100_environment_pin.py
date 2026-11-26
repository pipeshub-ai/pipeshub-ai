"""C20.18 / G-17 (WP-100): the run's pinned environment (``x.pipeshub.environment``) reaches the engine's sandbox.

- WP100-ENV-1: an agent-level pin reads as its definition, and its members this build does not read are handed on.
- WP100-ENV-2: a repository-level pin is its ``devcontainer.json``: its commands are the setup, the rest reported.
- WP100-ENV-3: a pin the worker would run only in part is refused whole (a secret or reserved variable, a
  devcontainer beside a setup, a script on a repository pin, an unknown level).
- WP100-ENV-4: a spec without one sets nothing up.
- WP100-ENV-5: through the engine kernel, the pinned environment is set up in the run's fresh sandbox before the
  engine starts, with its variables, and an invalid pin fails the run with ``environment_invalid`` before any
  sandbox is acquired.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.environments import (
    PIN_INVALID,
    EnvironmentInvalid,
    pinned_environment,
    read_environment,
)
from harness_core.testing.engines.durable import EngineWorld, engine_run_spec
from harness_core.testing.sandbox.fake import FAKE_PROGRAMS

if TYPE_CHECKING:

    from pydantic import JsonValue

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.testing.sandbox.fake import FakeProcessContext

AGENT_PIN: dict[str, JsonValue] = {
    "environmentId": "python-data",
    "level": "agent",
    "setup": "pip install -r requirements.txt",
    "env": {"LANG": "C.UTF-8"},
    "cachePaths": ["/root/.cache/pip"],
    "editionPart": ["pypi", "mirror"],
}
DEVCONTAINER = '{"name": "w", "image": "python:3.12", "containerEnv": {"LANG": "C"}, "postCreateCommand": "make"}'


def _spec(pin: dict[str, JsonValue] | None) -> RunSpec:
    spec = engine_run_spec()
    return spec if pin is None else spec.model_copy(update={"extensions": {"x.pipeshub": {"environment": pin}}})


def test_wp100_env_1_an_agent_pin_is_its_definition_and_its_edition_members() -> None:
    pinned = pinned_environment(_spec(AGENT_PIN))
    assert pinned is not None
    assert pinned.definition.environment_id == "python-data" and pinned.definition.level == "agent"
    assert pinned.definition.setup == "pip install -r requirements.txt"
    assert dict(pinned.definition.env) == {"LANG": "C.UTF-8"}
    assert pinned.definition.cache_paths == ("/root/.cache/pip",)
    assert dict(pinned.extension) == {"editionPart": ["pypi", "mirror"]}


def test_wp100_env_2_a_repository_pin_is_its_devcontainer_subset() -> None:
    pinned = read_environment(
        {"environmentId": "widgets", "level": "repository", "devcontainer": DEVCONTAINER}, workspace_root="/workspace"
    )
    assert pinned.definition.level == "repository"
    assert pinned.definition.setup is not None and pinned.definition.setup.endswith("make\n")
    assert "cd '/workspace'" in pinned.definition.setup
    assert dict(pinned.definition.env) == {"LANG": "C"}
    assert pinned.ignored == ("image",)


@pytest.mark.parametrize(
    ("pin", "reason"),
    [
        ({**AGENT_PIN, "env": {"GITHUB_TOKEN": "x"}}, "environment_variable_secret"),
        ({**AGENT_PIN, "env": {"HTTPS_PROXY": "http://elsewhere"}}, "environment_variable_reserved"),
        ({**AGENT_PIN, "devcontainer": DEVCONTAINER}, PIN_INVALID),
        ({"environmentId": "r", "level": "repository", "devcontainer": DEVCONTAINER, "setup": "x"}, PIN_INVALID),
        ({"environmentId": "r", "level": "repository"}, PIN_INVALID),
        ({**AGENT_PIN, "level": "image"}, PIN_INVALID),
        ({**AGENT_PIN, "cachePaths": ["/workspace"]}, "environment_cache_invalid"),
        ({"environmentId": "r", "level": "repository", "devcontainer": "{not json"}, "devcontainer_invalid"),
    ],
)
def test_wp100_env_3_a_pin_run_only_in_part_is_refused(pin: dict[str, JsonValue], reason: str) -> None:
    with pytest.raises(EnvironmentInvalid) as refused:
        pinned = pinned_environment(_spec(pin))
        # A cache folder over the workspace is refused where the controller asks for the snapshot's policy.
        assert pinned is not None
        _ = pinned.definition.policy("/workspace")
    assert refused.value.reason == reason


def test_wp100_env_4_a_spec_without_one_sets_nothing_up() -> None:
    assert pinned_environment(_spec(None)) is None


def _setup_world() -> tuple[EngineWorld, list[dict[str, str]]]:
    world = EngineWorld()
    setups: list[dict[str, str]] = []
    shell = FAKE_PROGRAMS["sh"]

    async def sh(context: FakeProcessContext) -> int:
        argv = context.argv
        if len(argv) > 3 and argv[1] == "-c" and argv[3] == "pxh-environment-setup":
            setups.append({"script": argv[2], **context.env})
            return 0
        return await shell(context)

    world.broker.programs["sh"] = sh
    return world, setups


async def test_wp100_env_5_the_kernel_sets_the_pinned_environment_up_before_the_engine() -> None:
    world, setups = _setup_world()
    world.admit("run-env", "say done", spec=_spec(AGENT_PIN))
    outcome = await world.attempt("run-env")
    assert outcome.status == "completed", outcome
    [setup] = setups
    assert setup["script"] == "pip install -r requirements.txt" and setup["LANG"] == "C.UTF-8"


async def test_wp100_env_5_an_invalid_pin_fails_the_run_before_any_sandbox() -> None:
    world, setups = _setup_world()
    world.admit("run-bad", "say done", spec=_spec({**AGENT_PIN, "env": {"API_KEY": "x"}}))
    outcome = await world.attempt("run-bad")
    assert (outcome.status, outcome.reason) == ("failed", "environment_variable_secret"), outcome
    assert setups == [] and world.broker.acquisitions == 0
