"""R31-GVISO (C6.01): a test stack chooses its engine sandboxes' runtime, and the floor follows the choice.

``E2E_SANDBOX_RUNTIME`` on ``run.sh``: ``auto`` (the default, and the human-test stack's) keeps the dev/test override
and the strongest runtime the daemon offers; ``runsc`` is a production install's floor (no override, so
``withoutGvisor`` is absent and a daemon without runsc refuses engines) and stops before a stack comes up on a daemon
without it; ``runc`` withholds runsc from the broker (``SANDBOX_WITHHOLD_RUNTIMES``) so a gVisor host still runs the
runc lane.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.cohesion.compose_files import BASE as BASE_COMPOSE
from tests.cohesion.compose_files import compose_services
from tests.cohesion.support import REPO_ROOT

RUN_SH = REPO_ROOT / "deployment" / "e2e" / "run.sh"
E2E_COMPOSE = REPO_ROOT / "deployment" / "e2e" / "docker-compose.e2e.yml"


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _runtime_env(runtime: str | None, *, runtimes: str = '{"runc":{},"runsc":{}}', **env: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as bin_dir:
        fake = Path(bin_dir) / "docker"
        fake.write_text(f"#!/bin/sh\necho '{runtimes}'\n", encoding="utf-8")
        fake.chmod(0o755)
        script = "\n".join(
            ("set -euo pipefail", 'die() { echo "$*" >&2; exit 1; }', "TEST_TRUST_MOUNT=/run/pipeshub/test-trust",
             _function(RUN_SH, "sandbox_runtime_env"), _function(RUN_SH, "acp_profiles_env"), "sandbox_runtime_env")
        )
        return subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", **({"E2E_SANDBOX_RUNTIME": runtime} if runtime else {}),
                 **env},
            capture_output=True,
            text=True,
            check=False,
        )


# R32-TRUST: the lines after the runtime's say how the stack trusts the ACP profiles (test_r32_stack_trust.py).
UNSIGNED = ["E2E_ACP_PROFILES=unsigned", "E2E_ACP_PROFILES_UNSIGNED=true", "E2E_TEST_TRUST_ROOT="]
SIGNED = ["E2E_ACP_PROFILES=signed", "E2E_ACP_PROFILES_UNSIGNED=false", "E2E_TEST_TRUST_ROOT=/run/pipeshub/test-trust"]


@pytest.mark.parametrize(
    ("runtime", "env", "lines"),
    [
        (None, {}, ["E2E_SANDBOX_RUNTIME=auto", "E2E_ENGINES_WITHOUT_GVISOR=true", "E2E_WITHHOLD_RUNTIMES=", *UNSIGNED]),
        ("auto", {"E2E_ENGINES_WITHOUT_GVISOR": "false"},
         ["E2E_SANDBOX_RUNTIME=auto", "E2E_ENGINES_WITHOUT_GVISOR=false", "E2E_WITHHOLD_RUNTIMES=", *UNSIGNED]),
        ("runsc", {}, ["E2E_SANDBOX_RUNTIME=runsc", "E2E_ENGINES_WITHOUT_GVISOR=false", "E2E_WITHHOLD_RUNTIMES=", *SIGNED]),
        # runc ignores a caller's override value: below gVisor the override is what lets engines run at all.
        ("runc", {"E2E_ENGINES_WITHOUT_GVISOR": "false"},
         ["E2E_SANDBOX_RUNTIME=runc", "E2E_ENGINES_WITHOUT_GVISOR=true", "E2E_WITHHOLD_RUNTIMES=runsc", *UNSIGNED]),
    ],
)
def test_gvs7_each_runtime_writes_its_floor_into_the_stack_env(
    runtime: str | None, env: dict[str, str], lines: list[str]
) -> None:
    done = _runtime_env(runtime, **env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == lines


def test_gvs7_runsc_on_a_daemon_without_it_stops_before_a_stack_comes_up() -> None:
    done = _runtime_env("runsc", runtimes='{"runc":{}}')
    assert done.returncode != 0 and "has no runsc" in done.stderr and "GVISOR.md" in done.stderr


def test_gvs7_an_unknown_runtime_is_refused() -> None:
    done = _runtime_env("kata")
    assert done.returncode != 0 and "is not auto, runc or runsc" in done.stderr


def test_gvs7_the_env_file_carries_the_choice_and_compose_hands_it_to_the_worker_and_the_broker() -> None:
    text = RUN_SH.read_text(encoding="utf-8")
    assert 'runtime_env="$(sandbox_runtime_env)" || exit 1' in text and "\n$runtime_env\n" in text
    e2e = compose_services(E2E_COMPOSE)
    assert "PIPESHUB_ENGINES_WITHOUT_GVISOR=${E2E_ENGINES_WITHOUT_GVISOR:-true}" in e2e["pipeshub-ai"]["environment"]
    broker = e2e["sandbox-broker"]["environment"]
    assert broker["SANDBOX_WITHHOLD_RUNTIMES"] == "${E2E_WITHHOLD_RUNTIMES:-}"
    assert broker["PIPESHUB_ENGINES_WITHOUT_GVISOR"] == "${E2E_ENGINES_WITHOUT_GVISOR:-true}"
    base = compose_services(BASE_COMPOSE)["sandbox-broker"]["environment"]
    assert base["SANDBOX_WITHHOLD_RUNTIMES"] == "${SANDBOX_WITHHOLD_RUNTIMES:-}"
