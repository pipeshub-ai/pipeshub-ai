"""R25-UI: the human test stack keeps a public URL that a proxy in front of its tunnel serves.

The stack's ``FRONTEND_PUBLIC_URL`` and ``ALLOWED_ORIGINS`` were the tunnel's ``http://127.0.0.1:PORT``, written by
``run.sh`` at ``up``; a public name edited into the stack's env file by hand was lost by the next ``up``.
``human-test-stack.sh --public-url URL`` (``up`` and ``refresh``) now validates the URL, records it as
``E2E_PUBLIC_URL`` in the stack's env file, and ``run.sh``'s ``write_public_url`` makes it the public URL and one
more allowed origin, once.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests.cohesion.support import REPO_ROOT

RUN_SH = REPO_ROOT / "deployment" / "e2e" / "run.sh"
HUMAN_SH = REPO_ROOT / "scripts" / "human-test-stack.sh"
PUBLIC = "https://stack.example.com"
LOOPBACK = "http://127.0.0.1:4100,http://localhost:4100"


def _function(path: Path, name: str) -> str:
    text = path.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _one_line(path: Path, name: str) -> str:
    return next(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"{name}() {{")
    )


def _run(script: str, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", script, "test", *args],
        env={"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", ""), **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def _options(*args: str) -> subprocess.CompletedProcess[str]:
    script = "\n".join(
        (
            "set -euo pipefail",
            'die() { echo "$*" >&2; exit 1; }',
            _function(HUMAN_SH, "keep_option"),
            _function(HUMAN_SH, "public_url_option"),
            _function(HUMAN_SH, "parse_options"),
            'parse_options "$@"',
            'echo "public=${E2E_PUBLIC_URL-unset}"',
        )
    )
    return _run(script, *args)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ((), "public=unset"),
        (("--public-url", PUBLIC), f"public={PUBLIC}"),
        (("--public-url", f"{PUBLIC}/"), f"public={PUBLIC}"),
        (("--public-url", "https://Stack.Example.com/"), f"public={PUBLIC}"),
        (("--public-url", "https://next.example:8443"), "public=https://next.example:8443"),
        (("--public-url", "default"), "public="),
        (("--engine-signin", "--public-url", PUBLIC), f"public={PUBLIC}"),
    ],
)
def test_pu1_the_option_is_normalised_to_an_origin(args: tuple[str, ...], expected: str) -> None:
    done = _options(*args)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == expected


@pytest.mark.parametrize(
    "value",
    [
        "http://stack.example.com",
        f"{PUBLIC}/app",
        f"{PUBLIC}/?x=1",
        f"{PUBLIC}#top",
        f"{PUBLIC}//",
        "https://user@stack.example.com",
        "https://",
        "stack.example.com",
        "https://-bad.example",
        "",
    ],
)
def test_pu2_anything_but_an_https_origin_is_refused(value: str) -> None:
    done = _options("--public-url", value)
    assert done.returncode == 1
    assert "public URL" in done.stderr, done.stderr


def test_pu2_a_refused_url_stops_up_and_refresh_before_they_touch_anything() -> None:
    with tempfile.TemporaryDirectory(prefix="pxh-pubu-") as home:
        for command in ("up", "refresh"):
            done = subprocess.run(
                ["bash", str(HUMAN_SH), command, "HEAD", "--public-url", "http://plain.example"],
                env={"PATH": os.environ.get("PATH", ""), "HOME": home, "HT1_HOME": f"{home}/stack"},
                capture_output=True,
                text=True,
                check=False,
            )
            assert done.returncode == 1
            assert "public URL" in done.stderr, done.stderr
        assert not Path(home, "stack").exists()


def _write_public_url(env_lines: str) -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix="pxh-pubu-") as scratch:
        env_file = Path(scratch) / "stack.env"
        _ = env_file.write_text(env_lines, encoding="utf-8")
        script = "\n".join(
            (
                "set -euo pipefail",
                f"ENV_FILE={env_file}",
                _one_line(RUN_SH, "envv"),
                _function(RUN_SH, "set_envv"),
                _function(RUN_SH, "write_public_url"),
                "write_public_url",
            )
        )
        done = _run(script)
        assert done.returncode == 0, done.stderr
        assert env_file.stat().st_mode & 0o777 == 0o600
        lines = [line for line in env_file.read_text(encoding="utf-8").splitlines() if line]
    names = [line.split("=", 1)[0] for line in lines]
    assert len(names) == len(set(names)), lines
    return dict(line.split("=", 1) for line in lines)


def test_pu3_a_new_stack_without_a_public_url_is_reached_through_the_tunnel() -> None:
    env = _write_public_url("E2E_APP_PORT=4100\nE2E_PUBLIC_URL=\n")
    assert env["ALLOWED_ORIGINS"] == LOOPBACK
    assert env["FRONTEND_PUBLIC_URL"] == "http://127.0.0.1:4100"


def test_pu3_a_public_url_becomes_the_public_url_and_one_more_origin() -> None:
    env = _write_public_url(f"E2E_APP_PORT=4100\nE2E_PUBLIC_URL={PUBLIC}\n")
    assert env["ALLOWED_ORIGINS"] == f"{LOOPBACK},{PUBLIC}"
    assert env["FRONTEND_PUBLIC_URL"] == PUBLIC


def test_pu3_the_hand_edited_env_is_kept_once() -> None:
    hand = f"E2E_APP_PORT=4100\nALLOWED_ORIGINS={LOOPBACK},{PUBLIC}\nFRONTEND_PUBLIC_URL={PUBLIC}\nE2E_PUBLIC_URL={PUBLIC}\n"
    env = _write_public_url(hand)
    assert env["ALLOWED_ORIGINS"] == f"{LOOPBACK},{PUBLIC}"
    assert env["FRONTEND_PUBLIC_URL"] == PUBLIC


def test_pu3_a_new_public_url_replaces_the_old_and_keeps_other_origins() -> None:
    before = (
        f"E2E_APP_PORT=4100\nALLOWED_ORIGINS={LOOPBACK},https://other.example,{PUBLIC}\n"
        f"FRONTEND_PUBLIC_URL={PUBLIC}\nE2E_PUBLIC_URL=https://next.example:8443\n"
    )
    env = _write_public_url(before)
    assert env["ALLOWED_ORIGINS"] == f"{LOOPBACK},https://other.example,https://next.example:8443"
    assert env["FRONTEND_PUBLIC_URL"] == "https://next.example:8443"


def test_pu3_default_goes_back_to_the_tunnel() -> None:
    before = f"E2E_APP_PORT=4100\nALLOWED_ORIGINS={LOOPBACK},{PUBLIC}\nFRONTEND_PUBLIC_URL={PUBLIC}\nE2E_PUBLIC_URL=\n"
    env = _write_public_url(before)
    assert env["ALLOWED_ORIGINS"] == LOOPBACK
    assert env["FRONTEND_PUBLIC_URL"] == "http://127.0.0.1:4100"


def _rebuild_options(env_lines: str, caller: dict[str, str]) -> str:
    with tempfile.TemporaryDirectory(prefix="pxh-pubu-") as scratch:
        env_file = Path(scratch) / "stack.env"
        _ = env_file.write_text(env_lines, encoding="utf-8")
        body = _function(RUN_SH, "rebuild")
        body = body[body.index("  local name\n") : body.index("  local next=")]
        script = "\n".join(
            (
                "set -euo pipefail",
                f"ENV_FILE={env_file}",
                _one_line(RUN_SH, "envv"),
                _function(RUN_SH, "set_envv"),
                _function(RUN_SH, "write_public_url"),
                "f() {",
                body,
                "}",
                "f",
            )
        )
        done = _run(script, env=caller)
        assert done.returncode == 0, done.stderr
        return env_file.read_text(encoding="utf-8")


def test_pu4_a_refresh_writes_the_callers_url_and_otherwise_keeps_the_stacks() -> None:
    hand = f"E2E_APP_PORT=4100\nALLOWED_ORIGINS={LOOPBACK},{PUBLIC}\nFRONTEND_PUBLIC_URL={PUBLIC}\n"
    assert _rebuild_options(hand, {}) == hand
    moved = _rebuild_options(hand, {"E2E_PUBLIC_URL": "https://next.example"})
    assert "FRONTEND_PUBLIC_URL=https://next.example\n" in moved
    assert f"ALLOWED_ORIGINS={LOOPBACK},https://next.example\n" in moved
    assert "E2E_PUBLIC_URL=https://next.example\n" in moved


def test_pu5_the_runner_and_the_script_carry_the_option_through() -> None:
    run = RUN_SH.read_text(encoding="utf-8")
    assert "E2E_PUBLIC_URL=${E2E_PUBLIC_URL:-}\n" in run
    # A new stack's env file is finished by write_public_url, not by fixed loopback lines.
    assert "\n  write_public_url\n  chmod 600" in run
    assert "FRONTEND_PUBLIC_URL=http://127.0.0.1:%s" not in run
    human = HUMAN_SH.read_text(encoding="utf-8")
    unset = human.index("unset E2E_ENGINE_SIGNIN E2E_SIGNIN_IDLE_MINUTES E2E_SIGNIN_MAX_HOURS E2E_PUBLIC_URL\n")
    assert unset < human.index("\ncmd_up() {")
    for command in ("cmd_up", "cmd_refresh"):
        assert 'require_public_url_support "$sha"' in _function(HUMAN_SH, command)
    # The marker require_public_url_support looks for in a commit's run.sh.
    assert "\nwrite_public_url() {" in run


def test_pu6_the_tunnel_output_and_credentials_name_the_public_url() -> None:
    with tempfile.TemporaryDirectory(prefix="pxh-pubu-") as work:
        _ = Path(work, "stack.env").write_text(
            f"E2E_APP_PORT=4100\nFRONTEND_PUBLIC_URL={PUBLIC}\nE2E_ADMIN_EMAIL=a@x\nE2E_ADMIN_PASSWORD=p\n", encoding="utf-8"
        )
        _ = Path(work, "member.json").write_text('{"email": "m@x", "password": "q"}', encoding="utf-8")
        _ = Path(work, "sha").write_text("abc\n", encoding="utf-8")
        script = "\n".join(
            (
                "set -euo pipefail",
                f"WORK={work} HT1_HOME={work} CREDENTIALS={work}/credentials.txt PROJECT=p",
                "say() { printf 'human-test-stack: %s\\n' \"$*\"; }",
                _one_line(HUMAN_SH, "envv"),
                _function(HUMAN_SH, "write_credentials"),
                _function(HUMAN_SH, "tunnel_line"),
                "write_credentials",
                "tunnel_line",
            )
        )
        done = _run(script)
        assert done.returncode == 0, done.stderr
        assert f"public URL         {PUBLIC}" in done.stdout
        assert f"Public URL: {PUBLIC}" in Path(work, "credentials.txt").read_text(encoding="utf-8")
        # Without a public URL, only the tunnel is named.
        _ = Path(work, "stack.env").write_text(
            "E2E_APP_PORT=4100\nFRONTEND_PUBLIC_URL=http://127.0.0.1:4100\nE2E_ADMIN_EMAIL=a@x\nE2E_ADMIN_PASSWORD=p\n",
            encoding="utf-8",
        )
        done = _run(script)
        assert done.returncode == 0, done.stderr
        assert "public URL" not in done.stdout
        assert "Public URL" not in Path(work, "credentials.txt").read_text(encoding="utf-8")


def _big_run_sh_repo(root: Path) -> str:
    """A repo whose run.sh is far past a pipe's buffer, with the line the checks look for at the top."""
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    run_sh = root / "deployment" / "e2e" / "run.sh"
    run_sh.parent.mkdir(parents=True)
    run_sh.write_text("write_public_url() {\n}\nTUNNELS_OVERLAY=x\n" + "# filler\n" * 60_000, encoding="utf-8")
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "t"], check=True)
    return subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()


def test_pu9_the_commit_checks_hold_on_a_run_sh_past_a_pipe_buffer() -> None:
    """``git show | grep -q`` under pipefail failed at random once run.sh passed 64 KB (the 31e9f21149 refresh)."""
    with tempfile.TemporaryDirectory() as tmp:
        sha = _big_run_sh_repo(Path(tmp))
        script = "\n".join(
            (
                "set -euo pipefail",
                'die() { echo "$*" >&2; exit 1; }',
                f'MAIN_REPO="{tmp}"',
                _function(HUMAN_SH, "commit_run_sh_has"),
                _function(HUMAN_SH, "require_public_url_support"),
                _function(HUMAN_SH, "require_signin_support"),
                'for _ in $(seq 40); do require_public_url_support "$1"; require_signin_support "$1"; done',
                'commit_run_sh_has "$1" "^no_such_line" && exit 3',
                "echo held",
            )
        )
        done = _run(script, sha, env={"E2E_PUBLIC_URL": PUBLIC, "E2E_ENGINE_SIGNIN": "1"})
        assert done.returncode == 0, done.stderr
        assert done.stdout.strip() == "held"
