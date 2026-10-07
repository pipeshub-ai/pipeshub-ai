# pyright: ignore-file

"""Blocks-snapshot helpers for the GitLab suite.

The parsing and normalisation machinery is shared with the Jira suite verbatim —
``parse_connector_blocks_via_processor`` runs the **production** block parser, so a
snapshot validates the whole path (connector block-groups → markdown/HTML parser →
fine-grained typed blocks), which is exactly what the indexing pipeline consumes.

Only the storage differs: GitLab has two frozen snapshots (an issue and a merge
request) rather than one, so load/bootstrap take an explicit path instead of closing
over a single module constant the way ``jira_block_utils`` does.

Regenerate both with ``GITLAB_BLOCKS_BOOTSTRAP=1``, hand-review the diff, commit.
"""

import json
from pathlib import Path
from typing import Any

from connectors.jira.jira_block_utils import (  # type: ignore[import-not-found]  # noqa: F401
    normalize_blocks_container,
    parse_connector_blocks_via_processor,
)

_FIXTURES = Path(__file__).with_name("fixtures")

ISSUE_BLOCKS_PATH = _FIXTURES / "gitlab_issue_blocks.expected.json"
MR_BLOCKS_PATH = _FIXTURES / "gitlab_mr_blocks.expected.json"

# Provenance recorded next to the snapshot and stripped before comparison. Without it
# an edit to the frozen fixture on the shared GitLab group reads as a parser
# regression, which is the wrong place to start looking.
META_KEY = "_meta"


def split_meta(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = dict(snapshot)
    return expected, expected.pop(META_KEY, None) or {}


def load_expected(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a committed snapshot as ``(expected, meta)``.

    Deliberately raises rather than skipping when the file is absent: a silently
    skipped snapshot test is indistinguishable from a passing one, and these are the
    only cases that validate the parsed block tree end to end.
    """
    if not path.exists():
        raise AssertionError(
            f"Expected blocks snapshot missing: {path.name}. Generate it once with "
            "GITLAB_BLOCKS_BOOTSTRAP=1, hand-review, and commit it."
        )
    return split_meta(json.loads(path.read_text(encoding="utf-8")))


def bootstrap_expected(
    path: Path, actual: dict[str, Any], *, meta: dict[str, Any] | None = None,
) -> None:
    """Write a snapshot from an observed container (local regeneration only)."""
    payload = dict(actual)
    if meta:
        payload[META_KEY] = meta
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def assert_snapshot_source_unchanged(
    meta: dict[str, Any], *, label: str, live_updated_ms: int,
) -> None:
    """Fail with a diagnosis, not a dict diff, when the frozen fixture moved.

    A legacy snapshot without provenance is accepted silently — the next bootstrap
    adds it.
    """
    recorded = meta.get("source_updated_ms")
    if recorded is None or int(recorded) == int(live_updated_ms):
        return
    raise AssertionError(
        f"{label} was edited on the shared GitLab group after the blocks snapshot was "
        f"taken (live updated={live_updated_ms}, snapshot updated={recorded}). The "
        "parser did not regress; the source moved. Review the fixture, then "
        "regenerate with GITLAB_BLOCKS_BOOTSTRAP=1 and commit the new snapshot."
    )
