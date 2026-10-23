"""wait_idle.sh against a stub docker that answers like docker.exe: CRLF output."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "wait_idle.sh"

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

# Counters are read through `docker exec`, the only path on Docker Desktop.
# SCEN picks how the stub's two containers (aaa, bbb) answer.
STUB = r"""#!/usr/bin/env bash
dir=$(dirname "$0")
case "$1" in
  info) exit 0;;
  inspect) if [[ "$*" == *Labels* ]]; then printf 'proj\r\n'; else printf 'aaa\r\n'; fi;;
  ps) printf 'aaa\r\nbbb\r\n';;
  exec)
    id=$2; n=$(cat "$dir/$id.n" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$dir/$id.n"
    case "$SCEN" in
      quiet) printf 'usage_usec %d\r\n' $((1000000 + n * 1000));;
      half) { [ "$id" = bbb ] && [ $((n % 2)) -eq 1 ]; } && exit 1
            printf 'usage_usec %d\r\n' $((1000000 + n * 1000));;
      blind) exit 1;;
      busy_then_blind) [ "$n" -gt 2 ] && exit 1; printf 'usage_usec %d\r\n' $((n * 900000000));;
    esac;;
esac
"""


def _run(tmp_path: Path, scen: str, timeout_s: int) -> subprocess.CompletedProcess[str]:
    stub = tmp_path / "docker"
    stub.write_text(STUB, newline="\n")
    stub.chmod(0o755)
    env = {**os.environ, "DOCKER": stub.as_posix(), "SCEN": scen}
    return subprocess.run(
        [BASH, str(SCRIPT), "50", str(timeout_s), "1"],
        env=env, capture_output=True, text=True, timeout=60,
    )


def test_crlf_ids_and_counters_still_read_as_quiet(tmp_path: Path) -> None:
    r = _run(tmp_path, "quiet", 5)
    assert r.returncode == 0
    assert "stack quiet" in r.stdout


def test_container_readable_at_one_sample_resets_the_window(tmp_path: Path) -> None:
    r = _run(tmp_path, "half", 4)
    assert r.returncode == 1
    assert "stack quiet" not in r.stdout
    assert "changed readability" in r.stdout


def test_nothing_readable_from_the_start_is_not_waited_out(tmp_path: Path) -> None:
    r = _run(tmp_path, "blind", 5)
    assert r.returncode == 0
    assert "needs cgroup v2" in r.stdout


def test_empty_read_after_a_busy_window_keeps_waiting(tmp_path: Path) -> None:
    r = _run(tmp_path, "busy_then_blind", 4)
    assert r.returncode == 1
    assert "stack busy" in r.stdout
    assert "needs cgroup v2" not in r.stdout


def test_container_state_is_clean_of_docker_exe_cr(tmp_path: Path) -> None:
    stub = tmp_path / "docker"
    stub.write_text("#!/usr/bin/env bash\nprintf 'running\\r\\n'\n", newline="\n")
    stub.chmod(0o755)
    common = SCRIPT.parent / "_common.sh"
    r = subprocess.run(
        [BASH, "-c", f'. "{common.as_posix()}"; [ "$(container_state)" = running ] && echo ok'],
        env={**os.environ, "DOCKER": stub.as_posix()}, capture_output=True, text=True, timeout=30,
    )
    assert r.stdout.strip() == "ok", r.stderr
