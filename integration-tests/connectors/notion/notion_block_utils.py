"""Normalization + expected-snapshot handling for the Notion streamed-blocks tests.

The connector streams a ``BlocksContainer`` (``application/octet-stream`` JSON) built from a
page's block tree — or from a data source's rows — with comments attached and images inlined as
base64. That output carries per-run volatile values (uuid ids, Notion urls, source timestamps,
content hashes, and large base64 data-URIs), so this module strips or placeholders them before
comparison and loads (or bootstraps) the committed snapshot.

Mirrors ``connectors/jira/jira_block_utils.py``; the volatile-key set differs because Notion
block groups carry different metadata.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_FIXTURES = Path(__file__).with_name("fixtures")
PAGE_SNAPSHOT_PATH = _FIXTURES / "notion_page_blocks.expected.json"
DATA_SOURCE_SNAPSHOT_PATH = _FIXTURES / "notion_data_source_blocks.expected.json"

# Per-run-varying keys on Block / BlockGroup dropped before comparison.
_VOLATILE_KEYS = frozenset({
    "id",
    "source_group_id",
    "source_id",
    "source_name",
    "weburl",
    "content_hash",
    "source_creation_date",
    "source_update_date",
    "source_modified_date",
    "source_created_at",
    "source_updated_at",
    # Names embed the run id (every fixture title is suffixed) and comment author display names.
    "name",
    "description",
    # Notion signed urls carry an expiring token and a per-upload path.
    "url",
    "signed_url",
})

# No \s in the payload class: these URIs are single-line in the snapshots, and
# allowing whitespace let the match run past the URI and swallow trailing prose.
_BASE64_DATA_URI = re.compile(r"data:[^;\s)]+;base64,[A-Za-z0-9+/=]+")
_NOTION_URL = re.compile(r"https://(?:www\.)?notion\.so/\S+")
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.IGNORECASE
)


def _scrub_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    scrubbed = _BASE64_DATA_URI.sub("data:PLACEHOLDER;base64,PLACEHOLDER", value)
    scrubbed = _NOTION_URL.sub("https://notion.so/PLACEHOLDER", scrubbed)
    return _UUID.sub("UUID-PLACEHOLDER", scrubbed)


def _normalize(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _VOLATILE_KEYS:
                continue
            if key == "children_records" and isinstance(value, list):
                # child_id / child_name are volatile; only the shape is pinned.
                out[key] = [
                    {"child_type": (child or {}).get("child_type")}
                    for child in value if isinstance(child, dict)
                ]
                continue
            out[key] = _normalize(value)
        return out
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    return _scrub_text(node)


def normalize_blocks_container(container: dict) -> dict:
    """Return a run-stable normalization of a ``BlocksContainer`` dict."""
    return _normalize(container)


def parse_streamed_container(payload: bytes | str) -> dict:
    """Parse the raw streamed body into a ``BlocksContainer`` dict."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)


def load_expected(path: Path) -> dict:
    """Load a committed expected snapshot, failing loudly if absent.

    Mirrors the Jira blocks IT: the snapshot is committed and hand-reviewed, and a missing one
    is a hard failure. Regenerate by running the test once with ``NOTION_BLOCKS_BOOTSTRAP=1``,
    hand-reviewing the JSON, and committing it.
    """
    if not path.is_file():
        raise AssertionError(
            f"Blocks expected snapshot not found at {path}. Regenerate it by running the "
            "streaming test once with NOTION_BLOCKS_BOOTSTRAP=1, hand-review the JSON "
            "(block nesting, comment threading, image placeholders, table rows), and commit it."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def bootstrap_expected(path: Path, actual: dict) -> None:
    """Write ``actual`` (already normalized) as the expected snapshot — local regeneration only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(actual, indent=2, sort_keys=True), encoding="utf-8")


def iter_blocks(container: dict) -> list[dict]:
    """All ``Block`` dicts in a container."""
    return list(container.get("blocks") or [])


def iter_block_groups(container: dict) -> list[dict]:
    """All ``BlockGroup`` dicts in a container."""
    return list(container.get("block_groups") or [])


def blocks_of_type(container: dict, block_type: str) -> list[dict]:
    return [b for b in iter_blocks(container) if b.get("type") == block_type]


def blocks_of_sub_type(container: dict, sub_type: str) -> list[dict]:
    return [b for b in iter_blocks(container) if b.get("sub_type") == sub_type]


def groups_of_sub_type(container: dict, sub_type: str) -> list[dict]:
    """Comment / callout / nested-block groups are distinguished by ``sub_type``.

    The connector emits every one of them as ``GroupType.TEXT_SECTION`` (see
    ``NotionBlockParser.create_comment_group`` / ``create_comment_thread_group``), so filtering
    on ``type`` alone cannot tell a comment thread from a callout wrapper.
    """
    return [g for g in iter_block_groups(container) if g.get("sub_type") == sub_type]
