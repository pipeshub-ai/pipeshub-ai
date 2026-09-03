# pyright: ignore-file

"""Blocks-snapshot helpers for the GitHub Teams suite.

The parsing and normalisation machinery is shared with the Jira suite verbatim —
``parse_connector_blocks_via_processor`` runs the **production** block parser, so a
snapshot validates the whole path (connector block-groups → HTML/markdown parser →
fine-grained typed blocks), which is exactly what the indexing pipeline consumes.

Only the storage differs: GitHub has two frozen snapshots (an issue and a PR) rather
than one, so load/bootstrap take an explicit path instead of closing over a single
module constant the way ``jira_block_utils`` does.

Regenerate both with ``GH_TEAMS_BLOCKS_BOOTSTRAP=1``, hand-review the diff, commit.
"""

import json
from pathlib import Path
from typing import Any

from connectors.jira.jira_block_utils import (  # type: ignore[import-not-found]  # noqa: F401
    normalize_blocks_container,
    parse_connector_blocks_via_processor,
)

_FIXTURES = Path(__file__).with_name("fixtures")

ISSUE_BLOCKS_PATH = _FIXTURES / "github_issue_blocks.expected.json"
PR_BLOCKS_PATH = _FIXTURES / "github_pr_blocks.expected.json"


def load_expected(path: Path) -> dict[str, Any]:
    """Read a committed snapshot.

    Deliberately raises rather than skipping when the file is absent: a silently
    skipped snapshot test is indistinguishable from a passing one, and this is the
    only case in the suite that validates the parsed block tree end to end.
    """
    if not path.exists():
        raise AssertionError(
            f"Expected blocks snapshot missing: {path.name}. Generate it once with "
            "GH_TEAMS_BLOCKS_BOOTSTRAP=1, hand-review, and commit it."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def bootstrap_expected(path: Path, actual: dict[str, Any]) -> None:
    """Write a snapshot from an observed container (local regeneration only)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(actual, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
