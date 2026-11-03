from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Edition of every path no overlay claims.
BASE_EDITION = "oss"

RULES_DIR = "scripts/qa/rules"
DEFAULT_OUT = "qa-out"


def tree_root() -> Path:
    """The checkout under test. The engine itself may run from another one (REPO)."""
    return Path(os.environ.get("QA_ROOT") or REPO)


def overlays(root: Path) -> list[Path]:
    """Rule overlays: scripts/<name>/qa directories that hold a rules/ dir."""
    return sorted(p.parent for p in (root / "scripts").glob("*/qa/rules") if p.is_dir())
