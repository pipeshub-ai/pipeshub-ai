"""Which build this process runs: release version, commit, and build time.

An image carries the values as environment variables, set from build args.
A run from a source checkout has no such variables, so it asks git once, at
import time. A value that cannot be found is `None`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import TypedDict

_ENV_KEYS = ("APP_VERSION", "GIT_COMMIT", "BUILD_TIME")
_GIT_TIMEOUT_SECONDS = 2


class BuildInfo(TypedDict):
    version: str | None
    commitId: str | None
    buildTime: str | None


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def resolve_build_info() -> BuildInfo:
    # The Dockerfile always defines these, empty when no build arg was passed,
    # so a container never shells out to git.
    if any(key in os.environ for key in _ENV_KEYS):
        return BuildInfo(
            version=os.environ.get("APP_VERSION", "").strip() or None,
            commitId=os.environ.get("GIT_COMMIT", "").strip() or None,
            buildTime=os.environ.get("BUILD_TIME", "").strip() or None,
        )
    tag = _git("describe", "--tags", "--abbrev=0", "--match", "v*")
    return BuildInfo(
        version=tag.removeprefix("v") if tag else None,
        commitId=_git("rev-parse", "HEAD"),
        buildTime=None,
    )


_BUILD_INFO = resolve_build_info()


def get_build_info() -> BuildInfo:
    return _BUILD_INFO
