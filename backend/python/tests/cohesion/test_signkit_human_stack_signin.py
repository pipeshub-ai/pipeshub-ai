"""SIGNKIT: the human test stack can switch the sign-in add-on on, and it stays off unless asked for.

SIGNGAP made a worker advertise ``vendor_signin`` only with the egress proxy's tunnels, and the stack had no way to
start with them: the e2e runner never added ``docker-compose.egress-tunnels.yml``. ``human-test-stack.sh --engine-signin``
now writes ``E2E_ENGINE_SIGNIN=1`` to the stack's env file and ``run.sh``'s ``compose`` adds the overlay for it.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.cohesion import test_c12_sandbox_plane as c12
from tests.cohesion.support import REPO_ROOT

E2E = REPO_ROOT / "deployment" / "e2e"
RUN_SH = E2E / "run.sh"
HUMAN_SH = REPO_ROOT / "scripts" / "human-test-stack.sh"
TUNNELS = (
    REPO_ROOT / "deployment" / "docker-compose" / "docker-compose.egress-tunnels.yml"
)
HUMAN_OVERLAYS = (str(c12.E2E_OVERLAY), str(E2E / "docker-compose.human.yml"))
HUMAN_ENV = (*c12.E2E_ENV, "E2E_HUMAN_DIR=/human")

needs_compose = c12.pytestmark


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _bash(
    script: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script],
        env={
            "PATH": os.environ.get("PATH", ""),
            "HOME": os.environ.get("HOME", ""),
            **(env or {}),
        },
        capture_output=True,
        text=True,
        check=False,
    )


def _compose_files(env_lines: str) -> str:
    """What run.sh's ``compose`` passes to ``docker compose`` for a stack whose env file holds ``env_lines``."""
    with tempfile.TemporaryDirectory(prefix="pxh-signkit-") as scratch:
        env_file = Path(scratch) / "stack.env"
        _ = env_file.write_text(env_lines, encoding="utf-8")
        # A `docker` on PATH, not a shell function: `compose` runs it through `env -u ...` (so a caller's exported
        # names never override the stack's file), which execs the binary and never sees a function.
        fake = Path(scratch) / "bin" / "docker"
        fake.parent.mkdir()
        _ = fake.write_text('#!/bin/sh\necho "$*"\n', encoding="utf-8")
        fake.chmod(0o755)
        script = "\n".join(
            (
                "set -euo pipefail",
                f'PATH="{fake.parent}:$PATH"',
                f"ENV_FILE={env_file}",
                "BASE_COMPOSE=base.yml OVERLAY=e2e.yml HUMAN_OVERLAY=human.yml MCP_OVERLAY=mcp.yml",
                "MATRIX_OVERLAY=matrix.yml SANDBOX_LEGACY=legacy.yml TUNNELS_OVERLAY=tunnels.yml PROJECT=p",
                _function(RUN_SH, "envv"),
                _function(RUN_SH, "compose"),
                "compose config",
            )
        )
        done = _bash(script)
    assert done.returncode == 0, done.stderr
    return done.stdout


def test_sk1_the_runner_adds_the_tunnels_overlay_only_when_the_stack_asks_for_it() -> (
    None
):
    assert "-f tunnels.yml" in _compose_files("E2E_HUMAN=1\nE2E_ENGINE_SIGNIN=1\n")
    for off in (
        "E2E_HUMAN=1\n",
        "E2E_HUMAN=1\nE2E_ENGINE_SIGNIN=\n",
        "E2E_HUMAN=1\nE2E_ENGINE_SIGNIN=0\n",
    ):
        assert "tunnels.yml" not in _compose_files(off)
    script = RUN_SH.read_text(encoding="utf-8")
    assert (
        'TUNNELS_OVERLAY="$ROOT/deployment/docker-compose/docker-compose.egress-tunnels.yml"'
        in script
    )
    # A new stack's env file carries the choice; empty (off) unless the caller set it.
    assert "E2E_ENGINE_SIGNIN=${E2E_ENGINE_SIGNIN:-}\n" in script


def test_sk2_a_rebuild_writes_the_callers_choice_and_otherwise_keeps_the_stacks() -> (
    None
):
    with tempfile.TemporaryDirectory(prefix="pxh-signkit-") as scratch:
        env_file = Path(scratch) / "stack.env"
        _ = env_file.write_text("A=1\nE2E_ENGINE_SIGNIN=\nB=2\n", encoding="utf-8")
        loop = _function(RUN_SH, "rebuild")
        loop = loop[loop.index("  local name\n") : loop.index("  local next=")]
        script = "\n".join(
            (
                "set -euo pipefail",
                f"ENV_FILE={env_file}",
                _function(RUN_SH, "set_envv"),
                "f() {",
                loop,
                "}",
                "f",
            )
        )
        assert (
            _bash(
                script, {"E2E_ENGINE_SIGNIN": "1", "E2E_SIGNIN_MAX_HOURS": "1"}
            ).returncode
            == 0
        )
        lines = env_file.read_text(encoding="utf-8").split()
        assert lines.count("E2E_ENGINE_SIGNIN=1") == 1
        assert "E2E_ENGINE_SIGNIN=" not in lines
        assert {"A=1", "B=2", "E2E_SIGNIN_MAX_HOURS=1"} <= set(lines)
        assert not any(line.startswith("E2E_SIGNIN_IDLE_MINUTES") for line in lines)
        assert env_file.stat().st_mode & 0o777 == 0o600
        # Nothing set by the caller: the stack keeps what it has.
        assert _bash(script).returncode == 0
        assert env_file.read_text(encoding="utf-8").split() == lines


def _options(*args: str) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        (
            "set -euo pipefail",
            'die() { echo "$*" >&2; exit 1; }',
            _function(HUMAN_SH, "keep_option"),
            _function(HUMAN_SH, "parse_options"),
            'parse_options "$@"',
            'echo "signin=${E2E_ENGINE_SIGNIN-unset} idle=${E2E_SIGNIN_IDLE_MINUTES-unset}'
            ' max=${E2E_SIGNIN_MAX_HOURS-unset}"',
        )
    )
    return subprocess.run(
        ["bash", "-c", script, "options", *args],
        env={"PATH": os.environ.get("PATH", "")},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), "signin=unset idle=unset max=unset"),
        (("--engine-signin",), "signin=1 idle=unset max=unset"),
        (("--no-engine-signin",), "signin=0 idle=unset max=unset"),
        (
            (
                "--engine-signin",
                "--signin-idle-minutes",
                "5",
                "--signin-max-hours",
                "1",
            ),
            "signin=1 idle=5 max=1",
        ),
        (("--signin-idle-minutes", "default"), "signin=unset idle= max=unset"),
    ],
)
def test_sk3_the_scripts_options(args: tuple[str, ...], expected: str) -> None:
    done = _options(*args)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == expected


@pytest.mark.parametrize(
    "args",
    [
        ("--signin-idle-minutes", "4"),
        ("--signin-idle-minutes", "121"),
        ("--signin-max-hours", "0"),
        ("--signin-max-hours", "25"),
        ("--signin-max-hours", "1h"),
        ("--signin-max-hours",),
        ("--engine-signin=yes",),
    ],
)
def test_sk4_a_wrong_option_stops_the_script_before_it_touches_anything(
    args: tuple[str, ...],
) -> None:
    with tempfile.TemporaryDirectory(prefix="pxh-signkit-") as home:
        for command in ("up", "refresh"):
            done = subprocess.run(
                ["bash", str(HUMAN_SH), command, "HEAD", *args],
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": home,
                    "HT1_HOME": f"{home}/stack",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            assert done.returncode == 1
            assert "option" in done.stderr or "whole number" in done.stderr, done.stderr
        assert not Path(home, "stack").exists()


def test_sk5_a_value_left_in_the_callers_shell_never_turns_the_add_on_on() -> None:
    script = HUMAN_SH.read_text(encoding="utf-8")
    unset = script.index(
        "unset E2E_ENGINE_SIGNIN E2E_SIGNIN_IDLE_MINUTES E2E_SIGNIN_MAX_HOURS E2E_PUBLIC_URL\n"
    )
    assert unset < script.index("\ncmd_up() {") and unset < script.index("\ncase ")


@needs_compose
def test_sk6_with_the_add_on_the_proxy_and_the_worker_both_read_the_tunnels_switch() -> (
    None
):
    on = c12._services(
        c12._render((*HUMAN_OVERLAYS, str(TUNNELS)), "graph-neo4j,sandbox,sandbox-broker", HUMAN_ENV)
    )
    for service in (c12.APP, c12.PROXY):
        assert (
            str(on[service]["environment"]["EGRESS_CONNECT_TUNNELS"]).lower() == "true"
        )
    assert c12.EGRESS in c12._networks(on[c12.PROXY])
    assert {name for name, svc in on.items() if c12.EGRESS in c12._networks(svc)} == {
        c12.PROXY
    }
    off = c12._services(c12._render(HUMAN_OVERLAYS, "graph-neo4j,sandbox,sandbox-broker", HUMAN_ENV))
    for service in (c12.APP, c12.PROXY):
        assert str(
            off[service]["environment"].get("EGRESS_CONNECT_TUNNELS", "")
        ).lower() in ("", "false")
    assert c12.EGRESS not in c12._networks(off[c12.PROXY])


@needs_compose
def test_sk7_the_keep_times_reach_the_worker_and_stay_the_products_by_default() -> None:
    default = c12._services(
        c12._render(HUMAN_OVERLAYS, "graph-neo4j,sandbox,sandbox-broker", HUMAN_ENV)
    )[c12.APP]["environment"]
    assert (
        default["HARNESS_SIGNIN_IDLE_MINUTES"] == ""
        and default["HARNESS_SIGNIN_MAX_HOURS"] == ""
    )
    short = c12._services(
        c12._render(
            HUMAN_OVERLAYS,
            "graph-neo4j,sandbox,sandbox-broker",
            (*HUMAN_ENV, "E2E_SIGNIN_IDLE_MINUTES=5", "E2E_SIGNIN_MAX_HOURS=1"),
        )
    )[c12.APP]["environment"]
    assert (
        short["HARNESS_SIGNIN_IDLE_MINUTES"] == "5"
        and short["HARNESS_SIGNIN_MAX_HOURS"] == "1"
    )


def _envv() -> str:
    """The script's one-line ``envv`` (``_function`` reads only multi-line ones)."""
    return next(line for line in HUMAN_SH.read_text(encoding="utf-8").splitlines() if line.startswith("envv() {"))


def _site_packages(env_lines: str | None, caller: str | None) -> str:
    with tempfile.TemporaryDirectory(prefix="pxh-signkit-") as work:
        (Path(work) / "main/backend/python/venv/lib/python3.12/site-packages").mkdir(parents=True)
        if env_lines is not None:
            _ = (Path(work) / "stack.env").write_text(env_lines, encoding="utf-8")
        script = "\n".join(
            (
                "set -euo pipefail",
                f"WORK={work} MAIN_REPO={work}/main",
                _envv(),
                _function(HUMAN_SH, "site_packages"),
                "site_packages",
            )
        )
        done = _bash(script, {"E2E_SITE_PACKAGES": caller} if caller else None)
    assert done.returncode == 0, done.stderr
    return done.stdout.strip().replace(work, "<work>")


@pytest.mark.parametrize(
    ("env_lines", "caller", "expected"),
    [
        ("E2E_SITE_PACKAGES=/kept\n", None, "/kept"),
        ("E2E_SITE_PACKAGES=/kept\n", "/caller", "/caller"),
        ("E2E_RUN=ht1\n", None, "<work>/main/backend/python/venv/lib/python3.12/site-packages"),
        (None, None, "<work>/main/backend/python/venv/lib/python3.12/site-packages"),
    ],
)
def test_sk5_a_refresh_mounts_the_packages_the_stack_was_started_with(
    env_lines: str | None, caller: str | None, expected: str
) -> None:
    assert _site_packages(env_lines, caller) == expected
