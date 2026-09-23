"""
Sample data repository helper.

Clones the pipeshub-ai/integration-test GitHub repo on demand and provides
helpers to locate and enumerate the sample data files.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple


DEFAULT_REPO_URL = "https://github.com/pipeshub-ai/integration-test.git"

# Bound the git calls. An unreachable host or a stalled transfer would otherwise
# block the session fixture that needs this data, and with it the whole test
# job, until the workflow's job-level timeout.
GIT_TIMEOUT_SECONDS = 300


def _find_repo_root(start: Path) -> Path:
    """Walk upwards from *start* until a .git folder is found."""
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return start


def _ensure_repo_cloned() -> Path:
    """
    Ensure the integration-test repository is cloned locally and return its path.

    Location and URL can be overridden via:
      - PIPESHUB_INTEGRATION_TEST_REPO_URL
      - PIPESHUB_INTEGRATION_TEST_CACHE_DIR
    """
    start = Path(__file__).resolve()
    repo_root = _find_repo_root(start)

    cache_root = Path(
        os.getenv(
            "PIPESHUB_INTEGRATION_TEST_CACHE_DIR",
            repo_root / ".integration-test-cache",
        )
    )
    cache_root.mkdir(parents=True, exist_ok=True)

    repo_dir = cache_root / "integration-test"
    repo_url = os.getenv("PIPESHUB_INTEGRATION_TEST_REPO_URL", DEFAULT_REPO_URL)

    if not (repo_dir / ".git").exists():
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(repo_dir)],
                check=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            # A killed clone can leave a partial checkout whose .git would make the
            # next run skip the clone and pull into a broken repo.
            shutil.rmtree(repo_dir, ignore_errors=True)
            raise RuntimeError(
                f"Timed out after {GIT_TIMEOUT_SECONDS}s cloning integration-test repo "
                f"from {repo_url}"
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"Failed to clone integration-test repo from {repo_url}: {exc}"
            ) from exc
    else:
        try:
            subprocess.run(
                ["git", "-C", str(repo_dir), "pull", "--ff-only"],
                check=True,
                timeout=GIT_TIMEOUT_SECONDS,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            pass  # non-fatal: keep using the cached copy

    return repo_dir


def ensure_sample_data_files_root() -> Path:
    """
    Return the path to sample-data/entities/files inside the integration-test repo,
    cloning on demand if necessary.
    """
    repo_dir = _ensure_repo_cloned()
    files_root = repo_dir / "sample-data" / "entities" / "files"
    if not files_root.exists():
        raise RuntimeError(
            f"sample-data/entities/files not found in integration-test repo at {files_root}"
        )
    return files_root


def count_sample_files() -> int:
    """Return the number of non-hidden files in the sample data directory."""
    files_root = ensure_sample_data_files_root()
    count = 0
    for _root, _dirs, files in os.walk(files_root):
        for name in files:
            if not name.startswith("."):
                count += 1
    return count


def list_sample_files() -> List[Tuple[str, str]]:
    """
    Return list of (relative_path, filename) tuples for all sample data files.

    The relative_path is relative to the files_root and uses forward slashes.
    """
    files_root = ensure_sample_data_files_root()
    result: List[Tuple[str, str]] = []
    for root, _dirs, files in os.walk(files_root):
        for name in files:
            if name.startswith("."):
                continue
            full = Path(root) / name
            rel = str(full.relative_to(files_root))
            result.append((rel, name))
    return result
