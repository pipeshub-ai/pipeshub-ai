"""WP82-CAP-1: the worker's file cap is Node's.

The sync keeps every collect within ``MAX_WORKSPACE_FILES`` so Node takes the commit; a cap that drifted from
Node's ``domain/workspace.ts`` would bring back run 6's refused saves (Node 20,000, the sync then up to 50,000).
"""

from __future__ import annotations

import re

from harness_core.workspace import MAX_WORKSPACE_FILES
from harness_core.workspace.sync import SyncLimits
from tests.cohesion.support import PYTHON_ROOT

NODE_WORKSPACE = PYTHON_ROOT.parent / "nodejs/apps/src/modules/agent-harness/domain/workspace.ts"


def test_wp82_cap_1_the_sync_and_node_hold_the_same_number_of_files() -> None:
    found = re.search(r"export const MAX_WORKSPACE_FILES = ([\d_]+);", NODE_WORKSPACE.read_text(encoding="utf-8"))
    assert found is not None, "Node's MAX_WORKSPACE_FILES moved"
    assert int(found.group(1).replace("_", "")) == MAX_WORKSPACE_FILES
    assert SyncLimits().max_workspace_files == MAX_WORKSPACE_FILES
