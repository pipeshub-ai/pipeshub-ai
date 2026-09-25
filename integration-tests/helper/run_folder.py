"""A folder of its own for each test run inside a shared bucket, container or share.

Object-store suites share one pre-provisioned bucket per provider. Two runs at
once (two pull requests, say) used to upload into, rename in and clear the same
bucket, and failed on each other's changes. Each run now works in its own
top-level folder, its connector syncs only that folder (the "Folders" sync
filter), and teardown clears only that folder.
"""

from __future__ import annotations

import os
import uuid

RUN_FOLDER_PREFIX = "it-"


def new_run_folder() -> str:
    """A unique top-level folder for this run, such as ``it-35381439546-1-3f2a9c1b/``."""
    run_id = os.getenv("GITHUB_RUN_ID", "local")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    return f"{RUN_FOLDER_PREFIX}{run_id}-{attempt}-{uuid.uuid4().hex[:8]}/"


def require_run_folder(folder: str) -> str:
    """Refuse anything but a run folder before deleting under it.

    Clearing takes a prefix. An empty or wrong prefix would reach every other
    run's files, or the whole bucket, so it is checked rather than trusted.
    """
    if (
        not folder
        or not folder.startswith(RUN_FOLDER_PREFIX)
        or not folder.endswith("/")
        or "/" in folder[:-1]
        or len(folder) <= len(RUN_FOLDER_PREFIX) + 1
    ):
        raise ValueError(f"Refusing to clear {folder!r}: not a test run folder")
    return folder


def folder_filter(folder: str) -> dict:
    """The connector config fragment that limits a sync to ``folder``."""
    return {
        "sync": {
            "values": {
                "folder_paths": {"value": [folder.rstrip("/")], "operator": "in", "type": "list"},
            }
        }
    }
