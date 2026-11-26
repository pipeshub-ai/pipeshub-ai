"""R32-TRUST (ADR-SEC-01 D7): a production-floor test stack verifies the ACP profiles as a release install does.

``E2E_SANDBOX_RUNTIME=runsc`` makes the stack sign the tree's profiles with its own test root at build (run.sh
``stack_trust_root``), mount it read-only into the worker and the broker and name it in ``PIPESHUB_TEST_TRUST_ROOT``;
no unsigned read. So the journey registry's ACP paths are feasible on that lane, which R31-SECKEY found 55 lines red
on ("This coding engine is not installed on this deployment"). The human-test stack runs at that floor by default
where the host has it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from harness_engines import release
from harness_engines.acp import registry
from harness_engines.profile_trust import TEST_ROOT_ENV, UNSIGNED_ENV, read_test_root
from tests.cohesion.compose_files import BASE as BASE_COMPOSE
from tests.cohesion.compose_files import compose_services
from tests.cohesion.support import REPO_ROOT

RUN_SH = REPO_ROOT / "deployment" / "e2e" / "run.sh"
E2E_COMPOSE = REPO_ROOT / "deployment" / "e2e" / "docker-compose.e2e.yml"
HUMAN = REPO_ROOT / "scripts" / "human-test-stack.sh"
JOURNEYS = REPO_ROOT / "docs" / "test-plan" / "data" / "journeys.json"
MOUNT = "/run/pipeshub/test-trust"


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _bash(script: str, *, runtimes: str = '{"runc":{},"runsc":{}}', **env: str) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as bin_dir:
        fake = Path(bin_dir) / "docker"
        fake.write_text(f"#!/bin/sh\necho '{runtimes}'\n", encoding="utf-8")
        fake.chmod(0o755)
        return subprocess.run(
            ["bash", "-c", script],
            env={"PATH": f"{bin_dir}:{os.environ.get('PATH', '')}", **env},
            capture_output=True,
            text=True,
            check=False,
        )


def _run_sh(*names: str) -> str:
    helpers = (
        "set -euo pipefail",
        'die() { echo "$*" >&2; exit 1; }',
        'say() { echo "$*"; }',
        f"TEST_TRUST_MOUNT={MOUNT}",
        f"ROOT={REPO_ROOT}",
        'envv() { grep -E "^$1=" "$ENV_FILE" | head -1 | cut -d= -f2-; }',
        *(_function(RUN_SH, name) for name in ("set_envv", "sandbox_runtime_env", "acp_profiles_env")),
        *(_function(RUN_SH, name) for name in names),
    )
    return "\n".join(helpers)


def _lines(done: subprocess.CompletedProcess[str]) -> dict[str, str]:
    assert done.returncode == 0, done.stderr
    return dict(line.split("=", 1) for line in done.stdout.splitlines() if "=" in line)


def _service_env(lines: dict[str, str], work: Path) -> dict[str, str]:
    """What docker-compose.e2e.yml hands the worker and the broker, with the mount read on the host."""
    root = lines["E2E_TEST_TRUST_ROOT"]
    return {
        UNSIGNED_ENV: lines["E2E_ACP_PROFILES_UNSIGNED"],
        TEST_ROOT_ENV: str(work / "test-trust") if root == MOUNT else root,
        "PIPESHUB_ENGINES_WITHOUT_GVISOR": lines["E2E_ENGINES_WITHOUT_GVISOR"],
    }


def _stack(tmp_path: Path, **env: str) -> dict[str, str]:
    """A stack's runtime lines and its trust folder, as run.sh writes them at build."""
    work = tmp_path / "run"
    work.mkdir()
    env_file = work / "stack.env"
    script = _run_sh("stack_trust_root") + '\nsandbox_runtime_env >"$ENV_FILE"\nstack_trust_root\ncat "$ENV_FILE"'
    done = _bash(script, WORK=str(work), ENV_FILE=str(env_file), E2E_SITE_PACKAGES=os.pathsep.join(sys.path), **env)
    return _service_env(_lines(done), work)


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    registry.packaged_acp_agents.cache_clear()
    read_test_root.cache_clear()


@pytest.mark.parametrize("asked", ["", "unsigned"])
def test_t1_runsc_signs_the_profiles_and_never_reads_them_unsigned(asked: str) -> None:
    done = _bash(_run_sh() + "\nsandbox_runtime_env", E2E_SANDBOX_RUNTIME="runsc", E2E_ACP_PROFILES=asked)
    lines = _lines(done)
    assert lines["E2E_ACP_PROFILES"] == "signed"
    assert lines["E2E_ACP_PROFILES_UNSIGNED"] == "false"
    assert lines["E2E_TEST_TRUST_ROOT"] == MOUNT


def test_t1_auto_keeps_todays_unsigned_read_and_may_ask_for_signed() -> None:
    assert _lines(_bash(_run_sh() + "\nsandbox_runtime_env"))["E2E_ACP_PROFILES_UNSIGNED"] == "true"
    signed = _lines(_bash(_run_sh() + "\nsandbox_runtime_env", E2E_ACP_PROFILES="signed"))
    assert (signed["E2E_ACP_PROFILES_UNSIGNED"], signed["E2E_TEST_TRUST_ROOT"]) == ("false", MOUNT)
    bad = _bash(_run_sh() + "\nsandbox_runtime_env", E2E_ACP_PROFILES="maybe")
    assert bad.returncode != 0 and "is not signed or unsigned" in bad.stderr


def test_t2_a_runsc_stack_installs_every_acp_engine_through_its_own_test_root(tmp_path: Path) -> None:
    env = _stack(tmp_path, E2E_SANDBOX_RUNTIME="runsc")
    assert env[UNSIGNED_ENV] == "false" and env["PIPESHUB_ENGINES_WITHOUT_GVISOR"] == "false"
    assert sorted(os.listdir(env[TEST_ROOT_ENV])) == ["acp_agents.json.sig", "trust-root.json"]
    acp = sorted(r.version.ref for r in release.release_allowlist() if r.version.kind.startswith("acp-"))
    installed = sorted(v.ref for v in release.installed_acp(profiles=release.acp_profiles(env)))
    assert installed == acp and len(acp) == 5


def test_t2_an_unsigned_stack_writes_an_empty_folder_for_the_mount(tmp_path: Path) -> None:
    env = _stack(tmp_path)
    assert env[TEST_ROOT_ENV] == ""
    assert os.listdir(tmp_path / "run" / "test-trust") == []


def test_t3_the_journey_registrys_acp_paths_are_feasible_on_the_runsc_lane(tmp_path: Path) -> None:
    """Each ``job.acp-*`` path of the registry names an engine the runsc lane installs: its cells are no gap there."""
    registry_doc = json.loads(JOURNEYS.read_text(encoding="utf-8"))
    paths = {p["id"]: p for p in registry_doc["paths"] if p["id"].startswith("job.acp-")}
    assert len(paths) == 5
    env = _stack(tmp_path, E2E_SANDBOX_RUNTIME="runsc")
    kinds = {v.kind for v in release.installed_acp(profiles=release.acp_profiles(env))}
    assert {p["code"]["engine"] for p in paths.values()} <= kinds
    # A gap naming no feature writes the whole path off (the runsc lane once lacked the ACP engines); a gap naming
    # features tracks probes not yet driven there, and may cover an ACP path like any other.
    lanes = {cell for gap in registry_doc["gaps"] if not gap.get("features") for cell in gap.get("paths", [])}
    assert not set(paths) & lanes, "no gap writes off an ACP path"


def test_t4_compose_hands_the_choice_and_the_read_only_root_to_the_worker_and_the_broker() -> None:
    e2e = compose_services(E2E_COMPOSE)
    app, broker = e2e["pipeshub-ai"], e2e["sandbox-broker"]
    assert "PIPESHUB_ACP_PROFILES_UNSIGNED=${E2E_ACP_PROFILES_UNSIGNED:-true}" in app["environment"]
    assert "PIPESHUB_TEST_TRUST_ROOT=${E2E_TEST_TRUST_ROOT:-}" in app["environment"]
    assert broker["environment"]["PIPESHUB_ACP_PROFILES_UNSIGNED"] == "${E2E_ACP_PROFILES_UNSIGNED:-true}"
    assert broker["environment"]["PIPESHUB_TEST_TRUST_ROOT"] == "${E2E_TEST_TRUST_ROOT:-}"
    mount = f"${{E2E_WORK}}/test-trust:{MOUNT}:ro"
    assert mount in app["volumes"] and mount in broker["volumes"]


def test_t4_a_production_install_ships_neither_the_unsigned_read_nor_a_test_root() -> None:
    base = compose_services(BASE_COMPOSE)
    assert "PIPESHUB_ACP_PROFILES_UNSIGNED=${PIPESHUB_ACP_PROFILES_UNSIGNED:-false}" in base["pipeshub-ai"]["environment"]
    assert base["sandbox-broker"]["environment"]["PIPESHUB_ACP_PROFILES_UNSIGNED"] == "${PIPESHUB_ACP_PROFILES_UNSIGNED:-false}"
    text = (REPO_ROOT / "deployment" / "docker-compose" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "PIPESHUB_TEST_TRUST_ROOT" not in text


def test_t5_a_rebuild_moves_a_stack_to_the_callers_runtime_and_an_old_stack_keeps_reading_unsigned(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "stack.env"
    env_file.write_text("E2E_SANDBOX_RUNTIME=auto\nE2E_ENGINES_WITHOUT_GVISOR=true\nE2E_WITHHOLD_RUNTIMES=\n")
    script = _run_sh("restack_runtime") + '\nrestack_runtime\ncat "$ENV_FILE"'
    kept = _lines(_bash(script, ENV_FILE=str(env_file)))
    assert (kept["E2E_SANDBOX_RUNTIME"], kept["E2E_ACP_PROFILES"]) == ("auto", "unsigned")
    moved = _lines(_bash(script, ENV_FILE=str(env_file), E2E_SANDBOX_RUNTIME="runsc"))
    assert (moved["E2E_SANDBOX_RUNTIME"], moved["E2E_ENGINES_WITHOUT_GVISOR"], moved["E2E_ACP_PROFILES"]) == (
        "runsc",
        "false",
        "signed",
    )
    again = _lines(_bash(script, ENV_FILE=str(env_file)))
    assert again["E2E_ACP_PROFILES"] == "signed", "a later rebuild keeps the stack's runtime"
    text = RUN_SH.read_text(encoding="utf-8")
    rebuild = text[text.index("\nrebuild() {") :]
    assert rebuild.index("restack_runtime") < rebuild.index("stack_trust_root") < rebuild.index("compose up")


@pytest.mark.parametrize(("runtimes", "chosen", "want"), [
    ('{"runc":{},"runsc":{}}', None, "runsc"),
    ('{"runc":{}}', None, "auto"),
    ('{"runc":{},"runsc":{}}', "auto", "auto"),
])
def test_t6_the_human_stack_runs_at_the_production_floor_by_default_where_the_host_has_it(
    tmp_path: Path, runtimes: str, chosen: str | None, want: str
) -> None:
    if chosen is not None:
        (tmp_path / "runtime").write_text(chosen + "\n")
    script = "\n".join((
        "set -euo pipefail",
        'die() { echo "$*" >&2; exit 1; }',
        f"HT1_HOME={tmp_path}",
        f"RUNTIME_CHOICE={tmp_path}/runtime",
        "commit_run_sh_has() { return 0; }",
        _function(HUMAN, "default_runtime"),
        _function(HUMAN, "choose_runtime"),
        'choose_runtime HEAD; echo "E2E_SANDBOX_RUNTIME=$E2E_SANDBOX_RUNTIME"',
    ))
    assert _lines(_bash(script, runtimes=runtimes))["E2E_SANDBOX_RUNTIME"] == want


def test_t6_a_commit_before_r32_trust_keeps_the_stacks_runtime_unless_one_was_asked_for(tmp_path: Path) -> None:
    script = "\n".join((
        "set -euo pipefail",
        'die() { echo "$*" >&2; exit 1; }',
        f"HT1_HOME={tmp_path}",
        f"RUNTIME_CHOICE={tmp_path}/runtime",
        "commit_run_sh_has() { return 1; }",
        _function(HUMAN, "default_runtime"),
        _function(HUMAN, "choose_runtime"),
        'choose_runtime OLD; echo "E2E_SANDBOX_RUNTIME=${E2E_SANDBOX_RUNTIME:-kept}"',
    ))
    assert _lines(_bash(script))["E2E_SANDBOX_RUNTIME"] == "kept"
    (tmp_path / "runtime").write_text("runsc\n")
    refused = _bash(script)
    assert refused.returncode != 0 and "predates --runtime" in refused.stderr
