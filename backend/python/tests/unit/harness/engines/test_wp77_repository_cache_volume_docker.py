"""WP77-GITF-D1..D3 (Docker lane): a repository fetch writes its cache on the data volume as each install runs it.

In a manual research run, every approved ``repository_fetch`` was refused ``PermissionError: [Errno 13] Permission
denied: '/data/pipeshub/harness'``. The worker ran as the host user on the e2e stack's app image, which has no
``/data/pipeshub``, so Docker made the volume's mount point root's 755, and the stack handed only its blob volume to
that user. The shipped image is not affected: its Dockerfile makes ``/data/pipeshub`` the app user's (a new volume
copies that), and its entrypoint hands a volume an older root-running release left to that user before dropping to it.

Each case starts from a new ``pxh-`` volume whose root is root's 755, which is both what the e2e stack gets and what an
upgrade from a root-running release has, then runs the fetch's own cache writes (``RepositoryFetcher``'s cache under
the worker's default folder, from this tree) the way that install starts the worker:

- D1: the shipped entrypoint (``deployment/docker-entrypoint.sh``, run as root as the image runs it), which drops to
  uid 1000 before the probe;
- D2: the e2e stack: ``run.sh``'s own ``own_app_volumes``, then the probe as the host user, as the app container runs;
- D3 (when ``PXH_WP77_APP_IMAGE`` names a built app image): that image, with its own entrypoint and its own code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

REPO = Path(__file__).resolve().parents[6]
PYTHON_ROOT = REPO / "backend" / "python"
HELPER_IMAGE = "python:3.12-slim"
SITE = Path(os.environ.get("PXH_SITE_PACKAGES", Path(__file__).resolve().parents[4] / "venv/lib/python3.12/site-packages"))
LABEL = f"pipeshub.harness.owner={os.environ.get('PXH_HARNESS_OWNER', 'x-lead')}"
# The fetch's writes: the cache folder made under the default path, a staged entry, and the entry put in place.
PROBE = r"""
import os, site, sys
sys.path.insert(0, "/app/python")
site.addsitedir("/site")  # as deployment/e2e/pyrun.py: the venv's .pth files apply, its stray modules come last
from pathlib import Path
from app.harness.repositories import DEFAULT_CACHE_DIR
from harness_core.repos.cache import RepositoryCache, TAR_FILE
cache = RepositoryCache(Path(DEFAULT_CACHE_DIR), max_bytes=1 << 20, max_age_s=60)
staged = cache.temporary()
(staged / TAR_FILE).write_bytes(b"x" * 512)
entry = cache.put("k" * 64, staged, head="0" * 40, ref=None, label="github.com/onyx-dot-app/onyx")
assert entry.tar.is_file() and cache.get("k" * 64) is not None
print("wrote", entry.tar, "as", os.geteuid(), flush=True)
"""
APP_PROBE = PROBE.replace('sys.path.insert(0, "/app/python")\nsite.addsitedir("/site")', "sys.path.insert(0, '/app/python')")


def _docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=300, check=check)


def _lane() -> str | None:
    if shutil.which("docker") is None:
        return "no docker CLI"
    if _docker("image", "inspect", HELPER_IMAGE, check=False).returncode != 0:
        return f"{HELPER_IMAGE} is not available locally"
    if not (SITE / "pydantic").is_dir():
        return f"no Python 3.12 site-packages at {SITE} (PXH_SITE_PACKAGES)"
    return None


def _root_volume(name: str) -> None:
    """A new volume whose root is root's 755, as Docker makes a mount point the image lacks, and as a root-running
    release leaves it."""
    _ = _docker("volume", "create", "--label", LABEL, name)
    _ = _docker("run", "--rm", "--label", LABEL, "--network", "none", "-v", f"{name}:/v", HELPER_IMAGE,
                "sh", "-c", "chown 0:0 /v && chmod 755 /v")


@pytest.fixture
def project() -> Iterator[str]:
    """A Compose-style project name; its volumes are ``<project>_<volume>``, and all of them go at the end."""
    if (why := _lane()) is not None:
        pytest.skip(why)
    name = f"pxh-wp77-{uuid.uuid4().hex[:10]}"
    try:
        yield name
    finally:
        listed = _docker("volume", "ls", "-q", "--filter", f"name=^{name}_", check=False).stdout.split()
        if listed:
            _ = _docker("volume", "rm", "-f", *listed, check=False)


@pytest.fixture
def volume(project: str) -> str:
    name = f"{project}_pipeshub_data"
    _root_volume(name)
    return name


def _code_mounts() -> list[str]:
    return ["-v", f"{PYTHON_ROOT}:/app/python:ro", "-v", f"{SITE}:/site:ro", "-w", "/tmp", "-e", "HOME=/tmp"]


def _owner(volume: str, path: str) -> str:
    return _docker("run", "--rm", "--label", LABEL, "--network", "none", "-v", f"{volume}:/data/pipeshub",
                   HELPER_IMAGE, "stat", "-c", "%u:%g", path).stdout.strip()


def test_wp77_gitf_d1_the_shipped_entrypoint_lets_the_worker_write_its_cache(volume: str) -> None:
    entrypoint = REPO / "deployment" / "docker-entrypoint.sh"
    done = _docker(
        "run", "--rm", "--label", LABEL, "--network", "none", "--memory", "512m", "--pids-limit", "256",
        "-v", f"{volume}:/data/pipeshub", "-v", f"{entrypoint}:/usr/local/bin/docker-entrypoint.sh:ro",
        *_code_mounts(), "--entrypoint", "sh", HELPER_IMAGE,
        "/usr/local/bin/docker-entrypoint.sh", "python", "-c", PROBE,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "as 1000" in done.stdout, done.stdout
    assert _owner(volume, "/data/pipeshub/harness/repository-cache") == "1000:1000"


def test_wp77_gitf_d2_the_e2e_stack_hands_the_data_volume_to_the_user_it_runs_the_app_as(
    project: str, volume: str
) -> None:
    uid, gid = os.getuid(), os.getgid()
    _root_volume(f"{project}_pipeshub_root_local")

    def probe() -> subprocess.CompletedProcess[str]:
        return _docker(
            "run", "--rm", "--label", LABEL, "--network", "none", "--memory", "512m", "--user", f"{uid}:{gid}",
            "-v", f"{volume}:/data/pipeshub", *_code_mounts(), HELPER_IMAGE, "python", "-c", PROBE, check=False,
        )

    before = probe()
    assert before.returncode != 0 and "Permission denied: '/data/pipeshub/harness'" in before.stderr, (
        "the original failure, before the stack owns the volume: " + before.stdout + before.stderr
    )
    script = (REPO / "deployment" / "e2e" / "run.sh").read_text(encoding="utf-8")
    start = script.index("\nAPP_WRITABLE_VOLUMES=")
    body = script[start : script.index("\n}\n", script.index("\nown_app_volumes() {")) + 3]
    shell = "\n".join([
        "set -euo pipefail",
        f"PROJECT={project} RUN=wp77 OWNER_LABEL={LABEL} RUN_LABEL_KEY=io.pipeshub.e2e.run",
        f"envv() {{ case $1 in E2E_UID) echo {uid} ;; E2E_GID) echo {gid} ;; esac; }}",
        body,
        "own_app_volumes",
    ])
    owned = subprocess.run(["bash", "-c", shell], capture_output=True, text=True, timeout=120, check=False)
    assert owned.returncode == 0, owned.stdout + owned.stderr
    done = probe()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"as {uid}" in done.stdout, done.stdout
    assert _owner(volume, "/data/pipeshub/harness/repository-cache") == f"{uid}:{gid}"


def test_wp77_gitf_d3_a_built_app_image_writes_the_cache_on_a_new_and_an_upgraded_volume(project: str, volume: str) -> None:
    """The image's own entrypoint and Python; this tree's code over ``/app/python`` (an image built from another
    commit may predate the cache). A new volume takes the image's ``/data/pipeshub`` (the app user's, from the
    Dockerfile); ``volume`` is root's, as an upgrade from a root-running release finds it."""
    image = os.environ.get("PXH_WP77_APP_IMAGE", "")
    if not image:
        pytest.skip("PXH_WP77_APP_IMAGE names no built app image (the shipped Dockerfile's)")
    fresh = f"{project}_fresh_data"
    for name in (fresh, volume):
        done = _docker(
            "run", "--rm", "--label", LABEL, "--network", "none", "--memory", "1g", "-v", f"{name}:/data/pipeshub",
            "-v", f"{PYTHON_ROOT}:/app/python:ro", image, "python", "-c", APP_PROBE, check=False,
        )
        assert done.returncode == 0, name + ": " + done.stdout + done.stderr
        assert "as 1000" in done.stdout, done.stdout
